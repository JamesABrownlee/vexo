"""
Playback control commands cog.

Keeps pause/resume/skip/queue/etc separate from the core music player implementation.
"""
import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from src.utils.logging import get_logger, Category

log = get_logger(__name__)


class PlayerControlsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @property
    def music(self):
        return self.bot.get_cog("MusicCog")

    @app_commands.command(name="pause", description="Pause the current song")
    async def pause(self, interaction: discord.Interaction):
        with log.span(
            Category.SYSTEM,
            "command_pause",
            module=__name__,
            cog=type(self).__name__,
            command="/pause",
            guild_id=interaction.guild_id,
            channel_id=getattr(interaction.channel, "id", None),
            user_id=getattr(interaction.user, "id", None),
        ):
            music = self.music
            if not music:
                await interaction.response.send_message("❌ Music system is not loaded.", ephemeral=True)
                return

            player = music.get_player(interaction.guild_id)
            if player.voice_client and player.voice_client.is_playing():
                player.voice_client.pause()
                await interaction.response.send_message("⏸️ Paused", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Nothing is playing", ephemeral=True)

    @app_commands.command(name="resume", description="Resume the paused song")
    async def resume(self, interaction: discord.Interaction):
        with log.span(
            Category.SYSTEM,
            "command_resume",
            module=__name__,
            cog=type(self).__name__,
            command="/resume",
            guild_id=interaction.guild_id,
            channel_id=getattr(interaction.channel, "id", None),
            user_id=getattr(interaction.user, "id", None),
        ):
            music = self.music
            if not music:
                await interaction.response.send_message("❌ Music system is not loaded.", ephemeral=True)
                return

            player = music.get_player(interaction.guild_id)
            if player.voice_client and player.voice_client.is_paused():
                player.voice_client.resume()
                await interaction.response.send_message("▶️ Resumed", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Nothing is paused", ephemeral=True)

    @app_commands.command(name="skip", description="Skip the current song")
    async def skip(self, interaction: discord.Interaction):
        with log.span(
            Category.SYSTEM,
            "command_skip",
            module=__name__,
            cog=type(self).__name__,
            command="/skip",
            guild_id=interaction.guild_id,
            channel_id=getattr(interaction.channel, "id", None),
            user_id=getattr(interaction.user, "id", None),
        ):
            music = self.music
            if not music:
                await interaction.response.send_message("❌ Music system is not loaded.", ephemeral=True)
                return

            player = music.get_player(interaction.guild_id)
            if not player.voice_client or not player.is_playing:
                await interaction.response.send_message("❌ Nothing is playing", ephemeral=True)
                return

            player.voice_client.stop()
            await interaction.response.send_message("⏭️ Skipped!", ephemeral=True)

    @app_commands.command(name="forceskip", description="Force skip (DJ only)")
    @app_commands.default_permissions(manage_channels=True)
    async def forceskip(self, interaction: discord.Interaction):
        with log.span(
            Category.SYSTEM,
            "command_forceskip",
            module=__name__,
            cog=type(self).__name__,
            command="/forceskip",
            guild_id=interaction.guild_id,
            channel_id=getattr(interaction.channel, "id", None),
            user_id=getattr(interaction.user, "id", None),
        ):
            music = self.music
            if not music:
                await interaction.response.send_message("❌ Music system is not loaded.", ephemeral=True)
                return

            player = music.get_player(interaction.guild_id)
            if player.voice_client and player.is_playing:
                player.voice_client.stop()
                await interaction.response.send_message("⏭️ Force skipped!", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Nothing is playing", ephemeral=True)

    @app_commands.command(name="queue", description="Show the current queue")
    async def queue(self, interaction: discord.Interaction):
        with log.span(
            Category.SYSTEM,
            "command_queue",
            module=__name__,
            cog=type(self).__name__,
            command="/queue",
            guild_id=interaction.guild_id,
            channel_id=getattr(interaction.channel, "id", None),
            user_id=getattr(interaction.user, "id", None),
        ):
            music = self.music
            if not music:
                await interaction.response.send_message("❌ Music system is not loaded.", ephemeral=True)
                return

            player = music.get_player(interaction.guild_id)
            embed = discord.Embed(title="🎵 Queue", color=discord.Color.blue())

            if player.current:
                embed.add_field(
                    name="Now Playing",
                    value=f"**{player.current.title}**\nby {player.current.artist}",
                    inline=False,
                )

            if player.queue.empty():
                embed.add_field(name="Up Next", value="Queue is empty", inline=False)
            else:
                items = list(player.queue._queue)[:10]
                upcoming = [f"{i}. **{item.title}** - {item.artist}" for i, item in enumerate(items, 1)]
                embed.add_field(name="Up Next", value="\n".join(upcoming), inline=False)

            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="clear", description="Clear the queue (DJ only)")
    @app_commands.default_permissions(manage_channels=True)
    async def clear(self, interaction: discord.Interaction):
        with log.span(
            Category.SYSTEM,
            "command_clear",
            module=__name__,
            cog=type(self).__name__,
            command="/clear",
            guild_id=interaction.guild_id,
            channel_id=getattr(interaction.channel, "id", None),
            user_id=getattr(interaction.user, "id", None),
        ):
            music = self.music
            if not music:
                await interaction.response.send_message("❌ Music system is not loaded.", ephemeral=True)
                return

            player = music.get_player(interaction.guild_id)
            while not player.queue.empty():
                try:
                    player.queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

            await interaction.response.send_message("🗑️ Queue cleared!", ephemeral=True)

    @app_commands.command(name="autoplay", description="Toggle autoplay/discovery mode")
    @app_commands.describe(enabled="Enable or disable autoplay")
    async def autoplay(self, interaction: discord.Interaction, enabled: bool):
        with log.span(
            Category.SYSTEM,
            "command_autoplay",
            module=__name__,
            cog=type(self).__name__,
            command="/autoplay",
            guild_id=interaction.guild_id,
            channel_id=getattr(interaction.channel, "id", None),
            user_id=getattr(interaction.user, "id", None),
            enabled=enabled,
        ):
            music = self.music
            if not music:
                await interaction.response.send_message("❌ Music system is not loaded.", ephemeral=True)
                return

            player = music.get_player(interaction.guild_id)
            player.autoplay = enabled

            status = "enabled" if enabled else "disabled"
            await interaction.response.send_message(f"🎲 Autoplay {status}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(PlayerControlsCog(bot))
