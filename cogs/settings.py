import discord
from discord import app_commands
from discord.ext import commands
import logging
import json
from database import db
from config import Config

logger = logging.getLogger('Vexo.Settings')

class Settings(commands.Cog):
    """Vexo-specific settings and permission management."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def is_bot_owner(self, interaction: discord.Interaction) -> bool:
        """Check if the user is the bot owner."""
        # Bot owners are typically defined in config or hardcoded
        # Here we also check the DB for configured owners
        if interaction.user.id == self.bot.owner_id:
            return True
        
        # Check database for this guild's owner (the person who invited the bot usually)
        guild_settings = await db.get_guild_settings(interaction.guild.id)
        return interaction.user.id == guild_settings.get("owner_id")

    async def is_admin(self, interaction: discord.Interaction) -> bool:
        """Check if user has admin role or is server admin."""
        if interaction.user.guild_permissions.administrator:
            return True
            
        guild_settings = await db.get_guild_settings(interaction.guild.id)
        admin_role_id = guild_settings.get("admin_role_id")
        
        if admin_role_id:
            role = interaction.guild.get_role(admin_role_id)
            if role in interaction.user.roles:
                return True
        return False

    @app_commands.command(name="set_admin_role", description="Set the role that can manage Vexo settings (Server Admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_admin_role(self, interaction: discord.Interaction, role: discord.Role):
        """Set the server-specific admin role for Vexo."""
        await db.set_guild_settings(interaction.guild.id, admin_role_id=role.id)
        await interaction.response.send_message(f"✅ Vexo Admin role set to: {role.mention}", ephemeral=True)

    @app_commands.command(name="vexo_settings", description="Control Vexo discovery and playback settings")
    async def vexo_settings(self, interaction: discord.Interaction):
        """Display and control settings."""
        if not await self.is_admin(interaction):
            return await interaction.response.send_message("❌ You don't have permission to manage Vexo settings.", ephemeral=True)
            
        # This could be a complex view with buttons for toggling discovery weights, etc.
        # For now, let's keep it simple.
        settings = await db.get_guild_settings(interaction.guild.id)
        
        embed = discord.Embed(
            title="🛠️ Vexo Server Settings",
            color=Config.COLOR_PRIMARY
        )
        embed.add_field(name="Admin Role", value=f"<@&{settings.get('admin_role_id')}>" if settings.get('admin_role_id') else "None set (Admins only)", inline=False)
        embed.add_field(name="Interactor Influence", value=f"{Config.DISCOVERY_INTERACTOR_INFLUENCE}x", inline=True)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="leave_server", description="Make the bot leave the server (Bot Owner only)")
    async def leave_server(self, interaction: discord.Interaction):
        """Allow bot owner to remove the bot from a server."""
        if not await self.is_bot_owner(interaction):
            return await interaction.response.send_message("❌ Only the Bot Owner can use this command.", ephemeral=True)
            
        await interaction.response.send_message("👋 Goodbye! Leaving the server as requested by the Vexo Owner.")
        await interaction.guild.leave()

async def setup(bot: commands.Bot):
    await bot.add_cog(Settings(bot))
