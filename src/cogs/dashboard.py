"""
Web Dashboard Cog - Modern analytics dashboard
By default allows loopback requests only; optionally protect admin endpoints with WEB_ADMIN_TOKEN.
"""
import asyncio
import json
import logging
import os
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
        self._cog_admin_token = os.getenv("WEB_ADMIN_TOKEN")
        self._cog_action_lock = asyncio.Lock()
    
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
        self.app.router.add_get("/api/users/{user_id}/detail", self._handle_user_detail)
        self.app.router.add_get("/ws/logs", self._handle_websocket)

        # Cog management (Discord extensions under src.cogs.*)
        self.app.router.add_get("/api/cogs", self._handle_cogs_list)
        self.app.router.add_post("/api/cogs/actions/{action}", self._handle_cogs_bulk_action)
        self.app.router.add_post("/api/cogs/{cog}/{action}", self._handle_cog_action)
        
        # Global & System
        self.app.router.add_get("/api/settings/global", self._handle_global_settings)
        self.app.router.add_post("/api/settings/global", self._handle_global_settings)
        self.app.router.add_get("/api/notifications", self._handle_notifications)
        self.app.router.add_post("/api/guilds/{guild_id}/leave", self._handle_leave_guild)
        
        # Service management
        self.app.router.add_get("/api/services", self._handle_services_list)
        self.app.router.add_post("/api/services/{service_id}/restart", self._handle_service_restart)

    def _is_loopback(self, request: web.Request) -> bool:
        remote = request.remote or ""
        return remote in {"127.0.0.1", "::1"}

    def _is_admin(self, request: web.Request) -> bool:
        """Authorize cog management endpoints.

        - If WEB_ADMIN_TOKEN is set: require header X-Admin-Token (or ?token=...).
        - Otherwise: only allow loopback requests.
        """
        if self._cog_admin_token:
            provided = request.headers.get("X-Admin-Token") or request.query.get("token")
            return bool(provided) and provided == self._cog_admin_token

        return self._is_loopback(request)

    def _normalize_extension(self, cog_name: str) -> str | None:
        """Convert user input to a safe extension module name under src.cogs.*."""
        name = (cog_name or "").strip()
        if not name:
            return None

        if name.endswith(".py"):
            name = name[:-3]

        module = name if "." in name else f"src.cogs.{name}"
        if not module.startswith("src.cogs."):
            return None

        stem = module.split(".")[-1]
        candidate = Path(__file__).parent / f"{stem}.py"
        if not candidate.exists():
            return None

        return module

    def _list_available_extensions(self) -> list[str]:
        cogs_dir = Path(__file__).parent
        modules: list[str] = []
        for cog_file in cogs_dir.glob("*.py"):
            if cog_file.name.startswith("_"):
                continue
            modules.append(f"src.cogs.{cog_file.stem}")
        return sorted(modules)

    async def _sync_commands(self) -> dict:
        try:
            await self.bot.tree.sync()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def _run_extension_action(self, action: str, module: str) -> dict:
        try:
            if action == "load":
                await self.bot.load_extension(module)
            elif action == "unload":
                await self.bot.unload_extension(module)
            elif action == "reload":
                if module in self.bot.extensions:
                    await self.bot.reload_extension(module)
                else:
                    await self.bot.load_extension(module)
            else:
                return {"ok": False, "module": module, "error": "invalid_action"}
            return {"ok": True, "module": module}
        except Exception as e:
            return {"ok": False, "module": module, "error": str(e)}

    async def _handle_cogs_list(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return web.json_response({"error": "unauthorized"}, status=401)

        available = self._list_available_extensions()
        loaded = sorted(list(self.bot.extensions.keys()))
        return web.json_response(
            {
                "available_extensions": available,
                "loaded_extensions": loaded,
                "loaded_cogs": sorted(list(self.bot.cogs.keys())),
                "auth": {"mode": "token" if self._cog_admin_token else "loopback"},
            }
        )

    async def _handle_cog_action(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return web.json_response({"error": "unauthorized"}, status=401)

        cog = request.match_info["cog"]
        action = request.match_info["action"]
        if action not in {"load", "unload", "reload"}:
            return web.json_response({"error": "invalid_action"}, status=400)

        module = self._normalize_extension(cog)
        if not module:
            return web.json_response({"error": "unknown_cog"}, status=404)

        payload = {}
        try:
            if request.can_read_body:
                payload = await request.json()
        except Exception:
            payload = {}

        sync = bool(payload.get("sync", True))

        # Reloading/unloading the dashboard from itself can kill the request. Do it asynchronously.
        if module == __name__ and action in {"reload", "unload"}:
            async def do_later():
                async with self._cog_action_lock:
                    await self._run_extension_action(action, module)
                    if sync:
                        await self._sync_commands()

            asyncio.create_task(do_later())
            return web.json_response({"accepted": True, "module": module, "action": action}, status=202)

        async with self._cog_action_lock:
            result = await self._run_extension_action(action, module)
            sync_result = {"ok": True}
            if sync and result.get("ok"):
                sync_result = await self._sync_commands()

        return web.json_response(
            {
                "action": action,
                "result": result,
                "synced": sync_result,
                "loaded_extensions": sorted(list(self.bot.extensions.keys())),
                "loaded_cogs": sorted(list(self.bot.cogs.keys())),
            }
        )

    async def _handle_cogs_bulk_action(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return web.json_response({"error": "unauthorized"}, status=401)

        action = request.match_info["action"]
        if action not in {"load_all", "unload_all", "reload_all"}:
            return web.json_response({"error": "invalid_action"}, status=400)

        payload = {}
        try:
            if request.can_read_body:
                payload = await request.json()
        except Exception:
            payload = {}

        sync = bool(payload.get("sync", True))
        include_dashboard = bool(payload.get("include_dashboard", False))

        available = self._list_available_extensions()
        targets = available
        if not include_dashboard:
            targets = [m for m in targets if m != __name__]

        if action == "unload_all":
            targets = [m for m in targets if m in self.bot.extensions]
        elif action == "load_all":
            targets = [m for m in targets if m not in self.bot.extensions]

        op = {"load_all": "load", "unload_all": "unload", "reload_all": "reload"}[action]
        results: list[dict] = []

        async with self._cog_action_lock:
            for module in targets:
                results.append(await self._run_extension_action(op, module))

            sync_result = {"ok": True}
            if sync:
                sync_result = await self._sync_commands()

        ok_count = sum(1 for r in results if r.get("ok"))
        return web.json_response(
            {
                "action": action,
                "operation": op,
                "results": results,
                "ok": ok_count,
                "failed": len(results) - ok_count,
                "synced": sync_result,
                "loaded_extensions": sorted(list(self.bot.extensions.keys())),
                "loaded_cogs": sorted(list(self.bot.cogs.keys())),
            }
        )
    
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
                data["reasoning"] = player.current.reasoning
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
            d["id"] = str(d["id"])
            d["formatted_id"] = d["id"]
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

    
    async def _handle_user_detail(self, request: web.Request) -> web.Response:
        """Get detailed info for a single user."""
        user_id = int(request.match_info["user_id"])
        if not hasattr(self.bot, "db"):
            return web.json_response({"error": "No database"}, status=503)

        # Basic user info
        user = await self.bot.db.fetch_one(
            "SELECT id, username, created_at, last_active, is_banned, opted_out FROM users WHERE id = ?",
            (user_id,),
        )
        if not user:
            return web.json_response({"error": "User not found"}, status=404)

        user_data = dict(user)
        user_data["id"] = str(user_data["id"])
        for key in ("created_at", "last_active"):
            val = user_data.get(key)
            if val and hasattr(val, "isoformat"):
                user_data[key] = val.isoformat()

        # Activity stats
        plays_row = await self.bot.db.fetch_one(
            "SELECT COUNT(*) as count FROM playback_history WHERE for_user_id = ?",
            (user_id,),
        )
        reactions_row = await self.bot.db.fetch_one(
            "SELECT COUNT(*) as count FROM song_reactions WHERE user_id = ?",
            (user_id,),
        )
        playlists_row = await self.bot.db.fetch_one(
            "SELECT COUNT(*) as count FROM imported_playlists WHERE user_id = ?",
            (user_id,),
        )

        # Recent songs requested
        recent_songs = await self.bot.db.fetch_all(
            """SELECT s.title, s.artist_name, ph.played_at, ph.discovery_source
               FROM playback_history ph
               JOIN songs s ON ph.song_id = s.id
               WHERE ph.for_user_id = ?
               ORDER BY ph.played_at DESC LIMIT 10""",
            (user_id,),
        )
        songs_data = []
        for s in recent_songs:
            d = dict(s)
            if d.get("played_at") and hasattr(d["played_at"], "isoformat"):
                d["played_at"] = d["played_at"].isoformat()
            songs_data.append(d)

        # Reactions (liked/disliked songs)
        liked_songs = await self.bot.db.fetch_all(
            """SELECT s.title, s.artist_name, sr.reaction
               FROM song_reactions sr
               JOIN songs s ON sr.song_id = s.id
               WHERE sr.user_id = ?
               ORDER BY sr.created_at DESC LIMIT 20""",
            (user_id,),
        )

        # Top preferences
        from src.database.crud import PreferenceCRUD
        pref_crud = PreferenceCRUD(self.bot.db)
        preferences = await pref_crud.get_all_preferences(user_id)

        # Imported playlists
        playlists = await self.bot.db.fetch_all(
            "SELECT platform, playlist_name, track_count, imported_at FROM imported_playlists WHERE user_id = ? ORDER BY imported_at DESC LIMIT 10",
            (user_id,),
        )
        playlists_data = []
        for p in playlists:
            d = dict(p)
            if d.get("imported_at") and hasattr(d["imported_at"], "isoformat"):
                d["imported_at"] = d["imported_at"].isoformat()
            playlists_data.append(d)

        return web.json_response({
            "user": user_data,
            "stats": {
                "plays": plays_row["count"] if plays_row else 0,
                "reactions": reactions_row["count"] if reactions_row else 0,
                "playlists": playlists_row["count"] if playlists_row else 0,
            },
            "recent_songs": songs_data,
            "liked_songs": [dict(s) for s in liked_songs],
            "preferences": preferences,
            "imported_playlists": playlists_data,
        })

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

    async def _handle_services_list(self, request: web.Request) -> web.Response:
        """Get list of services and their status."""
        import psutil
        import time
        
        process = psutil.Process()
        uptime_seconds = time.time() - process.create_time()
        
        # Format uptime
        days = int(uptime_seconds // 86400)
        hours = int((uptime_seconds % 86400) // 3600)
        mins = int((uptime_seconds % 3600) // 60)
        if days > 0:
            uptime_str = f"{days}d {hours}h {mins}m"
        elif hours > 0:
            uptime_str = f"{hours}h {mins}m"
        else:
            uptime_str = f"{mins}m"
        
        services = [
            {
                "id": "bot",
                "name": "Discord Bot",
                "description": "Core Discord bot handling commands and audio playback",
                "status": "online" if self.bot.is_ready() else "starting",
                "uptime": uptime_str,
                "restartable": True,
            },
            {
                "id": "dashboard",
                "name": "Dashboard API",
                "description": "Web API for the dashboard",
                "status": "online",
                "uptime": uptime_str,
                "restartable": False,
            },
        ]
        
        return web.json_response({"services": services})
    
    async def _handle_service_restart(self, request: web.Request) -> web.Response:
        """Restart a service."""
        if not self._is_admin(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        
        service_id = request.match_info["service_id"]
        
        if service_id == "bot":
            log.event(Category.SYSTEM, "bot_restart_requested", source="dashboard_api")
            
            # Try Docker restart first
            import os
            import socket
            import aiohttp
            
            if os.path.exists("/var/run/docker.sock"):
                try:
                    hostname = socket.gethostname()
                    connector = aiohttp.UnixConnector(path="/var/run/docker.sock")
                    async with aiohttp.ClientSession(connector=connector) as session:
                        url = f"http://localhost/containers/{hostname}/restart"
                        async with session.post(url) as resp:
                            if resp.status == 204:
                                log.event(Category.SYSTEM, "docker_restart_sent")
                                return web.json_response({"status": "restarting", "method": "docker"})
                            else:
                                text = await resp.text()
                                log.warning_cat(Category.SYSTEM, f"Docker restart failed: {resp.status} - {text}")
                except Exception as e:
                    log.warning_cat(Category.SYSTEM, f"Failed to restart via Docker socket: {e}")
            
            # Fallback to process exit (supervisor/docker will restart)
            async def do_restart():
                await asyncio.sleep(0.5)
                try:
                    await self.bot.close()
                except Exception:
                    pass
                os._exit(0)
            
            asyncio.create_task(do_restart())
            return web.json_response({"status": "restarting", "method": "process_exit"})
        
        elif service_id == "dashboard":
            return web.json_response({"error": "Dashboard cannot restart itself"}, status=400)
        
        else:
            return web.json_response({"error": "Unknown service"}, status=404)


async def setup(bot: commands.Bot):
    from src.config import config
    await bot.add_cog(DashboardCog(bot, config.WEB_HOST, config.WEB_PORT))
