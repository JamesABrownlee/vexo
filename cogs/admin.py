import sys
import io
import discord
from discord.ext import commands
from discord import app_commands
import logging

from utils.logger import set_logger, get_last_log_lines

logger = set_logger(logging.getLogger('MusicBot.Admin'))

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

    @app_commands.command(name="logs", description="Get the last 500 log lines (Admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def logs(self, interaction: discord.Interaction):
        """Send the last 500 log lines as a DM attachment."""
        await interaction.response.defer(ephemeral=True)
        
        logger.info(f"Logs requested by {interaction.user} (ID: {interaction.user.id})")
        
        # Get log content
        log_content = get_last_log_lines(500)
        
        # Create file-like object
        file = discord.File(
            io.BytesIO(log_content.encode('utf-8')),
            filename="vexo_logs.txt"
        )
        
        try:
            # Send DM
            await interaction.user.send(
                content="📋 **Vexo Logs** (Last 500 lines)",
                file=file
            )
            await interaction.followup.send("✅ Logs sent to your DMs!", ephemeral=True)
        except discord.Forbidden:
            # DMs disabled, send in channel as ephemeral
            file = discord.File(
                io.BytesIO(log_content.encode('utf-8')),
                filename="vexo_logs.txt"
            )
            await interaction.followup.send(
                content="📋 **Vexo Logs** (Last 500 lines)\n*Couldn't DM you, here's the file:*",
                file=file,
                ephemeral=True
            )

    @logs.error
    async def logs_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        else:
            logger.error(f"Error in /logs: {error}")
            await interaction.response.send_message(f"❌ An error occurred: {error}", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot))
