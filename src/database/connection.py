"""
Database Connection Manager - SQLite Async
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import aiosqlite

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Async SQLite database connection manager."""
    
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._connection: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()
    
    @classmethod
    async def create(cls, db_path: Path) -> "DatabaseManager":
        """Create and initialize the database manager."""
        manager = cls(db_path)
        await manager._init_db()
        return manager
    
    async def _init_db(self) -> None:
        """Initialize the database with schema."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        async with self.connection() as db:
            # Read and execute schema
            schema_path = Path(__file__).parent / "migrations" / "init_schema.sql"
            if schema_path.exists():
                schema = schema_path.read_text()
                await db.executescript(schema)
                await db.commit()
                logger.info("Database schema initialized")
            else:
                logger.warning(f"Schema file not found: {schema_path}")
            
            # Automatic Migrations
            # 1. Add is_ephemeral to songs if missing
            try:
                await db.execute("SELECT is_ephemeral FROM songs LIMIT 1")
            except Exception:
                logger.info("Migrating: Adding is_ephemeral column to songs table")
                try:
                    await db.execute("ALTER TABLE songs ADD COLUMN is_ephemeral BOOLEAN DEFAULT 0")
                    await db.commit()
                except Exception as e:
                    logger.error(f"Migration failed: {e}")

            # 2. Expand playback_history.discovery_source CHECK constraint (SQLite requires table rebuild).
            desired_sources = ("user_request", "similar", "artist", "same_artist", "wildcard", "library")
            try:
                cur = await db.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='playback_history'"
                )
                row = await cur.fetchone()
                create_sql = (row["sql"] if row and row["sql"] else "") if row is not None else ""

                needs_migration = False
                if create_sql:
                    for src in desired_sources:
                        if f"'{src}'" not in create_sql:
                            needs_migration = True
                            break
                else:
                    # If we can't read the create statement, don't attempt a risky rebuild.
                    needs_migration = False

                if needs_migration:
                    logger.info("Migrating: Expanding playback_history.discovery_source constraint")

                    # Verify expected columns exist before rebuilding.
                    cur = await db.execute("PRAGMA table_info(playback_history)")
                    cols = await cur.fetchall()
                    col_names = [c["name"] for c in cols] if cols else []
                    expected = [
                        "id",
                        "session_id",
                        "song_id",
                        "played_at",
                        "completed",
                        "skip_reason",
                        "discovery_source",
                        "discovery_reason",
                        "for_user_id",
                    ]
                    if not all(name in set(col_names) for name in expected):
                        logger.warning(
                            "Skipping playback_history migration due to unexpected schema",
                            extra={"found": col_names},
                        )
                    else:
                        await db.execute("PRAGMA foreign_keys = OFF")
                        await db.execute("BEGIN")
                        try:
                            await db.execute(
                                """
                                CREATE TABLE IF NOT EXISTS playback_history_new (
                                    id INTEGER PRIMARY KEY,
                                    session_id TEXT REFERENCES playback_sessions(id),
                                    song_id INTEGER REFERENCES songs(id),
                                    played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                                    completed BOOLEAN DEFAULT FALSE,
                                    skip_reason TEXT CHECK(skip_reason IN ('user', 'vote', 'error') OR skip_reason IS NULL),
                                    discovery_source TEXT CHECK(discovery_source IN ('user_request', 'similar', 'artist', 'same_artist', 'wildcard', 'library')),
                                    discovery_reason TEXT,
                                    for_user_id INTEGER REFERENCES users(id)
                                );
                                """
                            )
                            await db.execute(
                                """
                                INSERT INTO playback_history_new
                                    (id, session_id, song_id, played_at, completed, skip_reason, discovery_source, discovery_reason, for_user_id)
                                SELECT
                                    id, session_id, song_id, played_at, completed, skip_reason, discovery_source, discovery_reason, for_user_id
                                FROM playback_history;
                                """
                            )
                            await db.execute("DROP TABLE playback_history")
                            await db.execute("ALTER TABLE playback_history_new RENAME TO playback_history")
                            await db.commit()
                            logger.info("Migration complete: playback_history constraint expanded")
                        except Exception as e:
                            await db.rollback()
                            logger.error(f"Migration failed: {e}")
                        finally:
                            await db.execute("PRAGMA foreign_keys = ON")
            except Exception as e:
                logger.error(f"Migration check failed (playback_history): {e}")
    
    @asynccontextmanager
    async def connection(self) -> AsyncGenerator[aiosqlite.Connection, None]:
        """Get a database connection with automatic transaction handling."""
        async with self._lock:
            if self._connection is None:
                self._connection = await aiosqlite.connect(self.db_path)
                self._connection.row_factory = aiosqlite.Row
                # Enable foreign keys
                await self._connection.execute("PRAGMA foreign_keys = ON")
            
            try:
                yield self._connection
            except Exception:
                await self._connection.rollback()
                raise
    
    async def execute(self, query: str, params: tuple = ()) -> aiosqlite.Cursor:
        """Execute a query and return the cursor."""
        async with self.connection() as db:
            cursor = await db.execute(query, params)
            await db.commit()
            return cursor
    
    async def fetch_one(self, query: str, params: tuple = ()) -> dict | None:
        """Fetch a single row as a dictionary."""
        async with self.connection() as db:
            cursor = await db.execute(query, params)
            row = await cursor.fetchone()
            return dict(row) if row else None
    
    async def fetch_all(self, query: str, params: tuple = ()) -> list[dict]:
        """Fetch all rows as a list of dictionaries."""
        async with self.connection() as db:
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    
    async def close(self) -> None:
        """Close the database connection."""
        if self._connection:
            await self._connection.close()
            self._connection = None
            logger.info("Database connection closed")
