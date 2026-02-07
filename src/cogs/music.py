"""
Music Cog - Playback commands and audio streaming
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, UTC
from typing import Optional

import discord
from discord import app_commands
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


class NowPlayingView(discord.ui.View):
    """Interactive Now Playing controls."""
    
    def __init__(self, cog: "MusicCog", guild_id: int):
        super().__init__(timeout=300)  # 5 minute timeout
        self.cog = cog
        self.guild_id = guild_id
    
    @discord.ui.button(emoji="⏸️", style=discord.ButtonStyle.secondary)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self.cog.get_player(self.guild_id)
        if player.voice_client:
            if player.voice_client.is_playing():
                player.voice_client.pause()
                button.emoji = "▶️"
                await interaction.response.edit_message(view=self)
            elif player.voice_client.is_paused():
                player.voice_client.resume()
                button.emoji = "⏸️"
                await interaction.response.edit_message(view=self)
            else:
                await interaction.response.defer()
        else:
            await interaction.response.defer()
    
    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self.cog.get_player(self.guild_id)
        if player.voice_client:
            # Clear queue
            while not player.queue.empty():
                try:
                    player.queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            
            # Stop playing (this will break the loop in _play_loop)
            if player.is_playing or player.voice_client.is_playing():
                player.voice_client.stop()
            
            # Disconnect
            await player.voice_client.disconnect()
            player.voice_client = None
            
            await interaction.response.send_message("⏹️ Stopped and cleared queue!", ephemeral=True)
            self.stop()  # Stop listening for interactions
        else:
            await interaction.response.defer()
    
    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self.cog.get_player(self.guild_id)
        if player.voice_client and player.is_playing:
            player.voice_client.stop()
            await interaction.response.send_message("⏭️ Skipped!", ephemeral=True)
        else:
            await interaction.response.defer()
    
    @discord.ui.button(emoji="❤️", style=discord.ButtonStyle.secondary)
    async def like(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self.cog.get_player(self.guild_id)
        if player.current:
            # Database: Log Reaction
            if hasattr(self.cog.bot, "db") and self.cog.bot.db and player.current.song_db_id:
                try:
                    song_crud = SongCRUD(self.cog.bot.db)
                    reaction_crud = ReactionCRUD(self.cog.bot.db)
                    
                    # Make permanent if it was ephemeral
                    await song_crud.make_permanent(player.current.song_db_id)

                    # Log reaction
                    await reaction_crud.add_reaction(interaction.user.id, player.current.song_db_id, "like")
                    
                    # Library: Record as 'like'
                    from src.database.crud import LibraryCRUD
                    lib_crud = LibraryCRUD(self.cog.bot.db)
                    await lib_crud.add_to_library(interaction.user.id, player.current.song_db_id, "like")
                except Exception as e:
                    log.error_cat(Category.USER, "Failed to log like", error=str(e))
            
            await interaction.response.send_message(
                f"❤️ Liked **{player.current.title}**!",
                ephemeral=True
            )
        else:
            await interaction.response.defer()
    
    @discord.ui.button(emoji="👎", style=discord.ButtonStyle.secondary)
    async def dislike(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self.cog.get_player(self.guild_id)
        if player.current:
            # Database: Log Reaction
            if hasattr(self.cog.bot, "db") and self.cog.bot.db and player.current.song_db_id:
                try:
                    song_crud = SongCRUD(self.cog.bot.db)
                    reaction_crud = ReactionCRUD(self.cog.bot.db)
                    
                    # Make permanent (even disliking counts as interaction so we keep record)
                    await song_crud.make_permanent(player.current.song_db_id)

                    await reaction_crud.add_reaction(interaction.user.id, player.current.song_db_id, "dislike")
                except Exception as e:
                    log.error_cat(Category.USER, "Failed to log dislike", error=str(e))
            
            await interaction.response.send_message(
                f"👎 Disliked **{player.current.title}**",
                ephemeral=True
            )
        else:
            await interaction.response.defer()


class MusicCog(commands.Cog):
    """Music playback commands and queue management."""
    
    FFMPEG_BEFORE_OPTIONS = (
        "-reconnect 1 -reconnect_streamed 1 "
        "-reconnect_on_network_error 1 -reconnect_on_http_error 403,429,500,502,503 "
        "-reconnect_delay_max 15 -nostdin"
    )
    FFMPEG_OPTIONS = {
        "before_options": FFMPEG_BEFORE_OPTIONS,
        "options": "-vn",
    }
    IDLE_TIMEOUT = 300  # 5 minutes

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
    
    # ==================== COMMANDS ====================
    
    play_group = app_commands.Group(name="play", description="Play music commands")
    
    @play_group.command(name="song", description="Search and play a specific song")
    @app_commands.describe(query="Song name or search query")
    async def play_song(self, interaction: discord.Interaction, query: str):
        """Search for a song and add it to the queue."""
        await interaction.response.defer(ephemeral=True)
        
        # Check if user is in a voice channel
        if not interaction.user.voice:
            await interaction.followup.send("❌ You need to be in a voice channel!", ephemeral=True)
            return
        
        voice_channel = interaction.user.voice.channel
        player = self.get_player(interaction.guild_id)
        
        # Connect to voice channel if not already
        if not player.voice_client or not player.voice_client.is_connected():
            try:
                player.voice_client = await voice_channel.connect(self_deaf=True, timeout=20.0)
                log.event(Category.VOICE, Event.VOICE_CONNECTED, channel=voice_channel.name, guild=interaction.guild.name)
            except Exception as e:
                await interaction.followup.send(f"❌ Failed to connect: {e}", ephemeral=True)
                return
        
        # Search for the song
        # For specific song request, we want the BEST match, so limit 1
        results = await self.youtube.search(query, filter_type="songs", limit=1)
        
        if not results:
            await interaction.followup.send(f"❌ No results found for: `{query}`", ephemeral=True)
            return
        
        track = results[0]
        
        # Check max duration
        if hasattr(self.bot, "db") and self.bot.db:
            from src.database.crud import GuildCRUD
            guild_crud = GuildCRUD(self.bot.db)
            max_duration = await guild_crud.get_setting(interaction.guild_id, "max_song_duration")
            
            if max_duration and track.duration_seconds:
                try:
                    max_seconds = int(max_duration) * 60
                    if max_seconds > 0 and track.duration_seconds > max_seconds:
                        await interaction.followup.send(
                            f"❌ Song is too long! (Limit: {max_duration} mins)",
                            ephemeral=True
                        )
                        return
                except (ValueError, TypeError):
                    pass
                    
        log.event(Category.QUEUE, Event.TRACK_QUEUED, title=track.title, artist=track.artist)
        
        # Database persistence
        song_db_id = None
        if hasattr(self.bot, "db") and self.bot.db:
            try:
                user_crud = UserCRUD(self.bot.db)
                song_crud = SongCRUD(self.bot.db)
                
                # Ensure user exists
                await user_crud.get_or_create(interaction.user.id, interaction.user.name)
                
                # Ensure song exists
                song = await song_crud.get_or_create_by_yt_id(
                    canonical_yt_id=track.video_id,
                    title=track.title,
                    artist_name=track.artist,
                    duration_seconds=track.duration_seconds,
                    release_year=track.year,
                    album=track.album
                )
                song_db_id = song["id"]
                
                # Library: Record as 'request'
                from src.database.crud import LibraryCRUD
                lib_crud = LibraryCRUD(self.bot.db)
                await lib_crud.add_to_library(interaction.user.id, song_db_id, "request")
            except Exception as e:
                log.error_cat(Category.DATABASE, "Failed to persist song/user data", error=str(e))

        # Add to queue
        item = QueueItem(
            video_id=track.video_id,
            title=track.title,
            artist=track.artist,
            requester_id=interaction.user.id,
            discovery_source="user_request",
            song_db_id=song_db_id,
            duration_seconds=track.duration_seconds,
            year=track.year
        )
        await player.queue.put(item)
        player.last_activity = datetime.now(UTC)
        player.text_channel_id = interaction.channel_id  # Store for Now Playing
        
        # Start playback if not already playing
        if not player.is_playing:
            asyncio.create_task(self._play_loop(player))
        
        # Create embed
        embed = discord.Embed(
            title="🎵 Added to Queue",
            description=f"**{track.title}**\nby {track.artist}",
            color=discord.Color.green()
        )
        embed.set_footer(text=f"Requested by {interaction.user.display_name}")
        
        await interaction.followup.send(embed=embed, ephemeral=True)

    @play_group.command(name="artist", description="Play top songs by an artist and learn your preference")
    @app_commands.describe(artist_name="Artist name")
    async def play_artist(self, interaction: discord.Interaction, artist_name: str):
        """Search for an artist, boost preference, and queue top 5 songs."""
        await interaction.response.defer(ephemeral=True)
        
        # Check if user is in a voice channel
        if not interaction.user.voice:
            await interaction.followup.send("❌ You need to be in a voice channel!", ephemeral=True)
            return
            
        voice_channel = interaction.user.voice.channel
        player = self.get_player(interaction.guild_id)
        
        # Connect to voice channel if not already
        if not player.voice_client or not player.voice_client.is_connected():
            try:
                player.voice_client = await voice_channel.connect(self_deaf=True, timeout=20.0)
            except Exception as e:
                await interaction.followup.send(f"❌ Failed to connect: {e}", ephemeral=True)
                return

        # 1. Search artist on Spotify
        sp_artist = await self.bot.spotify.search_artist(artist_name)
        if not sp_artist:
            await interaction.followup.send(f"❌ Artist not found on Spotify: `{artist_name}`", ephemeral=True)
            return

        # 2. Boost preference
        if hasattr(self.bot, "preferences") and self.bot.preferences:
            await self.bot.preferences.boost_artist(interaction.user.id, sp_artist.name)

        # 3. Fetch top tracks
        top_tracks = await self.bot.spotify.get_artist_top_tracks(sp_artist.artist_id)
        if not top_tracks:
            await interaction.followup.send(f"❌ No top tracks found for artist: `{sp_artist.name}`", ephemeral=True)
            return

        # 4. Filter and add top 5 songs
        tracks_to_add = top_tracks[:5]
        
        queued_count = 0
        from src.database.crud import SongCRUD, UserCRUD, LibraryCRUD, GuildCRUD
        song_crud = SongCRUD(self.bot.db) if hasattr(self.bot, "db") else None
        lib_crud = LibraryCRUD(self.bot.db) if hasattr(self.bot, "db") else None
        guild_crud = GuildCRUD(self.bot.db) if hasattr(self.bot, "db") else None
        
        max_seconds = 0
        if guild_crud:
            try:
                max_dur = await guild_crud.get_setting(interaction.guild_id, "max_song_duration")
                if max_dur:
                    max_seconds = int(max_dur) * 60
            except (ValueError, TypeError):
                pass
        
        for track in tracks_to_add:
            # We need to find the YouTube ID for these Spotify tracks to play them
            # Use the normalizer to get the canonical YT data
            yt_track = await self.bot.normalizer.normalize_to_yt(track.title, track.artist)
            if not yt_track:
                continue
            
            # Check duration
            if max_seconds > 0 and yt_track.duration_seconds and yt_track.duration_seconds > max_seconds:
                continue
                
            song_db_id = None
            if song_crud:
                # Ensure user exists
                user_crud = UserCRUD(self.bot.db)
                await user_crud.get_or_create(interaction.user.id, interaction.user.name)
                
                # Ensure song exists
                song = await song_crud.get_or_create_by_yt_id(
                    canonical_yt_id=yt_track.video_id,
                    title=yt_track.title,
                    artist_name=yt_track.artist,
                    duration_seconds=yt_track.duration_seconds,
                    release_year=yt_track.year,
                    album=yt_track.album
                )
                song_db_id = song["id"]
                
                # Library: Record as 'request'
                if lib_crud:
                    await lib_crud.add_to_library(interaction.user.id, song_db_id, "request")

            item = QueueItem(
                video_id=yt_track.video_id,
                title=yt_track.title,
                artist=yt_track.artist,
                requester_id=interaction.user.id,
                discovery_source="user_request",
                discovery_reason=f"Top track by {sp_artist.name}",
                song_db_id=song_db_id,
                duration_seconds=yt_track.duration_seconds,
                year=yt_track.year
            )
            await player.queue.put(item)
            queued_count += 1

        if queued_count == 0:
            await interaction.followup.send(f"❌ Failed to find playable tracks for: `{sp_artist.name}`", ephemeral=True)
            return

        player.last_activity = datetime.now(UTC)
        player.text_channel_id = interaction.channel_id
        
        # Start playback if not already playing
        if not player.is_playing:
            asyncio.create_task(self._play_loop(player))

        # Create embed
        embed = discord.Embed(
            title="👩‍🎤 Artist Radio Queued",
            description=f"Added **{queued_count}** top tracks by **{sp_artist.name}**\nAlso boosted your preference for this artist! ❤️",
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Requested by {interaction.user.display_name}")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
    
    @play_group.command(name="any", description="Start playing with discovery mode")
    async def play_any(self, interaction: discord.Interaction):
        """Start discovery playback without a specific song."""
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            # Interaction might have expired or been acknowledged already, just log and continue if possible or return
            log.warning_cat(Category.SYSTEM, "Interaction expired (404) in play_any")
            return
        except Exception as e:
            log.error_cat(Category.SYSTEM, "Failed to defer interaction", error=str(e))
            return
        
        if not interaction.user.voice:
            await interaction.followup.send("❌ You need to be in a voice channel!", ephemeral=True)
            return
        
        voice_channel = interaction.user.voice.channel
        player = self.get_player(interaction.guild_id)
        
        # Connect to voice channel
        if not player.voice_client or not player.voice_client.is_connected():
            try:
                player.voice_client = await voice_channel.connect(self_deaf=True, timeout=20.0)
            except Exception as e:
                await interaction.followup.send(f"❌ Failed to connect: {e}", ephemeral=True)
                return
        
        player.autoplay = True
        player.last_activity = datetime.now(UTC)
        player.text_channel_id = interaction.channel_id  # Store for Now Playing
        
        # Start playback if not playing - discovery will kick in
        if not player.is_playing:
            asyncio.create_task(self._play_loop(player))
        
        await interaction.followup.send("🎲 **Discovery mode activated!** Finding songs for you...", ephemeral=True)
    
    @app_commands.command(name="pause", description="Pause the current song")
    async def pause(self, interaction: discord.Interaction):
        """Pause playback."""
        player = self.get_player(interaction.guild_id)
        
        if player.voice_client and player.voice_client.is_playing():
            player.voice_client.pause()
            await interaction.response.send_message("⏸️ Paused", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Nothing is playing", ephemeral=True)
    
    @app_commands.command(name="resume", description="Resume the paused song")
    async def resume(self, interaction: discord.Interaction):
        """Resume playback."""
        player = self.get_player(interaction.guild_id)
        
        if player.voice_client and player.voice_client.is_paused():
            player.voice_client.resume()
            await interaction.response.send_message("▶️ Resumed", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Nothing is paused", ephemeral=True)
    
    @app_commands.command(name="skip", description="Skip the current song")
    async def skip(self, interaction: discord.Interaction):
        """Skip the current song."""
        player = self.get_player(interaction.guild_id)
        
        if not player.voice_client or not player.is_playing:
            await interaction.response.send_message("❌ Nothing is playing", ephemeral=True)
            return
        
        player.voice_client.stop()
        await interaction.response.send_message("⏭️ Skipped!", ephemeral=True)
    
    @app_commands.command(name="forceskip", description="Force skip (DJ only)")
    @app_commands.default_permissions(manage_channels=True)
    async def forceskip(self, interaction: discord.Interaction):
        """Force skip without voting."""
        player = self.get_player(interaction.guild_id)
        
        if player.voice_client and player.is_playing:
            player.voice_client.stop()
            await interaction.response.send_message("⏭️ Force skipped!", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Nothing is playing", ephemeral=True)
    
    @app_commands.command(name="queue", description="Show the current queue")
    async def queue(self, interaction: discord.Interaction):
        """Show the queue."""
        player = self.get_player(interaction.guild_id)
        
        embed = discord.Embed(title="🎵 Queue", color=discord.Color.blue())
        
        # Current song
        if player.current:
            embed.add_field(
                name="Now Playing",
                value=f"**{player.current.title}**\nby {player.current.artist}",
                inline=False
            )
        
        # Upcoming songs
        if player.queue.empty():
            embed.add_field(name="Up Next", value="Queue is empty", inline=False)
        else:
            # Convert queue to list for display (peek without removing)
            items = list(player.queue._queue)[:10]
            upcoming = []
            for i, item in enumerate(items, 1):
                upcoming.append(f"{i}. **{item.title}** - {item.artist}")
            embed.add_field(name="Up Next", value="\n".join(upcoming), inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @app_commands.command(name="nowplaying", description="Show the current song")
    async def nowplaying(self, interaction: discord.Interaction):
        """Show current song with discovery info."""
        player = self.get_player(interaction.guild_id)
        
        if not player.current:
            await interaction.response.send_message("❌ Nothing is playing", ephemeral=True)
            return
        
        embed = discord.Embed(
            title="🎵 Now Playing",
            description=f"**{player.current.title}**\nby {player.current.artist}",
            color=discord.Color.green()
        )
        
        if player.current.discovery_reason:
            embed.add_field(name="Discovery", value=player.current.discovery_reason, inline=False)
        
        if player.current.for_user_id:
            user = self.bot.get_user(player.current.for_user_id)
            if user:
                embed.set_footer(text=f"🎲 Playing for {user.display_name}")
        elif player.current.requester_id:
            user = self.bot.get_user(player.current.requester_id)
            if user:
                embed.set_footer(text=f"Requested by {user.display_name}")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @app_commands.command(name="clear", description="Clear the queue (DJ only)")
    @app_commands.default_permissions(manage_channels=True)
    async def clear(self, interaction: discord.Interaction):
        """Clear the queue."""
        player = self.get_player(interaction.guild_id)
        
        # Clear the queue
        while not player.queue.empty():
            try:
                player.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        
        await interaction.response.send_message("🗑️ Queue cleared!", ephemeral=True)
    
    @app_commands.command(name="autoplay", description="Toggle autoplay/discovery mode")
    @app_commands.describe(enabled="Enable or disable autoplay")
    async def autoplay(self, interaction: discord.Interaction, enabled: bool):
        """Toggle autoplay mode."""
        player = self.get_player(interaction.guild_id)
        player.autoplay = enabled
        
        status = "enabled" if enabled else "disabled"
        await interaction.response.send_message(f"🎲 Autoplay {status}", ephemeral=True)
    
    # ==================== PLAYBACK LOOP ====================
    
    async def _play_loop(self, player: GuildPlayer):
        """Main playback loop for a guild."""
        player.is_playing = True
        
        try:
            while player.voice_client and player.voice_client.is_connected():
                player.skip_votes.clear()
                
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
                                    max_dur = await guild_crud.get_setting(player.guild_id, "max_song_duration")
                                    if max_dur:
                                        max_seconds = int(max_dur) * 60
                                except: pass
                            
                            # Try up to 3 times to find a song within duration limit
                            for _ in range(3):
                                item = await self._get_discovery_song(player)
                                if not item:
                                    break
                                
                                if max_seconds > 0 and item.duration_seconds and item.duration_seconds > max_seconds:
                                    log.event(Category.DISCOVERY, "song_skipped_duration", title=item.title, duration=item.duration_seconds, max_duration=max_seconds)
                                    continue
                                break
                            
                            if not item:
                                log.event(Category.DISCOVERY, Event.DISCOVERY_FAILED, guild_id=player.guild_id, reason="no_songs_available")
                                break
                        else:
                            break
                    else:
                        item = player.queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                
                player.current = item
                player.last_activity = datetime.now(UTC)
                
                # Database: Ensure session and log playback
                history_id = None
                if hasattr(self.bot, "db") and self.bot.db:
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
                        
                        # Metadata Enrichment Logic (Prioritizing Spotify for accuracy)
                        spotify = getattr(self.bot, "spotify", None)
                        if spotify:
                            try:
                                # Always attempt Spotify lookup for better Genre/Year quality
                                query = f"{item.artist} {item.title}"
                                sp_track = await spotify.search_track(query)
                                if sp_track:
                                    # Spotify is the source of truth for year and genre
                                    item.year = sp_track.release_year
                                    
                                    # Get precise genres from Spotify Artist
                                    artist = await spotify.get_artist(sp_track.artist_id)
                                    if artist and artist.genres:
                                        # Use primary genre
                                        item.genre = artist.genres[0].title()
                                        
                                        # Clear old/wrong genres and save confirmed one to DB
                                        if item.song_db_id:
                                            await song_crud.clear_genres(item.song_db_id)
                                            await song_crud.add_genre(item.song_db_id, item.genre)
                                    
                                    # Sync back to main song table
                                    if item.song_db_id:
                                        await song_crud.get_or_create_by_yt_id(
                                            canonical_yt_id=item.video_id,
                                            title=item.title,
                                            artist_name=item.artist,
                                            release_year=item.year,
                                            duration_seconds=item.duration_seconds
                                        )
                            except Exception as e:
                                log.debug_cat(Category.API, "Spotify enrichment failed", error=str(e))
                        
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
                
                # Get stream URL and HTTP headers
                stream_info = await self.youtube.get_stream_url(item.video_id)
                if not stream_info:
                    log.event(Category.PLAYBACK, Event.PLAYBACK_ERROR, level=logging.ERROR, video_id=item.video_id, reason="stream_url_failed")
                    continue

                item.url = stream_info.url

                # Pre-buffer next URL if enabled
                if player.pre_buffer and not player.queue.empty():
                    asyncio.create_task(self._pre_buffer_next(player))

                # Play the audio
                try:
                    ffmpeg_opts = self._build_ffmpeg_options(stream_info)
                    source = await discord.FFmpegOpusAudio.from_probe(stream_info.url, **ffmpeg_opts)
                    
                    play_complete = asyncio.Event()
                    
                    def after_play(error):
                        if error:
                            log.event(Category.PLAYBACK, Event.PLAYBACK_ERROR, level=logging.ERROR, error=str(error))
                        play_complete.set()
                    
                    player.voice_client.play(source, after=after_play)
                    
                    log.event(
                        Category.PLAYBACK, Event.TRACK_STARTED,
                        title=item.title, artist=item.artist, genre=item.genre or "Unknown",
                        user_id=item.for_user_id or item.requester_id,
                        source=item.discovery_source, reason=item.discovery_reason
                    )
                    
                    # Send Now Playing embed
                    await self._send_now_playing(player)
                    
                    # Wait for song to finish
                    await play_complete.wait()
                    
                    # Database: Log Playback End
                    if hasattr(self.bot, "db") and self.bot.db and item.history_id:
                         try:
                             playback_crud = PlaybackCRUD(self.bot.db)
                             # Default true unless skipped (we can check skip_votes or logic later, but for now assumption is valid if we reached here without break)
                             # If skipping happens, strict logic is needed, but 'completed' usually means 'finished playing' or 'was played'. 
                             # Here we mark it completed. If skipped, we might want to update it differently, but mark_completed takes a bool.
                             # If we were skipped, the wait() is broken? No, stop() calls after_play.
                             # So we check if we were stopped forcefully. 
                             # For simplicity, we mark completed=True. Refinement: if queue was cleared or force skipped?
                             # Let's assume True for now, user analytics usually count partial plays too.
                             completed = True
                             
                             # Check if skipped via votes (rough check)
                             if player.skip_votes and len(player.skip_votes) > 0:
                                 completed = False
                                 
                             await playback_crud.mark_completed(item.history_id, completed)
                         except Exception as e:
                             log.error_cat(Category.DATABASE, "Failed to log playback end", error=str(e))
                    
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
                    # Map discovery engine strategy names to DB-compatible discovery_source values
                    # The DB CHECK constraint expects 'same_artist', but the engine uses 'artist'
                    source_map = {"artist": "same_artist"}
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
    
    async def _send_now_playing(self, player: GuildPlayer):
        """Send Now Playing embed to the text channel."""
        if not player.current or not player.text_channel_id:
            return
        
        channel = self.bot.get_channel(player.text_channel_id)
        if not channel:
            return
            
        # Delete old message
        if player.last_np_msg:
            try:
                await player.last_np_msg.delete()
            except:
                pass
            player.last_np_msg = None
        
        try:
            item = player.current
            
            embed = discord.Embed(
                title="🎵 Now Playing",
                color=discord.Color.from_rgb(124, 58, 237)
            )
            
            embed.add_field(name="🎶 Track", value=f"**{item.title}**", inline=True)
            embed.add_field(name="🎤 Artist", value=item.artist, inline=True)
            
            if item.duration_seconds:
                minutes, seconds = divmod(item.duration_seconds, 60)
                duration_str = f"{minutes}:{seconds:02d}"
                embed.add_field(name="⏳ Duration", value=duration_str, inline=True)
            
            if item.genre:
                embed.add_field(name="🏷️ Genre", value=item.genre, inline=True)
            
            if item.year:
                embed.add_field(name="📅 Year", value=str(item.year), inline=True)
            
            if item.discovery_reason:
                embed.add_field(name="✨ Discovery", value=item.discovery_reason, inline=False)
            
            if item.for_user_id:
                embed.add_field(name="🎯 Playing for", value=f"<@{item.for_user_id}>", inline=True)
            elif item.requester_id:
                embed.add_field(name="📨 Requested by", value=f"<@{item.requester_id}>", inline=True)
            
            embed.set_thumbnail(url=f"https://img.youtube.com/vi/{item.video_id}/hqdefault.jpg")
            
            # Add Interaction Stats (Who requested, liked, disliked)
            if hasattr(self.bot, "db") and item.song_db_id:
                try:
                    stats = await self.bot.db.fetch_one("""
                        SELECT 
                            (SELECT GROUP_CONCAT(DISTINCT u.username) FROM playback_history ph JOIN users u ON ph.for_user_id = u.id WHERE ph.song_id = ? AND ph.discovery_source = "user_request") as requested_by,
                            (SELECT GROUP_CONCAT(DISTINCT u.username) FROM song_reactions sr JOIN users u ON sr.song_id = ? AND sr.reaction = 'like') as liked_by,
                            (SELECT GROUP_CONCAT(DISTINCT u.username) FROM song_reactions sr JOIN users u ON sr.user_id = u.id WHERE sr.song_id = ? AND sr.reaction = 'dislike') as disliked_by
                    """, (item.song_db_id, item.song_db_id, item.song_db_id))
                    
                    if stats:
                        if stats["requested_by"]:
                            embed.add_field(name="📨 Requested By", value=stats["requested_by"], inline=False)
                        if stats["liked_by"]:
                            embed.add_field(name="❤️ Liked By", value=stats["liked_by"], inline=False)
                        if stats["disliked_by"]:
                            embed.add_field(name="👎 Disliked By", value=stats["disliked_by"], inline=False)
                except Exception as e:
                    log.debug_cat(Category.DATABASE, "Failed to fetch interaction stats for embed", error=str(e))

            embed.add_field(name="📜 Queue", value=f"{player.queue.qsize()} songs", inline=True)
            yt_url = f"https://youtube.com/watch?v={item.video_id}"
            embed.add_field(name="🔗 Link", value=f"[YouTube]({yt_url})", inline=True)
            
            # Create view with buttons
            view = NowPlayingView(self, player.guild_id)
            
            player.last_np_msg = await channel.send(embed=embed, view=view)
        except Exception as e:
            log.debug_cat(Category.SYSTEM, "Failed to send Now Playing embed", error=str(e))
    
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
        """Check for idle players and disconnect."""
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
