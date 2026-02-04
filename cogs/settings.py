import discord
from discord import app_commands
from discord.ext import commands
import logging
from config import Config
from utils.logger import set_logger

logger = set_logger(logging.getLogger('Vexo.Settings'))

class Settings(commands.Cog):
    """Simplified permission management for Vexo."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def has_permission(self, interaction: discord.Interaction) -> bool:
        """Check if user has Administrator permission."""
        return interaction.user.guild_permissions.administrator

    @app_commands.command(name="leave_server", description="Make the bot leave the server (Administrator only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def leave_server(self, interaction: discord.Interaction):
        """Allow server admins to remove the bot."""
        await interaction.response.send_message("👋 Goodbye! Leaving the server as requested.")
        await interaction.guild.leave()

async def setup(bot: commands.Bot):
    await bot.add_cog(Settings(bot))
