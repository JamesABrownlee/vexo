"""
Music Cog - All music-related commands using yt-dlp.
Supports YouTube playback, queue management, smart autoplay, and 24/7 mode.
"""
import discord
from discord import app_commands
from discord.ext import commands, tasks
import asyncio
import yt_dlp
from typing import Optional, List, Set, Any, Tuple
from dataclasses import dataclass, field
from collections import deque
import random
import logging

from config import Config
from utils.embeds import (
    create_now_playing_embed,
    create_queue_embed,
    create_added_to_queue_embed,
    create_error_embed,
    create_success_embed,
    create_info_embed,
    create_upcoming_autoplay_embed,
    create_idle_embed,
)
from utils.views import NowPlayingView, AutoplayPreviewView
from database import db
from utils.discovery import discovery_engine
from utils.logger import set_logger
from utils.spotify import fetch_playlist_tracks, SpotifyError

logger = set_logger(logging.getLogger('Vexo.Music'))

async def genre_autocomplete(
    interaction: discord.Interaction,
    current: str
) -> List[app_commands.Choice[str]]:
    """Autocomplete helper for playlist genres (DB-backed)."""
    if not interaction.guild:
        return []
    genres = await db.get_genre_suggestions(interaction.user.id, interaction.guild.id, limit=50)
    results: List[app_commands.Choice[str]] = []
    current_lower = current.lower()
    for genre in genres:
        if current_lower and current_lower not in genre.lower():
            continue
        results.append(app_commands.Choice(name=genre[:100], value=genre))
        if len(results) >= 25:
            break
    return results


@dataclass
class Song:
    """Represents a song in the queue."""
    title: str
    url: str
    webpage_url: str
    duration: int  # in seconds
    thumbnail: Optional[str] = None
    author: str = "Unknown"


@dataclass
class GuildMusicState:
    """Music state for a guild."""
    guild_id: int = 0  # Guild ID for this state
    queue: List[Song] = field(default_factory=list)  # User requested songs
    current: Optional[Song] = None
    loop_mode: str = "off"  # off, song, queue
    is_24_7: bool = False
    is_autoplay: bool = True  # Always on by default in Vexo Smart
    volume: float = Config.DEFAULT_VOLUME
    voice_client: Optional[discord.VoiceClient] = None
    text_channel: Optional[discord.TextChannel] = None
    
    # Enhanced autoplay (10 songs total: 5 visible, 5 hidden)
    autoplay_visible: List[Song] = field(default_factory=list)
    autoplay_hidden: List[Song] = field(default_factory=list)
    is_fetching_autoplay: bool = False
    
    # Channel status feature
    is_channel_status: bool = False
    
    # Now playing message tracking
    now_playing_message: Optional[discord.Message] = None
    
    # Pre-fetching
    prefetched_source: Optional[Any] = None
    prefetched_song: Optional[Song] = None
    
    # Max duration filter (in seconds, 0 = no limit)
    max_duration: int = 0

    @property
    def total_autoplay(self) -> List[Song]:
        return self.autoplay_visible + self.autoplay_hidden


class YTDLSource(discord.PCMVolumeTransformer):
    """Audio source using yt-dlp."""
    
    ytdl = None  # Will be initialized on first use or with new options
    
    @classmethod
    def get_ytdl(cls):
        """Create or return YoutubeDL instance with current config."""
        if cls.ytdl is None:
            options = Config.YTDL_FORMAT_OPTIONS.copy()
            if Config.YTDL_COOKIES_PATH:
                options['cookiefile'] = Config.YTDL_COOKIES_PATH
            
            # Correct way to pass PO Token in modern yt-dlp Python API
            if Config.YTDL_PO_TOKEN:
                options['extractor_args'] = {
                    'youtube': {
                        'po_token': [Config.YTDL_PO_TOKEN]
                    }
                }
            
            cls.ytdl = yt_dlp.YoutubeDL(options)
        return cls.ytdl
    
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')
        self.webpage_url = data.get('webpage_url')
        self.duration = data.get('duration', 0)
        self.thumbnail = data.get('thumbnail')
        self.author = data.get('uploader', 'Unknown')
    
    @classmethod
    async def from_url(cls, url: str, *, loop=None, stream=True, volume=0.5):
        """Create audio source from URL."""
        loop = loop or asyncio.get_event_loop()
        
        data = await loop.run_in_executor(
            None, 
            lambda: cls.get_ytdl().extract_info(url, download=not stream)
        )
        
        if 'entries' in data:
            data = data['entries'][0]
        
        filename = data['url'] if stream else cls.get_ytdl().prepare_filename(data)
        
        return cls(
            discord.FFmpegPCMAudio(filename, **Config.FFMPEG_OPTIONS),
            data=data,
            volume=volume
        )
    
    @classmethod
    async def search(cls, query: str, *, loop=None) -> Optional[Song]:
        """Search for a song and return Song object."""
        loop = loop or asyncio.get_event_loop()
        
        if not query.startswith(('http://', 'https://')):
            query = f"ytsearch:{query}"
        
        try:
            data = await loop.run_in_executor(
                None,
                lambda: cls.get_ytdl().extract_info(query, download=False)
            )
            
            if data is None:
                return None
            
            if 'entries' in data:
                if not data['entries']:
                    return None
                data = data['entries'][0]
            
            return Song(
                title=data.get('title', 'Unknown'),
                url=data.get('url', ''),
                webpage_url=data.get('webpage_url', ''),
                duration=data.get('duration', 0) or 0,
                thumbnail=data.get('thumbnail'),
                author=data.get('uploader', 'Unknown')
            )
        except Exception:
            return None
    
    @classmethod
    async def search_by_artist(cls, artist: str, count: int = 3, *, loop=None) -> List[Song]:
        """Search for multiple songs by an artist."""
        loop = loop or asyncio.get_event_loop()
        query = f"ytsearch{count}:{artist} official music"
        
        try:
            data = await loop.run_in_executor(
                None,
                lambda: cls.get_ytdl().extract_info(query, download=False)
            )
            
            if data is None or 'entries' not in data:
                return []
            
            songs = []
            for entry in data['entries']:
                if entry:
                    songs.append(Song(
                        title=entry.get('title', 'Unknown'),
                        url=entry.get('url', ''),
                        webpage_url=entry.get('webpage_url', ''),
                        duration=entry.get('duration', 0) or 0,
                        thumbnail=entry.get('thumbnail'),
                        author=entry.get('uploader', 'Unknown')
                    ))
            return songs
        except Exception as e:
            logger.error(f"Error searching by artist '{artist}': {e}")
            return []
    
    @classmethod
    async def from_playlist(cls, playlist_url: str, *, loop=None, shuffle: bool = False, count: int = 10) -> List[Song]:
        """Extract songs from a YouTube playlist."""
        loop = loop or asyncio.get_event_loop()
        
        # Use extract_flat for faster playlist extraction
        opts = Config.YTDL_FORMAT_OPTIONS.copy()
        opts['extract_flat'] = 'in_playlist'
        opts['playlistend'] = 50  # Limit to first 50 for performance
        
        try:
            ytdl_instance = yt_dlp.YoutubeDL(opts)
            data = await loop.run_in_executor(
                None,
                lambda: ytdl_instance.extract_info(playlist_url, download=False)
            )
            
            if data is None or 'entries' not in data:
                return []
            
            entries = [e for e in data['entries'] if e]
            
            if shuffle:
                random.shuffle(entries)
            
            songs = []
            for entry in entries[:count]:
                songs.append(Song(
                    title=entry.get('title', 'Unknown'),
                    url=entry.get('url', ''),
                    webpage_url=entry.get('webpage_url') or f"https://youtube.com/watch?v={entry.get('id', '')}",
                    duration=entry.get('duration', 0) or 0,
                    thumbnail=entry.get('thumbnail'),
                    author=entry.get('uploader') or entry.get('channel', 'Unknown')
                ))
            
            logger.info(f"Extracted {len(songs)} songs from playlist (shuffled={shuffle})")
            return songs
        except Exception as e:
            logger.error(f"Error extracting playlist: {e}")
            return []


