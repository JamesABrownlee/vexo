import aiosqlite
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime

logger = logging.getLogger('Vexo.Database')

class Database:
    """Async database wrapper for Vexo."""
    
    def __init__(self, db_path: str = "data/vexo.db"):
        self.db_path = db_path
        
    async def initialize(self):
        """Initialize the database schema."""
        async with aiosqlite.connect(self.db_path) as db:
            # User Preferences: tracking likes/dislikes
            await db.execute('''
                CREATE TABLE IF NOT EXISTS user_preferences (
                    discord_id INTEGER,
                    track_id TEXT,
                    score INTEGER DEFAULT 0,
                    last_interaction TIMESTAMP,
                    PRIMARY KEY (discord_id, track_id)
                )
            ''')
            
            # Playback History: for avoiding duplicates
            await db.execute('''
                CREATE TABLE IF NOT EXISTS playback_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER,
                    track_id TEXT,
                    track_title TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Guild Settings: owner/admin and bot settings
            await db.execute('''
                CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id INTEGER PRIMARY KEY,
                    owner_id INTEGER,
                    admin_role_id INTEGER,
                    settings_json TEXT
                )
            ''')
            
            await db.commit()
            logger.info("Database initialized.")

    async def update_user_preference(self, discord_id: int, track_id: str, delta: int):
        """Update a user's preference score for a track."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                INSERT INTO user_preferences (discord_id, track_id, score, last_interaction)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(discord_id, track_id) DO UPDATE SET
                    score = score + excluded.score,
                    last_interaction = excluded.last_interaction
            ''', (discord_id, track_id, delta, datetime.now()))
            await db.commit()

    async def add_to_history(self, guild_id: int, track_id: str, track_title: str):
        """Record a played track in history."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                INSERT INTO playback_history (guild_id, track_id, track_title)
                VALUES (?, ?, ?)
            ''', (guild_id, track_id, track_title))
            await db.commit()

    async def get_recent_history(self, guild_id: int, limit: int = 50) -> List[str]:
        """Get recently played track IDs to avoid duplicates."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute('''
                SELECT track_id FROM playback_history 
                WHERE guild_id = ? 
                ORDER BY timestamp DESC LIMIT ?
            ''', (guild_id, limit)) as cursor:
                rows = await cursor.fetchall()
                return [row[0] for row in rows]

    async def get_user_preferences(self, discord_id: int) -> Dict[str, int]:
        """Get all preferences for a user."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute('''
                SELECT track_id, score FROM user_preferences WHERE discord_id = ?
            ''', (discord_id,)) as cursor:
                rows = await cursor.fetchall()
                return {row[0]: row[1] for row in rows}

    async def get_guild_settings(self, guild_id: int) -> Dict[str, Any]:
        """Get settings for a guild."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute('''
                SELECT owner_id, admin_role_id, settings_json FROM guild_settings WHERE guild_id = ?
            ''', (guild_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return {
                        "owner_id": row[0],
                        "admin_role_id": row[1],
                        "settings": json.loads(row[2]) if row[2] else {}
                    }
                return {}

    async def set_guild_settings(self, guild_id: int, owner_id: Optional[int] = None, 
                                 admin_role_id: Optional[int] = None, settings: Optional[Dict[str, Any]] = None):
        """Update guild settings."""
        async with aiosqlite.connect(self.db_path) as db:
            settings_json = json.dumps(settings) if settings is not None else None
            await db.execute('''
                INSERT INTO guild_settings (guild_id, owner_id, admin_role_id, settings_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    owner_id = COALESCE(excluded.owner_id, guild_settings.owner_id),
                    admin_role_id = COALESCE(excluded.admin_role_id, guild_settings.admin_role_id),
                    settings_json = COALESCE(excluded.settings_json, guild_settings.settings_json)
            ''', (guild_id, owner_id, admin_role_id, settings_json))
            await db.commit()

# Global instance
db = Database()
