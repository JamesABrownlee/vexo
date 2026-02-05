"""
Interactive Discord UI Views for the music bot.
Provides button-based controls and autoplay preview with jump-to functionality.
"""
import discord
from discord import ui
from typing import Optional, List, TYPE_CHECKING, Callable, Any
import asyncio

from config import Config

if TYPE_CHECKING:
    from cogs.music import GuildMusicState, Song

from utils.discovery import discovery_engine


class MusicControlView(ui.View):
    """
    Main playback control buttons.
    Buttons: Play/Pause, Skip, Stop, Shuffle, Loop, Volume -/+
    """
    
    def __init__(
        self, 
        cog: Any,
        guild_id: int,
        *,
        timeout: Optional[float] = 180.0
    ):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.guild_id = guild_id
        self._update_buttons()
    
    def _get_state(self) -> "GuildMusicState":
        """Get the current guild music state."""
        return self.cog.get_state(self.guild_id)
    
    def _update_buttons(self):
        """Update button states based on current playback state."""
        state = self._get_state()
        
        # Update play/pause button
        if state.voice_client and state.voice_client.is_paused():
            self.play_pause_button.emoji = "<:pl:1468753784523067505>"
            self.play_pause_button.style = discord.ButtonStyle.success
        else:
            self.play_pause_button.emoji = "<:pa:1468742763129344184>"
            self.play_pause_button.style = discord.ButtonStyle.secondary
        
        # Update loop button based on mode
        loop_styles = {
            "off": (discord.ButtonStyle.secondary, "<:ra:1468742761548349753>"),
            "song": (discord.ButtonStyle.primary, "<:ra:1468742761548349753>"),
            "queue": (discord.ButtonStyle.success, "<:ra:1468742761548349753>"),
        }
        style, emoji = loop_styles.get(state.loop_mode, (discord.ButtonStyle.secondary, "<:ra:1468742761548349753>"))
        self.loop_button.style = style
        self.loop_button.emoji = emoji
    
    async def _check_voice(self, interaction: discord.Interaction) -> bool:
        """Check if user is in the same voice channel as the bot."""
        if not interaction.guild:
            await interaction.response.send_message("<:cr:1468763462942457999> This only works in a server!", ephemeral=True)
            return False
        
        member = interaction.user
        if not isinstance(member, discord.Member):
            return False
        
        vc = interaction.guild.voice_client
        if not vc:
            await interaction.response.send_message("<:cr:1468763462942457999> I'm not connected to a voice channel!", ephemeral=True)
            return False
        
        if not member.voice or member.voice.channel != vc.channel:
            await interaction.response.send_message("<:cr:1468763462942457999> You must be in the same voice channel!", ephemeral=True)
            return False
        
        return True
    
    @ui.button(emoji="<:pa:1468742763129344184>", style=discord.ButtonStyle.secondary, row=0)
    async def play_pause_button(self, interaction: discord.Interaction, button: ui.Button):
        """Toggle play/pause."""
        if not await self._check_voice(interaction):
            return
        
        vc = interaction.guild.voice_client
        
        if vc.is_paused():
            vc.resume()
            button.emoji = "<:pa:1468742763129344184>"
            button.style = discord.ButtonStyle.secondary
            await interaction.response.edit_message(view=self)
        elif vc.is_playing():
            vc.pause()
            button.emoji = "<:pl:1468753784523067505>"
            button.style = discord.ButtonStyle.success
            await interaction.response.edit_message(view=self)
        else:
            await interaction.response.send_message("<:cr:1468763462942457999> Nothing is playing!", ephemeral=True)
    
    @ui.button(emoji="<:sk:1468742764165337280>", style=discord.ButtonStyle.secondary, row=0)
    async def skip_button(self, interaction: discord.Interaction, button: ui.Button):
        """Skip to next song."""
        if not await self._check_voice(interaction):
            return
        
        vc = interaction.guild.voice_client
        state = self._get_state()
        
        if vc.is_playing() or vc.is_paused():
            title = state.current.title if state.current else "Unknown"
            vc.stop()  # This triggers play_next
            await interaction.response.send_message(f"<:sk:1468742764165337280> Skipped: **{title}**", ephemeral=True)
        else:
            await interaction.response.send_message("<:cr:1468763462942457999> Nothing is playing!", ephemeral=True)
    
    @ui.button(emoji="<:st:1468742765327286426>", style=discord.ButtonStyle.secondary, row=0)
    async def stop_button(self, interaction: discord.Interaction, button: ui.Button):
        """Stop playback and clear session queues."""
        if not await self._check_voice(interaction):
            return
        
        state = self._get_state()
        vc = interaction.guild.voice_client
        
        state.queue.clear()
        state.autoplay_visible.clear()
        state.autoplay_hidden.clear()
        state.current = None
        state.loop_mode = "off"
        
        if vc.is_playing() or vc.is_paused():
            vc.stop()
        
        await interaction.response.send_message("<:st:1468742765327286426> Stopped playback and cleared session queues.", ephemeral=True)
    
    @ui.button(emoji="<:sh:1468742768540127302>", style=discord.ButtonStyle.secondary, row=0)
    async def shuffle_button(self, interaction: discord.Interaction, button: ui.Button):
        """Shuffle the queue."""
        if not await self._check_voice(interaction):
            return
        
        import random
        state = self._get_state()
        
        if not state.queue:
            await interaction.response.send_message("<:cr:1468763462942457999> The queue is empty!", ephemeral=True)
            return
        
        random.shuffle(state.queue)
        await interaction.response.send_message(f"<:sh:1468742768540127302> Shuffled {len(state.queue)} songs!", ephemeral=True)
    
    @ui.button(emoji="<:ra:1468742761548349753>", style=discord.ButtonStyle.secondary, row=0)
    async def loop_button(self, interaction: discord.Interaction, button: ui.Button):
        """Cycle through loop modes: off -> song -> queue -> off."""
        if not await self._check_voice(interaction):
            return
        
        state = self._get_state()
        
        # Cycle modes
        modes = ["off", "song", "queue"]
        current_idx = modes.index(state.loop_mode)
        state.loop_mode = modes[(current_idx + 1) % len(modes)]
        
        # Update button appearance
        loop_info = {
            "off": (discord.ButtonStyle.secondary, "<:ra:1468742761548349753>", "<:stop:1468764262477463729> Loop disabled"),
            "song": (discord.ButtonStyle.primary, "<:ra:1468742761548349753>", "<:ra:1468742761548349753> Looping current song"),
            "queue": (discord.ButtonStyle.success, "<:ra:1468742761548349753>", "<:ra:1468742761548349753> Looping entire queue"),
        }
        style, emoji, message = loop_info[state.loop_mode]
        button.style = style
        button.emoji = emoji
        
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(message, ephemeral=True)
    
    @ui.button(emoji="<:vd:1468742767206203534>", style=discord.ButtonStyle.secondary, row=1)
    async def volume_down_button(self, interaction: discord.Interaction, button: ui.Button):
        """Decrease volume."""
        if not await self._check_voice(interaction):
            return
        
        state = self._get_state()
        step = Config.VOLUME_STEP / 100
        new_volume = max(0.0, state.volume - step)
        state.volume = new_volume
        
        vc = interaction.guild.voice_client
        if vc and vc.source:
            vc.source.volume = new_volume
        
        await interaction.response.send_message(
            f"<:vd:1468742767206203534> Volume: **{int(new_volume * 100)}%**", 
            ephemeral=True
        )
    
    @ui.button(emoji="<:vu:1468742766639972423>", style=discord.ButtonStyle.secondary, row=1)
    async def volume_up_button(self, interaction: discord.Interaction, button: ui.Button):
        """Increase volume."""
        if not await self._check_voice(interaction):
            return
        
        state = self._get_state()
        step = Config.VOLUME_STEP / 100
        new_volume = min(1.0, state.volume + step)
        state.volume = new_volume
        
        vc = interaction.guild.voice_client
        if vc and vc.source:
            vc.source.volume = new_volume
        
        await interaction.response.send_message(
            f"<:vu:1468742766639972423> Volume: **{int(new_volume * 100)}%**", 
            ephemeral=True
        )


