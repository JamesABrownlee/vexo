"""
Beautiful Discord embeds for the music bot.
Uses a black and neon blue color scheme.
"""
import discord
from typing import Optional, List, TYPE_CHECKING
from config import Config
from utils.settings import VERSION_NUMBER, BUILD_NUMBER, VERSION_TYPE

def _set_footer(embed: discord.Embed, state: "GuildMusicState"):
    """Helper to set footer with volume, 24/7, and autoplay status."""
    footer_parts = [f"🔊 {int(state.volume * 100)}%"]
    if state.is_24_7:
        footer_parts.append("📻 24/7 \n")
    if state.is_autoplay:
        footer_parts.append("💡 Autoplay \n")
    if VERSION_TYPE == "DEVELOPMENT":
        footer_parts.append(f"Vexo {VERSION_NUMBER} [{VERSION_TYPE} Build {BUILD_NUMBER}]")  
    embed.set_footer(text=" • ".join(footer_parts))

if TYPE_CHECKING:
    from cogs.music import Song, GuildMusicState


def format_duration(seconds: int) -> str:
    """Format seconds to MM:SS or HH:MM:SS."""
    if seconds is None or seconds == 0:
        return "00:00"
    
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def create_progress_bar(current: int, total: int, length: int = 15) -> str:
    """Create a visual progress bar."""
    if total == 0:
        return "▱" * length
    
    filled = int((current / total) * length)
    empty = length - filled
    
    bar = "▰" * filled + "▱" * empty
    return f"`{bar}`"


def create_now_playing_embed(song: "Song", state: "GuildMusicState") -> discord.Embed:
    """Create a beautiful 'Now Playing' embed."""
    embed = discord.Embed(
        title="🎵 Now Playing",
        color=Config.COLOR_PRIMARY
    )
    
    # Track info
    embed.add_field(
        name="Track",
        value=f"**[{song.title}]({song.webpage_url})**",
        inline=False
    )
    
    embed.add_field(
        name="Artist",
        value=song.author or "Unknown",
        inline=True
    )
    
    embed.add_field(
        name="Duration",
        value=format_duration(song.duration),
        inline=True
    )
    
    # Loop status
    loop_status = "Off"
    if state.loop_mode == "song":
        loop_status = "🔂 Song"
    elif state.loop_mode == "queue":
        loop_status = "🔁 Queue"
    
    embed.add_field(
        name="Loop",
        value=loop_status,
        inline=True
    )

    embed.add_field(
        name="Genre",
        value=getattr(song, "genre", None) or "Unknown",
        inline=True
    )
    
    # Thumbnail
    if song.thumbnail:
        embed.set_thumbnail(url=song.thumbnail)
    
    # Footer with volume, 24/7, and autoplay status
    footer_parts = [f"🔊 {int(state.volume * 100)}%"]
    if state.is_24_7:
        footer_parts.append("📻 24/7 \n")
    if state.is_autoplay:
        footer_parts.append("💡 Autoplay \n")
    if VERSION_TYPE == "DEVELOPMENT":
        footer_parts.append(f"Vexo {VERSION_NUMBER} [{VERSION_TYPE} Build {BUILD_NUMBER}]")  
    
    embed.set_footer(text=" • ".join(footer_parts))
    
    return embed


def create_queue_embed(
    queue: List["Song"],
    current_song: Optional["Song"],
    page: int = 1,
    per_page: int = 10
) -> discord.Embed:
    """Create a queue display embed with pagination."""
    embed = discord.Embed(
        title="<:shc:1468761592329142374> Music Queue",
        color=Config.COLOR_PRIMARY
    )
    
    # Current track
    if current_song:
        embed.add_field(
            name="<:pl:1468753784523067505> Now Playing",
            value=f"**{current_song.title}** - {format_duration(current_song.duration)}",
            inline=False
        )
    
    # Queue items
    if len(queue) == 0:
        embed.add_field(
            name="Up Next",
            value="*Queue is empty. Use `/play` to add songs!*",
            inline=False
        )
    else:
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        page_items = queue[start_idx:end_idx]
        
        queue_text = ""
        for i, song in enumerate(page_items, start=start_idx + 1):
            queue_text += f"`{i}.` **{song.title}** - {format_duration(song.duration)}\n"
        
        embed.add_field(
            name=f"Up Next ({len(queue)} songs)",
            value=queue_text or "*No songs*",
            inline=False
        )
        
        # Pagination info
        total_pages = (len(queue) + per_page - 1) // per_page
        if total_pages > 1:
            embed.set_footer(text=f"Page {page}/{total_pages}")
    
    # Total duration
    total_sec = sum(song.duration for song in queue)
    if current_song:
        total_sec += current_song.duration
    embed.add_field(
        name="Total Duration",
        value=format_duration(total_sec),
        inline=True
    )
    
    return embed