class Music(commands.Cog):
    """Music commands cog."""

    playlist_group = app_commands.Group(name="playlist", description="Manage saved playlists")
    PLAYLIST_SCOPE_CHOICES = [
        app_commands.Choice(name="User (Only You)", value="user"),
        app_commands.Choice(name="Server (This Guild)", value="guild"),
        app_commands.Choice(name="Global (Everyone)", value="global"),
    ]
    SPOTIFY_MATCH_LIMIT = 50
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.guild_states: dict[int, GuildMusicState] = {}
        logger.info("Vexo Music Cog Initialized (Sync).")

    async def cog_load(self):
        """Called when the cog is loaded."""
        # Initialize database and wait for it
        await db.initialize()
        
        self.autoplay_refill_task.start()
        logger.info("Vexo Music Cog Ready (Async).")
    
    def cog_unload(self):
        self.autoplay_refill_task.cancel()
    
    def get_state(self, guild_id: int) -> GuildMusicState:
        """Get or create music state for guild."""
        if guild_id not in self.guild_states:
            state = GuildMusicState(guild_id)
            self.guild_states[guild_id] = state
            
            # Load persistent settings in the background
            async def load_settings():
                settings = await db.get_settings(guild_id)
                if settings:
                    # Update state with stored values
                    state.volume = settings.get("volume", Config.DEFAULT_VOLUME * 100) / 100
                    state.is_autoplay = settings.get("is_autoplay", True)
                    state.is_24_7 = settings.get("is_24_7", False)
                    state.is_channel_status = settings.get("is_channel_status", False)
                    state.max_duration = settings.get("max_duration", 0)
                    logger.info(f"Loaded persistent settings for guild {guild_id}: {settings}")
            
            asyncio.create_task(load_settings())
            
        return self.guild_states[guild_id]
    
    @tasks.loop(seconds=10)
    async def autoplay_refill_task(self):
        """Background task to keep autoplay buffers filled."""
        for guild_id, state in self.guild_states.items():
            if state.is_autoplay and not state.is_fetching_autoplay:
                # Keep total buffer (visible + hidden) at 10 songs
                if len(state.total_autoplay) < 10:
                    await self._refill_autoplay_buffer(guild_id)
    
    @autoplay_refill_task.before_loop
    async def before_autoplay_refill(self):
        await self.bot.wait_until_ready()
    
    async def _refill_autoplay_buffer(self, guild_id: int):
        """Refill the visible and hidden autoplay queues."""
        state = self.get_state(guild_id)
        if state.is_fetching_autoplay or not state.voice_client:
            return
        
        state.is_fetching_autoplay = True
        logger.info(f"Refilling Autoplay Queues for Guild {guild_id}...")
        
        try:
            total_needed = 10 - len(state.total_autoplay)
            if total_needed <= 0:
                return

            vc = state.voice_client.channel
            member_ids = [m.id for m in vc.members if not m.bot]
            
            # 1. Get recommendations
            recommendations = await discovery_engine.get_next_songs(guild_id, member_ids, count=total_needed)
            
            # 2. Convert to Song objects
            for track in recommendations:
                song = Song(
                    title=track['song'],
                    url=track['url'],
                    webpage_url=track['url'],
                    duration=0, # Discovery pool should ideally store duration too
                    thumbnail=None,
                    author=track['artist']
                )
                
                # Check for duplicates in current session
                if any(s.url == song.url for s in state.queue + state.total_autoplay):
                    continue
                
                # Fill balance: first to visible until 5, then hidden
                if len(state.autoplay_visible) < 5:
                    state.autoplay_visible.append(song)
                else:
                    state.autoplay_hidden.append(song)
            
            # 3. Fallback to playlist if discovery didn't fill the buffer
            still_needed = (5 - len(state.autoplay_visible)) + (5 - len(state.autoplay_hidden))
            if still_needed > 0 and Config.FALLBACK_PLAYLIST:
                logger.info(f"Discovery insufficient, fetching {still_needed} from fallback playlist...")
                try:
                    playlist_songs = await YTDLSource.from_playlist(Config.FALLBACK_PLAYLIST, shuffle=True, count=still_needed + 5)
                    for ps in playlist_songs:
                        # Skip duplicates
                        if any(s.url == ps.url or s.webpage_url == ps.webpage_url for s in state.queue + state.total_autoplay):
                            continue
                        # Fill balance
                        if len(state.autoplay_visible) < 5:
                            state.autoplay_visible.append(ps)
                        elif len(state.autoplay_hidden) < 5:
                            state.autoplay_hidden.append(ps)
                        else:
                            break
                except Exception as e:
                    logger.error(f"Failed to fetch fallback playlist: {e}")
            
            logger.info(f"Refill Complete: Visible={len(state.autoplay_visible)}, Hidden={len(state.autoplay_hidden)}")

        except Exception as e:
            logger.error(f"Error in discovery refill: {e}")
        finally:
            state.is_fetching_autoplay = False
    
    async def _pick_diverse_song(self, *args, **kwargs):
        # Legacy method replaced by discovery engine
        pass

    def _is_youtube_playlist_url(self, value: str) -> bool:
        return "list=" in value and ("youtube.com" in value or "youtu.be" in value)

    def _is_spotify_playlist_url(self, value: str) -> bool:
        return "spotify.com/playlist" in value or value.startswith("spotify:playlist:")

    async def _get_youtube_playlist_title(self, playlist_url: str) -> Optional[str]:
        loop = self.bot.loop or asyncio.get_event_loop()
        opts = Config.YTDL_FORMAT_OPTIONS.copy()
        opts["extract_flat"] = True
        opts["playlistend"] = 1
        try:
            ytdl_instance = yt_dlp.YoutubeDL(opts)
            data = await loop.run_in_executor(
                None,
                lambda: ytdl_instance.extract_info(playlist_url, download=False)
            )
            if data:
                return data.get("title")
        except Exception as e:
            logger.error(f"Failed to fetch YouTube playlist title: {e}")
        return None

    async def _match_spotify_tracks(self, tracks: List[Tuple[str, str]]) -> List[Song]:
        loop = self.bot.loop or asyncio.get_event_loop()
        limited = tracks[: self.SPOTIFY_MATCH_LIMIT]
        semaphore = asyncio.Semaphore(5)
        results: List[Optional[Song]] = [None] * len(limited)

        async def _match(index: int, title: str, artist: str):
            query = f"{artist} - {title}" if artist else title
            try:
                async with semaphore:
                    results[index] = await YTDLSource.search(query, loop=loop)
            except Exception as e:
                logger.error(f"Spotify track match failed for '{query}': {e}")

        await asyncio.gather(*[
            _match(i, title, artist) for i, (title, artist) in enumerate(limited)
        ])

        return [song for song in results if song]
    
    async def ensure_voice(self, interaction: discord.Interaction) -> Optional[discord.VoiceClient]:
        """Ensure the user is in a voice channel and bot can join."""
        if not interaction.guild:
            await interaction.response.send_message(
                embed=create_error_embed("This command can only be used in a server!"),
                ephemeral=True
            )
            return None
        
        member = interaction.user
        if not isinstance(member, discord.Member):
            return None
        
        if not member.voice or not member.voice.channel:
            await interaction.response.send_message(
                embed=create_error_embed("You must be in a voice channel!"),
                ephemeral=True
            )
            return None
        
        state = self.get_state(interaction.guild.id)
        
        if not interaction.guild.voice_client:
            try:
                vc = await member.voice.channel.connect()
                state.voice_client = vc
                return vc
            except Exception as e:
                await interaction.response.send_message(
                    embed=create_error_embed(f"Failed to join voice channel: {e}"),
                    ephemeral=True
                )
                return None
        else:
            vc = interaction.guild.voice_client
            if vc.channel != member.voice.channel:
                await interaction.response.send_message(
                    embed=create_error_embed("You must be in the same voice channel as the bot!"),
                    ephemeral=True
                )
                return None
            return vc
    
    def play_next(self, guild_id: int, error=None):
        """Play the next song in the session playlist."""
        state = self.get_state(guild_id)
        
        if error:
            logger.error(f"Player error in guild {guild_id}: {error}")
        
        # 1. Record History
        if state.current:
            asyncio.run_coroutine_threadsafe(
                db.add_to_history(guild_id, state.current.author, state.current.title, state.current.url),
                self.bot.loop
            )
        
        # 2. Check loop modes
        if state.loop_mode == "song" and state.current:
            self._play_song(guild_id, state.current)
            return
        
        if state.loop_mode == "queue" and state.current:
            state.queue.append(state.current)
        
        # 3. Get next song
        next_song = None
        
        if state.queue:
            next_song = state.queue.pop(0)
            logger.info(f"Transition: Playing next USER REQUESTED song: '{next_song.title}'")
        elif state.is_autoplay:
            # Find a song that passes the duration filter
            while state.autoplay_visible and next_song is None:
                candidate = state.autoplay_visible.pop(0)
                # Check duration filter (skip if duration is known and exceeds limit)
                if state.max_duration > 0 and candidate.duration > 0 and candidate.duration > state.max_duration:
                    logger.info(f"Autoplay: Skipping '{candidate.title}' - duration {candidate.duration}s exceeds limit {state.max_duration}s")
                    continue
                next_song = candidate
                logger.info(f"Transition: Playing next AUTOPLAY song: '{next_song.title}'")
                
                # Rotate hidden to visible
                if state.autoplay_hidden:
                    hidden_to_move = state.autoplay_hidden.pop(0)
                    state.autoplay_visible.append(hidden_to_move)
                    logger.debug(f"Queue Rotation: Moved '{hidden_to_move.title}' from hidden to visible.")
            
            if next_song is None:
                logger.info("Transition: Autoplay buffer empty (or all filtered), triggering emergency refill.")
                async def wait_and_play():
                    await self._refill_autoplay_buffer(guild_id)
                    # Try to find a valid song after refill
                    song_to_play = None
                    while state.autoplay_visible and song_to_play is None:
                        c = state.autoplay_visible.pop(0)
                        if state.max_duration > 0 and c.duration > 0 and c.duration > state.max_duration:
                            continue
                        song_to_play = c
                    if song_to_play:
                        self._play_song(guild_id, song_to_play)
                    elif not state.is_24_7 and state.voice_client:
                        await state.voice_client.disconnect()
                
                asyncio.run_coroutine_threadsafe(wait_and_play(), self.bot.loop)
                return

        if next_song:
            self._play_song(guild_id, next_song)
        else:
            state.current = None
            if not state.is_24_7 and state.voice_client:
                asyncio.run_coroutine_threadsafe(state.voice_client.disconnect(), self.bot.loop)
                logger.info(f"Session End: Disconnected from guild {guild_id} (Queue empty and legacy autoplay off).")
    
    async def _update_now_playing_message(self, guild_id: int):
        """Delete old now playing message and post a new one with current song."""
        state = self.get_state(guild_id)
        
        if not state.text_channel or not state.current:
            return
        
        # Delete old now playing message if it exists
        if state.now_playing_message:
            try:
                await state.now_playing_message.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass  # Message already deleted or no permission
            state.now_playing_message = None
        
        # Create new now playing embed with buttons
        embed = create_now_playing_embed(state.current, state)
        
        # Add autoplay buffer info if active
        if state.is_autoplay and state.autoplay_visible:
            next_up = state.autoplay_visible[0]
            embed.add_field(
                name="🎲 Autoplay Next",
                value=f"{next_up.title[:40]}..." if len(next_up.title) > 40 else next_up.title,
                inline=False
            )
        
        # Create interactive view
        view = NowPlayingView(self, guild_id)
        
        try:
            state.now_playing_message = await state.text_channel.send(embed=embed, view=view)
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.error(f"Failed to send now playing message: {e}")
    
    def _play_song(self, guild_id: int, song: Song):
        """Internal method to play a song."""
        state = self.get_state(guild_id)
        state.current = song
        
        async def play_async():
            try:
                # 1. Update channel status
                if state.is_channel_status and state.voice_client:
                    channel = state.voice_client.channel
                    if channel:
                        status_text = f"🎶 {song.title[:100]}"
                        try: await channel.edit(status=status_text)
                        except: pass

                # 2. Get Source (this fetches actual stream info including duration)
                source = await YTDLSource.from_url(
                    song.webpage_url,
                    loop=self.bot.loop,
                    stream=True,
                    volume=state.volume
                )
                
                # 3. Check duration filter AFTER fetching real duration
                actual_duration = source.duration or 0
                if state.max_duration > 0 and actual_duration > 0 and actual_duration > state.max_duration:
                    logger.info(f"Duration Filter: Skipping '{song.title}' - actual duration {actual_duration}s exceeds limit {state.max_duration}s")
                    # Skip to next song
                    self.play_next(guild_id)
                    return
                
                # 4. Update song with actual metadata from source
                if actual_duration > 0:
                    state.current.duration = actual_duration
                if source.thumbnail:
                    state.current.thumbnail = source.thumbnail
                
                # 5. Play
                if state.voice_client and state.voice_client.is_connected():
                    state.voice_client.play(
                        source,
                        after=lambda e: self.play_next(guild_id, e)
                    )
                    await self._update_now_playing_message(guild_id)

                # 5. Mood Refresh: Replace 1 hidden song with something similar
                if state.autoplay_hidden:
                    similar = await discovery_engine.get_mood_recommendation(guild_id, song.title, song.author)
                    if similar:
                        # Replace a random hidden song (index 5-9 in logical session list)
                        idx = random.randint(0, len(state.autoplay_hidden)-1)
                        old = state.autoplay_hidden[idx]
                        state.autoplay_hidden[idx] = Song(
                            title=similar['song'],
                            url=similar['url'],
                            webpage_url=similar['url'],
                            duration=0,
                            author=similar['artist']
                        )
                        logger.info(f"Mood Refresh: Swapped hidden '{old.title}' for '{state.autoplay_hidden[idx].title}' (Mood relevance).")
                    
            except Exception as e:
                logger.error(f"Error playing song '{song.title}': {e}")
                self.play_next(guild_id)
        
        asyncio.run_coroutine_threadsafe(play_async(), self.bot.loop)

    async def _prefetch_next(self, guild_id: int):
        """Pre-fetch the next song in the background."""
        state = self.get_state(guild_id)
        next_song = None
        
        if state.queue:
            next_song = state.queue[0]
        elif state.is_autoplay and state.autoplay_visible:
            next_song = state.autoplay_visible[0]
            
        if next_song:
            if not state.prefetched_song or state.prefetched_song.webpage_url != next_song.webpage_url:
                try:
                    logger.info(f"Pre-fetching next song: '{next_song.title}'")
                    state.prefetched_song = next_song
                    state.prefetched_source = await YTDLSource.from_url(
                        next_song.webpage_url,
                        loop=self.bot.loop,
                        stream=True,
                        volume=state.volume
                    )
                except Exception as e:
                    logger.error(f"Error pre-fetching song: {e}")
                    state.prefetched_source = None
                    state.prefetched_song = None
        elif state.is_autoplay and not state.is_fetching_autoplay:
            # Buffer is empty, trigger a refill so we can prefetch something
            await self._refill_autoplay_buffer(guild_id)
            # Re-call self to try and prefetch now that buffer might have items
            if state.autoplay_visible:
                await self._prefetch_next(guild_id)

    @playlist_group.command(name="add", description="Add a Spotify or YouTube playlist")
    @app_commands.describe(
        url="Spotify or YouTube playlist link",
        scope="Who can access this playlist",
        name="Optional display name",
        genre="Optional genre for grouping"
    )
    @app_commands.choices(scope=PLAYLIST_SCOPE_CHOICES)
    @app_commands.autocomplete(genre=genre_autocomplete)
    async def playlist_add(
        self,
        interaction: discord.Interaction,
        url: str,
        scope: app_commands.Choice[str],
        name: Optional[str] = None,
        genre: Optional[str] = None
    ):
        """Add a playlist for later selection."""
        if not interaction.guild:
            await interaction.response.send_message(
                embed=create_error_embed("This command can only be used in a server!"),
                ephemeral=True
            )
            return

        await interaction.response.defer()

        source = None
        playlist_title = None
        track_count = 0

        if self._is_spotify_playlist_url(url):
            source = "spotify"
            try:
                playlist_title, tracks, total = fetch_playlist_tracks(url, limit=1)
            except SpotifyError as e:
                await interaction.followup.send(embed=create_error_embed(str(e)))
                return
            track_count = total or len(tracks)
            if track_count == 0:
                await interaction.followup.send(
                    embed=create_error_embed("No tracks found in that Spotify playlist.")
                )
                return
        elif self._is_youtube_playlist_url(url):
            source = "youtube"
            songs = await YTDLSource.from_playlist(url, loop=self.bot.loop, shuffle=False, count=1)
            if not songs:
                await interaction.followup.send(
                    embed=create_error_embed("No songs found in that YouTube playlist.")
                )
                return
            playlist_title = await self._get_youtube_playlist_title(url)
            track_count = 0
        else:
            await interaction.followup.send(
                embed=create_error_embed("Please provide a valid Spotify or YouTube playlist link.")
            )
            return

        scope_value = scope.value
        guild_id = interaction.guild.id if scope_value == "guild" else None
        owner_id = interaction.user.id
        display_name = name or playlist_title
        genre_value = genre.strip().lower() if genre else None

        playlist_id = await db.add_playlist(
            scope=scope_value,
            url=url,
            source=source,
            user_id=owner_id,
            guild_id=guild_id,
            name=display_name,
            genre=genre_value
        )

        scope_label = {
            "user": "User",
            "guild": "Server",
            "global": "Global"
        }.get(scope_value, scope_value)

        title_part = f"**{display_name}**" if display_name else "playlist"
        extra = f" ({track_count} track{'s' if track_count != 1 else ''})" if track_count else ""
        genre_text = f" — Genre: **{genre_value}**" if genre_value else ""
        await interaction.followup.send(
            embed=create_success_embed(
                f"Saved {title_part}{extra} as **{scope_label}** playlist (ID `{playlist_id}`).{genre_text}"
            )
        )

    async def _playlist_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str
    ) -> List[app_commands.Choice[str]]:
        if not interaction.guild:
            return []
        playlists = await db.get_playlists_for_user(interaction.user.id, interaction.guild.id, limit=50)
        results: List[app_commands.Choice[str]] = []
        current_lower = current.lower()
        genre_filter = None
        if hasattr(interaction, "namespace") and getattr(interaction.namespace, "genre", None):
            genre_filter = str(interaction.namespace.genre).strip().lower()
        scope_label = {
            "user": "You",
            "guild": "Server",
            "global": "Global"
        }

        for plist in playlists:
            if genre_filter and (plist.get("genre") or "").lower() != genre_filter:
                continue
            label = plist.get("name") or plist.get("url") or "Playlist"
            suffix = scope_label.get(plist.get("scope"), "Unknown")
            display = f"{label} ({suffix})"
            if current_lower and current_lower not in display.lower():
                continue
            results.append(
                app_commands.Choice(
                    name=display[:100],
                    value=str(plist["id"])
                )
            )
            if len(results) >= 25:
                break

        return results

    async def _guild_playlist_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str
    ) -> List[app_commands.Choice[str]]:
        if not interaction.guild:
            return []
        playlists = await db.get_playlists_for_user(interaction.user.id, interaction.guild.id, limit=50)
        results: List[app_commands.Choice[str]] = []
        current_lower = current.lower()
        for plist in playlists:
            if plist.get("scope") != "guild":
                continue
            label = plist.get("name") or plist.get("url") or "Playlist"
            display = f"{label} (Server)"
            if current_lower and current_lower not in display.lower():
                continue
            results.append(
                app_commands.Choice(
                    name=display[:100],
                    value=str(plist["id"])
                )
            )
            if len(results) >= 25:
                break
        return results

    @playlist_group.command(name="play", description="Play a saved playlist")
    @app_commands.describe(
        playlist="Select a playlist to play",
        genre="Optional genre filter"
    )
    @app_commands.autocomplete(playlist=_playlist_autocomplete, genre=genre_autocomplete)
    async def playlist_play(self, interaction: discord.Interaction, playlist: str, genre: Optional[str] = None):
        """Play a previously saved playlist."""
        vc = await self.ensure_voice(interaction)
        if not vc:
            return

        await interaction.response.defer()

        try:
            playlist_id = int(playlist)
        except ValueError:
            await interaction.followup.send(
                embed=create_error_embed("Invalid playlist selection.")
            )
            return

        plist = await db.get_playlist_by_id(playlist_id)
        if not plist:
            await interaction.followup.send(
                embed=create_error_embed("That playlist was not found.")
            )
            return

        scope = plist.get("scope")
        if scope == "user" and plist.get("user_id") != interaction.user.id:
            await interaction.followup.send(
                embed=create_error_embed("You don't have access to that playlist.")
            )
            return
        if scope == "guild" and plist.get("guild_id") != interaction.guild.id:
            await interaction.followup.send(
                embed=create_error_embed("That playlist belongs to another server.")
            )
            return
        if genre:
            genre_filter = genre.strip().lower()
            if (plist.get("genre") or "").lower() != genre_filter:
                await interaction.followup.send(
                    embed=create_error_embed("That playlist doesn't match the selected genre.")
                )
                return

        state = self.get_state(interaction.guild.id)
        state.voice_client = vc
        state.text_channel = interaction.channel

        source = plist.get("source")
        url = plist.get("url")
        display_name = plist.get("name") or "playlist"

        songs: List[Song] = []
        matched_count = 0
        total_tracks = 0

        if source == "youtube":
            songs = await YTDLSource.from_playlist(url, loop=self.bot.loop, shuffle=False, count=50)
            total_tracks = len(songs)
            matched_count = len(songs)
        elif source == "spotify":
            try:
                playlist_title, tracks, total = fetch_playlist_tracks(url, limit=self.SPOTIFY_MATCH_LIMIT)
            except SpotifyError as e:
                await interaction.followup.send(embed=create_error_embed(str(e)))
                return
            total_tracks = len(tracks)
            if total_tracks == 0:
                await interaction.followup.send(
                    embed=create_error_embed("No tracks found in that Spotify playlist.")
                )
                return
            display_name = plist.get("name") or playlist_title or display_name
            songs = await self._match_spotify_tracks(tracks)
            matched_count = len(songs)
        else:
            await interaction.followup.send(
                embed=create_error_embed("Unknown playlist source.")
            )
            return

        if not songs:
            await interaction.followup.send(
                embed=create_error_embed("No playable songs were found for that playlist.")
            )
            return

        # Record interaction for first song
        await discovery_engine.record_interaction(
            interaction.user.id,
            songs[0].author,
            songs[0].title,
            songs[0].webpage_url,
            "request"
        )

        if vc.is_playing() or vc.is_paused():
            state.queue.extend(songs)
            await interaction.followup.send(
                embed=create_success_embed(
                    f"📋 **Added {len(songs)} songs from {display_name}!**"
                )
            )
        else:
            first_song = songs[0]
            state.queue.extend(songs[1:])
            self._play_song(interaction.guild.id, first_song)
            await interaction.followup.send(
                embed=create_success_embed(
                    f"📋 **Playing {display_name}!** Added {len(songs)} songs ({len(songs)-1} queued)"
                )
            )

        if source == "spotify" and total_tracks:
            await interaction.followup.send(
                embed=create_info_embed(
                    "Spotify Match Results",
                    f"Matched **{matched_count}/{total_tracks}** tracks to YouTube results."
                )
            )

        if not state.total_autoplay:
            asyncio.create_task(self._refill_autoplay_buffer(interaction.guild.id))

    @playlist_group.command(name="remove", description="Remove a playlist from your personal options")
    @app_commands.describe(playlist="Select a playlist to remove")
    @app_commands.autocomplete(playlist=_playlist_autocomplete)
    async def playlist_remove(self, interaction: discord.Interaction, playlist: str):
        """Remove a playlist from the user's personal list (or hide if guild/global)."""
        if not interaction.guild:
            await interaction.response.send_message(
                embed=create_error_embed("This command can only be used in a server!"),
                ephemeral=True
            )
            return

        await interaction.response.defer()

        try:
            playlist_id = int(playlist)
        except ValueError:
            await interaction.followup.send(
                embed=create_error_embed("Invalid playlist selection.")
            )
            return

        plist = await db.get_playlist_by_id(playlist_id)
        if not plist:
            await interaction.followup.send(
                embed=create_error_embed("That playlist was not found.")
            )
            return

        scope = plist.get("scope")
        if scope == "user":
            deleted = await db.delete_user_playlist(playlist_id, interaction.user.id)
            if not deleted:
                await interaction.followup.send(
                    embed=create_error_embed("You can't remove that user playlist.")
                )
                return
            await interaction.followup.send(
                embed=create_success_embed("Removed that playlist from your personal list.")
            )
            return

        hidden = await db.hide_playlist_for_user(playlist_id, interaction.user.id)
        if hidden:
            await interaction.followup.send(
                embed=create_success_embed("Removed that playlist from your personal list.")
            )
        else:
            await interaction.followup.send(
                embed=create_info_embed("No Change", "That playlist was already removed from your personal list.")
            )

    @playlist_group.command(name="remove_guild", description="Remove a server playlist (Admin only)")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(playlist="Select a server playlist to remove")
    @app_commands.autocomplete(playlist=_guild_playlist_autocomplete)
    async def playlist_remove_guild(self, interaction: discord.Interaction, playlist: str):
        """Remove a guild-scoped playlist."""
        if not interaction.guild:
            await interaction.response.send_message(
                embed=create_error_embed("This command can only be used in a server!"),
                ephemeral=True
            )
            return

        await interaction.response.defer()

        try:
            playlist_id = int(playlist)
        except ValueError:
            await interaction.followup.send(
                embed=create_error_embed("Invalid playlist selection.")
            )
            return

        deleted = await db.delete_guild_playlist(playlist_id, interaction.guild.id)
        if not deleted:
            await interaction.followup.send(
                embed=create_error_embed("That server playlist could not be removed.")
            )
            return

        await interaction.followup.send(
            embed=create_success_embed("Removed that server playlist.")
        )
    
    @app_commands.command(name="play", description="Play a song or playlist from YouTube")
    @app_commands.describe(query="Song name, YouTube URL, or playlist URL")
    async def play(self, interaction: discord.Interaction, query: str):
        """Play a song or playlist from YouTube."""
        vc = await self.ensure_voice(interaction)
        if not vc:
            return
        
        await interaction.response.defer()
        
        state = self.get_state(interaction.guild.id)
        state.voice_client = vc
        state.text_channel = interaction.channel
        
        # Check if this is a playlist URL
        is_playlist = "list=" in query and ("youtube.com" in query or "youtu.be" in query)
        
        if is_playlist:
            # Extract songs from playlist
            songs = await YTDLSource.from_playlist(query, loop=self.bot.loop, shuffle=False, count=50)
            
            if not songs:
                await interaction.followup.send(
                    embed=create_error_embed(f"No songs found in playlist: `{query}`")
                )
                return
            
            # Record interaction for first song
            await discovery_engine.record_interaction(
                interaction.user.id,
                songs[0].author,
                songs[0].title,
                songs[0].webpage_url,
                "request"
            )
            
            if vc.is_playing() or vc.is_paused():
                # Add all songs to queue
                state.queue.extend(songs)
                await interaction.followup.send(
                    embed=create_success_embed(f"📋 **Added {len(songs)} songs from playlist to queue!**")
                )
            else:
                # Play first song, queue the rest
                first_song = songs[0]
                state.queue.extend(songs[1:])
                self._play_song(interaction.guild.id, first_song)
                await interaction.followup.send(
                    embed=create_success_embed(f"📋 **Playing playlist!** Added {len(songs)} songs ({len(songs)-1} queued)")
                )
                
                # Trigger initial discovery refill if empty
                if not state.total_autoplay:
                    asyncio.create_task(self._refill_autoplay_buffer(interaction.guild.id))
        else:
            # Single song search (original behavior)
            song = await YTDLSource.search(query, loop=self.bot.loop)
            
            if not song:
                await interaction.followup.send(
                    embed=create_error_embed(f"No results found for: `{query}`")
                )
                return
            
            # Record "request" interaction
            await discovery_engine.record_interaction(
                interaction.user.id, 
                song.author, 
                song.title, 
                song.webpage_url, 
                "request"
            )
            
            if vc.is_playing() or vc.is_paused():
                # Add to the bottom of the requested songs (which is just the queue)
                # This naturally puts it above autoplay songs in the play_next logic
                state.queue.append(song)
                await interaction.followup.send(
                    embed=create_added_to_queue_embed(song, len(state.queue))
                )
                
                # Mood refresh: If playing, update hidden autoplay for this request
                asyncio.create_task(discovery_engine.get_mood_recommendation(interaction.guild.id, song.title, song.author))
            else:
                self._play_song(interaction.guild.id, song)
                await interaction.followup.send(
                    embed=create_now_playing_embed(song, state)
                )
                
                # Trigger initial discovery refill if empty
                if not state.total_autoplay:
                    asyncio.create_task(self._refill_autoplay_buffer(interaction.guild.id))

    @app_commands.command(name="just_play", description="Start smart autoplay without a specific request")
    async def just_play(self, interaction: discord.Interaction):
        """Start smart autoplay."""
        vc = await self.ensure_voice(interaction)
        if not vc:
            return
            
        await interaction.response.defer()
        
        state = self.get_state(interaction.guild.id)
        state.voice_client = vc
        state.text_channel = interaction.channel
        state.is_autoplay = True
        
        if vc.is_playing() or vc.is_paused():
            await interaction.followup.send(
                embed=create_info_embed("Vexo is already playing!", "Autoplay is now enabled.")
            )
            return

        # Trigger immediate refill
        await self._refill_autoplay_buffer(interaction.guild.id)
        
        # Fallback: If still empty, get random songs from the fallback playlist
        if not state.autoplay_visible and Config.FALLBACK_PLAYLIST:
            logger.info("Discovery Pool empty. Fetching from fallback playlist...")
            try:
                playlist_songs = await YTDLSource.from_playlist(Config.FALLBACK_PLAYLIST, shuffle=True, count=5)
                if playlist_songs:
                    # Filter by max_duration if set
                    if state.max_duration > 0:
                        playlist_songs = [s for s in playlist_songs if s.duration == 0 or s.duration <= state.max_duration]
                    state.autoplay_visible.extend(playlist_songs[:3])  # Take up to 3
            except Exception as e:
                logger.error(f"Failed to fetch fallback playlist: {e}")

        # Find a song that passes the duration filter
        next_song = None
        while state.autoplay_visible and next_song is None:
            candidate = state.autoplay_visible.pop(0)
            # Check duration filter (skip if duration is known and exceeds limit)
            if state.max_duration > 0 and candidate.duration > 0 and candidate.duration > state.max_duration:
                logger.info(f"Skipping '{candidate.title}' - duration {candidate.duration}s exceeds limit {state.max_duration}s")
                continue
            next_song = candidate
        
        if next_song:
            self._play_song(interaction.guild.id, next_song)
            await interaction.followup.send(
                embed=create_success_embed("🎶 **Vexo Discovery Started!**\nPlaying songs based on your vibe (or trending hits).")
            )
        else:
            await interaction.followup.send(
                embed=create_error_embed("Still couldn't find any songs! Try requesting something manually first.")
            )
    
    @app_commands.command(name="pause", description="Pause the current song")
    async def pause(self, interaction: discord.Interaction):
        """Pause playback."""
        if not interaction.guild or not interaction.guild.voice_client:
            await interaction.response.send_message(
                embed=create_error_embed("Not playing anything!"),
                ephemeral=True
            )
            return
        
        vc = interaction.guild.voice_client
        
        if vc.is_paused():
            await interaction.response.send_message(
                embed=create_info_embed("Already Paused", "Use `/resume` to continue."),
                ephemeral=True
            )
            return
        
        if vc.is_playing():
            vc.pause()
            await interaction.response.send_message(
                embed=create_success_embed("⏸️ Paused the music.")
            )
        else:
            await interaction.response.send_message(
                embed=create_error_embed("Nothing is playing!"),
                ephemeral=True
            )
    
    @app_commands.command(name="resume", description="Resume the paused song")
    async def resume(self, interaction: discord.Interaction):
        """Resume playback."""
        if not interaction.guild or not interaction.guild.voice_client:
            await interaction.response.send_message(
                embed=create_error_embed("Not connected!"),
                ephemeral=True
            )
            return
        
        vc = interaction.guild.voice_client
        
        if vc.is_paused():
            vc.resume()
            await interaction.response.send_message(
                embed=create_success_embed("▶️ Resumed the music.")
            )
        else:
            await interaction.response.send_message(
                embed=create_info_embed("Not Paused", "The player is not paused."),
                ephemeral=True
            )
    
    @app_commands.command(name="skip", description="Skip the current song")
    async def skip(self, interaction: discord.Interaction):
        """Skip the current track."""
        if not interaction.guild or not interaction.guild.voice_client:
            await interaction.response.send_message(
                embed=create_error_embed("Not playing anything!"),
                ephemeral=True
            )
            return
        
        state = self.get_state(interaction.guild.id)
        vc = interaction.guild.voice_client
        
        if vc.is_playing() or vc.is_paused():
            title = state.current.title if state.current else "Unknown"
            
            # Record skip interaction
            if state.current:
                await discovery_engine.record_interaction(
                    interaction.user.id,
                    state.current.author,
                    state.current.title,
                    state.current.webpage_url,
                    "skip"
                )
                
            vc.stop()
            await interaction.response.send_message(
                embed=create_success_embed(f"⏭️ Skipped: **{title}**")
            )
        else:
            await interaction.response.send_message(
                embed=create_error_embed("Nothing is playing!"),
                ephemeral=True
            )
    
    @app_commands.command(name="stop", description="Stop playing and clear the queue")
    async def stop(self, interaction: discord.Interaction):
        """Stop playback and clear queue."""
        if not interaction.guild:
            return
        
        state = self.get_state(interaction.guild.id)
        vc = interaction.guild.voice_client
        
        if not vc:
            await interaction.response.send_message(
                embed=create_error_embed("Not connected!"),
                ephemeral=True
            )
            return
        
        state.queue.clear()
        state.autoplay_visible.clear()
        state.autoplay_hidden.clear()
        state.current = None
        state.loop_mode = "off"
        
        if vc.is_playing() or vc.is_paused():
            vc.stop()
        
        if not state.is_24_7:
            await vc.disconnect()
            await interaction.response.send_message(
                embed=create_success_embed("⏹️ Stopped and disconnected.")
            )
        else:
            await interaction.response.send_message(
                embed=create_success_embed("⏹️ Stopped and cleared queue. (24/7 mode active)")
            )
    
    @app_commands.command(name="queue", description="View the music queue")
    @app_commands.describe(page="Page number to view")
    async def queue(self, interaction: discord.Interaction, page: int = 1):
        """View the queue."""
        if not interaction.guild:
            return
        
        state = self.get_state(interaction.guild.id)
        
        # Combine Requested + Visible Autoplay for the embed
        full_queue = state.queue + state.autoplay_visible
        embed = create_queue_embed(full_queue, state.current, page)
        
        # Footers for transparency
        footer_parts = [f"🎲 Autoplay ({len(state.autoplay_visible)} visible, {len(state.autoplay_hidden)} hidden)"]
        
        existing = embed.footer.text if embed.footer else ""
        new_footer = f"{existing} • {' • '.join(footer_parts)}".strip(" •")
        embed.set_footer(text=new_footer)
        
        await interaction.response.send_message(embed=embed)
    
    @app_commands.command(name="shuffle", description="Shuffle the queue")
    async def shuffle(self, interaction: discord.Interaction):
        """Shuffle the queue."""
        if not interaction.guild:
            return
        
        state = self.get_state(interaction.guild.id)
        
        if not state.queue:
            await interaction.response.send_message(
                embed=create_error_embed("The queue is empty!"),
                ephemeral=True
            )
            return
        
        random.shuffle(state.queue)
        await interaction.response.send_message(
            embed=create_success_embed(f"🔀 Shuffled {len(state.queue)} songs!")
        )
    
    @app_commands.command(name="loop", description="Toggle loop mode")
    @app_commands.describe(mode="Loop mode: off, song, or queue")
    @app_commands.choices(mode=[
        app_commands.Choice(name="Off", value="off"),
        app_commands.Choice(name="Song", value="song"),
        app_commands.Choice(name="Queue", value="queue"),
    ])
    async def loop(self, interaction: discord.Interaction, mode: str):
        """Set loop mode."""
        if not interaction.guild:
            return
        
        state = self.get_state(interaction.guild.id)
        state.loop_mode = mode
        
        # Persist setting
        asyncio.create_task(db.set_setting(interaction.guild.id, "loop_mode", mode))
        
        mode_display = {
            "off": "🚫 Loop disabled",
            "song": "🔂 Looping current song",
            "queue": "🔁 Looping entire queue"
        }
        
        await interaction.response.send_message(
            embed=create_success_embed(mode_display.get(mode, "Unknown mode"))
        )
    
    @app_commands.command(name="autoplay", description="Toggle autoplay mode")
    async def autoplay(self, interaction: discord.Interaction):
        """Toggle autoplay mode."""
        if not interaction.guild:
            return
        
        state = self.get_state(interaction.guild.id)
        state.is_autoplay = not state.is_autoplay
        state.text_channel = interaction.channel
        
        # Persist setting
        asyncio.create_task(db.set_setting(interaction.guild.id, "is_autoplay", state.is_autoplay))
        
        if state.is_autoplay:
            # Start buffering immediately
            asyncio.create_task(self._refill_autoplay_buffer(interaction.guild.id))
            
            await interaction.response.send_message(
                embed=create_success_embed("🎲 **Autoplay Enabled**\nI'll play similar songs based on the audience vibe!")
            )
        else:
            state.autoplay_visible.clear()
            state.autoplay_hidden.clear()
            await interaction.response.send_message(
                embed=create_success_embed("🎲 **Autoplay Disabled**")
            )
    
    @app_commands.command(name="favorites", description="Manage favorite artists for autoplay (Admin only)")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        action="What to do",
        artist="Artist name (for add/remove)"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="Add", value="add"),
        app_commands.Choice(name="Remove", value="remove"),
        app_commands.Choice(name="List", value="list"),
        app_commands.Choice(name="Clear", value="clear"),
    ])
    async def favorites(
        self, 
        interaction: discord.Interaction, 
        action: str,
        artist: Optional[str] = None
    ):
        """Manage favorite artists for autoplay."""
        if not interaction.guild:
            return
        
        # LEGACY: Favorite artists are now handled by Discovery Engine interaction & Pool.
        # This command is deprecated in Vexo Smart.
        await interaction.response.send_message(
            embed=create_info_embed("Vexo Recommendations", "Vexo now learns from your likes automatically! Use 👍/👎 or `/play` to teach me your taste."),
            ephemeral=True
        )
    
    @app_commands.command(name="nowplaying", description="Show the currently playing song with interactive controls")
    async def nowplaying(self, interaction: discord.Interaction):
        """Show current track info with interactive button controls."""
        if not interaction.guild:
            return
        
        state = self.get_state(interaction.guild.id)
        
        if not state.current:
            # Nothing playing - show idle state with suggestion
            suggestion = None
            
            # Try to get a suggestion from autoplay buffer
            if state.autoplay_visible:
                suggestion = state.autoplay_visible[0]
            
            embed = create_idle_embed(state, suggestion)
            view = NowPlayingView(self, interaction.guild.id)
            
            await interaction.response.send_message(embed=embed, view=view)
            return
        
        embed = create_now_playing_embed(state.current, state)
        
        # Add autoplay buffer info
        if state.is_autoplay and state.autoplay_visible:
            next_up = state.autoplay_visible[0]
            embed.add_field(
                name="🎲 Autoplay Next",
                value=f"{next_up.title[:40]}..." if len(next_up.title) > 40 else next_up.title,
                inline=False
            )
        
        # Create interactive view with buttons
        view = NowPlayingView(self, interaction.guild.id)
        
        await interaction.response.send_message(embed=embed, view=view)
    
    @app_commands.command(name="volume", description="Set the volume (Admin only)")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(level="Volume level (1-100)")
    async def volume(self, interaction: discord.Interaction, level: int):
        """Set volume level."""
        if not interaction.guild:
            return
        
        if not 0 <= level <= 100:
            await interaction.response.send_message(
                embed=create_error_embed("Volume must be between 0 and 100!"),
                ephemeral=True
            )
            return
        
        state = self.get_state(interaction.guild.id)
        state.volume = level / 100
        
        # Persist setting
        asyncio.create_task(db.set_setting(interaction.guild.id, "volume", level))
        
        vc = interaction.guild.voice_client
        if vc and vc.source:
            vc.source.volume = state.volume
        
        if level == 0:
            emoji = "🔇"
        elif level < 30:
            emoji = "🔈"
        elif level < 70:
            emoji = "🔉"
        else:
            emoji = "🔊"
        
        await interaction.response.send_message(
            embed=create_success_embed(f"{emoji} Volume set to **{level}%**")
        )
    
    @app_commands.command(name="247", description="Toggle 24/7 mode")
    async def twenty_four_seven(self, interaction: discord.Interaction):
        """Toggle 24/7 mode."""
        vc = await self.ensure_voice(interaction)
        if not vc:
            return
        
        state = self.get_state(interaction.guild.id)
        state.is_24_7 = not state.is_24_7
        
        # Persist setting
        asyncio.create_task(db.set_setting(interaction.guild.id, "is_24_7", state.is_24_7))
        
        if state.is_24_7:
            await interaction.response.send_message(
                embed=create_success_embed("📻 **24/7 Mode Enabled**\nI'll stay in the voice channel even when not playing.")
            )
        else:
            await interaction.response.send_message(
                embed=create_success_embed("📻 **24/7 Mode Disabled**\nI'll disconnect when the queue is empty.")
            )
    
    @app_commands.command(name="channel_status", description="Toggle voice channel status to show current song (Admin only)")
    @app_commands.default_permissions(administrator=True)
    async def channel_status(self, interaction: discord.Interaction):
        """Toggle voice channel status to show current song."""
        if not interaction.guild:
            return
        
        vc = await self.ensure_voice(interaction)
        if not vc:
            return
        
        state = self.get_state(interaction.guild.id)
        
        if not state.is_channel_status:
            # Enable - store original status
            state.is_channel_status = True
            state.original_channel_status = getattr(vc.channel, 'status', None)
            
            # Persist setting
            asyncio.create_task(db.set_setting(interaction.guild.id, "is_channel_status", True))
            
            # Immediately set status if something is playing
            if state.current:
                artist = state.current.author if state.current.author else "Unknown"
                title = state.current.title if state.current.title else "Unknown"
                if artist.lower() in title.lower():
                    status_text = f"🎶 {title[:80]}"
                else:
                    status_text = f"🎶 {artist[:30]} - {title[:50]}"
                try:
                    await vc.channel.edit(status=status_text[:500])
                except discord.Forbidden:
                    await interaction.response.send_message(
                        embed=create_error_embed("I don't have permission to edit the channel status!"),
                        ephemeral=True
                    )
                    state.is_channel_status = False
                    state.original_channel_status = None
                    return
            
            await interaction.response.send_message(
                embed=create_success_embed(
                    "🎶 **Channel Status Enabled**\n"
                    "Voice channel status will show: `🎶 Artist - Song`\n"
                    "Status will be cleared on disconnect."
                )
            )
        else:
            # Disable - clear status
            try:
                await vc.channel.edit(status=None)
            except:
                pass
            
            state.is_channel_status = False
            state.original_channel_status = None
            
            # Persist setting
            asyncio.create_task(db.set_setting(interaction.guild.id, "is_channel_status", False))
            
            await interaction.response.send_message(
                embed=create_success_embed("🎶 **Channel Status Disabled**\nStatus cleared.")
            )
    
    @app_commands.command(name="maxduration", description="Set max track duration for autoplay (Admin only)")
    @app_commands.describe(minutes="Max duration in minutes (0 = no limit)")
    @app_commands.default_permissions(administrator=True)
    async def maxduration(self, interaction: discord.Interaction, minutes: int):
        """Set maximum track duration for autoplay."""
        if not interaction.guild:
            return
        
        if minutes < 0:
            await interaction.response.send_message(
                embed=create_error_embed("Duration must be 0 or positive!"),
                ephemeral=True
            )
            return
        
        state = self.get_state(interaction.guild.id)
        state.max_duration = minutes * 60  # Convert to seconds
        
        # Persist setting
        asyncio.create_task(db.set_setting(interaction.guild.id, "max_duration", state.max_duration))
        
        if minutes == 0:
            await interaction.response.send_message(
                embed=create_success_embed("⏱️ **Max Duration Filter Disabled**\nAutoplay will pick songs of any length.")
            )
        else:
            await interaction.response.send_message(
                embed=create_success_embed(f"⏱️ **Max Duration Set to {minutes} minutes**\nAutoplay will skip songs longer than this.")
            )
        
        # Refresh buffer with new filter
        if state.is_autoplay:
            state.autoplay_visible.clear()
            state.autoplay_hidden.clear()
            asyncio.create_task(self._refill_autoplay_buffer(interaction.guild.id))
    
    @app_commands.command(name="pool", description="View the song pool for discovery/autoplay")
    @app_commands.describe(page="Page number (default: 1)")
    async def pool(self, interaction: discord.Interaction, page: int = 1):
        """Show the global song pool with scores."""
        await interaction.response.defer()
        
        # Get the global pool
        pool_data = await db.get_autoplay_pool(0)
        
        if not pool_data:
            await interaction.followup.send(
                embed=create_info_embed("🎵 Song Pool", "The pool is empty! Play some songs to populate it.")
            )
            return
        
        # Get user preferences for scoring
        user_prefs = await db.get_user_preferences(interaction.user.id)
        pref_scores = {p['url']: p['score'] for p in user_prefs}
        
        # Sort pool by preference score (highest first)
        sorted_pool = sorted(pool_data, key=lambda x: pref_scores.get(x['url'], 0), reverse=True)
        
        # Pagination
        per_page = 10
        total_pages = max(1, (len(sorted_pool) + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))
        start = (page - 1) * per_page
        end = start + per_page
        page_items = sorted_pool[start:end]
        
        # Build embed
        embed = discord.Embed(
            title="🎵 Song Pool",
            description=f"**{len(pool_data)} songs** available for discovery\nPage {page}/{total_pages}",
            color=Config.COLOR_PRIMARY
        )
        
        lines = []
        for i, track in enumerate(page_items, start=start+1):
            score = pref_scores.get(track['url'], 0)
            score_display = f"+{score}" if score > 0 else str(score) if score < 0 else "0"
            title = track['song'][:35] + "..." if len(track['song']) > 35 else track['song']
            lines.append(f"`{i}.` **{title}** — {track['artist']} `[{score_display}]`")
        
        embed.add_field(
            name="Tracks",
            value="\n".join(lines) if lines else "No tracks on this page.",
            inline=False
        )
        
        embed.set_footer(text=f"Use /pool {page+1} for next page • Scores are your personal preferences")
        
        await interaction.followup.send(embed=embed)
    
    @app_commands.command(name="disconnect", description="Disconnect from voice channel")
    async def disconnect(self, interaction: discord.Interaction):
        """Disconnect from voice channel."""
        if not interaction.guild or not interaction.guild.voice_client:
            await interaction.response.send_message(
                embed=create_error_embed("Not connected!"),
                ephemeral=True
            )
            return
        
        state = self.get_state(interaction.guild.id)
        
        # Clear channel status if enabled
        if state.is_channel_status:
            channel = interaction.guild.voice_client.channel
            if channel:
                try:
                    await channel.edit(status=None)
                except:
                    pass
            state.original_channel_status = None
        
        state.queue.clear()
        state.autoplay_visible.clear()
        state.autoplay_hidden.clear()
        state.current = None
        state.is_autoplay = False
        state.is_channel_status = False
        
        await interaction.guild.voice_client.disconnect()
        await interaction.response.send_message(
            embed=create_success_embed("👋 Disconnected from voice channel.")
        )
    
    @app_commands.command(name="clear", description="Clear the queue")
    async def clear(self, interaction: discord.Interaction):
        """Clear the queue."""
        if not interaction.guild:
            return
        
        state = self.get_state(interaction.guild.id)
        count = len(state.queue)
        state.queue.clear()
        
        await interaction.response.send_message(
            embed=create_success_embed(f"🗑️ Cleared **{count}** songs from the queue.")
        )
    
    @app_commands.command(name="upcoming", description="Show next 5 autoplay songs")
    async def upcoming(self, interaction: discord.Interaction):
        """Show upcoming autoplay songs."""
        if not interaction.guild:
            return
        
        state = self.get_state(interaction.guild.id)
        
        if not state.autoplay_visible:
            await interaction.response.send_message(
                embed=create_info_embed(
                    "Autoplay Buffer Empty",
                    "No songs in buffer yet. Play some songs to build listening history!"
                ),
                ephemeral=True
            )
            return
        
        embed = create_upcoming_autoplay_embed(state.autoplay_visible)
        await interaction.response.send_message(embed=embed)
    
    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState
    ):
        """Handle voice state changes - manage per-user autoplay slots."""
        if member.bot:
            return
        
        vc = member.guild.voice_client
        if not vc:
            return
        
        state = self.get_state(member.guild.id)
        
        # Check if user left the bot's channel
        user_left = before.channel == vc.channel and after.channel != vc.channel
        user_joined = before.channel != vc.channel and after.channel == vc.channel
        
        if user_left:
            logger.info(f"Voice State: User {member.name} left {vc.channel.name}")
            
            # Remove this user's slots from autoplay queues
            state.autoplay_visible = [s for s in state.autoplay_visible 
                                       if not hasattr(s, 'user_id') or s.user_id != member.id]
            state.autoplay_hidden = [s for s in state.autoplay_hidden 
                                      if not hasattr(s, 'user_id') or s.user_id != member.id]
            
            # Also remove from DB
            await db.remove_user_from_session_queue(member.guild.id, member.id)
            
            logger.info(f"Removed {member.name}'s slots from autoplay queues")
        
        elif user_joined and state.is_autoplay:
            logger.info(f"Voice State: User {member.name} joined {vc.channel.name}")
            # Trigger refill to add their slots to hidden queue
            asyncio.create_task(self._refill_autoplay_buffer(member.guild.id))
        
        # Auto-disconnect if VC is empty (only bot left)
        if vc.channel and len(vc.channel.members) == 1:
            if not state.is_24_7:
                state.queue.clear()
                state.autoplay_visible.clear()
                state.autoplay_hidden.clear()
                state.current = None
                await db.clear_session_queue(member.guild.id)
                await vc.disconnect()
                logger.info(f"Auto-Disconnect: VC empty in guild {member.guild.id}.")


async def setup(bot: commands.Bot):
    """Setup function for loading the cog."""
    await bot.add_cog(Music(bot))
