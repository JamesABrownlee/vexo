"""
Web Interface Cog for Vexo
Provides a web dashboard for viewing logs and managing settings.
"""
import asyncio
import logging
import discord
from discord.ext import commands
from aiohttp import web
import json
from datetime import datetime
from collections import deque
from typing import Optional

from utils.logger import set_logger

logger = set_logger(logging.getLogger('MusicBot.WebServer'))


class LogHandler(logging.Handler):
    """Custom log handler that stores logs in memory for web streaming."""
    
    def __init__(self, max_logs=1000):
        super().__init__()
        self.logs = deque(maxlen=max_logs)
        
    def emit(self, record):
        try:
            log_entry = {
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
        self.log_handler.setLevel(logging.DEBUG)
        
        # Attach to root logger to catch all logs
        root_logger = logging.getLogger()
        root_logger.addHandler(self.log_handler)
        
        # Set up routes
        self.app.router.add_get('/', self.index)
        self.app.router.add_get('/logs', self.get_logs)
        self.app.router.add_get('/logs/stream', self.stream_logs)
        self.app.router.add_get('/settings', self.get_settings)
        self.app.router.add_post('/settings', self.update_settings)
        self.app.router.add_get('/status', self.get_status)
        
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
                <div style="padding: 10px 0;">
                    <button class="btn" onclick="window.location.href='/settings'">⚙️ Settings</button>
                    <button class="btn" onclick="clearLogs()">🗑️ Clear Logs</button>
                    <button class="btn" onclick="toggleStream()">⏸️ Pause Stream</button>
                </div>
            </div>
        </div>
        
        <div class="card">
            <h2>📜 Live Logs</h2>
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
        
        // Load initial logs
        fetch('/logs')
            .then(r => r.json())
            .then(data => {
                displayLogs(data.logs);
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
        
        function displayLogs(logs) {
            const logsDiv = document.getElementById('logs');
            logsDiv.innerHTML = logs.map(formatLog).join('');
        }
        
        function appendLog(log) {
            const logsDiv = document.getElementById('logs');
            logsDiv.innerHTML += formatLog(log);
            if (logsDiv.scrollHeight - logsDiv.scrollTop < 700) {
                scrollToBottom();
            }
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
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }
        
        function scrollToBottom() {
            const logsDiv = document.getElementById('logs');
            logsDiv.scrollTop = logsDiv.scrollHeight;
        }
        
        function toggleStream() {
            streaming = !streaming;
            event.target.textContent = streaming ? '⏸️ Pause Stream' : '▶️ Resume Stream';
        }
        
        function clearLogs() {
            if (confirm('Clear all logs from view?')) {
                document.getElementById('logs').innerHTML = '';
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
        response.headers['Content-Type'] = 'text/event-stream'
        response.headers['Cache-Control'] = 'no-cache'
        response.headers['Connection'] = 'keep-alive'
        await response.prepare(request)
        
        last_index = len(self.log_handler.logs)
        
        try:
            while True:
                # Check for new logs
                current_logs = list(self.log_handler.logs)
                if len(current_logs) > last_index:
                    for log in current_logs[last_index:]:
                        data = f"data: {json.dumps(log)}\n\n"
                        await response.write(data.encode('utf-8'))
                    last_index = len(current_logs)
                
                await asyncio.sleep(0.5)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        
        return response
    
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