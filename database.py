import aiosqlite
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from utils.logger import set_logger

logger = set_logger(logging.getLogger('Vexo.Database'))

class Database:
    """Async database wrapper for Vexo."""
    
    def __init__(self, db_path: str = "data/vexo.db"):
        self.db_path = db_path
        
    async def initialize(self):
        """Initialize the database schema with robust migrations."""
        Path("data").mkdir(exist_ok=True)
        
        async with aiosqlite.connect(self.db_path) as db:
            # 1. Playback History
            await db.execute('''
                CREATE TABLE IF NOT EXISTS playback_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER,
                    artist TEXT,
                    song TEXT,
                    url TEXT,
                    user_requesting INTEGER,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 2. User Preferences
            await db.execute('''
                CREATE TABLE IF NOT EXISTS user_preferences (
                    user_id INTEGER,
                    artist TEXT,
                    liked_song TEXT,
                    score INTEGER,
                    url TEXT,
                    PRIMARY KEY (user_id, url)
                )
            ''')
            
            # 3. Guild Autoplay Pool
            await db.execute('''
                CREATE TABLE IF NOT EXISTS guild_autoplay_plist (
                    guild_id INTEGER,
                    artist TEXT,
                    song TEXT,
                    url TEXT,
                    PRIMARY KEY (guild_id, url)
                )
            ''')
            
            # 4. Current Session Playlist (legacy)
            await db.execute('''
                CREATE TABLE IF NOT EXISTS current_session_plist (
                    guild_id INTEGER,
                    type TEXT,
                    position INTEGER,
                    artist TEXT,
                    song TEXT,
                    url TEXT,
                    user_id INTEGER,
                    PRIMARY KEY (guild_id, type, position)
                )
            ''')

            # 5. Persistent Guild Settings
            await db.execute('''
                CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id INTEGER,
                    key TEXT,
                    value TEXT,
                    PRIMARY KEY (guild_id, key)
                )
            ''')
            
            # 6. Session Queue (per-user autoplay slots)
            await db.execute('''
                CREATE TABLE IF NOT EXISTS session_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER,
                    queue_type TEXT,
                    user_id INTEGER,
                    slot_type TEXT,
                    artist TEXT,
                    song TEXT,
                    url TEXT,
                    position INTEGER,
                    request_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create index for faster lookups
            await db.execute('''
                CREATE INDEX IF NOT EXISTS idx_session_queue_guild 
                ON session_queue(guild_id, queue_type)
            ''')

            # --- Robust Migration Logic ---
            
            # Special migration: Recreate user_preferences with proper PRIMARY KEY if needed
            # SQLite doesn't allow adding PRIMARY KEY constraints to existing tables
            async with db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='user_preferences'") as cursor:
                row = await cursor.fetchone()
                if row:
                    create_sql = row[0] or ""
                    # Check if the table has the composite primary key
                    if "PRIMARY KEY" not in create_sql or "(user_id, url)" not in create_sql.replace(" ", ""):
                        logger.info("Migrating user_preferences: Recreating table with proper PRIMARY KEY...")
                        # Rename old table
                        await db.execute("ALTER TABLE user_preferences RENAME TO user_preferences_old")
                        # Create new table with proper schema
                        await db.execute('''
                            CREATE TABLE user_preferences (
                                user_id INTEGER,
                                artist TEXT,
                                liked_song TEXT,
                                score INTEGER,
                                url TEXT,
                                PRIMARY KEY (user_id, url)
                            )
                        ''')
                        # Copy data (handling potential duplicates by aggregating scores)
                        await db.execute('''
                            INSERT INTO user_preferences (user_id, artist, liked_song, score, url)
                            SELECT user_id, artist, liked_song, SUM(score), url
                            FROM user_preferences_old
                            GROUP BY user_id, url
                        ''')
                        # Drop old table
                        await db.execute("DROP TABLE user_preferences_old")
                        logger.info("Migration complete: user_preferences table recreated.")
            
            # Special migration: Recreate guild_settings with proper PRIMARY KEY if needed
            async with db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='guild_settings'") as cursor:
                row = await cursor.fetchone()
                if row:
                    create_sql = row[0] or ""
                    # Check if the table has the composite primary key
                    if "PRIMARY KEY" not in create_sql or "(guild_id, key)" not in create_sql.replace(" ", ""):
                        logger.info("Migrating guild_settings: Recreating table with proper PRIMARY KEY...")
                        await db.execute("ALTER TABLE guild_settings RENAME TO guild_settings_old")
                        await db.execute('''
                            CREATE TABLE guild_settings (
                                guild_id INTEGER,
                                key TEXT,
                                value TEXT,
                                PRIMARY KEY (guild_id, key)
                            )
                        ''')
                        # Copy data (keep most recent value for duplicates)
                        await db.execute('''
                            INSERT OR REPLACE INTO guild_settings (guild_id, key, value)
                            SELECT guild_id, key, value FROM guild_settings_old
                        ''')
                        await db.execute("DROP TABLE guild_settings_old")
                        logger.info("Migration complete: guild_settings table recreated.")
            
            tables = {
                "guild_autoplay_plist": ["guild_id", "artist", "song", "url"],
                "current_session_plist": ["guild_id", "type", "position", "artist", "song", "url", "user_id"],
                "guild_settings": ["guild_id", "key", "value"]
            }

            for table, expected_cols in tables.items():
                async with db.execute(f"PRAGMA table_info({table})") as cursor:
                    existing_cols = {row[1]: row[2] for row in await cursor.fetchall()}
                    
                    # Special Case: Rename discord_id to user_id in user_preferences
                    if table == "user_preferences" and "discord_id" in existing_cols and "user_id" not in existing_cols:
                        await db.execute('ALTER TABLE user_preferences RENAME COLUMN discord_id TO user_id')
                        logger.info("Migrated user_preferences: Renamed discord_id to user_id.")
                        existing_cols["user_id"] = existing_cols.pop("discord_id")

                    for col in expected_cols:
                        if col not in existing_cols:
                            await db.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")
                            logger.info(f"Migration: Added missing column '{col}' to table '{table}'.")
            
            await db.commit()
            logger.info("Database initialized and migrated to latest schema.")

    async def add_to_history(self, guild_id: int, artist: str, song: str, url: str, user_id: Optional[int] = None):
        """Record a played track in history."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                INSERT INTO playback_history (guild_id, artist, song, url, user_requesting)
                VALUES (?, ?, ?, ?, ?)
            ''', (guild_id, artist, song, url, user_id))
            await db.commit()

    async def is_recently_played(self, guild_id: int, url: str, minutes: int = 120) -> bool:
        """Check if a song was played in the last N minutes."""
        async with aiosqlite.connect(self.db_path) as db:
            cutoff = (datetime.now() - timedelta(minutes=minutes)).strftime('%Y-%m-%d %H:%M:%S')
            async with db.execute('''
                SELECT 1 FROM playback_history 
                WHERE guild_id = ? AND url = ? AND timestamp > ?
                LIMIT 1
            ''', (guild_id, url, cutoff)) as cursor:
                return await cursor.fetchone() is not None

    async def update_user_preference(self, user_id: int, artist: str, song_title: str, url: str, delta: int):
        """Update a user's preference score."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                INSERT INTO user_preferences (user_id, artist, liked_song, score, url)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, url) DO UPDATE SET
                    score = score + excluded.score
            ''', (user_id, artist, song_title, delta, url))
            await db.commit()

    async def get_user_preferences(self, user_id: int) -> List[Dict[str, Any]]:
        """Get all preferences for a user."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('SELECT * FROM user_preferences WHERE user_id = ?', (user_id,)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def add_to_autoplay_pool(self, guild_id: int, artist: str, song: str, url: str):
        """Add a song to the guild's autoplay pool."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                INSERT OR IGNORE INTO guild_autoplay_plist (guild_id, artist, song, url)
                VALUES (?, ?, ?, ?)
            ''', (guild_id, artist, song, url))
            await db.commit()

    async def get_autoplay_pool(self, guild_id: int) -> List[Dict[str, Any]]:
        """Get the autoplay pool for a guild."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('SELECT * FROM guild_autoplay_plist WHERE guild_id = ?', (guild_id,)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def save_session_plist(self, guild_id: int, playlist: List[Dict[str, Any]]):
        """Save the current session playlist to DB."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('DELETE FROM current_session_plist WHERE guild_id = ?', (guild_id,))
            for i, item in enumerate(playlist):
                await db.execute('''
                    INSERT INTO current_session_plist (guild_id, type, position, artist, song, url, user_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (guild_id, item['type'], i, item.get('artist'), item.get('song'), item.get('url'), item.get('user_id')))
            await db.commit()

    async def load_session_plist(self, guild_id: int) -> List[Dict[str, Any]]:
        """Load the current session playlist from DB."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('''
                SELECT * FROM current_session_plist 
                WHERE guild_id = ? 
                ORDER BY position ASC
            ''', (guild_id,)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def set_setting(self, guild_id: int, key: str, value: Any):
        """Save a persistent setting for a guild."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                INSERT INTO guild_settings (guild_id, key, value)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id, key) DO UPDATE SET value = excluded.value
            ''', (guild_id, key, str(value)))
            await db.commit()

    async def get_settings(self, guild_id: int) -> Dict[str, Any]:
        """Load all persistent settings for a guild."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('SELECT key, value FROM guild_settings WHERE guild_id = ?', (guild_id,)) as cursor:
                rows = await cursor.fetchall()
                settings = {}
                for row in rows:
                    key, val = row['key'], row['value']
                    # Simple type conversion
                    if val.lower() == 'true': val = True
                    elif val.lower() == 'false': val = False
                    elif val.isdigit(): val = int(val)
                    else:
                        try: val = float(val)
                        except: pass
                    settings[key] = val
                return settings

    # --- Session Queue Methods (per-user autoplay slots) ---
    
    async def get_session_queue(self, guild_id: int, queue_type: str) -> List[Dict[str, Any]]:
        """Get the session queue for a guild by type (public/hidden/requested)."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('''
                SELECT * FROM session_queue 
                WHERE guild_id = ? AND queue_type = ?
                ORDER BY position ASC
            ''', (guild_id, queue_type)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def save_session_queue(self, guild_id: int, queue_type: str, items: List[Dict[str, Any]]):
        """Save/replace the session queue for a guild by type."""
        async with aiosqlite.connect(self.db_path) as db:
            # Clear existing
            await db.execute(
                'DELETE FROM session_queue WHERE guild_id = ? AND queue_type = ?',
                (guild_id, queue_type)
            )
            # Insert new items
            for i, item in enumerate(items):
                await db.execute('''
                    INSERT INTO session_queue 
                    (guild_id, queue_type, user_id, slot_type, artist, song, url, position, request_time)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    guild_id, queue_type, 
                    item.get('user_id'), item.get('slot_type', 'discovery'),
                    item.get('artist'), item.get('song'), item.get('url'),
                    i, item.get('request_time')
                ))
            await db.commit()

    async def remove_user_from_session_queue(self, guild_id: int, user_id: int):
        """Remove all slots belonging to a specific user from both queues."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                'DELETE FROM session_queue WHERE guild_id = ? AND user_id = ?',
                (guild_id, user_id)
            )
            await db.commit()
            logger.info(f"Removed user {user_id} slots from session queue in guild {guild_id}")

    async def clear_session_queue(self, guild_id: int):
        """Clear all session queue entries for a guild."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('DELETE FROM session_queue WHERE guild_id = ?', (guild_id,))
            await db.commit()

    async def get_user_liked_artists(self, user_id: int) -> List[str]:
        """Get list of artists the user has positively scored."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute('''
                SELECT DISTINCT artist FROM user_preferences 
                WHERE user_id = ? AND score > 0
            ''', (user_id,)) as cursor:
                rows = await cursor.fetchall()
                return [row[0].lower() for row in rows if row[0]]

    async def has_preferences(self, user_id: int) -> bool:
        """Check if a user has any preferences recorded."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                'SELECT 1 FROM user_preferences WHERE user_id = ? LIMIT 1',
                (user_id,)
            ) as cursor:
                return await cursor.fetchone() is not None

# Global instance
db = Database()
