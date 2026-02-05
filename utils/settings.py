"""
Persistent settings storage for the music bot.
Saves and loads guild settings to/from a JSON file.
"""
import json
import os
from pathlib import Path
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger('MusicBot.Settings')

LOGGING_LEVEL = logging.INFO
VERSION_NUMBER = "2.07"
BUILD_NUMBER = "44"
VERSION_TYPE = "DEVELOPMENT"  # Options: STABLE, TESTING, DEVELOPMENT

# Default settings for a guild
DEFAULT_SETTINGS = {
    # Admin settings
    "volume": 50,  # 0-100
    "max_duration": 300,  # seconds (5 minutes default)
    "is_24_7": False,
    "is_channel_status": False,
    "favorite_artists": [],  # List of artist names
    
    # User settings (still persisted but user-changeable)
    "is_autoplay": True,  # Autoplay enabled by default
    "loop_mode": "off",  # off, song, queue
}


class SettingsManager:
    """Manages persistent guild settings."""
    
    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.settings_file = self.data_dir / "guild_settings.json"
        self._settings: Dict[str, Dict[str, Any]] = {}
        self._load()
    
    def _load(self):
        """Load settings from disk."""
        if not self.data_dir.exists():
            self.data_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Created data directory: {self.data_dir}")
        
        if self.settings_file.exists():
            try:
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    self._settings = json.load(f)
                logger.info(f"Loaded settings for {len(self._settings)} guild(s)")
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Failed to load settings: {e}")
                self._settings = {}
        else:
            logger.info("No existing settings file, starting fresh")
            self._settings = {}
    
    def _save(self):
        """Save settings to disk."""
        try:
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(self._settings, f, indent=2, ensure_ascii=False)
        except IOError as e:
            logger.error(f"Failed to save settings: {e}")
    
    def get_guild_settings(self, guild_id: int) -> Dict[str, Any]:
        """Get all settings for a guild, with defaults for missing values."""
        guild_key = str(guild_id)
        if guild_key not in self._settings:
            self._settings[guild_key] = DEFAULT_SETTINGS.copy()
            self._settings[guild_key]["favorite_artists"] = []  # Ensure it's a new list
        
        # Merge with defaults for any missing keys
        settings = self._settings[guild_key]
        for key, default_value in DEFAULT_SETTINGS.items():
            if key not in settings:
                if isinstance(default_value, list):
                    settings[key] = default_value.copy()
                else:
                    settings[key] = default_value
        
        return settings
    
    def get(self, guild_id: int, key: str, default: Any = None) -> Any:
        """Get a specific setting for a guild."""
        settings = self.get_guild_settings(guild_id)
        return settings.get(key, default if default is not None else DEFAULT_SETTINGS.get(key))
    
    def set(self, guild_id: int, key: str, value: Any):
        """Set a specific setting for a guild and save."""
        settings = self.get_guild_settings(guild_id)
        settings[key] = value
        self._save()
        logger.debug(f"Guild {guild_id}: Set {key} = {value}")
    
    def update(self, guild_id: int, **kwargs):
        """Update multiple settings at once."""
        settings = self.get_guild_settings(guild_id)
        for key, value in kwargs.items():
            settings[key] = value
        self._save()
        logger.debug(f"Guild {guild_id}: Updated {list(kwargs.keys())}")
    
    def add_favorite_artist(self, guild_id: int, artist: str):
        """Add a favorite artist."""
        settings = self.get_guild_settings(guild_id)
        if artist not in settings["favorite_artists"]:
            settings["favorite_artists"].append(artist)
            self._save()
    
    def remove_favorite_artist(self, guild_id: int, artist: str) -> bool:
        """Remove a favorite artist. Returns True if removed."""
        settings = self.get_guild_settings(guild_id)
        if artist in settings["favorite_artists"]:
            settings["favorite_artists"].remove(artist)
            self._save()
            return True
        return False
    
    def clear_favorite_artists(self, guild_id: int) -> int:
        """Clear all favorite artists. Returns count cleared."""
        settings = self.get_guild_settings(guild_id)
        count = len(settings["favorite_artists"])
        settings["favorite_artists"] = []
        self._save()
        return count


# Global instance
settings_manager = SettingsManager()
