"""
Play commands cog.

Keeps `/play ...` commands separate from the music player / playback loop implementation.
"""
import asyncio
import time
from datetime import datetime, UTC

import discord
from discord import app_commands
from discord.ext import commands

from src.database.crud import SongCRUD, UserCRUD, GuildCRUD, LibraryCRUD
from src.utils.logging import get_logger, Category, Event

from src.cogs.music import QueueItem

log = get_logger(__name__)


class PlayCog(commands.Cog):
    """Slash commands for queuing/starting playback."""

    play_group = app_commands.Group(name="play", description="Play music commands")

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @property
    def music(self):
        return self.bot.get_cog("MusicCog")

    @play_group.command(name="song", description="Search and play a specific song")
    @app_commands.describe(query="Song name or search query")
    async def play_song(self, interaction: discord.Interaction, query: str):
        """Search for a song and add it to the queue."""
        music = self.music
        if not music:
            await interaction.response.send_message("❌ Music system is not loaded.", ephemeral=True)
            return

        cmd_t0 = time.perf_counter()
        with log.span(
            Category.SYSTEM,
            "command_play_song",
            module=__name__,
            cog=type(self).__name__,
            command="/play song",
            guild_id=interaction.guild_id,
            channel_id=getattr(interaction.channel, "id", None),
            user_id=getattr(interaction.user, "id", None),
            query=query,
        ):
            try:
                await interaction.response.defer(ephemeral=True)
            except discord.InteractionResponded:
                pass
            except discord.NotFound:
                log.warning_cat(Category.SYSTEM, "Interaction expired/unknown (404) in play_song", query=query)
                return
            except Exception as e:
                log.exception_cat(Category.SYSTEM, "Failed to defer interaction in play_song", error=str(e), query=query)
                return

            if not interaction.user.voice:
                try:
                    await interaction.followup.send("❌ You need to be in a voice channel!", ephemeral=True)
                except discord.NotFound:
                    pass
                return

            voice_channel = interaction.user.voice.channel
            player = music.get_player(interaction.guild_id)

            # Connect to voice channel if not already
            if not player.voice_client or not player.voice_client.is_connected():
                try:
                    player.voice_client = await voice_channel.connect(self_deaf=True, timeout=20.0)
                    log.event(Category.VOICE, Event.VOICE_CONNECTED, channel=voice_channel.name, guild=interaction.guild.name)
                except Exception as e:
                    try:
                        await interaction.followup.send(f"❌ Failed to connect: {e}", ephemeral=True)
                    except discord.NotFound:
                        pass
                    return

            results = await music.youtube.search(query, filter_type="songs", limit=1)
            if not results:
                try:
                    await interaction.followup.send(f"❌ No results found for: `{query}`", ephemeral=True)
                except discord.NotFound:
                    pass
                return

            track = results[0]

            def _coerce_duration_seconds(value) -> int | None:
                if value is None:
                    return None
                if isinstance(value, int):
                    return value
                if isinstance(value, float):
                    return int(value)
                if isinstance(value, str):
                    s = value.strip()
                    if not s:
                        return None
                    if ":" in s:
                        parts = s.split(":")
                        try:
                            nums = [int(p) for p in parts]
                        except ValueError:
                            return None
                        if len(nums) == 2:
                            m, sec = nums
                            return m * 60 + sec
                        if len(nums) == 3:
                            h, m, sec = nums
                            return h * 3600 + m * 60 + sec
                        return None
                    try:
                        return int(float(s))
                    except ValueError:
                        return None
                return None

            duration_seconds = _coerce_duration_seconds(getattr(track, "duration_seconds", None))

            # Check max duration
            if hasattr(self.bot, "db") and self.bot.db:
                guild_crud = GuildCRUD(self.bot.db)
                max_duration = await guild_crud.get_setting(interaction.guild_id, "max_song_duration")
                if max_duration and duration_seconds is not None:
                    try:
                        max_seconds = int(max_duration) * 60
                        if max_seconds > 0 and duration_seconds > max_seconds:
                            log.info_cat(
                                Category.USER,
                                "song_rejected_duration",
                                module=__name__,
                                cog=type(self).__name__,
                                command="/play song",
                                guild_id=interaction.guild_id,
                                query=query,
                                video_id=track.video_id,
                                duration_seconds=duration_seconds,
                                max_seconds=max_seconds,
                                max_minutes=max_duration,
                                ms=int((time.perf_counter() - cmd_t0) * 1000),
                            )
                            try:
                                await interaction.followup.send(
                                    f"❌ Song is too long! (Limit: {max_duration} mins)",
                                    ephemeral=True,
                                )
                            except discord.NotFound:
                                pass
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

                    await user_crud.get_or_create(interaction.user.id, interaction.user.name)

                    song = await song_crud.get_or_create_by_yt_id(
                        canonical_yt_id=track.video_id,
                        title=track.title,
                        artist_name=track.artist,
                        duration_seconds=duration_seconds,
                        release_year=track.year,
                        album=track.album,
                    )
                    song_db_id = song["id"]

                    lib_crud = LibraryCRUD(self.bot.db)
                    await lib_crud.add_to_library(interaction.user.id, song_db_id, "request")
                except Exception as e:
                    log.error_cat(Category.DATABASE, "Failed to persist song/user data", error=str(e))

            item = QueueItem(
                video_id=track.video_id,
                title=track.title,
                artist=track.artist,
                requester_id=interaction.user.id,
                discovery_source="user_request",
                song_db_id=song_db_id,
                duration_seconds=duration_seconds,
                year=track.year,
            )
            await player.queue.put(item)
            player.last_activity = datetime.now(UTC)
            player.text_channel_id = interaction.channel_id

            if not player.is_playing:
                asyncio.create_task(music._play_loop(player))

            embed = discord.Embed(
                title="🎵 Added to Queue",
                description=f"**{track.title}**\nby {track.artist}",
                color=discord.Color.green(),
            )
            embed.set_footer(text=f"Requested by {interaction.user.display_name}")

            try:
                await interaction.followup.send(embed=embed, ephemeral=True)
            except discord.NotFound:
                pass

    @play_group.command(name="artist", description="Play top songs by an artist and learn your preference")
    @app_commands.describe(artist_name="Artist name")
    async def play_artist(self, interaction: discord.Interaction, artist_name: str):
        """Search for an artist, boost preference, and queue top 5 songs."""
        music = self.music
        if not music:
            await interaction.response.send_message("❌ Music system is not loaded.", ephemeral=True)
            return

        with log.span(
            Category.SYSTEM,
            "command_play_artist",
            module=__name__,
            cog=type(self).__name__,
            command="/play artist",
            guild_id=interaction.guild_id,
            channel_id=getattr(interaction.channel, "id", None),
            user_id=getattr(interaction.user, "id", None),
            artist_name=artist_name,
        ):
            await interaction.response.defer(ephemeral=True)

            if not interaction.user.voice:
                await interaction.followup.send("❌ You need to be in a voice channel!", ephemeral=True)
                return

            voice_channel = interaction.user.voice.channel
            player = music.get_player(interaction.guild_id)

            if not player.voice_client or not player.voice_client.is_connected():
                try:
                    player.voice_client = await voice_channel.connect(self_deaf=True, timeout=20.0)
                except Exception as e:
                    await interaction.followup.send(f"❌ Failed to connect: {e}", ephemeral=True)
                    return

            sp = getattr(self.bot, "spotify", None)
            if not sp:
                await interaction.followup.send("❌ Spotify service is not available.", ephemeral=True)
                return

            sp_artist = await sp.search_artist(artist_name)
            if not sp_artist:
                await interaction.followup.send(f"❌ Artist not found on Spotify: `{artist_name}`", ephemeral=True)
                return

            if hasattr(self.bot, "preferences") and self.bot.preferences:
                await self.bot.preferences.boost_artist(interaction.user.id, sp_artist.name)

            top_tracks = await sp.get_artist_top_tracks(sp_artist.artist_id)
            if not top_tracks:
                await interaction.followup.send(f"❌ No top tracks found for artist: `{sp_artist.name}`", ephemeral=True)
                return

            tracks_to_add = top_tracks[:5]
            queued_count = 0

            song_db_id = None
            for yt_track in tracks_to_add:
                try:
                    song_db_id = None
                    if hasattr(self.bot, "db") and self.bot.db:
                        song_crud = SongCRUD(self.bot.db)
                        song = await song_crud.get_or_create_by_yt_id(
                            canonical_yt_id=yt_track.video_id,
                            title=yt_track.title,
                            artist_name=yt_track.artist,
                            duration_seconds=yt_track.duration_seconds,
                            release_year=yt_track.year,
                            album=yt_track.album,
                        )
                        song_db_id = song["id"]

                        lib_crud = LibraryCRUD(self.bot.db)
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
                        year=yt_track.year,
                    )
                    await player.queue.put(item)
                    queued_count += 1
                except Exception as e:
                    log.error_cat(Category.SYSTEM, "Failed to queue artist track", error=str(e), title=getattr(yt_track, "title", None))

            if queued_count == 0:
                await interaction.followup.send(f"❌ Failed to find playable tracks for: `{sp_artist.name}`", ephemeral=True)
                return

            player.last_activity = datetime.now(UTC)
            player.text_channel_id = interaction.channel_id

            if not player.is_playing:
                asyncio.create_task(music._play_loop(player))

            embed = discord.Embed(
                title="👩‍🎤 Artist Radio Queued",
                description=f"Added **{queued_count}** top tracks by **{sp_artist.name}**\nAlso boosted your preference for this artist!",
                color=discord.Color.blue(),
            )
            embed.set_footer(text=f"Requested by {interaction.user.display_name}")

            await interaction.followup.send(embed=embed, ephemeral=True)

    @play_group.command(name="any", description="Start playing with discovery mode")
    async def play_any(self, interaction: discord.Interaction):
        """Start discovery playback without a specific song."""
        music = self.music
        if not music:
            await interaction.response.send_message("❌ Music system is not loaded.", ephemeral=True)
            return

        with log.span(
            Category.SYSTEM,
            "command_play_any",
            module=__name__,
            cog=type(self).__name__,
            command="/play any",
            guild_id=interaction.guild_id,
            channel_id=getattr(interaction.channel, "id", None),
            user_id=getattr(interaction.user, "id", None),
        ):
            try:
                await interaction.response.defer(ephemeral=True)
            except discord.InteractionResponded:
                pass
            except discord.NotFound:
                log.warning_cat(Category.SYSTEM, "Interaction expired/unknown (404) in play_any")
                return

            if not interaction.user.voice:
                await interaction.followup.send("❌ You need to be in a voice channel!", ephemeral=True)
                return

            voice_channel = interaction.user.voice.channel
            player = music.get_player(interaction.guild_id)

            if not player.voice_client or not player.voice_client.is_connected():
                try:
                    player.voice_client = await voice_channel.connect(self_deaf=True, timeout=20.0)
                except Exception as e:
                    await interaction.followup.send(f"❌ Failed to connect: {e}", ephemeral=True)
                    return

            player.autoplay = True
            player.last_activity = datetime.now(UTC)
            player.text_channel_id = interaction.channel_id

            if not player.is_playing:
                asyncio.create_task(music._play_loop(player))

            await interaction.followup.send("🎲 **Discovery mode activated!** Finding songs for you...", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(PlayCog(bot))
