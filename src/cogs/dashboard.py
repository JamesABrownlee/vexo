"""
Web Dashboard Cog - Modern analytics dashboard
Runs on localhost only - no authentication required
"""
import asyncio
import json
import logging
from collections import deque
from datetime import datetime
from pathlib import Path

from aiohttp import web

from discord.ext import commands

from src.utils.logging import get_logger, Category, Event

log = get_logger(__name__)

STATIC_DIR = Path(__file__).parent.parent / "web" / "static"
TEMPLATE_DIR = Path(__file__).parent.parent / "web" / "templates"


class WebSocketLogHandler(logging.Handler):
    """Log handler that broadcasts to WebSocket clients with structured parsing."""
    
    def __init__(self, ws_manager, loop):
        super().__init__()
        self.ws_manager = ws_manager
        self.loop = loop
    
    def _parse_structured(self, message: str) -> dict:
        """Parse structured log message for category/event fields.
        
        Expected format: event_name category=cat key=value key2='quoted value'
        """
        import re
        result = {"category": None, "event": None, "fields": {}}
        
        if not message:
            return result
        
        # Extract key=value pairs (handles quoted values)
        kv_regex = r'(\w+)=(?:\'([^\']*)\'|"([^"]*)"|(\S+))'
        pairs = {}
        for match in re.finditer(kv_regex, message):
            key = match.group(1)
            val = match.group(2) or match.group(3) or match.group(4)
            pairs[key] = val
        
        # Extract category if present
        if "category" in pairs:
            result["category"] = pairs.pop("category")
        
        result["fields"] = pairs
        
        # First word before any key=value might be the event name
        cleaned = re.sub(kv_regex, '', message).strip()
        words = cleaned.split()
        if words and re.match(r'^[a-z_][a-z0-9_]*$', words[0]):
            result["event"] = words[0]
        
        return result
    
    def emit(self, record):
        if self.ws_manager.clients:
            try:
                message = record.getMessage()
                parsed = self._parse_structured(message)
                
                log_entry = {
                    "timestamp": record.created,
                    "level": record.levelname,
                    "message": message,
                    "logger": record.name,
                    "guild_id": getattr(record, "guild_id", None),
                    "category": parsed["category"],
                    "event": parsed["event"],
                    "fields": parsed["fields"],
                }
                
                # Check if we're in the same loop
                try:
                    current_loop = asyncio.get_running_loop()
                except RuntimeError:
                    current_loop = None

                if current_loop == self.loop:
                    asyncio.create_task(self.ws_manager.broadcast(log_entry))
                else:
                    asyncio.run_coroutine_threadsafe(
                        self.ws_manager.broadcast(log_entry), 
                        self.loop
                    )
            except Exception:
                # Prevent recursive logging loops if logging fails
                pass


class WebSocketManager:
    """Manages WebSocket connections for live logs."""
    
    def __init__(self):
        self.clients: set[web.WebSocketResponse] = set()
        self.recent_logs: deque = deque(maxlen=500)
    
    async def broadcast(self, message: dict):
        self.recent_logs.append(message)
        disconnected = set()
        for ws in self.clients:
            try:
                await ws.send_json(message)
            except Exception:
                disconnected.add(ws)
        self.clients -= disconnected


