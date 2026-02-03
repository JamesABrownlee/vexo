import sys
import discord
from discord.ext import commands
from discord import app_commands
import logging

logger = logging.getLogger('MusicBot')

class Admin(commands.Cog):
    """Administrative commands for the bot."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="restart_vexo", description="Restarts the Vexo bot (container).")
    @app_commands.checks.has_permissions(administrator=True)
    async def restart_vexo(self, interaction: discord.Interaction):
        """Restarts the bot by exiting the process."""
        await interaction.response.send_message("🔄 Restarting Vexo... Please wait.", ephemeral=True)
        logger.info(f"Restart initiated by {interaction.user} (ID: {interaction.user.id})")
        
        # Give some time for the message to be sent
        await self.bot.close()
        sys.exit(0)

    @restart_vexo.error
    async def restart_vexo_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        else:
            logger.error(f"Error in /restart_vexo: {error}")
            await interaction.response.send_message(f"❌ An error occurred: {error}", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot))
