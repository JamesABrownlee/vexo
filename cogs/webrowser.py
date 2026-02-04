"""
Web Interface Cog for Vexo
Provides a web dashboard for viewing logs and managing settings.
"""
import asyncio
import logging
import time
import discord
from discord.ext import commands
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
        
        # Set up routes
        self.app.router.add_get('/', self.index)
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
    
    async def index(self, request):
        """Serve the main dashboard page."""
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
        <h1>🎵 Vexo Dashboard</h1>
        <p class="subtitle">Real-time monitoring and administration</p>
        
        <div class="grid">
            <div class="card">
                <h2>📊 Bot Status</h2>
                <div id="status">Loading...</div>
            </div>
            <div class="card">
                <h2>🎛️ Quick Actions</h2>
                <div id="quickActions" style="padding: 10px 0;">
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

