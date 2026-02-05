"""
Web Interface Cog for Vexo
Provides a web dashboard for viewing logs and managing settings.
"""
import asyncio
import logging
import time
from pathlib import Path
import discord
from discord.ext import commands
import aiosqlite
import aiohttp
from aiohttp import web
import json
from datetime import datetime
from collections import deque
from typing import Optional

from utils.logger import set_logger
from utils.spotify import check_connectivity as spotify_check_connectivity

logger = set_logger(logging.getLogger('MusicBot.WebServer'))


class LogHandler(logging.Handler):
    """Custom log handler that stores logs in memory for web streaming."""
    
    def __init__(self, max_logs=1000):
        super().__init__()
        self.logs = deque(maxlen=max_logs)
        self._seq = 0
        
    def emit(self, record):
        try:
            self._seq += 1
            log_entry = {
                'id': self._seq,
                'timestamp': datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S'),
                'level': record.levelname,
                'name': record.name,
                'message': self.format(record)
            }
            self.logs.append(log_entry)
        except Exception:
            self.handleError(record)


class WebServer(commands.Cog):
    """Web interface for logs and admin settings."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.app = web.Application()
        self.runner: Optional[web.AppRunner] = None
        self.site: Optional[web.TCPSite] = None
        self.port = 8080
        
        # Set up log handler
        self.log_handler = LogHandler(max_logs=1000)
        self.log_handler.setLevel(logging.INFO)
        self.log_handler.setFormatter(logging.Formatter("%(message)s"))

        self._install_log_handler()
        
        # Set up routes
        self.app.router.add_get('/', self.dashboard)
        self.app.router.add_get('/stats', self.dashboard)
        self.app.router.add_get('/logs/view', self.index)
        self.app.router.add_get('/logs/watchtower', self.watchtower_logs_view)
        self.app.router.add_get('/api/stats', self.api_stats)
        self.app.router.add_get('/api/users', self.api_users)
        self.app.router.add_get('/api/user/stats', self.api_user_stats)
        self.app.router.add_get('/api/logs/file', self.api_log_file)
        self.app.router.add_get('/api/docker/logs', self.api_docker_logs)
        self.app.router.add_get('/logs', self.get_logs)
        self.app.router.add_get('/logs/stream', self.stream_logs)
        self.app.router.add_get('/settings', self.get_settings)
        self.app.router.add_post('/settings', self.update_settings)
        self.app.router.add_get('/status', self.get_status)
        self.app.router.add_get('/spotify/test', self.spotify_test)

        # Simple cache to avoid hammering the Spotify API from repeated clicks/refreshes
        self._spotify_check_cache = {"at": 0.0, "result": None}
        
        # Start web server
        self.bot.loop.create_task(self.start_server())

    def _install_log_handler(self):
        """Attach the in-memory log handler so the web UI can stream logs."""
        root_logger = logging.getLogger()
        if self.log_handler not in root_logger.handlers:
            root_logger.addHandler(self.log_handler)

        # Backfill: attach to already-initialized loggers (created before this cog loaded).
        for existing in logging.Logger.manager.loggerDict.values():
            if isinstance(existing, logging.Logger) and self.log_handler not in existing.handlers:
                existing.addHandler(self.log_handler)

        if self.log_handler not in logger.handlers:
            logger.addHandler(self.log_handler)
        
    async def start_server(self):
        """Start the aiohttp web server."""
        try:
            self.runner = web.AppRunner(self.app)
            await self.runner.setup()
            self.site = web.TCPSite(self.runner, '0.0.0.0', self.port)
            await self.site.start()
            logger.info(f"🌐 Web interface started on http://0.0.0.0:{self.port}")
        except Exception as e:
            logger.error(f"Failed to start web server: {e}")
    
    async def stop_server(self):
        """Stop the web server."""
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()
        logger.info("🌐 Web interface stopped")
    
    def _resolve_user_name(self, user_id: int) -> str:
        user = self.bot.get_user(int(user_id)) if user_id else None
        if user:
            return str(user)
        for guild in self.bot.guilds:
            member = guild.get_member(int(user_id))
            if member:
                return member.display_name
        return f"User {user_id}"

    async def dashboard(self, request):
        """Serve the main stats dashboard page."""
        html = """
