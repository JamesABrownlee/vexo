"""
Structured Logging Helper

Provides a structured logging interface with category/event fields
that can be parsed by the WebSocket log handler for frontend filtering.

Usage:
    from src.utils.logging import get_logger
    log = get_logger(__name__)
    
    # Structured event logging
    log.event("playback", "track_started", title="Song Name", artist="Artist")
    log.event("voice", "connected", guild_id=123, channel="Music")
    
    # Standard logging (with categories)
    log.info("playback", "Starting playback loop")
    log.error("api", "Failed to fetch data", error=str(e))
"""
import logging
import time
from contextlib import contextmanager
from typing import Any


class StructuredAdapter(logging.LoggerAdapter):
    """Logger adapter that formats messages with category/event structure."""
    
    def __init__(self, logger: logging.Logger, extra: dict | None = None):
        super().__init__(logger, extra or {})
    
    def process(self, msg: str, kwargs: dict) -> tuple[str, dict]:
        """Process the log message and kwargs."""
        return msg, kwargs
    
    def _format_structured(
        self,
        category: str,
        event: str | None,
        message: str = "",
        **fields: Any
    ) -> str:
        """Format a structured log message.
        
        Output format: event_name category=cat key1=val1 key2='val2 with spaces'
        """
        parts = []
        
        # Event name (if provided) comes first
        if event:
            parts.append(event)
        
        # Category is always included
        parts.append(f"category={category}")
        
        # Add extra fields
        for key, value in fields.items():
            if value is None:
                continue
            str_val = str(value)
            # Quote values with spaces
            if ' ' in str_val or '=' in str_val:
                parts.append(f"{key}='{str_val}'")
            else:
                parts.append(f"{key}={str_val}")
        
        # Add message at the end if provided
        if message:
            parts.append(message)
        
        return " ".join(parts)
    
    def event(
        self,
        category: str,
        event: str,
        level: int = logging.INFO,
        message: str = "",
        **fields: Any
    ) -> None:
        """Log a structured event.
        
        Args:
            category: Event category (e.g., 'playback', 'voice', 'api', 'discovery')
            event: Event name (e.g., 'track_started', 'connected', 'search_completed')
            level: Log level (default INFO)
            message: Optional additional message
            **fields: Key-value pairs to include in the log
        """
        msg = self._format_structured(category, event, message, **fields)
        self.log(level, msg)
    
    # =========================================
    # Category-aware standard logging methods
    # =========================================
    
    def info_cat(self, category: str, message: str, **fields: Any) -> None:
        """Log info with category."""
        msg = self._format_structured(category, None, message, **fields)
        self.info(msg)
    
    def debug_cat(self, category: str, message: str, **fields: Any) -> None:
        """Log debug with category."""
        msg = self._format_structured(category, None, message, **fields)
        self.debug(msg)
    
    def warning_cat(self, category: str, message: str, **fields: Any) -> None:
        """Log warning with category."""
        msg = self._format_structured(category, None, message, **fields)
        self.warning(msg)
    
    def error_cat(self, category: str, message: str, **fields: Any) -> None:
        """Log error with category."""
        msg = self._format_structured(category, None, message, **fields)
        self.error(msg)
    
    def exception_cat(self, category: str, message: str, **fields: Any) -> None:
        """Log exception with category."""
        msg = self._format_structured(category, None, message, **fields)
        self.exception(msg)

    @staticmethod
    def _truncate_field(value: Any, max_len: int = 240) -> str:
        text = str(value)
        text = " ".join(text.split())
        if len(text) > max_len:
            return text[: max_len - 1] + "…"
        return text

    @contextmanager
    def span(self, category: str, name: str, **fields: Any):
        """Log a start/end span with duration in ms.

        Emits:
          - `<name>_start` (info)
          - `<name>_end` (info, includes `ms=`)
          - `<name>_error` (exception, includes `ms=`) if an exception bubbles out.

        Notes:
          - Fields are stringified and truncated to keep logs readable.
          - Use for commands, API calls, DB ops, etc.
        """
        safe_fields = {k: self._truncate_field(v) for k, v in fields.items() if v is not None}
        t0 = time.perf_counter()
        self.info_cat(category, f"{name}_start", **safe_fields)
        try:
            yield
        except Exception as e:
            ms = int((time.perf_counter() - t0) * 1000)
            self.exception_cat(category, f"{name}_error", ms=ms, error=self._truncate_field(e), **safe_fields)
            raise
        else:
            ms = int((time.perf_counter() - t0) * 1000)
            self.info_cat(category, f"{name}_end", ms=ms, **safe_fields)


def get_logger(name: str) -> StructuredAdapter:
    """Get a structured logger for the given module name.
    
    Args:
        name: Module name (typically __name__)
        
    Returns:
        StructuredAdapter instance
    """
    logger = logging.getLogger(name)
    return StructuredAdapter(logger)


# Category constants for consistency
class Category:
    """Log category constants."""
    PLAYBACK = "playback"
    VOICE = "voice"
    QUEUE = "queue"
    DISCOVERY = "discovery"
    API = "api"
    DATABASE = "database"
    SYSTEM = "system"
    USER = "user"
    PREFERENCE = "preference"
    IMPORT = "import"


# Event name constants
class Event:
    """Common event name constants."""
    # Playback
    TRACK_STARTED = "track_started"
    TRACK_ENDED = "track_ended"
    TRACK_SKIPPED = "track_skipped"
    PLAYBACK_ERROR = "playback_error"
    
    # Voice
    VOICE_CONNECTED = "voice_connected"
    VOICE_DISCONNECTED = "voice_disconnected"
    VOICE_MOVED = "voice_moved"
    
    # Queue
    TRACK_QUEUED = "track_queued"
    QUEUE_CLEARED = "queue_cleared"
    
    # Discovery
    DISCOVERY_STARTED = "discovery_started"
    DISCOVERY_COMPLETED = "discovery_completed"
    DISCOVERY_FAILED = "discovery_failed"
    STRATEGY_SELECTED = "strategy_selected"
    
    # API
    SEARCH_STARTED = "search_started"
    SEARCH_COMPLETED = "search_completed"
    SEARCH_FAILED = "search_failed"
    API_ERROR = "api_error"
    
    # System
    COG_LOADED = "cog_loaded"
    COG_UNLOADED = "cog_unloaded"
    BOT_READY = "bot_ready"
    GUILD_JOINED = "guild_joined"
    GUILD_LEFT = "guild_left"
    
    # User
    COMMAND_USED = "command_used"
    REACTION_ADDED = "reaction_added"
    
    # Preference
    PREFERENCE_UPDATED = "preference_updated"
    PLAYLIST_IMPORTED = "playlist_imported"
