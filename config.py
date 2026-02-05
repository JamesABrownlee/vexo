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

    # Spotify
    SPOTIFY_CLIENT_ID: Optional[str] = os.getenv("SPOTIFY_CLIENT_ID")
    SPOTIFY_CLIENT_SECRET: Optional[str] = os.getenv("SPOTIFY_CLIENT_SECRET")

    # Spotify HTTP behavior (Spotipy)
    # Note: SPOTIFY_REQUEST_TIMEOUT can be a single float (seconds) or a "connect,read" tuple, e.g. "3,15".
    SPOTIFY_REQUEST_TIMEOUT: str = os.getenv("SPOTIFY_REQUEST_TIMEOUT", "10")
    SPOTIFY_RETRIES: int = int(os.getenv("SPOTIFY_RETRIES", "3"))
    SPOTIFY_STATUS_RETRIES: int = int(os.getenv("SPOTIFY_STATUS_RETRIES", "3"))
    SPOTIFY_BACKOFF_FACTOR: float = float(os.getenv("SPOTIFY_BACKOFF_FACTOR", "0.3"))
    SPOTIFY_STATUS_FORCELIST: str = os.getenv("SPOTIFY_STATUS_FORCELIST", "429,500,502,503,504")
    
    # Fallback playlist for when discovery pool is empty
    FALLBACK_PLAYLIST: Optional[str] = os.getenv("FALLBACK_PLAYLIST", "https://youtube.com/playlist?list=PLwVGR49CGF7mI6S-s1bFfNgYGm2S3ev-t")
    
    # Vexo Discovery Settings
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "data/vexo.db")
    DISCOVERY_WEIGHT_UPVOTE: int = 5
    DISCOVERY_WEIGHT_DOWNVOTE: int = -5
    DISCOVERY_WEIGHT_SKIP: int = -2
    DISCOVERY_WEIGHT_REQUEST: int = 2
    DISCOVERY_INTERACTOR_INFLUENCE: float = 1.2

    # --- Improved Discovery Algorithm Settings ---

    # Slot distribution ratios (must sum to 1.0)
    # Comfort: songs you already liked (weighted by score, not random)
    # Adjacent: new songs from liked artists / matching genres (the bridge)
    # Wildcard: surprising picks from the broader pool (the dopamine hit)
    DISCOVERY_RATIO_COMFORT: float = float(os.getenv("DISCOVERY_RATIO_COMFORT", "0.5"))
    DISCOVERY_RATIO_ADJACENT: float = float(os.getenv("DISCOVERY_RATIO_ADJACENT", "0.35"))
    DISCOVERY_RATIO_WILDCARD: float = float(os.getenv("DISCOVERY_RATIO_WILDCARD", "0.15"))

    # Temporal decay: halve the effective score every N days
    # (songs liked recently matter more than songs liked months ago)
    DISCOVERY_DECAY_HALF_LIFE_DAYS: int = int(os.getenv("DISCOVERY_DECAY_HALF_LIFE_DAYS", "14"))

    # Dedup window: don't replay a song within this many minutes
    DISCOVERY_DEDUP_MINUTES: int = int(os.getenv("DISCOVERY_DEDUP_MINUTES", "90"))

    # Genre matching bonus (when a pool track shares a genre with liked songs)
    DISCOVERY_GENRE_MATCH_SCORE: int = int(os.getenv("DISCOVERY_GENRE_MATCH_SCORE", "4"))

    # Collaborative filtering bonus (when another user in the same guild liked
    # both a song you liked AND this candidate)
    DISCOVERY_COLLAB_SCORE: int = int(os.getenv("DISCOVERY_COLLAB_SCORE", "3"))

    # Momentum bonus for matching the artist/genre of the last played song
    DISCOVERY_MOMENTUM_SCORE: int = int(os.getenv("DISCOVERY_MOMENTUM_SCORE", "2"))

    # Per-user slots count (total autoplay slots each user gets)
    DISCOVERY_SLOTS_PER_USER: int = int(os.getenv("DISCOVERY_SLOTS_PER_USER", "4"))
    
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
    
    # FFmpeg buffer size (default 2048k for smoother playback)
    FFMPEG_BUFFER_SIZE: str = os.getenv("FFMPEG_BUFFER_SIZE", "2048k")
    
    @property
    def ffmpeg_options(cls):
        """Generate FFmpeg options with configurable buffer."""
        return {
            'before_options': (
                '-reconnect 1 '
                '-reconnect_streamed 1 '
                '-reconnect_delay_max 5 '
                '-analyzeduration 50000000 '  # 50 seconds of analysis
                '-probesize 50000000 '        # 50MB probe size
                '-fflags +discardcorrupt '    # Discard corrupt packets
            ),
            'options': f'-vn -bufsize {cls.FFMPEG_BUFFER_SIZE}'
        }
    
    # Static fallback for imports
    FFMPEG_OPTIONS = {
        'before_options': (
            '-reconnect 1 '
            '-reconnect_streamed 1 '
            '-reconnect_delay_max 5 '
            '-analyzeduration 50000000 '
            '-probesize 50000000 '
            '-fflags +discardcorrupt '
        ),
        'options': f'-vn -bufsize {os.getenv("FFMPEG_BUFFER_SIZE", "2048k")}'
    }
    
    @classmethod
    def validate(cls) -> bool:
        """Validate required configuration."""
        if not cls.DISCORD_TOKEN:
            raise ValueError("DISCORD_TOKEN is required! Set it in your .env file.")
        return True
