"""
Music Cog - Core playback loop and audio streaming
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, UTC
from typing import Optional

import discord
from discord.ext import commands

from src.services.youtube import YouTubeService, YTTrack, StreamInfo
from src.database.crud import SongCRUD, UserCRUD, PlaybackCRUD, ReactionCRUD, GuildCRUD
from src.utils.logging import get_logger, Category, Event

log = get_logger(__name__)


@dataclass
class QueueItem:
    """Item in the music queue."""
    video_id: str
    title: str
    artist: str
    url: str | None = None  # Stream URL, resolved when needed
    requester_id: int | None = None
    discovery_source: str = "user_request"
    discovery_reason: str | None = None
    for_user_id: int | None = None  # Democratic turn tracking
    song_db_id: int | None = None  # Database ID after insertion
    history_id: int | None = None  # Playback history ID
    duration_seconds: int | None = None
    genre: str | None = None
    year: int | None = None


@dataclass
class GuildPlayer:
    """Per-guild music player state."""
    guild_id: int
    voice_client: discord.VoiceClient | None = None
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    current: QueueItem | None = None
    session_id: str | None = None
    is_playing: bool = False
    autoplay: bool = True
    pre_buffer: bool = True
    last_activity: datetime = field(default_factory=lambda: datetime.now(UTC))
    skip_votes: set = field(default_factory=set)
    _next_url: str | None = None  # Pre-buffered URL
    text_channel_id: int | None = None  # For Now Playing messages
    last_np_msg: discord.Message | None = None
    start_time: datetime | None = None  # When current song started
    _next_discovery: QueueItem | None = None  # Prefetched discovery song
    _prefetch_task: asyncio.Task | None = None  # Background prefetch task
    _consecutive_failures: int = 0  # Track consecutive failures for auto-recovery
    _last_health_check: datetime = field(default_factory=lambda: datetime.now(UTC))
    _np_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class MusicCog(commands.Cog):
    """Core music playback engine (queue + loop + voice streaming)."""
    
    FFMPEG_BEFORE_OPTIONS = (
        "-reconnect 1 -reconnect_streamed 1 "
        "-reconnect_on_network_error 1 -reconnect_on_http_error 403,429,500,502,503 "
        "-reconnect_delay_max 5"
    )
    FFMPEG_OPTIONS = "-vn -b:a 128k"
    
    IDLE_TIMEOUT = 300  # 5 minutes
    STREAM_FETCH_TIMEOUT = 30  # Max seconds to fetch stream URL
    PLAYBACK_TIMEOUT = 600  # Max seconds for a single song (10 min safety)
    DISCOVERY_TIMEOUT = 20  # Max seconds for discovery operation
    MAX_CONSECUTIVE_FAILURES = 3  # Auto-restart playback loop after this many failures
    SPOTIFY_ENRICH_TIMEOUT = 6  # Seconds; runs in background to avoid delaying playback
    
    @staticmethod
    def _build_ffmpeg_options(stream_info: StreamInfo) -> dict:
        """Build FFmpeg options, injecting HTTP headers from yt-dlp when available."""
        before = MusicCog.FFMPEG_BEFORE_OPTIONS
        if stream_info.http_headers:
            # Pass User-Agent and Referer so YouTube's CDN recognises the client
            ua = stream_info.http_headers.get("User-Agent")
            referer = stream_info.http_headers.get("Referer")
            if ua:
                # shlex.split handles the quoting correctly
                before = f'-user_agent "{ua}" ' + before
            if referer:
                before = f'-referer "{referer}" ' + before
        return {"before_options": before, "options": "-vn"}
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.players: dict[int, GuildPlayer] = {}
        self.youtube = YouTubeService()
        self._idle_check_task: asyncio.Task | None = None
    
    async def cog_load(self):
        """Called when the cog is loaded."""
        self._idle_check_task = asyncio.create_task(self._idle_check_loop())
        log.event(Category.SYSTEM, Event.COG_LOADED, cog="music")
    
    async def cog_unload(self):
        """Called when the cog is unloaded."""
        if self._idle_check_task:
            self._idle_check_task.cancel()
        
        # Disconnect from all voice channels
        for player in self.players.values():
            if player.voice_client:
                await player.voice_client.disconnect(force=True)
        
        log.event(Category.SYSTEM, Event.COG_UNLOADED, cog="music")

    def get_player(self, guild_id: int) -> GuildPlayer:
        """Get or create a player for a guild."""
        if guild_id not in self.players:
            self.players[guild_id] = GuildPlayer(guild_id=guild_id)
        return self.players[guild_id]
    

    # ==================== PLAYBACK LOOP ====================
    
    async def _play_loop(self, player: GuildPlayer):
        """Main playback loop for a guild with self-healing capabilities."""
        player.is_playing = True
        player._consecutive_failures = 0
        
        try:
            while player.voice_client and player.voice_client.is_connected():
                player.skip_votes.clear()
                player._last_health_check = datetime.now(UTC)
                iteration_t0 = time.perf_counter()
                log.info_cat(
                    Category.PLAYBACK,
                    "play_loop_iteration_start",
                    guild_id=player.guild_id,
                    queue_size=player.queue.qsize(),
                    autoplay=player.autoplay,
                    pre_buffer=player.pre_buffer,
                )
                
                # Get next item from queue or discovery
                try:
                    if player.queue.empty():
                        if player.autoplay:
                            # Check max duration setting
                            from src.database.crud import GuildCRUD
                            guild_crud = GuildCRUD(self.bot.db) if hasattr(self.bot, "db") else None
                            max_seconds = 0
                            if guild_crud:
                                try:
                                    setting_t0 = time.perf_counter()
                                    max_dur = await guild_crud.get_setting(player.guild_id, "max_song_duration")
                                    log.info_cat(
                                        Category.DATABASE,
                                        "guild_setting_fetched",
                                        guild_id=player.guild_id,
                                        setting="max_song_duration",
                                        value=max_dur,
                                        ms=int((time.perf_counter() - setting_t0) * 1000),
                                    )
                                    if max_dur:
                                        max_seconds = int(max_dur) * 60
                                except: pass
                            
                            # Use prefetched discovery song if available
                            item = None
                            if player._next_discovery:
                                item = player._next_discovery
                                player._next_discovery = None
                                log.debug_cat(Category.DISCOVERY, "Using prefetched discovery song", title=item.title)
                            else:
                                # Try to get discovery song with timeout
                                try:
                                    discovery_t0 = time.perf_counter()
                                    log.info_cat(
                                        Category.DISCOVERY,
                                        "discovery_fetch_start",
                                        guild_id=player.guild_id,
                                        max_seconds=max_seconds,
                                        timeout_s=self.DISCOVERY_TIMEOUT,
                                    )
                                    item = await asyncio.wait_for(
                                        self._get_discovery_song_with_retry(player, max_seconds),
                                        timeout=self.DISCOVERY_TIMEOUT
                                    )
                                    log.info_cat(
                                        Category.DISCOVERY,
                                        "discovery_fetch_end",
                                        guild_id=player.guild_id,
                                        found=bool(item),
                                        ms=int((time.perf_counter() - discovery_t0) * 1000),
                                    )
                                except asyncio.TimeoutError:
                                    log.warning_cat(Category.DISCOVERY, "Discovery timed out", guild_id=player.guild_id)
                                    player._consecutive_failures += 1
                                    if player._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                                        log.error_cat(Category.DISCOVERY, "Max consecutive failures reached, stopping", guild_id=player.guild_id)
                                        break
                                    continue
                            
                            if not item:
                                log.event(Category.DISCOVERY, Event.DISCOVERY_FAILED, guild_id=player.guild_id, reason="no_songs_available")
                                break
                        else:
                            break
                    else:
                        queue_t0 = time.perf_counter()
                        item = player.queue.get_nowait()
                        log.info_cat(
                            Category.QUEUE,
                            "queue_item_dequeued",
                            guild_id=player.guild_id,
                            video_id=getattr(item, "video_id", None),
                            ms=int((time.perf_counter() - queue_t0) * 1000),
                            queue_size=player.queue.qsize(),
                        )
                except asyncio.QueueEmpty:
                    break
                
                player.current = item
                player.last_activity = datetime.now(UTC)

                log.info_cat(
                    Category.PLAYBACK,
                    "track_selected",
                    guild_id=player.guild_id,
                    video_id=item.video_id,
                    title=item.title,
                    artist=item.artist,
                    source=item.discovery_source,
                    queue_size=player.queue.qsize(),
                    ms_since_iteration_start=int((time.perf_counter() - iteration_t0) * 1000),
                )
                
                # Database: Ensure session and log playback
                history_id = None
                if hasattr(self.bot, "db") and self.bot.db:
                    db_t0 = time.perf_counter()
                    try:
                        playback_crud = PlaybackCRUD(self.bot.db)
                        song_crud = SongCRUD(self.bot.db)
                        guild_crud = GuildCRUD(self.bot.db)
                        user_crud = UserCRUD(self.bot.db)

                        # 1. Ensure Guild & Session
                        if not player.session_id:
                            # Verify guild exists
                            if player.voice_client and player.voice_client.guild:
                                await guild_crud.get_or_create(
                                    player.guild_id, 
                                    player.voice_client.guild.name
                                )
                            
                            player.session_id = await playback_crud.create_session(
                                guild_id=player.guild_id,
                                channel_id=player.voice_client.channel.id
                            )
                        
                        # 2. Check Song Existence and Persistence Policy
                        if not item.song_db_id:
                            # User requests are PERMANENT (is_ephemeral=False)
                            # Discovery songs are EPHEMERAL (is_ephemeral=True)
                            is_ephemeral = (item.discovery_source != "user_request")
                            
                            song = await song_crud.get_or_create_by_yt_id(
                                canonical_yt_id=item.video_id,
                                title=item.title,
                                artist_name=item.artist,
                                is_ephemeral=is_ephemeral,
                                duration_seconds=item.duration_seconds,
                                release_year=item.year
                            )
                            item.song_db_id = song["id"]
                            
                            # If it was ephemeral and now requested by user, make it permanent
                            # If it was ephemeral and now requested by user, make it permanent
                            if not is_ephemeral and song.get("is_ephemeral"):
                                await song_crud.make_permanent(song["id"])

                        # Metadata enrichment (Spotify) intentionally runs in the background after playback starts.
                        
                        # Fallback: Populate from DB if Spotify failed or was unavailable
                        if (not item.year or not item.genre) and item.song_db_id:
                            if 'song' not in locals():
                                song = await song_crud.get_by_id(item.song_db_id)
                            
                            if song:
                                if not item.year: item.year = song.get("release_year")
                                if not item.duration_seconds: item.duration_seconds = song.get("duration_seconds")
                                
                                if not item.genre:
                                    genres = await song_crud.get_genres(item.song_db_id)
                                    if genres:
                                        item.genre = genres[0].title()

                        # 3. Log play
                        if item.song_db_id:
                             # Ensure user exists for FK constraint
                             target_user_id = item.for_user_id or item.requester_id
                             if target_user_id:
                                 # Try to find user in guild
                                 member = player.voice_client.guild.get_member(target_user_id)
                                 username = member.name if member else "Unknown User"
                                 await user_crud.get_or_create(target_user_id, username)
                             
                             history_id = await playback_crud.log_track(
                                 session_id=player.session_id,
                                 song_id=item.song_db_id,
                                 discovery_source=item.discovery_source,
                                 discovery_reason=item.discovery_reason,
                                 for_user_id=target_user_id
                             )
                             item.history_id = history_id

                             # Library: Record as 'request' if discovery source is user_request
                             if item.discovery_source == "user_request" and target_user_id and item.song_db_id:
                                 from src.database.crud import LibraryCRUD
                                 lib_crud = LibraryCRUD(self.bot.db)
                                 await lib_crud.add_to_library(target_user_id, item.song_db_id, "request")
                    except Exception as e:
                        log.error_cat(Category.DATABASE, "Failed to log playback start", error=str(e))
                    finally:
                        log.info_cat(
                            Category.DATABASE,
                            "db_playback_start_block_end",
                            guild_id=player.guild_id,
                            video_id=item.video_id,
                            song_db_id=item.song_db_id,
                            history_id=getattr(item, "history_id", None),
                            ms=int((time.perf_counter() - db_t0) * 1000),
                        )
                
                # Get stream URL and HTTP headers with timeout
                try:
                    stream_t0 = time.perf_counter()
                    log.info_cat(
                        Category.PLAYBACK,
                        "stream_fetch_start",
                        guild_id=player.guild_id,
                        video_id=item.video_id,
                        timeout_s=self.STREAM_FETCH_TIMEOUT,
                    )
                    stream_info = await asyncio.wait_for(
                        self.youtube.get_stream_url(item.video_id),
                        timeout=self.STREAM_FETCH_TIMEOUT
                    )
                    log.info_cat(
                        Category.PLAYBACK,
                        "stream_fetch_end",
                        guild_id=player.guild_id,
                        video_id=item.video_id,
                        ok=bool(stream_info),
                        ms=int((time.perf_counter() - stream_t0) * 1000),
                    )
                except asyncio.TimeoutError:
                    log.warning_cat(Category.PLAYBACK, "Stream URL fetch timed out", video_id=item.video_id)
                    player._consecutive_failures += 1
                    if player._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                        log.error_cat(Category.PLAYBACK, "Max consecutive stream failures, pausing", guild_id=player.guild_id)
                        await asyncio.sleep(5)  # Brief pause before retry
                        player._consecutive_failures = 0
                    continue
                
                if not stream_info:
                    log.event(Category.PLAYBACK, Event.PLAYBACK_ERROR, level=logging.ERROR, video_id=item.video_id, reason="stream_url_failed")
                    player._consecutive_failures += 1
                    continue
                
                # Reset failure counter on successful stream fetch
                player._consecutive_failures = 0
                item.url = stream_info.url

                # Start prefetching next song in background (for discovery mode)
                if player.autoplay and player.queue.empty():
                    log.info_cat(Category.DISCOVERY, "discovery_prefetch_scheduled", guild_id=player.guild_id, video_id=item.video_id)
                    asyncio.create_task(self._prefetch_discovery_song(player))
                elif player.pre_buffer and not player.queue.empty():
                    log.info_cat(Category.QUEUE, "prebuffer_scheduled", guild_id=player.guild_id, video_id=item.video_id, queue_size=player.queue.qsize())
                    asyncio.create_task(self._pre_buffer_next(player))

                # Play the audio
                try:
                    ffmpeg_opts = self._build_ffmpeg_options(stream_info)
                    probe_t0 = time.perf_counter()
                    log.info_cat(
                        Category.PLAYBACK,
                        "ffmpeg_probe_start",
                        guild_id=player.guild_id,
                        video_id=item.video_id,
                    )
                    source = await discord.FFmpegOpusAudio.from_probe(stream_info.url, **ffmpeg_opts)
                    log.info_cat(
                        Category.PLAYBACK,
                        "ffmpeg_probe_end",
                        guild_id=player.guild_id,
                        video_id=item.video_id,
                        ms=int((time.perf_counter() - probe_t0) * 1000),
                    )
                    
                    play_complete = asyncio.Event()
                    
                    def after_play(error):
                        if error:
                            log.event(Category.PLAYBACK, Event.PLAYBACK_ERROR, level=logging.ERROR, error=str(error))
                        # Always set event to prevent infinite wait
                        try:
                            play_complete.set()
                        except Exception:
                            pass
                    
                    player.voice_client.play(source, after=after_play)
                    player.start_time = datetime.now(UTC)

                    # Don’t delay playback for Spotify/metadata. Enrich in background and refresh Now Playing.
                    log.info_cat(Category.API, "spotify_enrich_scheduled", guild_id=player.guild_id, video_id=item.video_id)
                    asyncio.create_task(self._spotify_enrich_and_refresh_now_playing(player, item))
                    
                    log.event(
                        Category.PLAYBACK, Event.TRACK_STARTED,
                        title=item.title, artist=item.artist, genre=item.genre or "Unknown",
                        user_id=item.for_user_id or item.requester_id,
                        source=item.discovery_source, reason=item.discovery_reason
                    )
                    
                    # Send Now Playing embed
                    np_t0 = time.perf_counter()
                    log.info_cat(Category.PLAYBACK, "now_playing_send_start", guild_id=player.guild_id, video_id=item.video_id)
                    await self._notify_now_playing(player)
                    log.info_cat(
                        Category.PLAYBACK,
                        "now_playing_send_end",
                        guild_id=player.guild_id,
                        video_id=item.video_id,
                        ms=int((time.perf_counter() - np_t0) * 1000),
                    )
                    
                    # Wait for song to finish WITH TIMEOUT for self-healing
                    # Use song duration + buffer, or default safety timeout
                    max_wait = self.PLAYBACK_TIMEOUT
                    if item.duration_seconds:
                        max_wait = item.duration_seconds + 60  # Song duration + 1 min buffer
                    
                    try:
                        wait_t0 = time.perf_counter()
                        log.info_cat(
                            Category.PLAYBACK,
                            "playback_wait_start",
                            guild_id=player.guild_id,
                            video_id=item.video_id,
                            max_wait_s=max_wait,
                            duration_seconds=item.duration_seconds,
                        )
                        await asyncio.wait_for(play_complete.wait(), timeout=max_wait)
                        log.info_cat(
                            Category.PLAYBACK,
                            "playback_wait_end",
                            guild_id=player.guild_id,
                            video_id=item.video_id,
                            ms=int((time.perf_counter() - wait_t0) * 1000),
                        )
                    except asyncio.TimeoutError:
                        log.warning_cat(Category.PLAYBACK, "Playback timeout - auto-healing", 
                                       title=item.title, max_wait=max_wait)
                        # Force stop the current source to recover
                        if player.voice_client and player.voice_client.is_playing():
                            player.voice_client.stop()
                    
                    # Database: Log Playback End
                    if hasattr(self.bot, "db") and self.bot.db and item.history_id:
                        try:
                            playback_crud = PlaybackCRUD(self.bot.db)
                            completed = True

                            if player.skip_votes and len(player.skip_votes) > 0:
                                completed = False

                            await playback_crud.mark_completed(item.history_id, completed)
                        except Exception as e:
                            log.error_cat(Category.DATABASE, "Failed to log playback end", error=str(e))

                    log.info_cat(
                        Category.PLAYBACK,
                        "track_playback_cycle_end",
                        guild_id=player.guild_id,
                        video_id=item.video_id,
                        title=item.title,
                        artist=item.artist,
                        ms_since_iteration_start=int((time.perf_counter() - iteration_t0) * 1000),
                    )
                    
                except Exception as e:
                    log.event(Category.PLAYBACK, Event.PLAYBACK_ERROR, level=logging.ERROR, title=item.title, error=str(e))
                    continue
                
                player.current = None
        
        finally:
            player.is_playing = False
            player.current = None
    
    async def _get_discovery_song(self, player: GuildPlayer) -> QueueItem | None:
        """Get next song from discovery engine."""
        # Get voice channel members
        if not player.voice_client or not player.voice_client.channel:
            return None
        
        voice_members = [m.id for m in player.voice_client.channel.members if not m.bot]
        if not voice_members:
            return None
        
        # Try discovery engine first
        if hasattr(self.bot, "discovery") and self.bot.discovery:
            try:
                # Get Cooldown Setting
                cooldown = 7200 # Default 2 hours
                if hasattr(self.bot, "db"):
                    from src.database.crud import GuildCRUD
                    guild_crud = GuildCRUD(self.bot.db)
                    setting = await guild_crud.get_setting(player.guild_id, "replay_cooldown")
                    if setting:
                        try:
                            cooldown = int(setting)
                        except ValueError:
                            pass

                discovered = await self.bot.discovery.get_next_song(
                    player.guild_id,
                    voice_members,
                    cooldown_seconds=cooldown
                )
                if discovered:
                    # Normalize discovery source names for DB compatibility across schema versions.
                    # Some older DBs used 'artist' and some used 'same_artist'.
                    source_map = {"same_artist": "artist"}
                    db_source = source_map.get(discovered.strategy, discovered.strategy)
                    return QueueItem(
                        video_id=discovered.video_id,
                        title=discovered.title,
                        artist=discovered.artist,
                        discovery_source=db_source,
                        discovery_reason=discovered.reason,
                        for_user_id=discovered.for_user_id,
                        duration_seconds=discovered.duration_seconds,
                        genre=discovered.genre,
                        year=discovered.year,
                    )
            except Exception as e:
                log.event(Category.DISCOVERY, Event.DISCOVERY_FAILED, level=logging.ERROR, error=str(e))
        else:
            log.warning_cat(Category.DISCOVERY, "Discovery engine not initialized")
        
        # Fallback: Get random track from charts
        log.event(Category.DISCOVERY, "fallback_to_charts", guild_id=player.guild_id)
        return await self._get_chart_fallback()
    
    async def _get_discovery_song_with_retry(self, player: GuildPlayer, max_seconds: int = 0) -> QueueItem | None:
        """Get discovery song with retry logic for duration limits."""
        for attempt in range(3):
            item = await self._get_discovery_song(player)
            if not item:
                return None
            
            if max_seconds > 0 and item.duration_seconds and item.duration_seconds > max_seconds:
                log.event(Category.DISCOVERY, "song_skipped_duration", 
                         title=item.title, duration=item.duration_seconds, 
                         max_duration=max_seconds, attempt=attempt + 1)
                continue
            return item
        return None
    
    async def _prefetch_discovery_song(self, player: GuildPlayer):
        """Prefetch the next discovery song in background to reduce delay."""
        if player._next_discovery:
            return  # Already have one prefetched
        
        try:
            # Get max duration setting
            from src.database.crud import GuildCRUD
            guild_crud = GuildCRUD(self.bot.db) if hasattr(self.bot, "db") else None
            max_seconds = 0
            if guild_crud:
                try:
                    max_dur = await guild_crud.get_setting(player.guild_id, "max_song_duration")
                    if max_dur:
                        max_seconds = int(max_dur) * 60
                except: pass
            
            # Get discovery song with timeout
            item = await asyncio.wait_for(
                self._get_discovery_song_with_retry(player, max_seconds),
                timeout=self.DISCOVERY_TIMEOUT
            )
            
            if item:
                # Also prefetch stream URL for zero-delay playback
                try:
                    stream_info = await asyncio.wait_for(
                        self.youtube.get_stream_url(item.video_id),
                        timeout=self.STREAM_FETCH_TIMEOUT
                    )
                    if stream_info:
                        item.url = stream_info.url
                        log.debug_cat(Category.DISCOVERY, "Prefetched discovery song with URL", title=item.title)
                except asyncio.TimeoutError:
                    log.debug_cat(Category.DISCOVERY, "Prefetch stream URL timed out", title=item.title)
                
                player._next_discovery = item
                
        except asyncio.TimeoutError:
            log.debug_cat(Category.DISCOVERY, "Discovery prefetch timed out")
        except Exception as e:
            log.debug_cat(Category.DISCOVERY, "Discovery prefetch failed", error=str(e))
    
    async def _get_chart_fallback(self) -> QueueItem | None:
        """Get a random track from Top 100 US/UK charts as fallback."""
        import random
        
        region = random.choice(["US", "UK"])
        query = f"Top 100 Songs {region} 2024"
        
        log.event(Category.DISCOVERY, Event.SEARCH_STARTED, query=query, type="chart_playlist")
        
        # Try to find a chart playlist
        playlists = await self.youtube.search_playlists(query, limit=3)
        
        if playlists:
            playlist = random.choice(playlists)
            log.event(Category.DISCOVERY, Event.SEARCH_COMPLETED, playlist=playlist.get('title', 'Unknown'))
            
            # Get tracks from playlist
            tracks = await self.youtube.get_playlist_tracks(playlist["browse_id"], limit=50)
            if tracks:
                track = random.choice(tracks)
                return QueueItem(
                    video_id=track.video_id,
                    title=track.title,
                    artist=track.artist,
                    discovery_source="wildcard",
                    discovery_reason=f"🎲 Random from {region} Top 100",
                    duration_seconds=track.duration_seconds,
                    year=track.year
                )
        
        # Direct search fallback - search for popular songs
        log.event(Category.DISCOVERY, "fallback_direct_search")
        results = await self.youtube.search("top hits 2024 popular", filter_type="songs", limit=20)
        
        if results:
            track = random.choice(results)
            log.event(Category.DISCOVERY, Event.SEARCH_COMPLETED, title=track.title, type="direct_search")
            return QueueItem(
                video_id=track.video_id,
                title=track.title,
                artist=track.artist,
                discovery_source="wildcard",
                discovery_reason="🎲 Popular track from charts",
                duration_seconds=track.duration_seconds,
                year=track.year
            )
        
        log.warning_cat(Category.DISCOVERY, "No chart tracks found via any method")
        return None

    async def _notify_now_playing(self, player: GuildPlayer):
        """Ask the NowPlaying cog to render/update the Now Playing message."""
        np = self.bot.get_cog("NowPlayingCog")
        if not np:
            return
        send_fn = getattr(np, "send_now_playing_for_player", None)
        if not send_fn:
            return
        try:
            await send_fn(player)
        except Exception as e:
            log.debug_cat(Category.SYSTEM, "NowPlaying update failed", error=str(e), guild_id=player.guild_id)

    async def _spotify_enrich_and_refresh_now_playing(self, player: GuildPlayer, item: QueueItem):
        """Enrich current track metadata via Spotify without delaying playback, then refresh Now Playing."""
        spotify = getattr(self.bot, "spotify", None)
        if not spotify:
            return

        if item.year and item.genre:
            return

        try:
            query = f"{item.artist} {item.title}"
            sp_track = await asyncio.wait_for(
                spotify.search_track(query),
                timeout=self.SPOTIFY_ENRICH_TIMEOUT,
            )
            if not sp_track:
                return

            if not item.year:
                item.year = sp_track.release_year

            artist = await asyncio.wait_for(
                spotify.get_artist(sp_track.artist_id),
                timeout=self.SPOTIFY_ENRICH_TIMEOUT,
            )
            if artist and artist.genres and not item.genre:
                item.genre = artist.genres[0].title()

            if hasattr(self.bot, "db") and self.bot.db and item.song_db_id:
                try:
                    song_crud = SongCRUD(self.bot.db)

                    if item.genre:
                        await song_crud.clear_genres(item.song_db_id)
                        await song_crud.add_genre(item.song_db_id, item.genre)

                    await song_crud.get_or_create_by_yt_id(
                        canonical_yt_id=item.video_id,
                        title=item.title,
                        artist_name=item.artist,
                        release_year=item.year,
                        duration_seconds=item.duration_seconds,
                    )
                except Exception as e:
                    log.debug_cat(Category.DATABASE, "Failed to persist Spotify enrichment", error=str(e))

        except asyncio.TimeoutError:
            log.debug_cat(Category.API, "Spotify enrichment timed out", title=item.title, artist=item.artist)
            return
        except Exception as e:
            log.debug_cat(Category.API, "Spotify enrichment failed", error=str(e))
            return

        # Only refresh if this is still the currently playing item.
        try:
            # Give the initial Now Playing send a chance to complete to avoid racing two sends.
            await asyncio.sleep(1)
            if not player.current or player.current.video_id != item.video_id:
                return
            await self._notify_now_playing(player)
        except Exception as e:
            log.debug_cat(Category.SYSTEM, "Failed to refresh Now Playing after Spotify enrichment", error=str(e))
    

    async def _pre_buffer_next(self, player: GuildPlayer):
        """Pre-buffer the next song's URL."""
        try:
            # Peek at next item without removing
            if player.queue.empty():
                return

            next_item = list(player.queue._queue)[0]
            if not next_item.url:
                stream_info = await self.youtube.get_stream_url(next_item.video_id)
                if stream_info:
                    next_item.url = stream_info.url
                    player._next_url = stream_info.url
                    log.debug_cat(Category.QUEUE, "Pre-buffered URL", title=next_item.title)
        except Exception as e:
            log.debug_cat(Category.QUEUE, "Pre-buffer failed", error=str(e))
    
    async def _idle_check_loop(self):
        """Check for idle players, stuck players, and disconnect when needed."""
        STUCK_THRESHOLD = 1800  # 30 minutes without health check update = stuck
        
        while True:
            await asyncio.sleep(60)  # Check every minute
            
            now = datetime.now(UTC)
            for guild_id, player in list(self.players.items()):
                if player.voice_client and player.voice_client.is_connected():
                    # Check if idle for too long
                    if not player.is_playing and (now - player.last_activity).seconds > self.IDLE_TIMEOUT:
                        log.event(Category.VOICE, Event.VOICE_DISCONNECTED, guild_id=guild_id, reason="idle_timeout")
                        await player.voice_client.disconnect()
                        player.voice_client = None
                        continue
                    
                    # Check if player is stuck (playing but no health check update)
                    if player.is_playing and hasattr(player, '_last_health_check'):
                        time_since_health = (now - player._last_health_check).total_seconds()
                        if time_since_health > STUCK_THRESHOLD:
                            log.warning_cat(Category.PLAYBACK, "Stuck player detected - auto-restarting", 
                                          guild_id=guild_id, stuck_seconds=time_since_health)
                            
                            # Try to stop current playback
                            try:
                                if player.voice_client.is_playing():
                                    player.voice_client.stop()
                            except Exception:
                                pass
                            
                            # Reset state and restart play loop
                            player.is_playing = False
                            player._consecutive_failures = 0
                            player.current = None
                            
                            # Restart play loop if autoplay is on
                            if player.autoplay:
                                asyncio.create_task(self._play_loop(player))
                                log.event(Category.PLAYBACK, "play_loop_restarted", guild_id=guild_id)
    
    # ==================== EVENTS ====================
    
    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState
    ):
        """Handle voice state changes."""
        if member.bot:
            return
        
        player = self.players.get(member.guild.id)
        if not player or not player.voice_client:
            return
        
        # Check if bot is alone in voice channel
        if player.voice_client.channel:
            members = [m for m in player.voice_client.channel.members if not m.bot]
            if not members:
                # Everyone left, stop and disconnect
                if player.voice_client.is_playing():
                    player.voice_client.stop()
                await player.voice_client.disconnect()
                player.voice_client = None
                log.event(Category.VOICE, Event.VOICE_DISCONNECTED, guild=member.guild.name, reason="everyone_left")


async def setup(bot: commands.Bot):
    """Load the music cog."""
    await bot.add_cog(MusicCog(bot))
