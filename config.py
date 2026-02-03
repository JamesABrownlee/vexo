"""
Configuration loader for the Discord Music Bot.
Loads settings from environment variables.
"""
import os
from typing import Optional
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Bot configuration from environment variables."""
    
    # Discord
    DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN", "")
    
    # Bot Settings
    BOT_PREFIX: str = os.getenv("BOT_PREFIX", "!")
    DEFAULT_VOLUME: float = float(os.getenv("DEFAULT_VOLUME", "50")) / 100  # 0.0 - 1.0
    VOLUME_STEP: int = int(os.getenv("VOLUME_STEP", "10"))  # Volume button increment (1-50)
    
    # YouTube / yt-dlp authentication
    YTDL_COOKIES_PATH: Optional[str] = os.getenv("YTDL_COOKIES_PATH")
    YTDL_PO_TOKEN: Optional[str] = os.getenv("YTDL_PO_TOKEN")
    
    # Vexo Discovery Settings
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "data/vexo.db")
    DISCOVERY_WEIGHT_UPVOTE: int = 5
    DISCOVERY_WEIGHT_DOWNVOTE: int = -5
    DISCOVERY_WEIGHT_SKIP: int = -2
    DISCOVERY_WEIGHT_REQUEST: int = 2
    DISCOVERY_INTERACTOR_INFLUENCE: float = 1.2
    
    # Theme Colors (Vexo - Black & Neon Blue)
    COLOR_PRIMARY: int = 0x00D4FF  # Neon Blue
    COLOR_SUCCESS: int = 0x00FF88  # Neon Green
    COLOR_ERROR: int = 0xFF3366    # Neon Red
    COLOR_WARNING: int = 0xFFAA00  # Neon Orange
    COLOR_DARK: int = 0x0D0D0D     # Near Black
    
    # yt-dlp options
    YTDL_FORMAT_OPTIONS = {
        'format': 'bestaudio/best',
        'noplaylist': False,
        'nocheckcertificate': True,
        'ignoreerrors': False,
        'logtostderr': False,
        'quiet': True,
        'no_warnings': True,
        'default_search': 'ytsearch',
        'source_address': '0.0.0.0',
        'extract_flat': False,
        # Workaround flags for 403 errors
        'youtube_include_dash_manifest': False,
    }
    
    FFMPEG_OPTIONS = {
        'before_options': (
            '-reconnect 1 '
            '-reconnect_streamed 1 '
            '-reconnect_delay_max 5 '
            '-analyzeduration 10000000 '  # 10 seconds of analysis
            '-probesize 10000000 '        # 10MB probe size
            '-fflags +discardcorrupt '    # Discard corrupt packets
        ),
        'options': '-vn -bufsize 64k'     # 64kb buffer, no video
    }
    
    @classmethod
    def validate(cls) -> bool:
        """Validate required configuration."""
        if not cls.DISCORD_TOKEN:
            raise ValueError("DISCORD_TOKEN is required! Set it in your .env file.")
        return True