class DashboardCog(commands.Cog):
    """Web dashboard for stats and analytics."""
    
    def __init__(self, bot: commands.Bot, host: str = "127.0.0.1", port: int = 8080):
        self.bot = bot
        self.host = host
        self.port = port
        self.app: web.Application | None = None
        self.runner: web.AppRunner | None = None
        self.ws_manager = WebSocketManager()
        self._log_handler: WebSocketLogHandler | None = None
    
    async def cog_load(self):
        self.app = web.Application()
        self._setup_routes()
        
        self._log_handler = WebSocketLogHandler(self.ws_manager, self.bot.loop)
        self._log_handler.setLevel(logging.INFO)
        logging.getLogger().addHandler(self._log_handler)
        
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, self.host, self.port)
        await site.start()
        
        log.event(Category.SYSTEM, "dashboard_started", host=self.host, port=self.port)
    
    async def cog_unload(self):
        if self._log_handler:
            logging.getLogger().removeHandler(self._log_handler)
        if self.runner:
            await self.runner.cleanup()
    
    def _setup_routes(self):
        # Static files
        if STATIC_DIR.exists():
            self.app.router.add_static('/static', STATIC_DIR)
        
        # Pages
        self.app.router.add_get("/", self._handle_index)
        
        # API
        self.app.router.add_get("/api/status", self._handle_status)
        self.app.router.add_get("/api/guilds", self._handle_guilds)
        self.app.router.add_get("/api/guilds/{guild_id}", self._handle_guild_detail)
        self.app.router.add_get("/api/guilds/{guild_id}/settings", self._handle_guild_settings)
        self.app.router.add_post("/api/guilds/{guild_id}/settings", self._handle_update_settings)
        self.app.router.add_post("/api/guilds/{guild_id}/control/{action}", self._handle_control)
        self.app.router.add_get("/api/analytics", self._handle_analytics)
        self.app.router.add_get("/api/songs", self._handle_songs)
        self.app.router.add_get("/api/genres", self._handle_genres)
        self.app.router.add_get("/api/library", self._handle_library)
        self.app.router.add_get("/api/users", self._handle_users)
        self.app.router.add_get("/api/users/{user_id}/preferences", self._handle_user_prefs)
        self.app.router.add_get("/ws/logs", self._handle_websocket)
        
        # Global & System
        self.app.router.add_get("/api/settings/global", self._handle_global_settings)
        self.app.router.add_post("/api/settings/global", self._handle_global_settings)
        self.app.router.add_get("/api/notifications", self._handle_notifications)
        self.app.router.add_post("/api/guilds/{guild_id}/leave", self._handle_leave_guild)
    
    async def _handle_index(self, request: web.Request) -> web.Response:
        html_file = TEMPLATE_DIR / "index.html"
        if html_file.exists():
            return web.Response(text=html_file.read_text(encoding='utf-8'), content_type="text/html")
        return web.Response(text="Dashboard template not found", status=404)
    
    async def _handle_status(self, request: web.Request) -> web.Response:
        import psutil
        process = psutil.Process()
        return web.json_response({
            "status": "online",
            "guilds": len(self.bot.guilds),
            "voice_connections": len(self.bot.voice_clients),
            "latency_ms": round(self.bot.latency * 1000, 2),
            "cpu_percent": psutil.cpu_percent(),
            "ram_percent": psutil.virtual_memory().percent,
            "process_ram_mb": round(process.memory_info().rss / 1024 / 1024, 2)
        })
    
    async def _handle_guilds(self, request: web.Request) -> web.Response:
        music = self.bot.get_cog("MusicCog")
        guilds = []
        for guild in self.bot.guilds:
            player = music.get_player(guild.id) if music else None
            data = {
                "id": str(guild.id),
                "name": guild.name,
                "member_count": guild.member_count,
                "is_playing": bool(player and player.is_playing),
            }
            if player and player.current:
                data["current_song"] = player.current.title
                data["current_artist"] = player.current.artist
                data["video_id"] = player.current.video_id
                data["discovery_reason"] = player.current.discovery_reason
                data["duration_seconds"] = player.current.duration_seconds
                data["genre"] = player.current.genre
                data["year"] = player.current.year
                if player.current.for_user_id:
                    user = self.bot.get_user(player.current.for_user_id)
                    data["for_user"] = user.display_name if user else str(player.current.for_user_id)
                
                # Fetch detailed interaction stats for current song
                if hasattr(self.bot, "db") and player.current.song_db_id:
                    stats = await self.bot.db.fetch_one("""
                        SELECT 
                            (SELECT GROUP_CONCAT(DISTINCT u.username) FROM playback_history ph JOIN users u ON ph.for_user_id = u.id WHERE ph.song_id = ? AND ph.discovery_source = "user_request") as requested_by,
                            (SELECT GROUP_CONCAT(DISTINCT u.username) FROM song_reactions sr JOIN users u ON sr.user_id = u.id WHERE sr.song_id = ? AND sr.reaction = 'like') as liked_by,
                            (SELECT GROUP_CONCAT(DISTINCT u.username) FROM song_reactions sr JOIN users u ON sr.user_id = u.id WHERE sr.song_id = ? AND sr.reaction = 'dislike') as disliked_by
                    """, (player.current.song_db_id, player.current.song_db_id, player.current.song_db_id))
                    if stats:
                        data["requested_by"] = stats["requested_by"]
                        data["liked_by"] = stats["liked_by"]
                        data["disliked_by"] = stats["disliked_by"]
            guilds.append(data)
        return web.json_response({"guilds": guilds})
    
    async def _handle_guild_detail(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info["guild_id"])
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return web.json_response({"error": "Not found"}, status=404)
        
        music = self.bot.get_cog("MusicCog")
        player = music.get_player(guild_id) if music else None
        
        return web.json_response({
            "id": str(guild.id),
            "name": guild.name,
            "member_count": guild.member_count,
            "queue_size": player.queue.qsize() if player else 0,
        })
    
    async def _handle_guild_settings(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info["guild_id"])
        if not hasattr(self.bot, "db"):
            return web.json_response({})
        from src.database.crud import GuildCRUD
        crud = GuildCRUD(self.bot.db)
        settings = await crud.get_all_settings(guild_id)
        return web.json_response(settings)
    
    async def _handle_update_settings(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info["guild_id"])
        data = await request.json()
        
        if hasattr(self.bot, "db"):
            from src.database.crud import GuildCRUD
            crud = GuildCRUD(self.bot.db)
            
            # Save settings
            if "pre_buffer" in data:
                await crud.set_setting(guild_id, "pre_buffer", str(data["pre_buffer"]).lower())
            if "buffer_amount" in data:
                 await crud.set_setting(guild_id, "buffer_amount", str(data["buffer_amount"]))
            if "replay_cooldown" in data:
                 await crud.set_setting(guild_id, "replay_cooldown", str(data["replay_cooldown"]))
            if "max_song_duration" in data:
                 await crud.set_setting(guild_id, "max_song_duration", str(data["max_song_duration"]))
                 
            # Apply to active player if exists
            music = self.bot.get_cog("MusicCog")
            if music:
                player = music.get_player(guild_id)
                if player:
                    if "pre_buffer" in data:
                        player.pre_buffer = bool(data["pre_buffer"])
                        
        return web.json_response({"status": "ok"})
    
    async def _handle_control(self, request: web.Request) -> web.Response:
        """Handle playback controls."""
        guild_id = int(request.match_info["guild_id"])
        action = request.match_info["action"]
        
        music = self.bot.get_cog("MusicCog")
        if not music:
            return web.json_response({"error": "Music cog not loaded"}, status=503)
        
        player = music.get_player(guild_id)
        if not player.voice_client:
            return web.json_response({"error": "Not connected"}, status=400)
        
        try:
            if action == "pause":
                if player.voice_client.is_playing():
                    player.voice_client.pause()
                elif player.voice_client.is_paused():
                    player.voice_client.resume()
            
            elif action == "skip":
                player.voice_client.stop()
            
            elif action == "stop":
                # Clear queue and stop
                while not player.queue.empty():
                    try:
                        player.queue.get_nowait()
                    except (asyncio.QueueEmpty, Exception):
                        break
                
                if player.voice_client.is_playing() or player.voice_client.is_paused():
                    player.voice_client.stop()
                
                await player.voice_client.disconnect()
            
            return web.json_response({"status": "ok", "action": action})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
    
    async def _handle_songs(self, request: web.Request) -> web.Response:
        """Get song library."""
        if not hasattr(self.bot, "db"):
            return web.json_response({"songs": []})
        
        guild_id = request.query.get("guild_id")
        params = []
        where_clause = ""
        
        if guild_id:
            # Filter by playback history in this guild
            where_clause = "WHERE ps.guild_id = ?"
            params.append(int(guild_id))
        
        query = f"""
            SELECT 
                ph.played_at,
                s.title,
                s.artist_name,
                s.duration_seconds,
                (SELECT GROUP_CONCAT(DISTINCT sg.genre) FROM song_genres sg WHERE sg.song_id = s.id) as genre,
                CASE WHEN ph.discovery_source = 'user_request' THEN u.username ELSE NULL END as requested_by,
                (SELECT GROUP_CONCAT(DISTINCT u2.username) 
                 FROM song_reactions sr 
                 JOIN users u2 ON sr.user_id = u2.id 
                 WHERE sr.song_id = s.id AND sr.reaction = 'like') as liked_by,
                (SELECT GROUP_CONCAT(DISTINCT u2.username) 
                 FROM song_reactions sr 
                 JOIN users u2 ON sr.user_id = u2.id 
                 WHERE sr.song_id = s.id AND sr.reaction = 'dislike') as disliked_by
            FROM playback_history ph
            JOIN songs s ON ph.song_id = s.id
            JOIN playback_sessions ps ON ph.session_id = ps.id
            LEFT JOIN users u ON ph.for_user_id = u.id
            {where_clause}
            ORDER BY ph.played_at DESC
            LIMIT 100
        """
        songs = await self.bot.db.fetch_all(query, tuple(params))
        
        # Serialize for JSON
        data = []
        for s in songs:
            item = dict(s)
            # Handle datetime fields if they exist as objects
            for key in ["created_at", "last_played"]:
                if key in item and item[key]:
                    if hasattr(item[key], "isoformat"): # datetime object
                        item[key] = item[key].isoformat()
                    # If string, leave as is
            data.append(item)
            
        return web.json_response({"songs": data})
    
    async def _handle_genres(self, request: web.Request) -> web.Response:
        """Get list of all genres."""
        if not hasattr(self.bot, "db"):
            return web.json_response({"genres": []})
            
        from src.database.crud import SongCRUD
        crud = SongCRUD(self.bot.db)
        genres = await crud.get_all_genres()
        return web.json_response({"genres": genres})
    
    async def _handle_analytics(self, request: web.Request) -> web.Response:
        """Get analytics data."""
        if not hasattr(self.bot, "db"):
            return web.json_response({"error": "No database"})
        
        from src.database.crud import AnalyticsCRUD
        crud = AnalyticsCRUD(self.bot.db) # Updated
        
        guild_id = request.query.get("guild_id")
        gid = int(guild_id) if guild_id else None
        
        # We only really care about getting top_songs filtered by guild here for the dashboard
        # But the frontend might expect full stats. Let's start with top songs.
        # Enhanced Analytics
        top_songs = await crud.get_top_songs(limit=5, guild_id=gid)
        top_users = await crud.get_top_users(limit=5, guild_id=gid)
        stats = await crud.get_total_stats(guild_id=gid)
        
        # New requested stats
        top_liked_songs = await crud.get_top_liked_songs(limit=5)
        top_liked_artists = await crud.get_top_liked_artists(limit=5)
        top_liked_genres = await crud.get_top_liked_genres(limit=5)
        top_played_artists = await crud.get_top_played_artists(limit=5, guild_id=gid)
        top_played_genres = await crud.get_top_played_genres(limit=5, guild_id=gid)
        top_useful_users = await crud.get_top_useful_users(limit=5)
        
        # Extended stats for charts
        discovery_stats = await crud.get_discovery_breakdown(guild_id=gid)
        genre_dist = await crud.get_top_played_genres(limit=15, guild_id=gid)
        
        formatted_users = []
        for u in top_users:
            d = dict(u)
            formatted_users.append({
                "id": str(d["id"]),
                "name": d["username"],
                "plays": d["plays"],
                "total_likes": d["reactions"],
                "playlists_imported": d["playlists"],
            })

        return web.json_response({
            "total_songs": stats["total_songs"],
            "total_users": stats["total_users"],
            "total_plays": stats["total_plays"],
            "top_songs": [dict(r) for r in top_songs],
            "top_users": formatted_users,
            "top_liked_songs": [dict(r) for r in top_liked_songs],
            "top_liked_artists": [dict(r) for r in top_liked_artists],
            "top_liked_genres": [dict(r) for r in top_liked_genres],
            "top_played_artists": [dict(r) for r in top_played_artists],
            "top_played_genres": [dict(r) for r in top_played_genres],
            "top_useful_users": [dict(r) for r in top_useful_users],
            "discovery_breakdown": [dict(r) for r in discovery_stats],
            "genre_distribution": [dict(r) for r in genre_dist],
        })
    
    async def _handle_top_songs(self, request: web.Request) -> web.Response:
        """Get top songs list."""
        if not hasattr(self.bot, "db"):
             return web.json_response({"songs": []})
        
        from src.database.crud import AnalyticsCRUD
        crud = AnalyticsCRUD(self.bot.db)
        
        guild_id = request.query.get("guild_id")
        gid = int(guild_id) if guild_id else None
        
        songs = await crud.get_top_songs(limit=10, guild_id=gid)
        return web.json_response({"songs": [dict(r) for r in songs]})
    
    async def _handle_users(self, request: web.Request) -> web.Response:
        """Get users list."""
        if not hasattr(self.bot, "db"):
             return web.json_response({"users": []})
             
        from src.database.crud import AnalyticsCRUD
        crud = AnalyticsCRUD(self.bot.db)
        
        guild_id = request.query.get("guild_id")
        gid = int(guild_id) if guild_id else None
        
        users = await crud.get_top_users(limit=50, guild_id=gid)
        
        # Format
        data = []
        for u in users:
            d = dict(u)
            d["formatted_id"] = str(d["id"])
            data.append(d)
        return web.json_response({"users": data})

    async def _handle_global_settings(self, request: web.Request) -> web.Response:
        """Get or update global settings."""
        if not hasattr(self.bot, "db"):
            return web.json_response({})
        
        from src.database.crud import SystemCRUD
        crud = SystemCRUD(self.bot.db)
        
        if request.method == "POST":
            data = await request.json()
            for key, value in data.items():
                await crud.set_global_setting(key, value)
            return web.json_response({"status": "ok"})
        else:
            limit = await crud.get_global_setting("max_concurrent_servers")
            return web.json_response({"max_concurrent_servers": limit})

    async def _handle_notifications(self, request: web.Request) -> web.Response:
        """Get notifications."""
        if not hasattr(self.bot, "db"):
            return web.json_response({"notifications": []})
        
        from src.database.crud import SystemCRUD
        crud = SystemCRUD(self.bot.db)
        notifications = await crud.get_recent_notifications()
        # Serialize datetime
        # Serialize datetime
        data = []
        from datetime import datetime
        for n in notifications:
            d = dict(n)
            # Handle SQLite string or datetime object
            if isinstance(n["created_at"], str):
                try:
                    # Depending on how it's stored, it might be ISO format
                    dt = datetime.fromisoformat(n["created_at"])
                    d["created_at"] = dt.timestamp()
                except ValueError:
                    d["created_at"] = 0
            elif isinstance(n["created_at"], datetime):
                d["created_at"] = n["created_at"].timestamp()
            else:
                d["created_at"] = 0
            data.append(d)
        return web.json_response({"notifications": data})

    async def _handle_leave_guild(self, request: web.Request) -> web.Response:
        """Force bot to leave a guild."""
        guild_id = int(request.match_info["guild_id"])
        guild = self.bot.get_guild(guild_id)
        if guild:
            await guild.leave()
            
            # Log notification
            if hasattr(self.bot, "db"):
                from src.database.crud import SystemCRUD
                crud = SystemCRUD(self.bot.db)
                await crud.add_notification("info", f"Manually left server: {guild.name}")
                
            return web.json_response({"status": "ok"})
        return web.json_response({"error": "Guild not found"}, status=404)

    async def _handle_library(self, request: web.Request) -> web.Response:
        """Get unified song library."""
        if not hasattr(self.bot, "db"):
            return web.json_response({"library": []})
        
        guild_id = request.query.get("guild_id")
        if guild_id:
            guild_id = int(guild_id)
            
        from src.database.crud import LibraryCRUD
        crud = LibraryCRUD(self.bot.db)
        library = await crud.get_library(guild_id=guild_id)
        
        # Omit verbose logging for API calls
        
        # Serialize timestamps
        for entry in library:
            if "last_added" in entry and isinstance(entry["last_added"], datetime):
                entry["last_added"] = entry["last_added"].isoformat()
                
        return web.json_response({"library": library})

    
    async def _handle_user_prefs(self, request: web.Request) -> web.Response:
        user_id = int(request.match_info["user_id"])
        if not hasattr(self.bot, "db"):
            return web.json_response({})
        
        from src.database.crud import PreferenceCRUD
        crud = PreferenceCRUD(self.bot.db)
        prefs = await crud.get_all_preferences(user_id)
        return web.json_response(prefs)
    
    async def _handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.ws_manager.clients.add(ws)
        for log in self.ws_manager.recent_logs:
            await ws.send_json(log)
        try:
            async for _ in ws:
                pass
        finally:
            self.ws_manager.clients.discard(ws)
        return ws


async def setup(bot: commands.Bot):
    from src.config import config
    await bot.add_cog(DashboardCog(bot, config.WEB_HOST, config.WEB_PORT))