def create_added_to_queue_embed(song: "Song", position: int) -> discord.Embed:
    """Create an embed for when a track is added to queue."""
    embed = discord.Embed(
        title="✅ Added to Queue",
        description=f"**[{song.title}]({song.webpage_url})**",
        color=Config.COLOR_SUCCESS
    )
    
    embed.add_field(name="Artist", value=song.author or "Unknown", inline=True)
    embed.add_field(name="Duration", value=format_duration(song.duration), inline=True)
    embed.add_field(name="Position", value=f"#{position}", inline=True)
    
    if song.thumbnail:
        embed.set_thumbnail(url=song.thumbnail)
    
    return embed


def create_error_embed(message: str) -> discord.Embed:
    """Create an error embed."""
    return discord.Embed(
        title="<:cr:1468763462942457999> Error",
        description=message,
        color=Config.COLOR_ERROR
    )


def create_success_embed(message: str) -> discord.Embed:
    """Create a success embed."""
    return discord.Embed(
        title="✅ Success",
        description=message,
        color=Config.COLOR_SUCCESS
    )


def create_info_embed(title: str, message: str) -> discord.Embed:
    """Create an info embed."""
    return discord.Embed(
        title=f"ℹ️ {title}",
        description=message,
        color=Config.COLOR_PRIMARY
    )


def create_idle_embed(state: "GuildMusicState", suggestion: Optional["Song"] = None) -> discord.Embed:
    """Create an idle/paused embed when nothing is playing."""
    embed = discord.Embed(
        title="🎵 Music Player",
        description="<:pa:1468742763129344184> **Nothing playing**\n\nUse `/play <song>` to start listening!",
        color=Config.COLOR_DARK
    )
    
    # Show suggestion if available
    if suggestion:
        embed.add_field(
            name="💡 Suggested",
            value=f"**[{suggestion.title}]({suggestion.webpage_url})**\nby {suggestion.author}",
            inline=False
        )
    
    # Show queue info if any
    if hasattr(state, 'queue') and state.queue:
        embed.add_field(
            name="<:shc:1468761592329142374> Queue",
            value=f"{len(state.queue)} song(s) waiting",
            inline=True
        )
    
    # Footer with status indicators
    # max vaolume icon is 🔊, 24/7 is 📻, autoplay is 💡
    footer_parts = [f"🔊 {int(state.volume * 100)}%"]
    if state.is_24_7:
        footer_parts.append("📻 24/7 \n")
    if state.is_autoplay:
        footer_parts.append("💡Autoplay \n")
    if VERSION_TYPE == "DEVELOPMENT":
        footer_parts.append(f"Vexo {VERSION_NUMBER} [{VERSION_TYPE} Build {BUILD_NUMBER}]")  
    embed.set_footer(text=" • ".join(footer_parts))
    
    return embed


def format_duration(seconds: int) -> str:
    """Format seconds to MM:SS or HH:MM:SS."""
    if seconds is None or seconds == 0:
        return "00:00"
    
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def create_upcoming_autoplay_embed(songs: list) -> discord.Embed:
    """
    Create an embed showing upcoming autoplay songs with jump instructions.
    
    Args:
        songs: List of Song objects (max 5)
    """
    embed = discord.Embed(
        title="<:di:1468760195495628861> Upcoming Autoplay Songs",
        description="Click a number button to jump to that song!",
        color=Config.COLOR_PRIMARY
    )
    
    if not songs:
        embed.add_field(
            name="No songs buffered",
            value="The autoplay buffer is empty. Play some songs to build history!",
            inline=False
        )
        return embed
    
    # Build song list
    number_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
    song_list = []
    
    for i, song in enumerate(songs[:5]):
        # Import here to avoid circular import
        duration = f"{song.duration // 60}:{song.duration % 60:02d}" if song.duration else "??:??"
        title = song.title[:45] + "..." if len(song.title) > 45 else song.title
        artist = song.author[:20] + "..." if len(song.author) > 20 else song.author
        song_list.append(f"{number_emojis[i]} **{title}**\n┗ {artist} • `{duration}`")
    
    embed.add_field(
        name="Up Next",
        value="\n\n".join(song_list),
        inline=False
    )
    
    embed.set_footer(text="💡 Songs are pre-loaded based on your listening history and favorites")
    
    return embed
