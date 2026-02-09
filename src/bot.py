"""
Smart Discord Music Bot - Main Entry Point
"""
import asyncio
import json
import logging
import signal
import sys
import time
from pathlib import Path

import discord
from discord.ext import commands

from src.utils.logging import get_logger, Category, Event

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
log = get_logger("bot")


class MusicBot(commands.Bot):
    """Smart Discord Music Bot with preference learning."""
    
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        intents.guilds = True
        
        super().__init__(
            command_prefix="!",  # Fallback prefix, we use slash commands
            intents=intents,
            help_command=None,
        )
        
        # Will be initialized in setup_hook
        self.db = None
        self.discovery = None
        self.preferences = None

        # Interaction timing (for command start/end logs)
        self._interaction_started: dict[int, float] = {}
        self._loop_lag_task: asyncio.Task | None = None

    @staticmethod
    def _truncate(value, max_len: int = 240) -> str:
        text = str(value)
        text = " ".join(text.split())
        if len(text) > max_len:
            return text[: max_len - 1] + "…"
        return text

    @classmethod
    def _summarize_options(cls, options) -> dict:
        """Extract a small, log-friendly dict of app command options."""
        out: dict[str, str] = {}
        if not options:
            return out

        for opt in options:
            if not isinstance(opt, dict):
                continue
            name = opt.get("name")
            if not name:
                continue
            if "value" in opt:
                out[str(name)] = cls._truncate(opt.get("value"))
            elif "options" in opt:
                # Subcommands/groups: flatten one level with dotted keys.
                for sub in opt.get("options") or []:
                    if isinstance(sub, dict) and "name" in sub and "value" in sub:
                        out[f"{name}.{sub.get('name')}"] = cls._truncate(sub.get("value"))
        return out

    def _log_interaction_start(self, interaction: discord.Interaction) -> None:
        try:
            data = interaction.data or {}
            itype = str(getattr(interaction.type, "name", interaction.type))

            if interaction.type == discord.InteractionType.application_command:
                name = data.get("name")
                opts = self._summarize_options(data.get("options"))
                log.info_cat(
                    Category.SYSTEM,
                    "app_command_received",
                    module=__name__,
                    interaction_id=interaction.id,
                    interaction_type=itype,
                    command=name,
                    options=json.dumps(opts) if opts else None,
                    guild_id=interaction.guild_id,
                    channel_id=getattr(interaction.channel, "id", None),
                    user_id=getattr(interaction.user, "id", None),
                )
            elif interaction.type == discord.InteractionType.component:
                custom_id = data.get("custom_id")
                log.info_cat(
                    Category.USER,
                    "component_interaction_received",
                    module=__name__,
                    interaction_id=interaction.id,
                    interaction_type=itype,
                    custom_id=custom_id,
                    guild_id=interaction.guild_id,
                    channel_id=getattr(interaction.channel, "id", None),
                    message_id=getattr(getattr(interaction, "message", None), "id", None),
                    user_id=getattr(interaction.user, "id", None),
                )
        except Exception:
            return

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        # Track timing for application commands, and emit a "received" log early.
        # Never allow tracing/logging to break Discord's interaction pipeline.
        try:
            if interaction and interaction.id:
                self._log_interaction_start(interaction)
                if interaction.type == discord.InteractionType.application_command:
                    self._interaction_started[interaction.id] = time.perf_counter()
        except Exception as e:
            try:
                log.exception_cat(
                    Category.SYSTEM,
                    "interaction_trace_failed",
                    module=__name__,
                    interaction_id=getattr(interaction, "id", None),
                    interaction_type=str(getattr(getattr(interaction, "type", None), "name", getattr(interaction, "type", None))),
                    error=self._truncate(e),
                )
            except Exception:
                pass

        # In this codebase/library, interactions are processed by the internal machinery
        # (app command tree + component dispatch). The on_interaction event is for observation,
        # and calling super().on_interaction is not required (and may not exist depending on the discord lib).
        return None

    async def on_app_command_completion(self, interaction: discord.Interaction, command) -> None:
        t0 = self._interaction_started.pop(getattr(interaction, "id", 0), None)
        ms = int((time.perf_counter() - t0) * 1000) if t0 else None

        try:
            cmd_name = getattr(command, "qualified_name", None) or getattr(command, "name", None)
            cb = getattr(command, "callback", None)
            module = getattr(cb, "__module__", None) if cb else None
            binding = getattr(command, "binding", None)
            cog = type(binding).__name__ if binding else None

            log.info_cat(
                Category.SYSTEM,
                "app_command_completed",
                module=module or __name__,
                cog=cog,
                command=cmd_name,
                interaction_id=getattr(interaction, "id", None),
                guild_id=getattr(interaction, "guild_id", None),
                channel_id=getattr(interaction.channel, "id", None),
                user_id=getattr(interaction.user, "id", None),
                ms=ms,
            )
        except Exception:
            return

    async def on_app_command_error(self, interaction: discord.Interaction, error: Exception) -> None:
        t0 = self._interaction_started.pop(getattr(interaction, "id", 0), None)
        ms = int((time.perf_counter() - t0) * 1000) if t0 else None
        try:
            data = interaction.data or {}
            cmd_name = data.get("name")
            log.exception_cat(
                Category.SYSTEM,
                "app_command_error",
                module=__name__,
                command=cmd_name,
                interaction_id=getattr(interaction, "id", None),
                guild_id=getattr(interaction, "guild_id", None),
                channel_id=getattr(interaction.channel, "id", None),
                user_id=getattr(interaction.user, "id", None),
                ms=ms,
                error=self._truncate(error),
            )
        except Exception:
            return
    
    async def setup_hook(self) -> None:
        """Called when the bot is starting up."""
        log.event(Category.SYSTEM, "setup_started")

        # Event loop lag monitor (helps diagnose interaction 404s caused by stalls)
        if not self._loop_lag_task:
            self._loop_lag_task = asyncio.create_task(self._loop_lag_monitor())
        
        # Store start time for uptime tracking
        from datetime import datetime, UTC
        self._start_time = datetime.now(UTC)
        
        # Initialize database
        from src.config import config
        from src.database.connection import DatabaseManager
        from src.database.crud import SongCRUD, UserCRUD, GuildCRUD, PlaybackCRUD, PreferenceCRUD, ReactionCRUD
        
        self.db = await DatabaseManager.create(config.DATABASE_PATH)
        log.event(Category.DATABASE, "initialized", path=config.DATABASE_PATH)
        
        # Initialize services
        from src.services.youtube import YouTubeService
        from src.services.spotify import SpotifyService
        from src.services.normalizer import SongNormalizer
        from src.services.discovery import DiscoveryEngine
        from src.services.preferences import PreferenceManager
        
        self.youtube = YouTubeService(config.YTDL_COOKIES_PATH, config.YTDL_PO_TOKEN)
        self.spotify = SpotifyService(config.SPOTIFY_CLIENT_ID, config.SPOTIFY_CLIENT_SECRET)
        self.normalizer = SongNormalizer(self.youtube)
        
        
        # Initialize CRUD helpers
        pref_crud = PreferenceCRUD(self.db)
        playback_crud = PlaybackCRUD(self.db)
        reaction_crud = ReactionCRUD(self.db)
        song_crud = SongCRUD(self.db)
        user_crud = UserCRUD(self.db)
        
        # Initialize discovery engine (128-dim vector scoring)
        self.discovery = DiscoveryEngine(
            youtube=self.youtube,
            spotify=self.spotify,
            normalizer=self.normalizer,
            preference_crud=pref_crud,
            playback_crud=playback_crud,
            reaction_crud=reaction_crud,
            song_crud=song_crud,
        )
        
        # Initialize preference manager
        self.preferences = PreferenceManager(pref_crud, song_crud, user_crud)
        
        log.event(Category.SYSTEM, "services_initialized")
        
        # Load all cogs from the cogs directory
        cogs_dir = Path(__file__).parent / "cogs"
        if cogs_dir.exists():
            for cog_file in cogs_dir.glob("*.py"):
                if cog_file.name.startswith("_"):
                    continue
                cog_name = f"src.cogs.{cog_file.stem}"
                try:
                    await self.load_extension(cog_name)
                    log.event(Category.SYSTEM, Event.COG_LOADED, cog=cog_name)
                except Exception as e:
                    log.event(Category.SYSTEM, "cog_load_failed", level=logging.ERROR, cog=cog_name, error=str(e))
        
        # Sync slash commands
        log.event(Category.SYSTEM, "commands_syncing")
        await self.tree.sync()
        log.event(Category.SYSTEM, "commands_synced")
    
    async def on_ready(self) -> None:
        """Called when the bot is fully ready."""
        log.event(Category.SYSTEM, Event.BOT_READY, user=str(self.user), user_id=self.user.id, guilds=len(self.guilds))
        
        # Set presence
        activity = discord.Activity(
            type=discord.ActivityType.listening,
            name="/play"
        )
        await self.change_presence(activity=activity)
    
    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Called when the bot joins a new guild."""
        log.event(Category.SYSTEM, Event.GUILD_JOINED, guild=guild.name, guild_id=guild.id)
        
        # Check max servers limit
        if self.db:
            from src.database.crud import SystemCRUD
            crud = SystemCRUD(self.db)
            limit = await crud.get_global_setting("max_concurrent_servers")
            
            if limit is not None:
                try:
                    limit_int = int(limit)
                    if len(self.guilds) > limit_int:
                        log.event(Category.SYSTEM, "server_limit_exceeded", level=logging.WARNING, limit=limit_int, guild=guild.name)
                        await guild.leave()
                        await crud.add_notification(
                            "warning", 
                            f"Auto-left server '{guild.name}' because the limit of {limit_int} servers was exceeded."
                        )
                except ValueError:
                    pass
    
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Called when the bot is removed from a guild."""
        log.event(Category.SYSTEM, Event.GUILD_LEFT, guild=guild.name, guild_id=guild.id)
    
    async def close(self) -> None:
        """Cleanup when the bot is shutting down."""
        log.event(Category.SYSTEM, "shutdown_started")

        if self._loop_lag_task:
            self._loop_lag_task.cancel()
            self._loop_lag_task = None
        
        # Disconnect from all voice channels
        for vc in self.voice_clients:
            try:
                await vc.disconnect(force=True)
            except Exception:
                pass
        
        await super().close()

    async def _loop_lag_monitor(self) -> None:
        """Periodically measure event-loop lag and log warnings when it spikes."""
        interval = 1.0
        warn_ms = 500
        last = time.perf_counter()
        while True:
            await asyncio.sleep(interval)
            now = time.perf_counter()
            drift_ms = int((now - last - interval) * 1000)
            last = now
            if drift_ms >= warn_ms:
                log.warning_cat(Category.SYSTEM, "event_loop_lag", module=__name__, drift_ms=drift_ms)


async def main():
    """Main entry point."""
    from src.config import config
    
    bot = MusicBot()
    
    # Handle shutdown signals
    loop = asyncio.get_event_loop()
    
    def signal_handler():
        log.event(Category.SYSTEM, "shutdown_signal")
        asyncio.create_task(bot.close())
    
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass
    
    try:
        await bot.start(config.DISCORD_TOKEN)
    except KeyboardInterrupt:
        log.event(Category.SYSTEM, "keyboard_interrupt")
    finally:
        if not bot.is_closed():
            await bot.close()


if __name__ == "__main__":
    asyncio.run(main())
