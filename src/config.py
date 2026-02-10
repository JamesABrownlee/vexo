"""
Smart Discord Music Bot - Configuration
"""
import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    """Application configuration loaded from environment variables."""
    
    # Discord
    DISCORD_TOKEN: str
    
    # Spotify API
    SPOTIFY_CLIENT_ID: str
    SPOTIFY_CLIENT_SECRET: str
    
    # Database
    DATABASE_PATH: Path
    
    # Web Dashboard
    WEB_HOST: str
    WEB_PORT: int
    WEB_ADMIN_TOKEN: str | None
    
    # Optional: YouTube cookies for age-restricted content
    YTDL_COOKIES_PATH: str | None
    YTDL_PO_TOKEN: str | None

    # Optional: Radio presenter / TTS integration
    RADIO_PRESENTER_API_URL: str | None
    RADIO_PRESENTER_VOICE: str | None
    
    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        discord_token = os.getenv("DISCORD_TOKEN")
        if not discord_token:
            raise ValueError("DISCORD_TOKEN environment variable is required")
        
        spotify_client_id = os.getenv("SPOTIFY_CLIENT_ID")
        spotify_client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
        if not spotify_client_id or not spotify_client_secret:
            raise ValueError("SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET are required")
        
        database_path = Path(os.getenv("DATABASE_PATH", "./data/musicbot.db"))
        database_path.parent.mkdir(parents=True, exist_ok=True)
        
        return cls(
            DISCORD_TOKEN=discord_token,
            SPOTIFY_CLIENT_ID=spotify_client_id,
            SPOTIFY_CLIENT_SECRET=spotify_client_secret,
            DATABASE_PATH=database_path,
            WEB_HOST=os.getenv("WEB_HOST", "127.0.0.1"),
            WEB_PORT=int(os.getenv("WEB_PORT", "8080")),
            WEB_ADMIN_TOKEN=os.getenv("WEB_ADMIN_TOKEN"),
            YTDL_COOKIES_PATH=os.getenv("YTDL_COOKIES_PATH"),
            YTDL_PO_TOKEN=os.getenv("YTDL_PO_TOKEN"),
            RADIO_PRESENTER_API_URL=os.getenv("RADIO_PRESENTER_API_URL", "http://100.97.230.126:3050/api/radio-presenter"),
            RADIO_PRESENTER_VOICE=os.getenv("RADIO_PRESENTER_VOICE"),
        )


# Global config instance
config = Config.from_env()