class AutoplayPreviewView(ui.View):
    """
    Shows next 5 autoplay songs with jump-to buttons.
    Users can click a numbered button to skip directly to that song.
    """
    
    def __init__(
        self,
        cog: Any,
        guild_id: int,
        *,
        timeout: Optional[float] = 180.0
    ):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.guild_id = guild_id
        self._build_buttons()
    
    def _get_state(self) -> "GuildMusicState":
        """Get the current guild music state."""
        return self.cog.get_state(self.guild_id)
    
    def _build_buttons(self):
        """Build jump buttons based on current visible buffer."""
        state = self._get_state()
        buffer = state.autoplay_visible[:5]
        
        # Number emojis for buttons
        number_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
        
        for i, song in enumerate(buffer):
            button = ui.Button(
                emoji=number_emojis[i],
                style=discord.ButtonStyle.primary,
                custom_id=f"jump_{i}",
                row=0
            )
            button.callback = self._make_jump_callback(i)
            self.add_item(button)
        
        # Add refresh button
        refresh_btn = ui.Button(
            emoji="<:ra:1468742761548349753>",
            label="Refresh",
            style=discord.ButtonStyle.secondary,
            custom_id="refresh",
            row=1
        )
        refresh_btn.callback = self._refresh_callback
        self.add_item(refresh_btn)
    
    def _make_jump_callback(self, index: int):
        """Create a callback for jumping to a specific song."""
        async def callback(interaction: discord.Interaction):
            await self._jump_to_song(interaction, index)
        return callback
    
    async def _check_voice(self, interaction: discord.Interaction) -> bool:
        """Check if user is in the same voice channel as the bot."""
        if not interaction.guild:
            await interaction.response.send_message("<:cr:1468763462942457999> This only works in a server!", ephemeral=True)
            return False
        
        member = interaction.user
        if not isinstance(member, discord.Member):
            return False
        
        vc = interaction.guild.voice_client
        if not vc:
            await interaction.response.send_message("<:cr:1468763462942457999> I'm not connected to a voice channel!", ephemeral=True)
            return False
        
        if not member.voice or member.voice.channel != vc.channel:
            await interaction.response.send_message("<:cr:1468763462942457999> You must be in the same voice channel!", ephemeral=True)
            return False
        
        return True
    
    async def _jump_to_song(self, interaction: discord.Interaction, index: int):
        """Jump to a specific song in the autoplay buffer."""
        if not await self._check_voice(interaction):
            return
        
        state = self._get_state()
        
        if index >= len(state.autoplay_visible):
            await interaction.response.send_message(
                "<:cr:1468763462942457999> That song is no longer in the buffer!", 
                ephemeral=True
            )
            return
        
        # Get the target song
        target_song = state.autoplay_visible[index]
        
        # Slice the visible buffer to the target
        state.autoplay_visible = state.autoplay_visible[index:]
        
        # Clear any current queue items
        state.queue.clear()
        
        # Stop current playback
        vc = interaction.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
        
        await interaction.response.send_message(
            f"<:sk:1468742764165337280> Jumping to: **{target_song.title}**",
            ephemeral=True
        )
    
    async def _refresh_callback(self, interaction: discord.Interaction):
        """Refresh the autoplay preview embed."""
        from utils.embeds import create_upcoming_autoplay_embed
        
        state = self._get_state()
        embed = create_upcoming_autoplay_embed(state.autoplay_visible)
        
        # Rebuild the view
        new_view = AutoplayPreviewView(self.cog, self.guild_id)
        
        await interaction.response.edit_message(embed=embed, view=new_view)


