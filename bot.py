"""
Discord Music Bot - Main Entry Point
A feature-rich music bot with YouTube playback using yt-dlp.
"""
import asyncio
import logging
import discord
from discord.ext import commands

from config import Config

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('MusicBot')


class MusicBot(commands.Bot):
    """Custom bot class."""
    
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        
        super().__init__(
            command_prefix=Config.BOT_PREFIX,
            intents=intents,
            activity=discord.Activity(
                type=discord.ActivityType.listening,
                name="music 🎵"
            )
        )
    
    async def setup_hook(self):
        """Called when the bot starts, before login."""
        # Load cogs
        await self.load_extension("cogs.music")
        logger.info("Loaded music cog")
        
        await self.load_extension("cogs.admin")
        logger.info("Loaded admin cog")

        await self.load_extension("cogs.settings")
        logger.info("Loaded settings cog")

        
        # Sync slash commands
        await self.tree.sync()
        logger.info("Synced slash commands")
    
    async def on_ready(self):
        """Called when the bot is fully ready."""
        logger.info(f"Logged in as {self.user} (ID: {self.user.id})")
        logger.info(f"Connected to {len(self.guilds)} guild(s)")
        logger.info("━" * 50)
        logger.info("🎵 Vexo 2.04u is ready!")
        logger.info("━" * 50)


async def main():
    """Main entry point."""
    Config.validate()
    
    bot = MusicBot()
    
    async with bot:
        await bot.start(Config.DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