<!DOCTYPE html>
<html>
<head>
    <title>Vexo Dashboard</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #0D0D0D;
            color: #fff;
            padding: 20px;
        }
        .container { max-width: 1400px; margin: 0 auto; }
        h1 {
            color: #00D4FF;
            margin-bottom: 10px;
            font-size: 2.5em;
            text-shadow: 0 0 10px rgba(0, 212, 255, 0.5);
        }
        .subtitle {
            color: #888;
            margin-bottom: 26px;
            font-size: 1.1em;
        }
        .grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin-bottom: 20px;
        }
        .grid-3 {
            display: grid;
            grid-template-columns: 1fr 1fr 1fr;
            gap: 20px;
            margin-bottom: 20px;
        }
        .card {
            background: #1a1a1a;
            border: 1px solid #333;
            border-radius: 8px;
            padding: 20px;
        }
        .card h2 {
            color: #00D4FF;
            margin-bottom: 15px;
            font-size: 1.3em;
        }
        .status-item {
            display: flex;
            justify-content: space-between;
            padding: 10px 0;
            border-bottom: 1px solid #333;
            gap: 14px;
        }
        .status-item:last-child { border-bottom: none; }
        .status-label { color: #888; }
        .status-value {
            color: #00D4FF;
            font-weight: bold;
            text-align: right;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .btn {
            background: #00D4FF;
            color: #000;
            border: none;
            padding: 10px 16px;
            border-radius: 5px;
            cursor: pointer;
            font-weight: bold;
            margin-right: 10px;
        }
        .btn:hover { background: #00b8e6; }
        .btn.secondary {
            background: #1a1a1a;
            border: 1px solid #333;
            color: #00D4FF;
        }
        .btn.secondary:hover { background: #111; }
        .filter-row {
            margin-top: 10px;
            display: flex;
            gap: 10px;
            align-items: center;
            flex-wrap: wrap;
        }
        select {
            background: #0f0f0f;
            border: 1px solid #333;
            color: #fff;
            padding: 8px 10px;
            border-radius: 5px;
        }
        h3 {
            margin-top: 14px;
            margin-bottom: 8px;
            font-size: 1.05em;
            color: #00FF88;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 0.95em;
        }
        th, td {
            padding: 10px 8px;
            border-bottom: 1px solid #333;
            vertical-align: top;
        }
        th { color: #00D4FF; text-align: left; font-weight: 600; }
        td { color: #ddd; }
        .muted { color: #888; }
        .error { color: #FF3366; }
        code { color: #00FF88; }
        @media (max-width: 900px) {
            .grid { grid-template-columns: 1fr; }
            .grid-3 { grid-template-columns: 1fr; }
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🎵 Vexo Dashboard</h1>
        <p class="subtitle">Insights about your data, plus tools for monitoring and administration.</p>

        <div class="grid">
            <div class="card">
                <h2>📊 Bot Status</h2>
                <div id="status">Loading...</div>
            </div>
            <div class="card">
                <h2>🎛️ Quick Actions</h2>
                <div id="quickActions" style="padding: 10px 0;">
                    <button class="btn" onclick="window.location.href='/logs/view'">📝 Vexo Logs</button>
                    <button class="btn" onclick="window.location.href='/logs/watchtower'">🧭 Watchtower Logs</button>
                    <button class="btn secondary" onclick="window.location.href='/settings'">⚙️ Settings</button>
                </div>
                <div id="spotifyTestResult" style="padding: 6px 0; color: #888;"></div>
            </div>
        </div>

        <div class="grid-3">
            <div class="card">
                <h2>📦 Data Overview</h2>
                <div id="overview" class="muted">Loading...</div>
            </div>
            <div class="card">
                <h2>🎯 Decision Model</h2>
                <div id="decisionModel" class="muted">Loading...</div>
            </div>
            <div class="card">
                <h2>🎛️ Runtime</h2>
                <div id="runtime" class="muted">Loading...</div>
            </div>
        </div>

        <div class="grid">
            <div class="card">
                <h2>🔥 Most Played Tracks</h2>
                <div id="topPlayed" class="muted">Loading...</div>
            </div>
            <div class="card">
                <h2>❤️ Most Liked Tracks</h2>
                <div id="topLiked" class="muted">Loading...</div>
            </div>
        </div>

        <div class="grid">
            <div class="card">
                <h2>👥 Users & Their Top Like</h2>
                <div id="userLikes" class="muted">Loading...</div>
            </div>
            <div class="card">
                <h2>⏱️ Recent Plays</h2>
                <div id="recentPlays" class="muted">Loading...</div>
            </div>
        </div>

        <div class="grid">
            <div class="card" style="grid-column: 1 / -1;">
                <h2>🔎 User Drilldown</h2>
                <div class="filter-row">
                    <label for="userSelect" class="status-label">User</label>
                    <select id="userSelect" onchange="onUserSelectChange()">
                        <option value="">Select a user...</option>
                    </select>
                    <span id="userSelectStatus" class="muted"></span>
                </div>
                <div id="userDrilldown" class="muted">Select a user to see their top liked and requested tracks.</div>
            </div>
        </div>
    </div>

    <script>
        const MAX_ROWS = 10;

        loadStatus();
        setInterval(loadStatus, 5000);

        loadStats();
        setInterval(loadStats, 15000);

        loadUserList();

        function loadStatus() {
            fetch('/status')
                .then(r => r.json())
                .then(data => {
                    document.getElementById('status').innerHTML = `
                        <div class="status-item">
                            <span class="status-label">Bot Name</span>
                            <span class="status-value">${escapeHtml(data.bot_name)}</span>
                        </div>
                        <div class="status-item">
                            <span class="status-label">Guilds</span>
                            <span class="status-value">${escapeHtml(String(data.guilds))}</span>
                        </div>
                        <div class="status-item">
                            <span class="status-label">Uptime</span>
                            <span class="status-value">${escapeHtml(data.uptime)}</span>
                        </div>
                        <div class="status-item">
                            <span class="status-label">Latency</span>
                            <span class="status-value">${escapeHtml(String(data.latency))}ms</span>
                        </div>
                    `;
                });
        }

        function loadStats() {
            fetch('/api/stats?limit=' + encodeURIComponent(String(MAX_ROWS)))
                .then(r => r.json())
                .then(data => {
                    if (!data || data.ok === false) {
                        const msg = data && data.error ? data.error : 'Unknown error';
                        document.getElementById('overview').innerHTML = `<div class="error">${escapeHtml(msg)}</div>`;
                        return;
                    }
                    renderOverview(data);
                    renderDecisionModel(data);
                    renderRuntime(data);
                    renderTopPlayed(data);
                    renderTopLiked(data);
                    renderUserLikes(data);
                    renderRecentPlays(data);
                })
                .catch(err => {
                    document.getElementById('overview').innerHTML = `<div class="error">Failed to load stats: ${escapeHtml(err)}</div>`;
                });
        }

        function renderOverview(data) {
            const o = data.overview || {};
            document.getElementById('overview').innerHTML = `
                <div class="status-item"><span class="status-label">Database</span><span class="status-value">${escapeHtml(o.db_path || 'Unknown')}</span></div>
                <div class="status-item"><span class="status-label">Plays</span><span class="status-value">${escapeHtml(String(o.total_plays ?? 0))}</span></div>
                <div class="status-item"><span class="status-label">Unique Tracks Played</span><span class="status-value">${escapeHtml(String(o.unique_played_tracks ?? 0))}</span></div>
                <div class="status-item"><span class="status-label">Preference Rows</span><span class="status-value">${escapeHtml(String(o.preference_rows ?? 0))}</span></div>
                <div class="status-item"><span class="status-label">Users w/ Preferences</span><span class="status-value">${escapeHtml(String(o.users_with_preferences ?? 0))}</span></div>
            `;
        }

        function renderDecisionModel(data) {
            const w = (data.config && data.config.discovery_weights) || {};
            document.getElementById('decisionModel').innerHTML = `
                <div class="muted">Autoplay uses per-user slots:</div>
                <ul class="muted" style="padding-left: 18px; margin-top: 8px;">
                    <li><b>Liked</b>: songs you upvoted</li>
                    <li><b>Discovery</b>: similar artists/keywords from the global pool</li>
                    <li><b>Recency filter</b>: avoids repeats in the last ~30 minutes</li>
                </ul>
                <div class="muted" style="margin-top: 10px;">Interaction weights:</div>
                <div class="status-item"><span class="status-label">Upvote</span><span class="status-value">${escapeHtml(String(w.upvote ?? ''))}</span></div>
                <div class="status-item"><span class="status-label">Downvote</span><span class="status-value">${escapeHtml(String(w.downvote ?? ''))}</span></div>
                <div class="status-item"><span class="status-label">Skip</span><span class="status-value">${escapeHtml(String(w.skip ?? ''))}</span></div>
                <div class="status-item"><span class="status-label">Request</span><span class="status-value">${escapeHtml(String(w.request ?? ''))}</span></div>
            `;
        }

        function renderRuntime(data) {
            const r = data.runtime || {};
            const guilds = Array.isArray(r.guilds) ? r.guilds : [];
            const lines = guilds.map(g => {
                const name = g.guild_name || ('Guild ' + String(g.guild_id));
                const autoplay = g.is_autoplay ? 'ON' : 'OFF';
                const current = g.current && g.current.title ? ` — ${escapeHtml(g.current.title)}` : '';
                return `<div class="status-item"><span class="status-label">${escapeHtml(name)}</span><span class="status-value">${escapeHtml(autoplay)}${current}</span></div>`;
            }).join('');
            document.getElementById('runtime').innerHTML = guilds.length
                ? lines
                : '<div class="muted">No active guild state yet.</div>';
        }

        function renderTopPlayed(data) {
            const rows = Array.isArray(data.top_played_tracks) ? data.top_played_tracks : [];
            if (!rows.length) {
                document.getElementById('topPlayed').innerHTML = '<div class="muted">No playback history yet.</div>';
                return;
            }
            document.getElementById('topPlayed').innerHTML = tableHtml(
                ['Track', 'Plays'],
                rows.map(r => [
                    `${escapeHtml(r.artist || 'Unknown')} — ${escapeHtml(r.song || 'Unknown')}`,
                    escapeHtml(String(r.plays ?? 0)),
                ])
            );
        }

        function renderTopLiked(data) {
            const rows = Array.isArray(data.top_liked_tracks) ? data.top_liked_tracks : [];
            if (!rows.length) {
                document.getElementById('topLiked').innerHTML = '<div class="muted">No likes yet.</div>';
                return;
            }
            document.getElementById('topLiked').innerHTML = tableHtml(
                ['Track', 'Net', 'Likes'],
                rows.map(r => [
                    `${escapeHtml(r.artist || 'Unknown')} — ${escapeHtml(r.song || 'Unknown')}`,
                    escapeHtml(String(r.net_score ?? 0)),
                    escapeHtml(String(r.likes ?? 0)),
                ])
            );
        }

        function renderUserLikes(data) {
            const rows = Array.isArray(data.users_top_like) ? data.users_top_like : [];
            if (!rows.length) {
                document.getElementById('userLikes').innerHTML = '<div class="muted">No user preferences yet.</div>';
                return;
            }
            document.getElementById('userLikes').innerHTML = tableHtml(
                ['User', 'Top Like', 'Score'],
                rows.map(r => [
                    escapeHtml(r.user_name || String(r.user_id || 'Unknown')),
                    `${escapeHtml(r.artist || 'Unknown')} — ${escapeHtml(r.song || 'Unknown')}`,
                    escapeHtml(String(r.score ?? 0)),
                ])
            );
        }

        function renderRecentPlays(data) {
            const rows = Array.isArray(data.recent_plays) ? data.recent_plays : [];
            if (!rows.length) {
                document.getElementById('recentPlays').innerHTML = '<div class="muted">No recent plays.</div>';
                return;
            }
            document.getElementById('recentPlays').innerHTML = tableHtml(
                ['When', 'Track'],
                rows.map(r => [
                    escapeHtml(r.timestamp || ''),
                    `${escapeHtml(r.artist || 'Unknown')} — ${escapeHtml(r.song || 'Unknown')}`,
                ])
            );
        }

        function tableHtml(headers, rows) {
            const thead = headers.map(h => `<th>${escapeHtml(h)}</th>`).join('');
            const tbody = rows.map(cols => `<tr>${cols.map(c => `<td>${c}</td>`).join('')}</tr>`).join('');
            return `<table><thead><tr>${thead}</tr></thead><tbody>${tbody}</tbody></table>`;
        }

        function loadUserList() {
            const status = document.getElementById('userSelectStatus');
            if (status) status.textContent = 'Loading users...';

            fetch('/api/users')
                .then(r => r.json())
                .then(data => {
                    if (!data || data.ok === false) {
                        const msg = data && data.error ? data.error : 'Unknown error';
                        if (status) status.textContent = msg;
                        return;
                    }

                    const users = Array.isArray(data.users) ? data.users : [];
                    const select = document.getElementById('userSelect');
                    const current = select.value;
                    select.innerHTML = '<option value="">Select a user...</option>';

                    users.forEach(u => {
                        const id = String(u.user_id ?? '');
                        if (!id) return;
                        const name = u.user_name ? String(u.user_name) : ('User ' + id);
                        const opt = document.createElement('option');
                        opt.value = id;
                        opt.textContent = name + ' (' + id + ')';
                        select.appendChild(opt);
                    });

                    if (current) {
                        select.value = current;
                    }

                    if (status) status.textContent = users.length ? (users.length + ' users') : 'No users yet';
                })
                .catch(err => {
                    if (status) status.textContent = 'Failed to load users: ' + err;
                });
        }

        function onUserSelectChange() {
            const select = document.getElementById('userSelect');
            const userId = select.value;
            const out = document.getElementById('userDrilldown');

            if (!userId) {
                out.innerHTML = 'Select a user to see their top liked and requested tracks.';
                return;
            }

            out.innerHTML = 'Loading user stats...';

            fetch('/api/user/stats?user_id=' + encodeURIComponent(userId) + '&limit=' + encodeURIComponent(String(MAX_ROWS)))
                .then(r => r.json())
                .then(data => {
                    if (!data || data.ok === false) {
                        const msg = data && data.error ? data.error : 'Unknown error';
                        out.innerHTML = `<div class="error">${escapeHtml(msg)}</div>`;
                        return;
                    }

                    const user = data.user || {};
                    const liked = Array.isArray(data.top_liked_tracks) ? data.top_liked_tracks : [];
                    const requested = Array.isArray(data.top_requested_tracks) ? data.top_requested_tracks : [];

                    let html = `<div class="muted">User: <b>${escapeHtml(user.user_name || ('User ' + String(user.user_id || userId)))}</b> (${escapeHtml(String(user.user_id || userId))})</div>`;

                    html += '<h3>Most Liked Tracks</h3>';
                    if (!liked.length) {
                        html += '<div class="muted">No liked tracks recorded yet.</div>';
                    } else {
                        html += tableHtml(
                            ['Track', 'Score'],
                            liked.map(r => [
                                `${escapeHtml(r.artist || 'Unknown')} — ${escapeHtml(r.song || 'Unknown')}`,
                                escapeHtml(String(r.score ?? 0)),
                            ])
                        );
                    }

                    html += '<h3>Most Requested Tracks</h3>';
                    if (!requested.length) {
                        html += '<div class="muted">No request history recorded yet.</div>';
                    } else {
                        html += tableHtml(
                            ['Track', 'Plays', 'Last Played'],
                            requested.map(r => [
                                `${escapeHtml(r.artist || 'Unknown')} — ${escapeHtml(r.song || 'Unknown')}`,
                                escapeHtml(String(r.plays ?? 0)),
                                escapeHtml(String(r.last_played || '')),
                            ])
                        );
                    }

                    out.innerHTML = html;
                })
                .catch(err => {
                    out.innerHTML = `<div class="error">Failed to load user stats: ${escapeHtml(err)}</div>`;
                });
        }

        function testSpotify(btn) {
            const out = document.getElementById('spotifyTestResult');
            if (out) {
                out.textContent = 'Testing Spotify...';
                out.style.color = '#888';
            }

            const originalText = btn ? btn.textContent : '';
            if (btn) {
                btn.disabled = true;
                btn.textContent = 'Testing...';
            }

            fetch('/spotify/test')
                .then(r => r.json())
                .then(data => {
                    if (!out) return;

                    if (data.ok) {
                        let msg = `Spotify OK (${data.latency_ms}ms${data.cached ? ', cached' : ''})`;
                        if (data.sample && (data.sample.artist || data.sample.track)) {
                            const artist = data.sample.artist || '';
                            const track = data.sample.track || '';
                            const sample = [artist, track].filter(Boolean).join(' - ');
                            if (sample) msg += ` — ${sample}`;
                        }
                        if (data.checked_at) msg += ` @ ${data.checked_at}`;
                        out.textContent = msg;
                        out.style.color = '#00FF88';
                    } else {
                        const err = data.error || 'Unknown error';
                        out.textContent = `Spotify FAIL: ${err}${data.checked_at ? ' @ ' + data.checked_at : ''}`;
                        out.style.color = '#FF3366';
                    }
                })
                .catch(err => {
                    if (!out) return;
                    out.textContent = `Spotify test error: ${err}`;
                    out.style.color = '#FF3366';
                })
                .finally(() => {
                    if (btn) {
                        btn.disabled = false;
                        btn.textContent = originalText || 'Test Spotify';
                    }
                });
        }

        function escapeHtml(text) {
            const s = String(text ?? '');
            return s.replace(/[&<>"']/g, (ch) => ({
                '&': '&amp;',
                '<': '&lt;',
                '>': '&gt;',
                '"': '&quot;',
                "'": '&#39;',
            }[ch]));
        }

        const quickActions = document.getElementById('quickActions');
        if (quickActions) {
            const btn = document.createElement('button');
            btn.className = 'btn';
            btn.textContent = 'Test Spotify';
            btn.onclick = () => testSpotify(btn);
            quickActions.appendChild(btn);
        }
    </script>
</body>
</html>
        """
        return web.Response(text=html, content_type='text/html')

    async def watchtower_logs_view(self, request):
        """Serve the watchtower logs page."""
        html = """
<!DOCTYPE html>
<html>
<head>
    <title>Watchtower Logs - Vexo</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #0D0D0D;
            color: #fff;
            padding: 20px;
        }
        .container { max-width: 1200px; margin: 0 auto; }
        h1 {
            color: #00D4FF;
            margin-bottom: 10px;
            font-size: 2.2em;
            text-shadow: 0 0 10px rgba(0, 212, 255, 0.5);
        }
        .subtitle { color: #888; margin-bottom: 18px; }
        .btn {
            background: #00D4FF;
            color: #000;
            border: none;
            padding: 10px 16px;
            border-radius: 5px;
            cursor: pointer;
            font-weight: bold;
            margin-right: 10px;
        }
        .btn:hover { background: #00b8e6; }
        .btn.secondary {
            background: #1a1a1a;
            border: 1px solid #333;
            color: #00D4FF;
        }
        .btn.secondary:hover { background: #111; }
        pre {
            background: #000;
            border: 1px solid #333;
            border-radius: 8px;
            padding: 15px;
            height: 700px;
            overflow-y: auto;
            font-family: 'Consolas', 'Monaco', monospace;
            font-size: 0.9em;
            white-space: pre-wrap;
            word-break: break-word;
            margin-top: 14px;
        }
        .muted { color: #888; }
        .error { color: #FF3366; }
        code { color: #00FF88; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🧭 Watchtower Logs</h1>
        <p class="subtitle">Docker logs for the <code>watchtower</code> container.</p>

        <div style="margin-bottom: 10px;">
            <button class="btn secondary" onclick="window.location.href='/'">← Dashboard</button>
            <button class="btn secondary" onclick="window.location.href='/logs/view'">📝 Vexo Logs</button>
            <button class="btn" onclick="refreshLogs()">🔄 Refresh</button>
            <button class="btn" onclick="downloadLogs()">💾 Download</button>
            <span id="status" class="muted"></span>
        </div>

        <pre id="out">Loading...</pre>

        <p class="muted" style="margin-top: 10px;">
            If this shows <span class="error">Docker socket not available</span>, mount <code>/var/run/docker.sock</code> into the vexo container.
        </p>
    </div>

    <script>
        refreshLogs();

        function refreshLogs() {
            const status = document.getElementById('status');
            const out = document.getElementById('out');
            status.textContent = 'Refreshing...';
            fetch('/api/docker/logs?container=watchtower&tail=500')
                .then(async r => {
                    const body = await r.text();
                    if (!r.ok) {
                        status.textContent = 'Unavailable';
                        out.textContent = body || ('HTTP ' + r.status);
                        return;
                    }
                    status.textContent = 'OK';
                    out.textContent = body || '(no logs)';
                    out.scrollTop = out.scrollHeight;
                })
                .catch(err => {
                    status.textContent = 'Error';
                    out.textContent = 'Failed to load logs: ' + err;
                });
        }

        function downloadLogs() {
            fetch('/api/docker/logs?container=watchtower&tail=5000')
                .then(async r => {
                    const body = await r.text();
                    const blob = new Blob([body], { type: 'text/plain' });
                    const a = document.createElement('a');
                    a.href = URL.createObjectURL(blob);
                    a.download = `watchtower-logs-${new Date().toISOString()}.txt`;
                    a.click();
                });
        }
    </script>
</body>
</html>
        """
        return web.Response(text=html, content_type='text/html')

    async def index(self, request):
        """Serve the Vexo live logs page."""
        html = """
<!DOCTYPE html>
<html>
<head>
    <title>Vexo Dashboard</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #0D0D0D;
            color: #fff;
            padding: 20px;
        }
        .container { max-width: 1400px; margin: 0 auto; }
        h1 {
            color: #00D4FF;
            margin-bottom: 10px;
            font-size: 2.5em;
            text-shadow: 0 0 10px rgba(0, 212, 255, 0.5);
        }
        .subtitle {
            color: #888;
            margin-bottom: 30px;
            font-size: 1.1em;
        }
        .grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin-bottom: 20px;
        }
        .card {
            background: #1a1a1a;
            border: 1px solid #333;
            border-radius: 8px;
            padding: 20px;
        }
        .card h2 {
            color: #00D4FF;
            margin-bottom: 15px;
            font-size: 1.3em;
        }
        .status-item {
            display: flex;
            justify-content: space-between;
            padding: 10px 0;
            border-bottom: 1px solid #333;
        }
        .status-item:last-child { border-bottom: none; }
        .status-label { color: #888; }
        .status-value {
            color: #00D4FF;
            font-weight: bold;
        }
        #logs {
            background: #000;
            border: 1px solid #333;
            border-radius: 8px;
            padding: 15px;
            height: 600px;
            overflow-y: auto;
            font-family: 'Consolas', 'Monaco', monospace;
            font-size: 0.9em;
        }
        .log-entry {
            padding: 4px 0;
            border-bottom: 1px solid #1a1a1a;
        }
        .log-time { color: #666; }
        .log-level {
            padding: 2px 6px;
            border-radius: 3px;
            font-weight: bold;
            margin: 0 8px;
        }
        .log-level.DEBUG { background: #555; color: #aaa; }
        .log-level.INFO { background: #0066cc; color: #fff; }
        .log-level.WARNING { background: #ff9900; color: #000; }
        .log-level.ERROR { background: #cc0000; color: #fff; }
        .log-level.CRITICAL { background: #ff0000; color: #fff; }
        .log-name { color: #00ff88; }
        .log-message { color: #00d4ff; }
        .btn {
            background: #00D4FF;
            color: #000;
            border: none;
            padding: 10px 20px;
            border-radius: 5px;
            cursor: pointer;
            font-weight: bold;
            margin-right: 10px;
        }
        .btn:hover { background: #00b8e6; }
        .controls {
            margin-top: 15px;
            display: flex;
            gap: 10px;
        }
        .filter-row {
            margin-top: 10px;
            display: flex;
            gap: 10px;
            align-items: center;
        }
        select {
            background: #0f0f0f;
            border: 1px solid #333;
            color: #fff;
            padding: 8px 10px;
            border-radius: 5px;
        }
        @media (max-width: 768px) {
            .grid { grid-template-columns: 1fr; }
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>📝 Vexo Logs</h1>
        <p class="subtitle">Live logs and monitoring</p>
        
        <div class="grid">
            <div class="card">
                <h2>📊 Bot Status</h2>
                <div id="status">Loading...</div>
            </div>
            <div class="card">
                <h2>🎛️ Quick Actions</h2>
                <div id="quickActions" style="padding: 10px 0;">
                    <button class="btn" onclick="window.location.href='/'">← Dashboard</button>
                    <button class="btn" onclick="window.location.href='/logs/watchtower'">🧭 Watchtower Logs</button>
                    <button class="btn" onclick="window.location.href='/settings'">⚙️ Settings</button>
                    <button class="btn" onclick="clearLogs()">🗑️ Clear Logs</button>
                    <button class="btn" onclick="toggleStream(this)">⏸️ Pause Stream</button>
                </div>
            </div>
        </div>
        
        <div class="card">
            <h2>📜 Live Logs</h2>
            <div class="filter-row">
                <label for="moduleFilter" class="status-label">Module</label>
                <select id="moduleFilter" onchange="applyFilter()">
                    <option value="">All Modules</option>
                </select>
            </div>
            <div id="logs"></div>
            <div class="controls">
                <button class="btn" onclick="scrollToBottom()">⬇️ Scroll to Bottom</button>
                <button class="btn" onclick="downloadLogs()">💾 Download Logs</button>
            </div>
        </div>
    </div>
    
    <script>
        let streaming = true;
        let eventSource = null;
        let allLogs = [];
        let moduleSet = new Set();
        const MAX_UI_LOGS = 1000;
        let pendingHtml = '';
        let flushHandle = null;

        // Add Spotify connectivity test button
        const quickActions = document.getElementById('quickActions');
        if (quickActions) {
            const btn = document.createElement('button');
            btn.className = 'btn';
            btn.textContent = 'Test Spotify';
            btn.onclick = () => testSpotify(btn);
            quickActions.appendChild(btn);

            const result = document.createElement('div');
            result.id = 'spotifyTestResult';
            result.style.padding = '6px 0';
            result.style.color = '#888';
            quickActions.parentElement.appendChild(result);
        }
        
        // Load initial logs
        fetch('/logs')
            .then(r => r.json())
            .then(data => {
                allLogs = data.logs;
                if (allLogs.length > MAX_UI_LOGS) {
                    allLogs = allLogs.slice(-MAX_UI_LOGS);
                }
                updateModuleOptions();
                renderLogs();
                scrollToBottom();
            });
        
        // Load status
        loadStatus();
        setInterval(loadStatus, 5000);
        
        // Start streaming logs
        startStream();
        
        function startStream() {
            if (eventSource) eventSource.close();
            eventSource = new EventSource('/logs/stream');
            eventSource.onmessage = function(event) {
                if (!streaming) return;
                const log = JSON.parse(event.data);
                allLogs.push(log);
                if (allLogs.length > MAX_UI_LOGS) {
                    allLogs.splice(0, allLogs.length - MAX_UI_LOGS);
                }
                if (!moduleSet.has(log.name)) {
                    moduleSet.add(log.name);
                    updateModuleOptions();
                }
                appendLog(log);
            };
        }
        
        function loadStatus() {
            fetch('/status')
                .then(r => r.json())
                .then(data => {
                    document.getElementById('status').innerHTML = `
                        <div class="status-item">
                            <span class="status-label">Bot Name</span>
                            <span class="status-value">${data.bot_name}</span>
                        </div>
                        <div class="status-item">
                            <span class="status-label">Guilds</span>
                            <span class="status-value">${data.guilds}</span>
                        </div>
                        <div class="status-item">
                            <span class="status-label">Uptime</span>
                            <span class="status-value">${data.uptime}</span>
                        </div>
                        <div class="status-item">
                            <span class="status-label">Latency</span>
                            <span class="status-value">${data.latency}ms</span>
                        </div>
                    `;
                });
        }
        
        function appendLog(log) {
            const selected = document.getElementById('moduleFilter').value;
            if (selected && log.name !== selected) return;
            pendingHtml += formatLog(log);
            scheduleFlush();
        }

        function scheduleFlush() {
            if (flushHandle !== null) return;
            flushHandle = setTimeout(() => {
                flushHandle = null;
                flushPending();
            }, 150);
        }

        function flushPending() {
            if (!pendingHtml) return;
            const logsDiv = document.getElementById('logs');
            const nearBottom = (logsDiv.scrollHeight - logsDiv.scrollTop) < 700;
            logsDiv.insertAdjacentHTML('beforeend', pendingHtml);
            pendingHtml = '';
            while (logsDiv.children.length > MAX_UI_LOGS) {
                logsDiv.removeChild(logsDiv.firstElementChild);
            }
            if (nearBottom) scrollToBottom();
        }

        function renderLogs() {
            const logsDiv = document.getElementById('logs');
            const selected = document.getElementById('moduleFilter').value;
            const filtered = selected
                ? allLogs.filter(l => l.name === selected)
                : allLogs;
            logsDiv.innerHTML = filtered.map(formatLog).join('');
            pendingHtml = '';
        }

        function applyFilter() {
            renderLogs();
            scrollToBottom();
        }

        function updateModuleOptions() {
            moduleSet = new Set(allLogs.map(l => l.name));
            const select = document.getElementById('moduleFilter');
            const current = select.value;
            select.innerHTML = '<option value="">All Modules</option>';
            Array.from(moduleSet).sort().forEach(name => {
                const opt = document.createElement('option');
                opt.value = name;
                opt.textContent = name;
                if (name === current) opt.selected = true;
                select.appendChild(opt);
            });
        }
        
        function formatLog(log) {
            return `<div class="log-entry">
                <span class="log-time">${log.timestamp}</span>
                <span class="log-level ${log.level}">${log.level}</span>
                <span class="log-name">${log.name}</span>
                <span class="log-message">${escapeHtml(log.message)}</span>
            </div>`;
        }
        
        function escapeHtml(text) {
            const s = String(text ?? '');
            return s.replace(/[&<>"']/g, (ch) => ({
                '&': '&amp;',
                '<': '&lt;',
                '>': '&gt;',
                '"': '&quot;',
                "'": '&#39;',
            }[ch]));
        }
        
        function scrollToBottom() {
            const logsDiv = document.getElementById('logs');
            logsDiv.scrollTop = logsDiv.scrollHeight;
        }
        
        function testSpotify(btn) {
            const out = document.getElementById('spotifyTestResult');
            if (out) {
                out.textContent = 'Testing Spotify...';
                out.style.color = '#888';
            }

            const originalText = btn ? btn.textContent : '';
            if (btn) {
                btn.disabled = true;
                btn.textContent = 'Testing...';
            }

            fetch('/spotify/test')
                .then(r => r.json())
                .then(data => {
                    if (!out) return;

                    if (data.ok) {
                        let msg = `Spotify OK (${data.latency_ms}ms${data.cached ? ', cached' : ''})`;
                        if (data.sample && (data.sample.artist || data.sample.track)) {
                            const artist = data.sample.artist || '';
                            const track = data.sample.track || '';
                            const sample = [artist, track].filter(Boolean).join(' - ');
                            if (sample) msg += ` — ${sample}`;
                        }
                        if (data.checked_at) msg += ` @ ${data.checked_at}`;
                        out.textContent = msg;
                        out.style.color = '#00FF88';
                    } else {
                        const err = data.error || 'Unknown error';
                        out.textContent = `Spotify FAIL: ${err}${data.checked_at ? ' @ ' + data.checked_at : ''}`;
                        out.style.color = '#FF3366';
                    }
                })
                .catch(err => {
                    if (!out) return;
                    out.textContent = `Spotify test error: ${err}`;
                    out.style.color = '#FF3366';
                })
                .finally(() => {
                    if (btn) {
                        btn.disabled = false;
                        btn.textContent = originalText || 'Test Spotify';
                    }
                });
        }

        function toggleStream(btn) {
            streaming = !streaming;
            btn.textContent = streaming ? '⏸️ Pause Stream' : '▶️ Resume Stream';
        }
        
        function clearLogs() {
            if (confirm('Clear all logs from view?')) {
                allLogs = [];
                moduleSet = new Set();
                updateModuleOptions();
                renderLogs();
            }
        }
        
        function downloadLogs() {
            fetch('/logs')
                .then(r => r.json())
                .then(data => {
                    const text = data.logs.map(l => 
                        `${l.timestamp} ${l.level} ${l.name} ${l.message}`
                    ).join('\\n');
                    const blob = new Blob([text], { type: 'text/plain' });
                    const a = document.createElement('a');
                    a.href = URL.createObjectURL(blob);
                    a.download = `vexo-logs-${new Date().toISOString()}.txt`;
                    a.click();
                });
        }
    </script>
</body>
</html>
        """
        return web.Response(text=html, content_type='text/html')
    
    async def get_logs(self, request):
        """Get all logs as JSON."""
        return web.json_response({
            'logs': list(self.log_handler.logs)
        })

    async def api_log_file(self, request):
        """Tail logs from persisted files (data/vexo.log, data/watchtower.log)."""
        source = (request.query.get('source') or 'vexo').strip().lower()
        try:
            tail = int(request.query.get('tail') or '500')
        except ValueError:
            tail = 500
        tail = max(10, min(tail, 5000))

        filename = "data/vexo.log" if source == "vexo" else "data/watchtower.log" if source == "watchtower" else None
        if not filename:
            return web.Response(status=400, text="Invalid source. Use source=vexo or source=watchtower.")

        path = Path(filename)
        if not path.exists():
            return web.Response(status=404, text=f"Log file not found: {filename}")

        def _read_tail() -> str:
            try:
                with path.open('r', encoding='utf-8', errors='replace') as f:
                    from collections import deque as _dq
                    return ''.join(_dq(f, maxlen=tail))
            except Exception as e:
                return f"Error reading {filename}: {e}"

        text = await asyncio.to_thread(_read_tail)
        return web.Response(text=text, content_type='text/plain; charset=utf-8')

    async def api_docker_logs(self, request):
        """Fetch docker logs for specific containers via /var/run/docker.sock (if mounted)."""
        container = (request.query.get('container') or '').strip()
        if container not in {"vexo", "watchtower"}:
            return web.Response(status=400, text="Invalid container. Use container=vexo or container=watchtower.")

        try:
            tail = int(request.query.get('tail') or '500')
        except ValueError:
            tail = 500
        tail = max(10, min(tail, 5000))

        sock = Path("/var/run/docker.sock")
        if not sock.exists():
            fallback = Path("data/vexo.log" if container == "vexo" else "data/watchtower.log")
            if fallback.exists():
                def _read_tail() -> str:
                    try:
                        with fallback.open('r', encoding='utf-8', errors='replace') as f:
                            from collections import deque as _dq
                            return ''.join(_dq(f, maxlen=tail))
                    except Exception as e:
                        return f"Error reading {fallback}: {e}"

                text = await asyncio.to_thread(_read_tail)
                return web.Response(text=text, content_type='text/plain; charset=utf-8')

            return web.Response(
                status=503,
                text="Docker socket not available. Mount /var/run/docker.sock into the vexo container to enable this."
            )

        params = {
            "stdout": "true",
            "stderr": "true",
            "tail": str(tail),
            "timestamps": "false",
        }

        try:
            connector = aiohttp.UnixConnector(path=str(sock))
            async with aiohttp.ClientSession(connector=connector) as session:
                url = f"http://docker/containers/{container}/logs"
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    body = await resp.read()
                    if resp.status >= 400:
                        return web.Response(status=resp.status, text=body.decode('utf-8', errors='replace'))
                    return web.Response(text=body.decode('utf-8', errors='replace'), content_type='text/plain; charset=utf-8')
        except asyncio.TimeoutError:
            return web.Response(status=504, text="Timed out contacting Docker API.")
        except Exception as e:
            return web.Response(status=500, text=f"Failed to fetch docker logs: {e}")

    async def api_stats(self, request):
        """Get aggregated bot stats from the database + runtime state."""
        try:
            limit = int(request.query.get("limit") or "10")
        except ValueError:
            limit = 10
        limit = max(5, min(limit, 50))

        from config import Config
        from database import db as vexo_db

        db_path = vexo_db.db_path
        db_file = Path(db_path)
        if not db_file.exists():
            return web.json_response({
                "ok": False,
                "error": f"Database not found at {db_path}.",
            }, status=404)

        overview = {
            "db_path": db_path,
            "db_size_bytes": db_file.stat().st_size if db_file.exists() else 0,
        }

        top_played_tracks = []
        top_liked_tracks = []
        users_top_like = []
        recent_plays = []

        try:
            async with aiosqlite.connect(db_path) as conn:
                conn.row_factory = aiosqlite.Row

                async with conn.execute("SELECT COUNT(*) AS n FROM playback_history") as cur:
                    overview["total_plays"] = (await cur.fetchone())["n"]
                async with conn.execute("SELECT COUNT(DISTINCT url) AS n FROM playback_history WHERE url IS NOT NULL AND url != ''") as cur:
                    overview["unique_played_tracks"] = (await cur.fetchone())["n"]
                async with conn.execute("SELECT COUNT(*) AS n FROM user_preferences") as cur:
                    overview["preference_rows"] = (await cur.fetchone())["n"]
                async with conn.execute("SELECT COUNT(DISTINCT user_id) AS n FROM user_preferences") as cur:
                    overview["users_with_preferences"] = (await cur.fetchone())["n"]

                async with conn.execute('''
                    SELECT artist, song, url, COUNT(*) AS plays, MAX(timestamp) AS last_played
                    FROM playback_history
                    GROUP BY url, artist, song
                    ORDER BY plays DESC, datetime(last_played) DESC
                    LIMIT ?
                ''', (limit,)) as cur:
                    rows = await cur.fetchall()
                    top_played_tracks = [dict(r) for r in rows]

                async with conn.execute('''
                    SELECT artist,
                           liked_song AS song,
                           url,
                           SUM(score) AS net_score,
                           SUM(CASE WHEN score > 0 THEN 1 ELSE 0 END) AS likes,
                           SUM(CASE WHEN score < 0 THEN 1 ELSE 0 END) AS dislikes
                    FROM user_preferences
                    GROUP BY url, artist, liked_song
                    HAVING net_score > 0
                    ORDER BY net_score DESC, likes DESC
                    LIMIT ?
                ''', (limit,)) as cur:
                    rows = await cur.fetchall()
                    top_liked_tracks = [dict(r) for r in rows]

                # Best liked song per user (python grouping; avoids SQLite window dependency)
                async with conn.execute('''
                    SELECT user_id, artist, liked_song AS song, url, score
                    FROM user_preferences
                    WHERE score > 0
                    ORDER BY user_id ASC, score DESC
                    LIMIT 5000
                ''') as cur:
                    rows = await cur.fetchall()

                best_by_user = {}
                for r in rows:
                    uid = int(r["user_id"])
                    if uid not in best_by_user:
                        best_by_user[uid] = dict(r)

                users_top_like = [
                    {
                        **entry,
                        "user_name": self._resolve_user_name(uid),
                    }
                    for uid, entry in best_by_user.items()
                ]
                users_top_like.sort(key=lambda x: int(x.get("score") or 0), reverse=True)
                users_top_like = users_top_like[: max(limit, 20)]

                async with conn.execute('''
                    SELECT timestamp, guild_id, artist, song, url, user_requesting
                    FROM playback_history
                    ORDER BY datetime(timestamp) DESC
                    LIMIT 20
                ''') as cur:
                    rows = await cur.fetchall()
                    recent_plays = [dict(r) for r in rows]

        except Exception as e:
            return web.json_response({
                "ok": False,
                "error": f"Stats query failed: {e}",
            }, status=500)

        runtime_guilds = []
        music_cog = self.bot.get_cog("Music")
        if music_cog and hasattr(music_cog, "guild_states"):
            for gid, state in getattr(music_cog, "guild_states", {}).items():
                guild = self.bot.get_guild(int(gid))
                runtime_guilds.append({
                    "guild_id": int(gid),
                    "guild_name": guild.name if guild else None,
                    "is_autoplay": bool(getattr(state, "is_autoplay", False)),
                    "loop_mode": getattr(state, "loop_mode", "off"),
                    "queue_len": len(getattr(state, "queue", []) or []),
                    "autoplay_visible_len": len(getattr(state, "autoplay_visible", []) or []),
                    "autoplay_hidden_len": len(getattr(state, "autoplay_hidden", []) or []),
                    "current": {
                        "title": getattr(getattr(state, "current", None), "title", None),
                        "author": getattr(getattr(state, "current", None), "author", None),
                        "url": getattr(getattr(state, "current", None), "webpage_url", None)
                            or getattr(getattr(state, "current", None), "url", None),
                    } if getattr(state, "current", None) else None,
                })

        payload = {
            "ok": True,
            "overview": overview,
            "top_played_tracks": top_played_tracks,
            "top_liked_tracks": top_liked_tracks,
            "users_top_like": users_top_like,
            "recent_plays": recent_plays,
            "runtime": {"guilds": runtime_guilds},
            "config": {
                "discovery_weights": {
                    "upvote": Config.DISCOVERY_WEIGHT_UPVOTE,
                    "downvote": Config.DISCOVERY_WEIGHT_DOWNVOTE,
                    "skip": Config.DISCOVERY_WEIGHT_SKIP,
                    "request": Config.DISCOVERY_WEIGHT_REQUEST,
                }
            }
        }
        return web.json_response(payload)

    async def api_users(self, request):
        """List known users from preferences and request history."""
        from database import db as vexo_db

        db_path = vexo_db.db_path
        db_file = Path(db_path)
        if not db_file.exists():
            return web.json_response({
                "ok": False,
                "error": f"Database not found at {db_path}.",
            }, status=404)

        user_ids: set[int] = set()
        try:
            async with aiosqlite.connect(db_path) as conn:
                async with conn.execute("SELECT DISTINCT user_id FROM user_preferences") as cur:
                    for row in await cur.fetchall():
                        if row and row[0] is not None:
                            user_ids.add(int(row[0]))

                async with conn.execute(
                    "SELECT DISTINCT user_requesting FROM playback_history WHERE user_requesting IS NOT NULL"
                ) as cur:
                    for row in await cur.fetchall():
                        if row and row[0] is not None:
                            user_ids.add(int(row[0]))
        except Exception as e:
            return web.json_response({
                "ok": False,
                "error": f"User list query failed: {e}",
            }, status=500)

        users = [
            {"user_id": uid, "user_name": self._resolve_user_name(uid)}
            for uid in user_ids
        ]
        users.sort(key=lambda u: ((u.get("user_name") or "").lower(), int(u.get("user_id") or 0)))

        return web.json_response({
            "ok": True,
            "users": users,
        })

    async def api_user_stats(self, request):
        """Per-user breakdown: top liked tracks + most-requested tracks."""
        user_id_raw = (request.query.get("user_id") or "").strip()
        if not user_id_raw:
            return web.json_response({"ok": False, "error": "Missing user_id."}, status=400)

        try:
            user_id = int(user_id_raw)
        except ValueError:
            return web.json_response({"ok": False, "error": "Invalid user_id."}, status=400)

        try:
            limit = int(request.query.get("limit") or "10")
        except ValueError:
            limit = 10
        limit = max(3, min(limit, 50))

        from database import db as vexo_db

        db_path = vexo_db.db_path
        db_file = Path(db_path)
        if not db_file.exists():
            return web.json_response({
                "ok": False,
                "error": f"Database not found at {db_path}.",
            }, status=404)

        top_liked = []
        top_requested = []
        try:
            async with aiosqlite.connect(db_path) as conn:
                conn.row_factory = aiosqlite.Row

                async with conn.execute('''
                    SELECT artist, liked_song AS song, url, score
                    FROM user_preferences
                    WHERE user_id = ? AND score > 0
                    ORDER BY score DESC
                    LIMIT ?
                ''', (user_id, limit)) as cur:
                    top_liked = [dict(r) for r in await cur.fetchall()]

                async with conn.execute('''
                    SELECT artist, song, url, COUNT(*) AS plays, MAX(timestamp) AS last_played
                    FROM playback_history
                    WHERE user_requesting = ?
                    GROUP BY url, artist, song
                    ORDER BY plays DESC, datetime(last_played) DESC
                    LIMIT ?
                ''', (user_id, limit)) as cur:
                    top_requested = [dict(r) for r in await cur.fetchall()]

        except Exception as e:
            return web.json_response({
                "ok": False,
                "error": f"User stats query failed: {e}",
            }, status=500)

        return web.json_response({
            "ok": True,
            "user": {"user_id": user_id, "user_name": self._resolve_user_name(user_id)},
            "top_liked_tracks": top_liked,
            "top_requested_tracks": top_requested,
        })
    
    async def stream_logs(self, request):
        """Server-Sent Events stream for real-time logs."""
        response = web.StreamResponse()
        response.headers['Content-Type'] = 'text/event-stream; charset=utf-8'
        response.headers['Cache-Control'] = 'no-cache'
        response.headers['Connection'] = 'keep-alive'
        response.headers['X-Accel-Buffering'] = 'no'
        await response.prepare(request)

        # If the client reconnects, EventSource provides the last received id.
        last_event_id = request.headers.get('Last-Event-ID')
        try:
            last_id = int(last_event_id) if last_event_id else 0
        except ValueError:
            last_id = 0

        # Default behavior: only stream new logs after the connection is established.
        if last_id == 0 and self.log_handler.logs:
            last_id = self.log_handler.logs[-1].get('id', 0)

        # Suggest client reconnect delay
        await response.write(b"retry: 2000\n\n")

        keepalive_every = 15.0
        last_keepalive = asyncio.get_running_loop().time()

        try:
            while True:
                current_logs = list(self.log_handler.logs)
                for log in current_logs:
                    log_id = int(log.get('id', 0) or 0)
                    if log_id <= last_id:
                        continue
                    payload = json.dumps(log)
                    data = f"id: {log_id}\ndata: {payload}\n\n"
                    await response.write(data.encode('utf-8'))
                    last_id = log_id

                now = asyncio.get_running_loop().time()
                if now - last_keepalive >= keepalive_every:
                    # SSE comment as keepalive ping
                    await response.write(b": keepalive\n\n")
                    last_keepalive = now

                await asyncio.sleep(0.5)
        except (BrokenPipeError, ConnectionResetError, asyncio.CancelledError):
            pass

        return response

    async def spotify_test(self, request):
        """Test Spotify connectivity using client credentials."""
        now = time.monotonic()
        cached = False
        cached_result = self._spotify_check_cache.get("result")
        if cached_result and (now - float(self._spotify_check_cache.get("at", 0.0))) < 5.0:
            cached = True
            result = dict(cached_result)
            latency_ms = 0
        else:
            start = time.monotonic()
            # spotipy is synchronous; run in a thread so we don't block the aiohttp loop
            result = await asyncio.to_thread(spotify_check_connectivity)
            latency_ms = int((time.monotonic() - start) * 1000)
            self._spotify_check_cache = {"at": now, "result": result}

        payload = {
            **(result or {}),
            "cached": cached,
            "latency_ms": latency_ms,
            "checked_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
        return web.json_response(payload)
    
    async def get_status(self, request):
        """Get bot status information."""
        uptime = datetime.now() - self.bot.start_time if hasattr(self.bot, 'start_time') else datetime.now()
        uptime_str = str(uptime).split('.')[0]
        
        return web.json_response({
            'bot_name': str(self.bot.user) if self.bot.user else 'Unknown',
            'guilds': len(self.bot.guilds),
            'uptime': uptime_str,
            'latency': round(self.bot.latency * 1000)
        })
    
    async def get_settings(self, request):
        """Get settings page (placeholder)."""
        html = """
<!DOCTYPE html>
<html>
<head>
    <title>Vexo Settings</title>
    <meta charset="UTF-8">
    <style>
        body {
            font-family: Arial, sans-serif;
            background: #0D0D0D;
            color: #fff;
            padding: 20px;
        }
        .container { max-width: 800px; margin: 0 auto; }
        h1 { color: #00D4FF; }
        a { color: #00D4FF; text-decoration: none; }
    </style>
</head>
<body>
    <div class="container">
        <h1>⚙️ Settings</h1>
        <p>Settings management coming soon...</p>
        <p><a href="/">← Back to Dashboard</a></p>
    </div>
</body>
</html>
        """
        return web.Response(text=html, content_type='text/html')
    
    async def update_settings(self, request):
        """Update settings endpoint (placeholder)."""
        return web.json_response({'status': 'not implemented'})
    
    def cog_unload(self):
        """Clean up when cog is unloaded."""
        asyncio.create_task(self.stop_server())


async def setup(bot: commands.Bot):
    # Store start time
    if not hasattr(bot, 'start_time'):
        bot.start_time = datetime.now()
    await bot.add_cog(WebServer(bot))