class NowPlayingView(ui.View):
    """
    Combined view showing now playing with all controls.
    Includes playback buttons and a button to show upcoming autoplay songs.
    """
    
    def __init__(
        self,
        cog: Any,
        guild_id: int,
        *,
        timeout: Optional[float] = None
    ):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.guild_id = guild_id
        self._add_control_buttons()
    
    def _get_state(self) -> "GuildMusicState":
        """Get the current guild music state."""
        return self.cog.get_state(self.guild_id)
    
    def _add_control_buttons(self):
        """Add all control buttons."""
        state = self._get_state()
        
        # Row 0: Main playback controls
        # Play/Pause
        is_paused = state.voice_client and state.voice_client.is_paused()
        play_pause = ui.Button(
            emoji="<:pl:1468753784523067505>" if is_paused else "<:pa:1468742763129344184>",
            style=discord.ButtonStyle.success if is_paused else discord.ButtonStyle.secondary,
            custom_id="play_pause",
            row=0
        )
        play_pause.callback = self._play_pause_callback
        self.add_item(play_pause)
        
        # Skip
        skip = ui.Button(emoji="<:sk:1468742764165337280>", style=discord.ButtonStyle.primary, custom_id="skip", row=0)
        skip.callback = self._skip_callback
        self.add_item(skip)
        
        # Stop
        stop = ui.Button(emoji="<:st:1468742765327286426>", style=discord.ButtonStyle.danger, custom_id="stop", row=0)
        stop.callback = self._stop_callback
        self.add_item(stop)
        
        # Shuffle
        shuffle = ui.Button(emoji="<:sh:1468742768540127302>", style=discord.ButtonStyle.secondary, custom_id="shuffle", row=0)
        shuffle.callback = self._shuffle_callback
        self.add_item(shuffle)
        
        # Loop
        loop_styles = {
            "off": (discord.ButtonStyle.secondary, "<:ra:1468742761548349753>"),
            "song": (discord.ButtonStyle.primary, "<:ra:1468742761548349753>"),
            "queue": (discord.ButtonStyle.success, "<:ra:1468742761548349753>"),
        }
        style, emoji = loop_styles.get(state.loop_mode, (discord.ButtonStyle.secondary, "<:ra:1468742761548349753>"))
        loop = ui.Button(emoji=emoji, style=style, custom_id="loop", row=0)
        loop.callback = self._loop_callback
        self.add_item(loop)
        
        # Row 1: Volume and autoplay controls
        vol_down = ui.Button(emoji="<:vd:1468742767206203534>", style=discord.ButtonStyle.secondary, custom_id="vol_down", row=1)
        vol_down.callback = self._volume_down_callback
        self.add_item(vol_down)
        
        vol_up = ui.Button(emoji="<:vu:1468742766639972423>", style=discord.ButtonStyle.secondary, custom_id="vol_up", row=1)
        vol_up.callback = self._volume_up_callback
        self.add_item(vol_up)
        
        # Autoplay toggle
        autoplay_style = discord.ButtonStyle.success if state.is_autoplay else discord.ButtonStyle.secondary
        autoplay = ui.Button(emoji="<:di:1468760195495628861>", style=autoplay_style, custom_id="autoplay", row=1)
        autoplay.callback = self._autoplay_callback
        self.add_item(autoplay)
        
        # Show upcoming (only if autoplay is on)
        if state.is_autoplay and state.autoplay_visible:
            upcoming = ui.Button(
                emoji="<:shc:1468761592329142374>", 
                label=f"({len(state.autoplay_visible)})",
                style=discord.ButtonStyle.secondary, 
                custom_id="upcoming", 
                row=1
            )
            upcoming.callback = self._upcoming_callback
            self.add_item(upcoming)

        # Row 2: Vexo Voting
        upvote = ui.Button(emoji="<:li:1468742770356257029>", style=discord.ButtonStyle.secondary, custom_id="upvote", row=2)
        upvote.callback = self._upvote_callback
        self.add_item(upvote)

        downvote = ui.Button(emoji="<:dl:1468742771337597140>", style=discord.ButtonStyle.secondary, custom_id="downvote", row=2)
        downvote.callback = self._downvote_callback
        self.add_item(downvote)
    
    async def _check_voice(self, interaction: discord.Interaction) -> bool:
        """Check if user is in the same voice channel as the bot, or auto-connect if not connected."""
        if not interaction.guild:
            await interaction.response.send_message("<:cr:1468763462942457999> This only works in a server!", ephemeral=True)
            return False
        
        member = interaction.user
        if not isinstance(member, discord.Member):
            return False
        
        vc = interaction.guild.voice_client
        
        # If bot is not connected, try to auto-connect
        if not vc:
            # Check if user is a mod (can connect bot from anywhere)
            is_mod = member.guild_permissions.administrator or member.guild_permissions.manage_guild
            
            if not member.voice or not member.voice.channel:
                if is_mod:
                    await interaction.response.send_message("<:cr:1468763462942457999> You must be in a voice channel to connect the bot!", ephemeral=True)
                else:
                    await interaction.response.send_message("<:cr:1468763462942457999> I'm not connected. Join a voice channel first!", ephemeral=True)
                return False
            
            # Auto-connect to user's voice channel
            try:
                vc = await member.voice.channel.connect()
                state = self._get_state()
                state.voice_client = vc
                state.text_channel = interaction.channel
            except Exception as e:
                await interaction.response.send_message(f"<:cr:1468763462942457999> Failed to connect: {e}", ephemeral=True)
                return False
        
        # Check if user is in same channel (mods can skip this check)
        is_mod = member.guild_permissions.administrator or member.guild_permissions.manage_guild
        if not is_mod and (not member.voice or member.voice.channel != vc.channel):
            await interaction.response.send_message("<:cr:1468763462942457999> You must be in the same voice channel!", ephemeral=True)
            return False
        
        return True
    
    async def _play_pause_callback(self, interaction: discord.Interaction):
        """Toggle play/pause, or start playing suggested song if nothing playing."""
        if not await self._check_voice(interaction):
            return
        
        vc = interaction.guild.voice_client
        state = self._get_state()
        
        if vc.is_paused():
            vc.resume()
            await interaction.response.send_message(f"<:pl:1468753784523067505> **{interaction.user.display_name}** resumed playback")
        elif vc.is_playing():
            vc.pause()
            await interaction.response.send_message(f"<:pa:1468742763129344184> **{interaction.user.display_name}** paused playback")
        else:
            # Nothing playing - try to play a suggested song
            song_to_play = None
            
            # Check autoplay buffer first
            if state.autoplay_visible:
                song_to_play = state.autoplay_visible.pop(0)
            
            if song_to_play:
                # Play the suggested song
                self.cog._play_song(self.guild_id, song_to_play)
                await interaction.response.send_message(
                    f"<:pl:1468753784523067505> **{interaction.user.display_name}** started playing: **{song_to_play.title}**"
                )
            else:
                await interaction.response.send_message(
                    "<:cr:1468763462942457999> Nothing to play! Use `/play <song>` to add a song.", 
                    ephemeral=True
                )
    
    async def _skip_callback(self, interaction: discord.Interaction):
        """Skip to next song."""
        if not await self._check_voice(interaction):
            return
        
        vc = interaction.guild.voice_client
        state = self._get_state()
        
        if vc.is_playing() or vc.is_paused():
            title = state.current.title if state.current else "Unknown"
            
            # Record skip interaction
            if state.current:
                await discovery_engine.record_interaction(
                    interaction.user.id,
                    state.current.author,
                    state.current.title,
                    state.current.webpage_url,
                    "skip"
                )
                
            vc.stop()
            await interaction.response.send_message(f"<:sk:1468742764165337280> **{interaction.user.display_name}** skipped: **{title}**")
        else:
            await interaction.response.send_message("<:cr:1468763462942457999> Nothing is playing!", ephemeral=True)
    
    async def _stop_callback(self, interaction: discord.Interaction):
        """Stop playback."""
        if not await self._check_voice(interaction):
            return
        
        state = self._get_state()
        vc = interaction.guild.voice_client
        
        state.queue.clear()
        state.autoplay_visible.clear()
        state.autoplay_hidden.clear()
        state.current = None
        state.loop_mode = "off"
        
        if vc.is_playing() or vc.is_paused():
            vc.stop()
        
        await interaction.response.send_message(f"<:st:1468742765327286426> **{interaction.user.display_name}** stopped playback and cleared queue.")
    
    async def _shuffle_callback(self, interaction: discord.Interaction):
        """Shuffle the queue."""
        if not await self._check_voice(interaction):
            return
        
        import random
        state = self._get_state()
        
        if not state.queue:
            await interaction.response.send_message("<:cr:1468763462942457999> Queue is empty!", ephemeral=True)
            return
        
        random.shuffle(state.queue)
        await interaction.response.send_message(f"<:sh:1468742768540127302> Shuffled {len(state.queue)} songs!", ephemeral=True)
    
    async def _loop_callback(self, interaction: discord.Interaction):
        """Cycle loop modes."""
        if not await self._check_voice(interaction):
            return
        
        state = self._get_state()
        modes = ["off", "song", "queue"]
        current_idx = modes.index(state.loop_mode)
        state.loop_mode = modes[(current_idx + 1) % len(modes)]
        
        messages = {
            "off": "<:stop:1468764262477463729> Loop disabled",
            "song": "<:ra:1468742761548349753> Looping current song",
            "queue": "<:ra:1468742761548349753> Looping entire queue"
        }
        await interaction.response.send_message(f"{messages[state.loop_mode]} (by **{interaction.user.display_name}**)") 
    
    async def _volume_down_callback(self, interaction: discord.Interaction):
        """Decrease volume."""
        if not await self._check_voice(interaction):
            return
        
        state = self._get_state()
        step = Config.VOLUME_STEP / 100
        state.volume = max(0.0, state.volume - step)
        
        vc = interaction.guild.voice_client
        if vc and vc.source:
            vc.source.volume = state.volume
        
        await interaction.response.send_message(f"<:vd:1468742767206203534> Volume: **{int(state.volume * 100)}%**", ephemeral=True)
    
    async def _volume_up_callback(self, interaction: discord.Interaction):
        """Increase volume."""
        if not await self._check_voice(interaction):
            return
        
        state = self._get_state()
        step = Config.VOLUME_STEP / 100
        state.volume = min(1.0, state.volume + step)
        
        vc = interaction.guild.voice_client
        if vc and vc.source:
            vc.source.volume = state.volume
        
        await interaction.response.send_message(f"<:vu:1468742766639972423> Volume: **{int(state.volume * 100)}%**", ephemeral=True)
    
    async def _autoplay_callback(self, interaction: discord.Interaction):
        """Toggle autoplay."""
        if not await self._check_voice(interaction):
            return
        
        state = self._get_state()
        state.is_autoplay = not state.is_autoplay
        state.text_channel = interaction.channel
        
        if state.is_autoplay:
            # Trigger buffer refill
            asyncio.create_task(self.cog._refill_autoplay_buffer(self.guild_id))
            await interaction.response.send_message(f"<:di:1468760195495628861> Autoplay **enabled** by **{interaction.user.display_name}**!")
        else:
            state.autoplay_visible.clear()
            state.autoplay_hidden.clear()
            await interaction.response.send_message(f"<:di:1468760195495628861> Autoplay **disabled** by **{interaction.user.display_name}** (Queues cleared).")
    
    async def _upcoming_callback(self, interaction: discord.Interaction):
        """Show upcoming autoplay songs with jump buttons."""
        from utils.embeds import create_upcoming_autoplay_embed
        
        state = self._get_state()
        
        if not state.autoplay_visible:
            await interaction.response.send_message(
                "<:cr:1468763462942457999> No songs in autoplay buffer! Enable autoplay first.",
                ephemeral=True
            )
            return
        
        embed = create_upcoming_autoplay_embed(state.autoplay_visible)
        view = AutoplayPreviewView(self.cog, self.guild_id)
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def _upvote_callback(self, interaction: discord.Interaction):
        """Record an upvote."""
        state = self._get_state()
        if not state.current:
            return await interaction.response.send_message("<:cr:1468763462942457999> Nothing is playing!", ephemeral=True)
            
        await discovery_engine.record_interaction(
            interaction.user.id, 
            state.current.author, 
            state.current.title, 
            state.current.webpage_url, 
            "upvote"
        )
        await interaction.response.send_message(f"<:li:1468742770356257029> **{interaction.user.display_name}** liked this song!", ephemeral=True)

    async def _downvote_callback(self, interaction: discord.Interaction):
        """Record a downvote."""
        state = self._get_state()
        if not state.current:
            return await interaction.response.send_message("<:cr:1468763462942457999> Nothing is playing!", ephemeral=True)
            
        await discovery_engine.record_interaction(
            interaction.user.id, 
            state.current.author, 
            state.current.title, 
            state.current.webpage_url, 
            "downvote"
        )
        await interaction.response.send_message(f"<:dl:1468742771337597140> **{interaction.user.display_name}** disliked this song!", ephemeral=True)
