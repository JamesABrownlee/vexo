import aiosqlite
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta

logger = logging.getLogger('Vexo.Database')

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
            
            # 4. Current Session Playlist
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

            # --- Robust Migration Logic ---
            tables = {
                "playback_history": ["guild_id", "artist", "song", "url", "user_requesting"],
                "user_preferences": ["user_id", "artist", "liked_song", "score", "url"],
                "guild_autoplay_plist": ["guild_id", "artist", "song", "url"],
                "current_session_plist": ["guild_id", "type", "position", "artist", "song", "url", "user_id"]
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

# Global instance
db = Database()
