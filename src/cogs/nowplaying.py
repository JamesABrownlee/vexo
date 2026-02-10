"""
Now Playing cog.

Owns:
- `/nowplaying` command
- Now Playing message rendering/sending
- persistence/cleanup of the last Now Playing message per guild (across restarts)
- interactive view buttons
"""
import aiohttp
import asyncio
import io
import time
from datetime import datetime, UTC
from urllib.parse import quote_plus

import discord
from discord import app_commands
from discord.ext import commands

from src.database.crud import SongCRUD, ReactionCRUD, LibraryCRUD, NowPlayingMessageCRUD
from src.utils.logging import get_logger, Category

log = get_logger(__name__)


class NowPlayingView(discord.ui.View):
    """Interactive Now Playing controls with dynamic queue select."""

    # Persistent view: timeout must be None and every component needs a custom_id.
    def __init__(self, bot: commands.Bot, queue_items: list = None):
        super().__init__(timeout=None)
        self.bot = bot
        self._busy_lock = asyncio.Lock()

        # Add select menu with queue items if provided.
        # Note: discord.py adds decorator-defined children during View.__init__.
        # If we add the select after that, it appears at the end; we want it first.
        if queue_items:
            existing_items = list(self.children)

            select_options = [
                discord.SelectOption(
                    label=f"{i+1}. {qi.title[:50]}",
                    description=qi.artist[:100],
                    value=str(i),
                )
                for i, qi in enumerate(queue_items[:10])  # Limit to 10 items
            ]

            if select_options:
                select = discord.ui.Select(
                    placeholder="⏭️ Choose next song...",
                    custom_id="np:skip_to",
                    options=select_options,
                    min_values=1,
                    max_values=1,
                    row=0,
                )
                select.callback = self.skip_to_callback

                self.clear_items()
                self.add_item(select)
                for item in existing_items:
                    self.add_item(item)

    @property
    def music(self):
        return self.bot.get_cog("MusicCog")

    async def _set_all_disabled(self, disabled: bool, interaction: discord.Interaction | None = None) -> None:
        for item in self.children:
            try:
                item.disabled = disabled
            except Exception:
                pass

        if interaction is not None:
            try:
                msg = getattr(interaction, "message", None)
                if msg is not None:
                    await msg.edit(view=self)
            except Exception:
                pass

    def _guild_id_from_interaction(self, interaction: discord.Interaction) -> int | None:
        try:
            return int(interaction.guild_id) if interaction.guild_id else None
        except Exception:
            return None

    async def _safe_defer(self, interaction: discord.Interaction, *, ephemeral: bool = True) -> bool:
        try:
            t0 = time.perf_counter()
            try:
                await interaction.response.defer(ephemeral=ephemeral)
            except discord.InteractionResponded:
                log.debug_cat(
                    Category.USER,
                    "interaction_defer_already_responded",
                    module=__name__,
                    interaction_id=getattr(interaction, "id", None),
                    custom_id=(interaction.data or {}).get("custom_id") if isinstance(interaction.data, dict) else None,
                    guild_id=getattr(interaction, "guild_id", None),
                    channel_id=getattr(getattr(interaction, "channel", None), "id", None),
                    message_id=getattr(getattr(interaction, "message", None), "id", None),
                    user_id=getattr(getattr(interaction, "user", None), "id", None),
                )
                return True
            ms = int((time.perf_counter() - t0) * 1000)
            # If defer itself took a long time, it's a strong signal of event-loop lag / network delay.
            log_fn = log.warning_cat if ms >= 2500 else log.info_cat
            log_fn(
                Category.USER,
                "interaction_defer_ok",
                module=__name__,
                interaction_id=getattr(interaction, "id", None),
                interaction_type=str(getattr(getattr(interaction, "type", None), "name", getattr(interaction, "type", None))),
                custom_id=(interaction.data or {}).get("custom_id") if isinstance(interaction.data, dict) else None,
                guild_id=getattr(interaction, "guild_id", None),
                channel_id=getattr(getattr(interaction, "channel", None), "id", None),
                message_id=getattr(getattr(interaction, "message", None), "id", None),
                user_id=getattr(getattr(interaction, "user", None), "id", None),
                ms=ms,
            )
            return True
        except discord.NotFound:
            log.warning_cat(
                Category.USER,
                "interaction_defer_not_found",
                module=__name__,
                interaction_id=getattr(interaction, "id", None),
                custom_id=(interaction.data or {}).get("custom_id") if isinstance(interaction.data, dict) else None,
                guild_id=getattr(interaction, "guild_id", None),
                channel_id=getattr(getattr(interaction, "channel", None), "id", None),
                message_id=getattr(getattr(interaction, "message", None), "id", None),
                user_id=getattr(getattr(interaction, "user", None), "id", None),
            )
            return False
        except Exception as e:
            log.exception_cat(Category.SYSTEM, "Failed to defer NowPlayingView interaction", error=str(e))
            return False

    async def _safe_send(self, interaction: discord.Interaction, content: str, *, ephemeral: bool = True) -> None:
        try:
            if interaction.response.is_done():
                await interaction.followup.send(content, ephemeral=ephemeral)
            else:
                await interaction.response.send_message(content, ephemeral=ephemeral)
        except discord.NotFound:
            return
        except Exception as e:
            log.exception_cat(Category.SYSTEM, "Failed to send NowPlayingView response", error=str(e))
            return

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item):
        try:
            log.exception_cat(
                Category.SYSTEM,
                "NowPlayingView item callback error",
                error=str(error),
                item_type=type(item).__name__,
                guild_id=self._guild_id_from_interaction(interaction),
            )
        except Exception:
            pass

        try:
            await self._safe_send(interaction, "❌ That button failed. Try again.", ephemeral=True)
        except Exception:
            return

    @discord.ui.button(emoji="<:pause1:1470585635386556527>", style=discord.ButtonStyle.secondary, custom_id="np:pause_resume")
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        with log.span(
            Category.USER,
            "np_button_pause_resume",
            module=__name__,
            view="NowPlayingView",
            custom_id=getattr(button, "custom_id", None),
            interaction_id=getattr(interaction, "id", None),
            guild_id=interaction.guild_id,
            channel_id=getattr(interaction.channel, "id", None),
            message_id=getattr(getattr(interaction, "message", None), "id", None),
            user_id=getattr(interaction.user, "id", None),
        ):
            if not await self._safe_defer(interaction, ephemeral=True):
                return

        music = self.music
        if not music:
            return

        guild_id = self._guild_id_from_interaction(interaction)
        if not guild_id:
            await self._safe_send(interaction, "❌ This button can only be used in a server.", ephemeral=True)
            return

        if self._busy_lock.locked():
            await self._safe_send(interaction, "⏳ Bot is busy — try again in a moment.", ephemeral=True)
            return

        async with self._busy_lock:
            await self._set_all_disabled(True, interaction)
            try:
                player = music.get_player(guild_id)
                if player.voice_client:
                    if player.voice_client.is_playing():
                        player.voice_client.pause()
                        await self._safe_send(interaction, "⏸ Paused", ephemeral=True)
                    elif player.voice_client.is_paused():
                        player.voice_client.resume()
                        await self._safe_send(interaction, "▶ Resumed", ephemeral=True)
            except Exception as e:
                log.exception_cat(Category.SYSTEM, "NowPlayingView pause/resume failed", error=str(e))
                return
            finally:
                await self._set_all_disabled(False, interaction)

    @discord.ui.button(emoji="⏹", style=discord.ButtonStyle.danger, custom_id="np:stop")
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        with log.span(
            Category.USER,
            "np_button_stop",
            module=__name__,
            view="NowPlayingView",
            custom_id=getattr(button, "custom_id", None),
            interaction_id=getattr(interaction, "id", None),
            guild_id=interaction.guild_id,
            channel_id=getattr(interaction.channel, "id", None),
            message_id=getattr(getattr(interaction, "message", None), "id", None),
            user_id=getattr(interaction.user, "id", None),
        ):
            if not await self._safe_defer(interaction, ephemeral=True):
                return

        music = self.music
        if not music:
            return

        guild_id = self._guild_id_from_interaction(interaction)
        if not guild_id:
            await self._safe_send(interaction, "❌ This button can only be used in a server.", ephemeral=True)
            return

        if self._busy_lock.locked():
            await self._safe_send(interaction, "⏳ Bot is busy — try again in a moment.", ephemeral=True)
            return

        async with self._busy_lock:
            await self._set_all_disabled(True, interaction)
            try:
                player = music.get_player(guild_id)
                if not player.voice_client:
                    return

                while not player.queue.empty():
                    try:
                        player.queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break

                if player.is_playing or player.voice_client.is_playing():
                    player.voice_client.stop()

                await player.voice_client.disconnect()
                player.voice_client = None

                await self._safe_send(interaction, "⏹ Stopped and cleared queue!", ephemeral=True)
                # Keep disabled until a new Now Playing message is posted.
                await self._set_all_disabled(True, interaction)
            except Exception as e:
                log.exception_cat(Category.SYSTEM, "NowPlayingView stop failed", error=str(e))
                return

    @discord.ui.button(emoji="⏭", style=discord.ButtonStyle.secondary, custom_id="np:skip")
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        with log.span(
            Category.USER,
            "np_button_skip",
            module=__name__,
            view="NowPlayingView",
            custom_id=getattr(button, "custom_id", None),
            interaction_id=getattr(interaction, "id", None),
            guild_id=interaction.guild_id,
            channel_id=getattr(interaction.channel, "id", None),
            message_id=getattr(getattr(interaction, "message", None), "id", None),
            user_id=getattr(interaction.user, "id", None),
        ):
            if not await self._safe_defer(interaction, ephemeral=True):
                return

        music = self.music
        if not music:
            return

        guild_id = self._guild_id_from_interaction(interaction)
        if not guild_id:
            await self._safe_send(interaction, "❌ This button can only be used in a server.", ephemeral=True)
            return

        if self._busy_lock.locked():
            await self._safe_send(interaction, "⏳ Bot is busy — try again in a moment.", ephemeral=True)
            return

        async with self._busy_lock:
            await self._set_all_disabled(True, interaction)
            try:
                player = music.get_player(guild_id)
                if player.voice_client and player.is_playing:
                    player.voice_client.stop()
                    await self._safe_send(interaction, "⏭️ Skipped!", ephemeral=True)
            except Exception as e:
                log.exception_cat(Category.SYSTEM, "NowPlayingView skip failed", error=str(e))
                return
            finally:
                await self._set_all_disabled(False, interaction)

    @discord.ui.button(emoji="❤️", style=discord.ButtonStyle.secondary, custom_id="np:like")
    async def like(self, interaction: discord.Interaction, button: discord.ui.Button):
        with log.span(
            Category.USER,
            "np_button_like",
            module=__name__,
            view="NowPlayingView",
            custom_id=getattr(button, "custom_id", None),
            interaction_id=getattr(interaction, "id", None),
            guild_id=interaction.guild_id,
            channel_id=getattr(interaction.channel, "id", None),
            message_id=getattr(getattr(interaction, "message", None), "id", None),
            user_id=getattr(interaction.user, "id", None),
        ):
            if not await self._safe_defer(interaction, ephemeral=True):
                return

        music = self.music
        if not music:
            return

        guild_id = self._guild_id_from_interaction(interaction)
        if not guild_id:
            await self._safe_send(interaction, "❌ This button can only be used in a server.", ephemeral=True)
            return

        if self._busy_lock.locked():
            await self._safe_send(interaction, "⏳ Bot is busy — try again in a moment.", ephemeral=True)
            return

        async with self._busy_lock:
            await self._set_all_disabled(True, interaction)

        try:
            player = music.get_player(guild_id)
            current = player.current
            if not current:
                return

            title = current.title
            song_db_id = current.song_db_id

            if hasattr(music.bot, "db") and music.bot.db and song_db_id:
                try:
                    song_crud = SongCRUD(music.bot.db)
                    reaction_crud = ReactionCRUD(music.bot.db)

                    await song_crud.make_permanent(song_db_id)
                    await reaction_crud.add_reaction(interaction.user.id, song_db_id, "like")

                    lib_crud = LibraryCRUD(music.bot.db)
                    await lib_crud.add_to_library(interaction.user.id, song_db_id, "like")
                except Exception as e:
                    log.error_cat(Category.USER, "Failed to log like", error=str(e))

            await self._safe_send(interaction, f"❤️ Liked **{title}**!", ephemeral=True)
        except Exception as e:
            log.exception_cat(Category.SYSTEM, "NowPlayingView like failed", error=str(e))
            return
        finally:
            await self._set_all_disabled(False, interaction)

    @discord.ui.button(emoji="👎", style=discord.ButtonStyle.secondary, custom_id="np:dislike")
    async def dislike(self, interaction: discord.Interaction, button: discord.ui.Button):
        with log.span(
            Category.USER,
            "np_button_dislike",
            module=__name__,
            view="NowPlayingView",
            custom_id=getattr(button, "custom_id", None),
            interaction_id=getattr(interaction, "id", None),
            guild_id=interaction.guild_id,
            channel_id=getattr(interaction.channel, "id", None),
            message_id=getattr(getattr(interaction, "message", None), "id", None),
            user_id=getattr(interaction.user, "id", None),
        ):
            if not await self._safe_defer(interaction, ephemeral=True):
                return

        music = self.music
        if not music:
            return

        guild_id = self._guild_id_from_interaction(interaction)
        if not guild_id:
            await self._safe_send(interaction, "❌ This button can only be used in a server.", ephemeral=True)
            return

        if self._busy_lock.locked():
            await self._safe_send(interaction, "⏳ Bot is busy — try again in a moment.", ephemeral=True)
            return

        async with self._busy_lock:
            await self._set_all_disabled(True, interaction)

        try:
            player = music.get_player(guild_id)
            current = player.current
            if not current:
                return

            title = current.title
            song_db_id = current.song_db_id

            if hasattr(music.bot, "db") and music.bot.db and song_db_id:
                try:
                    song_crud = SongCRUD(music.bot.db)
                    reaction_crud = ReactionCRUD(music.bot.db)

                    await song_crud.make_permanent(song_db_id)
                    await reaction_crud.add_reaction(interaction.user.id, song_db_id, "dislike")
                except Exception as e:
                    log.error_cat(Category.USER, "Failed to log dislike", error=str(e))

            await self._safe_send(interaction, f"👎 Disliked **{title}**", ephemeral=True)
        except Exception as e:
            log.exception_cat(Category.SYSTEM, "NowPlayingView dislike failed", error=str(e))
            return
        finally:
            await self._set_all_disabled(False, interaction)

    async def skip_to_callback(self, interaction: discord.Interaction):
        """Skip to a specific song in the queue (callback for dynamic select)."""
        with log.span(
            Category.USER,
            "np_select_skip_to",
            module=__name__,
            view="NowPlayingView",
            custom_id="np:skip_to",
            interaction_id=getattr(interaction, "id", None),
            guild_id=interaction.guild_id,
            user_id=getattr(interaction.user, "id", None),
        ):
            if not await self._safe_defer(interaction, ephemeral=True):
                return

        music = self.music
        if not music:
            return

        guild_id = self._guild_id_from_interaction(interaction)
        if not guild_id:
            await self._safe_send(interaction, "❌ This can only be used in a server.", ephemeral=True)
            return

        if self._busy_lock.locked():
            await self._safe_send(interaction, "⏳ Bot is busy — try again in a moment.", ephemeral=True)
            return

        async with self._busy_lock:
            await self._set_all_disabled(True, interaction)

        try:
            player = music.get_player(guild_id)
            # Selected value is present in interaction.data['values']
            values = interaction.data.get("values") if isinstance(interaction.data, dict) else None
            selected_index = int(values[0]) if values and len(values) > 0 else 0
            queue_items = list(player.queue._queue)

            if selected_index < 0 or selected_index >= len(queue_items):
                await self._safe_send(interaction, "❌ Invalid queue position.", ephemeral=True)
                return

            # Remove all items before the selected index
            for _ in range(selected_index):
                try:
                    player.queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

            selected_song = queue_items[selected_index]
            await self._safe_send(interaction, f"⏭️ Skipped to **{selected_song.title}**", ephemeral=True)
        except Exception as e:
            log.exception_cat(Category.SYSTEM, "NowPlayingView skip_to failed", error=str(e))
            await self._safe_send(interaction, "❌ Error skipping to song.", ephemeral=True)
        finally:
            await self._set_all_disabled(False, interaction)


class NowPlayingCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._persistent_view: NowPlayingView | None = None
        self._sticky_bump_cooldown_s: int = 20
        self._last_sticky_bump_at: dict[int, datetime] = {}

    @property
    def music(self):
        return self.bot.get_cog("MusicCog")

    async def cog_load(self):
        # Register a persistent view so buttons keep working after restarts.
        # (We still send a fresh view per message; this is just for dispatching interactions.)
        if not self._persistent_view:
            self._persistent_view = NowPlayingView(self.bot)
            self.bot.add_view(self._persistent_view)

        if hasattr(self.bot, "db") and self.bot.db:
            await self._cleanup_persisted_now_playing_messages()

    async def cog_unload(self):
        if self._persistent_view:
            try:
                self.bot.remove_view(self._persistent_view)
            except Exception:
                pass
            self._persistent_view = None

    async def _cleanup_persisted_now_playing_messages(self) -> None:
        """Delete any persisted Now Playing message(s) so we don't spam channels after restarts."""
        crud = NowPlayingMessageCRUD(self.bot.db)
        rows = await crud.list_all()
        for row in rows:
            guild_id = row.get("guild_id")
            channel_id = row.get("channel_id")
            message_id = row.get("message_id")

            try:
                channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
                if hasattr(channel, "fetch_message"):
                    try:
                        msg = await channel.fetch_message(message_id)
                        await msg.delete()
                        log.info_cat(
                            Category.SYSTEM,
                            "startup_deleted_now_playing_message",
                            guild_id=guild_id,
                            channel_id=channel_id,
                            message_id=message_id,
                        )
                    except discord.NotFound:
                        pass
                    except discord.Forbidden:
                        log.warning_cat(
                            Category.SYSTEM,
                            "Missing permissions to delete startup Now Playing message",
                            guild_id=guild_id,
                            channel_id=channel_id,
                            message_id=message_id,
                        )
            except Exception as e:
                log.debug_cat(Category.SYSTEM, "Startup Now Playing cleanup failed", error=str(e))
            finally:
                try:
                    if guild_id is not None:
                        await crud.delete(int(guild_id))
                except Exception:
                    pass

    async def send_now_playing_for_player(self, player, *, repost: bool = False) -> None:
        """Post a Now Playing view immediately with a loading embed, then swap to the image when ready.

        If `repost=True`, tries to delete the existing Now Playing message and send a new one (to "bump" it).
        """
        if not player.current or not player.text_channel_id:
            return

        channel = self.bot.get_channel(player.text_channel_id)
        if not channel:
            return

        item = player.current
        video_id = item.video_id

        # Create view with dynamic queue select options (top 10)
        queue_items = list(player.queue._queue)[:10]
        view = NowPlayingView(self.bot, queue_items=queue_items)

        loading_embed = discord.Embed(
            title="🎵 Now Playing",
            description=f"<a:discordloading:1470579528760950977>\nTrack: **{item.title}**\nArtist: {item.artist}",
            color=0x7c3aed,
        )
        if item.discovery_reason:
            loading_embed.add_field(name="Discovery", value=item.discovery_reason, inline=False)

        msg = None
        existing_art: tuple[bytes, str] | None = None

        try:
            async with player._np_lock:
                # Try reuse the persisted message (edit instead of delete/send).
                if hasattr(self.bot, "db") and self.bot.db:
                    try:
                        np_crud = NowPlayingMessageCRUD(self.bot.db)
                        old = await np_crud.get(player.guild_id)
                        if old:
                            old_channel_id = old.get("channel_id")
                            old_message_id = old.get("message_id")

                            # If channel changed, do not reuse old message.
                            if int(old_channel_id) == int(player.text_channel_id):
                                try:
                                    old_channel = self.bot.get_channel(old_channel_id) or await self.bot.fetch_channel(old_channel_id)
                                    if hasattr(old_channel, "fetch_message"):
                                        msg = await old_channel.fetch_message(old_message_id)
                                except Exception:
                                    msg = None
                            else:
                                try:
                                    await np_crud.delete(player.guild_id)
                                except Exception:
                                    pass
                    except Exception as e:
                        log.debug_cat(Category.SYSTEM, "Now Playing persistence lookup failed", error=str(e), guild_id=player.guild_id)

                # Fallback: if we still have the in-memory reference, try reuse it.
                if msg is None and player.last_np_msg is not None:
                    msg = player.last_np_msg

                # If we want to bump the message to the bottom, delete it and re-send.
                if repost and msg is not None:
                    # Best effort: reuse existing artwork from the current message so we don't show "loading" again.
                    try:
                        if getattr(msg, "attachments", None):
                            att = msg.attachments[0]
                            filename = getattr(att, "filename", None) or "nowplaying.png"
                            data = await att.read()
                            if data:
                                existing_art = (data, filename)
                    except Exception:
                        existing_art = None

                    try:
                        await msg.delete()
                    except discord.Forbidden:
                        # Can't delete; fall back to edit-in-place.
                        repost = False
                    except Exception:
                        repost = False
                    else:
                        msg = None

                # Show loading state immediately.
                if msg is not None:
                    try:
                        try:
                            await msg.edit(embed=loading_embed, view=view, attachments=[])
                        except TypeError:
                            # Older libs may not support attachments= in edit.
                            await msg.edit(embed=loading_embed, view=view)
                    except Exception:
                        msg = None

                if msg is None:
                    if existing_art is not None:
                        data, filename = existing_art
                        file = discord.File(io.BytesIO(data), filename=filename)
                        msg = await channel.send(file=file, view=view)
                    else:
                        msg = await channel.send(embed=loading_embed, view=view)

                player.last_np_msg = msg

                if hasattr(self.bot, "db") and self.bot.db:
                    try:
                        np_crud = NowPlayingMessageCRUD(self.bot.db)
                        await np_crud.upsert(player.guild_id, player.text_channel_id, msg.id)
                    except Exception as e:
                        log.debug_cat(Category.SYSTEM, "Failed to persist Now Playing message", error=str(e), guild_id=player.guild_id)

            # Fetch image and swap the message after releasing the lock.
            if existing_art is None:
                asyncio.create_task(
                    self._swap_loading_to_image(
                        guild_id=player.guild_id,
                        channel_id=player.text_channel_id,
                        message_id=msg.id,
                        video_id=video_id,
                    )
                )
        except Exception as e:
            log.exception_cat(
                Category.SYSTEM,
                "send_now_playing_for_player failed",
                error=str(e),
                guild_id=player.guild_id,
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Optional 'sticky' behaviour: bump Now Playing to the bottom by re-posting after users chat."""
        try:
            if not message.guild or message.author.bot:
                return

            music = self.music
            if not music:
                return

            player = music.get_player(message.guild.id)
            if not player.current or not player.text_channel_id or not player.is_playing:
                return

            if message.channel.id != player.text_channel_id:
                return

            if not player.last_np_msg:
                return

            # If Now Playing is already the last message, nothing to do.
            last_message_id = getattr(message.channel, "last_message_id", None)
            if last_message_id and int(last_message_id) == int(player.last_np_msg.id):
                return

            now = datetime.now(UTC)
            last = self._last_sticky_bump_at.get(message.guild.id)
            if last and (now - last).total_seconds() < self._sticky_bump_cooldown_s:
                return

            self._last_sticky_bump_at[message.guild.id] = now
            await self.send_now_playing_for_player(player, repost=True)
        except Exception as e:
            log.debug_cat(Category.SYSTEM, "sticky_now_playing_bump_failed", error=str(e))

    async def _swap_loading_to_image(self, *, guild_id: int, channel_id: int, message_id: int, video_id: str) -> None:
        """Fetch the dashboard-rendered image and edit the message to show it."""
        music = self.music
        if not music:
            return

        player = music.get_player(guild_id)
        if not player.current or player.current.video_id != video_id:
            return

        channel = self.bot.get_channel(channel_id)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception:
                return

        try:
            msg = await channel.fetch_message(message_id)
        except Exception:
            return

        item = player.current

        # Fetch additional stats for rendering, if available.
        requested_by_str = ""
        liked_by_str = ""
        disliked_by_str = ""

        if hasattr(self.bot, "db") and self.bot.db and item.song_db_id:
            try:
                stats = await self.bot.db.fetch_one(
                    """
                    SELECT 
                        (SELECT GROUP_CONCAT(DISTINCT u.username) FROM playback_history ph JOIN users u ON ph.for_user_id = u.id WHERE ph.song_id = ? AND ph.discovery_source = "user_request") as requested_by,
                        (SELECT GROUP_CONCAT(DISTINCT u.username) FROM song_reactions sr JOIN users u ON sr.song_id = ? AND sr.user_id = u.id AND sr.reaction = 'like') as liked_by,
                        (SELECT GROUP_CONCAT(DISTINCT u.username) FROM song_reactions sr JOIN users u ON sr.song_id = ? AND sr.user_id = u.id AND sr.reaction = 'dislike') as disliked_by
                    """,
                    (item.song_db_id, item.song_db_id, item.song_db_id),
                )
                if stats:
                    requested_by_str = stats.get("requested_by") or ""
                    liked_by_str = stats.get("liked_by") or ""
                    disliked_by_str = stats.get("disliked_by") or ""
            except Exception:
                pass

        current_time_str = "0:00"
        progress_percent = 0
        if player.start_time:
            elapsed = (datetime.now(UTC) - player.start_time).total_seconds()
            minutes, seconds = divmod(int(elapsed), 60)
            current_time_str = f"{minutes}:{seconds:02d}"
            if item.duration_seconds:
                progress_percent = min(100, int((elapsed / item.duration_seconds) * 100))

        total_time_str = "0:00"
        if item.duration_seconds:
            minutes, seconds = divmod(item.duration_seconds, 60)
            total_time_str = f"{minutes}:{seconds:02d}"

        for_user_str = ""
        target_user_id = item.for_user_id or item.requester_id
        if target_user_id and player.voice_client and player.voice_client.guild:
            member = player.voice_client.guild.get_member(target_user_id)
            if member:
                for_user_str = member.display_name
            else:
                user = self.bot.get_user(target_user_id)
                if user:
                    for_user_str = user.display_name

        params = {
            "title": item.title,
            "artist": item.artist,
            "thumbnail": f"https://img.youtube.com/vi/{item.video_id}/hqdefault.jpg",
            "genre": item.genre or "",
            "year": str(item.year) if item.year else "",
            "progress": str(progress_percent),
            "duration": total_time_str,
            "current": current_time_str,
            "requestedBy": requested_by_str,
            "likedBy": liked_by_str,
            "dislikedBy": disliked_by_str,
            "queueSize": str(player.queue.qsize()),
            "discoveryReason": item.discovery_reason or "",
            "forUser": for_user_str,
            "videoUrl": f"https://youtube.com/watch?v={item.video_id}",
        }

        query_str = "&".join([f"{k}={quote_plus(str(v))}" for k, v in params.items()])
        image_url = f"http://dashboard:3000/api/now-playing/image?{query_str}"

        # Updated view (queue may have changed)
        queue_items = list(player.queue._queue)[:10]
        view = NowPlayingView(self.bot, queue_items=queue_items)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(image_url, timeout=5) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"dashboard image http {resp.status}")
                    image_data = await resp.read()

            file = discord.File(io.BytesIO(image_data), filename="nowplaying.png")

            # Replace the loading embed with the image.
            try:
                await msg.edit(embed=None, view=view, attachments=[file])
            except TypeError:
                # Some libs require deleting and re-sending to attach a file.
                try:
                    await msg.delete()
                except Exception:
                    return
                new_msg = await channel.send(file=file, view=view)
                player.last_np_msg = new_msg
                if hasattr(self.bot, "db") and self.bot.db:
                    try:
                        np_crud = NowPlayingMessageCRUD(self.bot.db)
                        await np_crud.upsert(guild_id, channel_id, new_msg.id)
                    except Exception:
                        pass
        except Exception as e:
            # Keep the loading embed; optionally update it to show the failure.
            try:
                err_embed = discord.Embed(
                    title="🎵 Now Playing",
                    description=f"**{item.title}**\n{item.artist}\n\n⚠️ Artwork unavailable.",
                    color=0x7c3aed,
                )
                await msg.edit(embed=err_embed, view=view)
            except Exception:
                pass

    @app_commands.command(name="nowplaying", description="Show the current song")
    async def nowplaying(self, interaction: discord.Interaction):
        with log.span(
            Category.SYSTEM,
            "command_nowplaying",
            module=__name__,
            cog=type(self).__name__,
            command="/nowplaying",
            guild_id=interaction.guild_id,
            channel_id=getattr(interaction.channel, "id", None),
            user_id=getattr(interaction.user, "id", None),
        ):
            music = self.music
            if not music:
                await interaction.response.send_message("❌ Music system is not loaded.", ephemeral=True)
                return

            player = music.get_player(interaction.guild_id)
            if not player.current:
                await interaction.response.send_message("❌ Nothing is playing", ephemeral=True)
                return

            embed = discord.Embed(
                title="🎵 Now Playing",
                description=f"**{player.current.title}**\nby {player.current.artist}",
                color=discord.Color.green(),
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


async def setup(bot: commands.Bot):
    await bot.add_cog(NowPlayingCog(bot))
