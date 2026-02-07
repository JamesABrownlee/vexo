// Dashboard JavaScript
const API = {
    status: '/api/status',
    guilds: '/api/guilds',
    guild: (id) => `/api/guilds/${id}`,
    settings: (id) => `/api/guilds/${id}/settings`,
    analytics: '/api/analytics',
    users: '/api/users',
    songs: '/api/songs',
    library: '/api/library',
    topSongs: '/api/analytics/top-songs',
    userPrefs: (id) => `/api/users/${id}/preferences`,
    notifications: '/api/notifications',
    settings_global: '/api/settings/global',
    leave_guild: (id) => `/api/guilds/${id}/leave`,
};

// State
let ws = null;
let currentGuild = null;
let currentScope = 'global';

// ============================================================
// LOG STATE
// ============================================================
const logState = {
    entries: [],           // All log entries (capped at MAX_LOGS)
    maxEntries: 1000,
    autoScroll: true,
    levelFilter: 'all',    // 'all' | 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR'
    searchQuery: '',
    sourceFilter: '',
    guildFilter: '',
    counts: { all: 0, DEBUG: 0, INFO: 0, WARNING: 0, ERROR: 0 },
    knownSources: new Set(),
    knownGuilds: new Map(), // guild_id -> name
    wsConnected: false,
    searchTimeout: null,
};

// ============================================================
// INITIALIZE
// ============================================================
document.addEventListener('DOMContentLoaded', () => {
    initWebSocket();
    fetchStatus();
    fetchGuilds();
    fetchAnalytics();
    fetchSongs();
    fetchLibrary();
    fetchUsers();
    fetchNotifications();

    setInterval(fetchStatus, 5000);
    setInterval(fetchGuilds, 10000);
    setInterval(fetchAnalytics, 15000);
    setInterval(fetchSongs, 30000);
    setInterval(fetchLibrary, 30000);
    setInterval(fetchNotifications, 15000);

    // Tab handling
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', (e) => {
            e.preventDefault();
            switchTab(tab.dataset.tab);
        });
    });

    // Log UI initialization
    initLogControls();
});

// ============================================================
// LOG CONTROLS SETUP
// ============================================================
function initLogControls() {
    // Level filter pills
    document.querySelectorAll('.logs-pill').forEach(pill => {
        pill.addEventListener('click', () => {
            const level = pill.dataset.level;
            document.querySelectorAll('.logs-pill').forEach(p => p.classList.remove('active'));
            pill.classList.add('active');
            logState.levelFilter = level;
            applyLogFilters();
        });
    });

    // Search input
    const searchInput = document.getElementById('log-search');
    if (searchInput) {
        searchInput.addEventListener('input', () => {
            clearTimeout(logState.searchTimeout);
            logState.searchTimeout = setTimeout(() => {
                logState.searchQuery = searchInput.value.toLowerCase().trim();
                applyLogFilters();
            }, 150);
        });
    }

    // Source filter
    const sourceSelect = document.getElementById('log-filter-source');
    if (sourceSelect) {
        sourceSelect.addEventListener('change', () => {
            logState.sourceFilter = sourceSelect.value;
            applyLogFilters();
        });
    }

    // Guild filter
    const guildSelect = document.getElementById('log-filter-guild');
    if (guildSelect) {
        guildSelect.addEventListener('change', () => {
            logState.guildFilter = guildSelect.value;
            applyLogFilters();
        });
    }

    // Auto-scroll toggle
    const scrollBtn = document.getElementById('log-toggle-scroll');
    if (scrollBtn) {
        scrollBtn.classList.add('active');
        scrollBtn.addEventListener('click', () => {
            logState.autoScroll = !logState.autoScroll;
            scrollBtn.classList.toggle('active', logState.autoScroll);
            const label = document.getElementById('logs-scroll-label');
            if (label) label.textContent = logState.autoScroll ? 'Auto-scroll ON' : 'Auto-scroll OFF';
            if (logState.autoScroll) scrollLogsToBottom();
        });
    }

    // Clear button
    const clearBtn = document.getElementById('log-clear');
    if (clearBtn) {
        clearBtn.addEventListener('click', clearLogs);
    }

    // Keyboard: Ctrl+K to focus search
    document.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
            e.preventDefault();
            const el = document.getElementById('log-search');
            if (el) el.focus();
        }
    });

    // Detect manual scroll to pause auto-scroll
    const viewport = document.getElementById('logs-viewport');
    if (viewport) {
        viewport.addEventListener('scroll', () => {
            if (!logState.autoScroll) return;
            const { scrollTop, scrollHeight, clientHeight } = viewport;
            // If user scrolled up more than 50px from bottom, pause auto-scroll
            if (scrollHeight - scrollTop - clientHeight > 50) {
                logState.autoScroll = false;
                const scrollBtn = document.getElementById('log-toggle-scroll');
                if (scrollBtn) scrollBtn.classList.remove('active');
                const label = document.getElementById('logs-scroll-label');
                if (label) label.textContent = 'Auto-scroll OFF';
            }
        });
    }
}

// ============================================================
// WEBSOCKET FOR LIVE LOGS
// ============================================================
function initWebSocket() {
    try {
        ws = new WebSocket(`ws://${location.host}/ws/logs`);
        ws.onopen = () => {
            logState.wsConnected = true;
            updateWsStatus(true);
        };
        ws.onmessage = (e) => {
            const log = JSON.parse(e.data);
            addLogEntry(log);
        };
        ws.onclose = () => {
            logState.wsConnected = false;
            updateWsStatus(false);
            setTimeout(initWebSocket, 3000);
        };
        ws.onerror = () => {
            logState.wsConnected = false;
            updateWsStatus(false);
        };
    } catch (e) {
        console.error('WS Error', e);
    }
}

function updateWsStatus(connected) {
    const indicator = document.getElementById('logs-ws-indicator');
    if (!indicator) return;
    const dot = indicator.querySelector('.logs-ws-dot');
    if (dot) {
        dot.classList.toggle('disconnected', !connected);
    }
    indicator.lastChild.textContent = connected ? ' Connected' : ' Disconnected';
}

// ============================================================
// LOG ENTRY PROCESSING
// ============================================================
function addLogEntry(log) {
    // Store entry
    const entry = {
        timestamp: log.timestamp,
        level: log.level || 'INFO',
        message: log.message || '',
        logger: log.logger || '',
        guild_id: log.guild_id || null,
        parsed: parseLogMessage(log.message || ''),
    };

    logState.entries.push(entry);

    // Cap entries
    if (logState.entries.length > logState.maxEntries) {
        const removed = logState.entries.shift();
        // Remove first DOM node
        const viewport = document.getElementById('logs-viewport');
        if (viewport && viewport.firstElementChild && viewport.firstElementChild.classList.contains('log-row')) {
            viewport.removeChild(viewport.firstElementChild);
        }
        // Decrement count
        logState.counts[removed.level] = Math.max(0, (logState.counts[removed.level] || 0) - 1);
        logState.counts.all = Math.max(0, logState.counts.all - 1);
    }

    // Update counts
    logState.counts.all++;
    logState.counts[entry.level] = (logState.counts[entry.level] || 0) + 1;
    updateLogCounts();

    // Track sources
    const shortSource = shortenLogger(entry.logger);
    if (shortSource && !logState.knownSources.has(shortSource)) {
        logState.knownSources.add(shortSource);
        updateSourceFilter();
    }

    // Track guilds
    if (entry.guild_id && !logState.knownGuilds.has(String(entry.guild_id))) {
        logState.knownGuilds.set(String(entry.guild_id), String(entry.guild_id));
        updateGuildFilter();
    }

    // Remove empty state
    const emptyEl = document.getElementById('logs-empty');
    if (emptyEl) emptyEl.remove();

    // Render entry
    const row = createLogRow(entry);
    const viewport = document.getElementById('logs-viewport');
    if (!viewport) return;

    // Apply filter visibility
    if (!matchesFilters(entry)) {
        row.classList.add('log-hidden');
    }

    viewport.appendChild(row);
    updateShownCount();

    if (logState.autoScroll) {
        scrollLogsToBottom();
    }
}

// ============================================================
// LOG MESSAGE PARSER
// ============================================================
function parseLogMessage(msg) {
    // Try to detect structured log format: event_name key1=val1 key2='val2' ...
    // Or key=value pairs anywhere in the message
    const result = { event: null, pairs: [], text: msg };

    if (!msg) return result;

    // Extract key=value pairs (handles quoted values)
    const kvRegex = /(\w+)=(?:'([^']*)'|"([^"]*)"|(\S+))/g;
    const pairs = [];
    let match;
    let cleaned = msg;

    while ((match = kvRegex.exec(msg)) !== null) {
        const key = match[1];
        const val = match[2] !== undefined ? match[2] : match[3] !== undefined ? match[3] : match[4];
        pairs.push({ key, val });
    }

    if (pairs.length > 0) {
        // Remove all kv pairs from message to find the event/text part
        cleaned = msg.replace(kvRegex, '').trim();

        // The first word of the cleaned text might be the event name
        const words = cleaned.split(/\s+/).filter(Boolean);
        if (words.length > 0 && /^[a-z_][a-z0-9_]*$/i.test(words[0])) {
            result.event = words[0];
            result.text = words.slice(1).join(' ');
        } else {
            result.text = cleaned;
        }
        result.pairs = pairs;
    } else {
        // No kv pairs - check if first token looks like an event name
        const words = msg.split(/\s+/);
        if (words.length > 1 && /^[a-z_][a-z0-9_]*$/.test(words[0]) && words[0].includes('_')) {
            result.event = words[0];
            result.text = words.slice(1).join(' ');
        }
    }

    return result;
}

function shortenLogger(loggerName) {
    if (!loggerName) return '';
    // "src.cogs.music" -> "music", "src.services.youtube" -> "youtube"
    const parts = loggerName.split('.');
    return parts[parts.length - 1] || loggerName;
}

// ============================================================
// LOG ENTRY DOM CREATION
// ============================================================
function createLogRow(entry) {
    const row = document.createElement('div');
    row.className = `log-row level-${entry.level}`;
    row.dataset.level = entry.level;
    row.dataset.source = shortenLogger(entry.logger);
    row.dataset.guild = entry.guild_id || '';

    // Timestamp
    const ts = document.createElement('span');
    ts.className = 'log-ts';
    const d = new Date(entry.timestamp * 1000);
    ts.textContent = d.toLocaleTimeString('en-GB', { hour12: false }) + '.' + String(d.getMilliseconds()).padStart(3, '0');

    // Level dot
    const dot = document.createElement('span');
    dot.className = 'log-level-dot';

    // Body
    const body = document.createElement('span');
    body.className = 'log-body';

    // Source badge
    const shortSource = shortenLogger(entry.logger);
    if (shortSource) {
        const source = document.createElement('span');
        source.className = 'log-source';
        source.textContent = shortSource;
        body.appendChild(source);
    }

    // Parsed content
    const parsed = entry.parsed;
    if (parsed.event) {
        const eventEl = document.createElement('span');
        eventEl.className = 'log-event';
        eventEl.textContent = parsed.event;
        body.appendChild(eventEl);
    }

    if (parsed.pairs.length > 0) {
        parsed.pairs.forEach(({ key, val }) => {
            const kv = document.createElement('span');
            kv.className = 'log-kv';
            kv.innerHTML = `<span class="log-kv-key">${escapeHtml(key)}</span><span class="log-kv-eq">=</span><span class="log-kv-val">${escapeHtml(val)}</span>`;
            body.appendChild(kv);
        });
    }

    if (parsed.text) {
        const msgEl = document.createElement('span');
        msgEl.className = 'log-msg';
        msgEl.textContent = parsed.event || parsed.pairs.length > 0 ? ' ' + parsed.text : parsed.text;
        body.appendChild(msgEl);
    }

    // If no structured content was generated, show raw message
    if (!parsed.event && parsed.pairs.length === 0 && !parsed.text) {
        const msgEl = document.createElement('span');
        msgEl.className = 'log-msg';
        msgEl.textContent = entry.message;
        body.appendChild(msgEl);
    }

    // Detail section (click to expand)
    const detail = document.createElement('div');
    detail.className = 'log-detail';
    detail.innerHTML = `
        <div class="log-detail-line"><span class="log-detail-label">Level</span> <span class="log-detail-value">${entry.level}</span></div>
        <div class="log-detail-line"><span class="log-detail-label">Source</span> <span class="log-detail-value">${escapeHtml(entry.logger)}</span></div>
        ${entry.guild_id ? `<div class="log-detail-line"><span class="log-detail-label">Guild</span> <span class="log-detail-value">${entry.guild_id}</span></div>` : ''}
        <div class="log-detail-line"><span class="log-detail-label">Raw</span> <span class="log-detail-value">${escapeHtml(entry.message)}</span></div>
    `;

    row.appendChild(ts);
    row.appendChild(dot);
    row.appendChild(body);
    row.appendChild(detail);

    // Click to expand/collapse
    row.addEventListener('click', () => {
        row.classList.toggle('expanded');
    });

    return row;
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// ============================================================
// FILTERING
// ============================================================
function applyLogFilters() {
    const viewport = document.getElementById('logs-viewport');
    if (!viewport) return;

    const rows = viewport.querySelectorAll('.log-row');
    let shown = 0;

    rows.forEach((row, idx) => {
        const entry = logState.entries[idx];
        if (!entry) return;

        if (matchesFilters(entry)) {
            row.classList.remove('log-hidden');
            shown++;
        } else {
            row.classList.add('log-hidden');
        }
    });

    updateShownCount();
}

function matchesFilters(entry) {
    // Level filter
    if (logState.levelFilter !== 'all' && entry.level !== logState.levelFilter) {
        return false;
    }

    // Source filter
    if (logState.sourceFilter && shortenLogger(entry.logger) !== logState.sourceFilter) {
        return false;
    }

    // Guild filter
    if (logState.guildFilter && String(entry.guild_id || '') !== logState.guildFilter) {
        return false;
    }

    // Search query
    if (logState.searchQuery) {
        const haystack = entry.message.toLowerCase();
        if (!haystack.includes(logState.searchQuery)) {
            return false;
        }
    }

    return true;
}

// ============================================================
// UI UPDATES
// ============================================================
function updateLogCounts() {
    const ids = { all: 'log-count-all', DEBUG: 'log-count-debug', INFO: 'log-count-info', WARNING: 'log-count-warning', ERROR: 'log-count-error' };
    for (const [level, id] of Object.entries(ids)) {
        const el = document.getElementById(id);
        if (el) el.textContent = logState.counts[level] || 0;
    }
}

function updateShownCount() {
    const viewport = document.getElementById('logs-viewport');
    const el = document.getElementById('logs-shown-count');
    if (!viewport || !el) return;
    const visible = viewport.querySelectorAll('.log-row:not(.log-hidden)').length;
    el.textContent = visible;
}

function updateSourceFilter() {
    const select = document.getElementById('log-filter-source');
    if (!select) return;
    const current = select.value;
    const sorted = [...logState.knownSources].sort();
    select.innerHTML = '<option value="">All Sources</option>' + sorted.map(s => `<option value="${s}"${s === current ? ' selected' : ''}>${s}</option>`).join('');
}

function updateGuildFilter() {
    const select = document.getElementById('log-filter-guild');
    if (!select) return;
    const current = select.value;
    select.innerHTML = '<option value="">All Guilds</option>';
    for (const [id, name] of logState.knownGuilds) {
        const opt = document.createElement('option');
        opt.value = id;
        opt.textContent = name;
        if (id === current) opt.selected = true;
        select.appendChild(opt);
    }
}

function scrollLogsToBottom() {
    const viewport = document.getElementById('logs-viewport');
    if (viewport) {
        viewport.scrollTop = viewport.scrollHeight;
    }
}

function clearLogs() {
    logState.entries = [];
    logState.counts = { all: 0, DEBUG: 0, INFO: 0, WARNING: 0, ERROR: 0 };
    updateLogCounts();

    const viewport = document.getElementById('logs-viewport');
    if (viewport) {
        viewport.innerHTML = `
            <div class="logs-empty" id="logs-empty">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="40" height="40"><path d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                <span>Waiting for log events...</span>
            </div>`;
    }
    updateShownCount();
}

// ============================================================
// FETCH FUNCTIONS (existing - unchanged)
// ============================================================
async function fetchStatus() {
    try {
        const res = await fetch(API.status);
        const data = await res.json();
        updateStatus(data);
    } catch (e) {
        console.error('Failed to fetch status', e);
        try { updateStatus({ status: 'offline' }); } catch (e2) { }
    }
}

function updateStatus(data) {
    try {
        const dot = document.querySelector('.status-dot');
        const text = document.getElementById('status-text');
        const guildCount = document.getElementById('stat-guilds');
        const voiceCount = document.getElementById('stat-voice');
        const latency = document.getElementById('stat-latency-val');
        const sidebarLatency = document.getElementById('stat-latency');
        const cpu = document.getElementById('stat-cpu');
        const ram = document.getElementById('stat-ram');

        if (dot) dot.className = `status-dot status-${data.status === 'online' ? 'online' : 'offline'}`;
        if (text) text.textContent = data.status === 'online' ? `Online (${data.latency_ms || 0}ms)` : 'Offline';
        if (guildCount) guildCount.textContent = data.guilds || 0;
        if (voiceCount) voiceCount.textContent = data.voice_connections || 0;
        if (latency) latency.textContent = `${data.latency_ms || 0}ms`;
        if (sidebarLatency) sidebarLatency.textContent = `${data.latency_ms || 0}ms`;
        if (cpu) cpu.textContent = data.cpu_percent || 0;
        if (ram) ram.textContent = data.ram_percent || 0;
    } catch (e) { console.error('Error updating status', e); }
}

async function fetchGuilds() {
    try {
        const res = await fetch(API.guilds);
        const data = await res.json();

        if (!currentGuild && data.guilds && data.guilds.length > 0) {
            currentGuild = data.guilds[0].id;
        }

        updateTopBar(data.guilds || []);
        updateGuildList(data.guilds || []);
        updateNowPlaying(data.guilds || []);

        // Update known guilds for log filter
        (data.guilds || []).forEach(g => {
            if (g.id && !logState.knownGuilds.has(String(g.id))) {
                logState.knownGuilds.set(String(g.id), g.name || String(g.id));
                updateGuildFilter();
            }
        });
    } catch (e) {
        console.error('Failed to fetch guilds', e);
    }
}

function updateTopBar(guilds) {
    const nav = document.getElementById('server-nav');
    if (!nav) return;

    let html = `<div class="server-nav-item ${currentScope === 'global' ? 'active' : ''}" onclick="switchScope('global')">Global</div>`;

    html += guilds.map(g => `
        <div class="server-nav-item ${currentScope === g.id ? 'active' : ''}" onclick="switchScope('${g.id}')">
            ${g.name || 'Server'}
            ${g.is_playing ? ' 🔊' : ''}
        </div>
    `).join('');

    nav.innerHTML = html;
}

function updateGuildList(guilds) {
    const list = document.getElementById('guild-list');
    if (!list) return;

    list.innerHTML = guilds.map(g => `
        <div class="user-item ${g.id === currentGuild ? 'active' : ''}" onclick="selectGuild(event, '${g.id}')">
            <div class="user-avatar">${(g.name || '?').charAt(0)}</div>
            <div class="user-info">
                <div class="user-name">${g.name || 'Unknown Server'}</div>
                <div class="user-stats">${g.member_count || 0} members</div>
            </div>
            <div class="user-actions" style="display: flex; gap: 0.5rem; align-items: center;">
                ${g.is_playing ? '<span style="color: var(--success); font-size: 0.8rem;">▶ Playing</span>' : ''}
                <button class="btn btn-secondary" style="padding: 0.2rem 0.5rem; font-size: 0.7rem;" onclick="event.stopPropagation(); leaveGuild('${g.id}')">Leave</button>
            </div>
        </div>
    `).join('');
}

async function leaveGuild(id) {
    if (!confirm('Are you sure you want the bot to leave this server?')) return;
    try {
        const res = await fetch(API.leave_guild(id), { method: 'POST' });
        if (res.ok) fetchGuilds();
        else alert('Failed to leave server');
    } catch (e) { console.error(e); alert('Error leaving server'); }
}

function updateNowPlaying(guilds) {
    const np = document.getElementById('now-playing');
    if (!np) return;

    let playing = null;
    if (currentScope === 'global') {
        playing = guilds.find(g => g.is_playing && g.current_song);
    } else {
        playing = guilds.find(g => g.id === currentScope && g.is_playing && g.current_song);
    }

    if (playing) {
        let durationStr = '';
        if (playing.duration_seconds) {
            const mins = Math.floor(playing.duration_seconds / 60);
            const secs = playing.duration_seconds % 60;
            durationStr = `${mins}:${secs.toString().padStart(2, '0')}`;
        }

        np.innerHTML = `
            <div class="np-content">
                <img class="np-artwork" src="https://img.youtube.com/vi/${playing.video_id || 'dQw4w9WgXcQ'}/hqdefault.jpg" alt="Album art">
                <div class="np-info">
                    <div class="np-title">${playing.current_song || 'Unknown'}</div>
                    <div class="np-artist">${playing.current_artist || 'Unknown Artist'}</div>
                    <div class="np-metadata">
                        ${durationStr ? `<span>⏳ ${durationStr}</span>` : ''}
                        ${playing.genre ? `<span>🏷️ ${playing.genre}</span>` : ''}
                        ${playing.year ? `<span>📅 ${playing.year}</span>` : ''}
                    </div>
                    ${playing.discovery_reason ? `<div class="np-discovery">${playing.discovery_reason}</div>` : ''}
                    <div class="np-controls">
                        <button class="np-btn" onclick="control('pause')">⏸️</button>
                        <button class="np-btn" onclick="control('skip')">⏭️</button>
                        <button class="np-btn" onclick="control('stop')">⏹️</button>
                    </div>
                </div>
            </div>
        `;
        np.style.display = 'block';
    } else {
        np.innerHTML = `<div class="np-content" style="justify-content: center;"><span style="color: var(--text-muted);">Nothing playing</span></div>`;
    }
}

async function fetchAnalytics() {
    try {
        let url = API.analytics;
        if (currentScope !== 'global') url += `?guild_id=${currentScope}`;
        const res = await fetch(url);
        const data = await res.json();
        updateAnalytics(data);
    } catch (e) { console.error('Failed to fetch analytics', e); }
}

function updateAnalytics(data) {
    try {
        const songsEl = document.getElementById('stat-songs');
        const usersEl = document.getElementById('stat-users');
        const playsEl = document.getElementById('stat-plays');
        if (songsEl) songsEl.textContent = data.total_songs || 0;
        if (usersEl) usersEl.textContent = data.total_users || 0;
        if (playsEl) playsEl.textContent = data.total_plays || 0;

        // Top songs
        const songTable = document.getElementById('top-songs-table');
        if (songTable) {
            if (!data.top_songs || data.top_songs.length === 0) {
                songTable.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--text-muted); padding: 2rem;">No songs played yet</td></tr>';
            } else {
                songTable.innerHTML = data.top_songs.slice(0, 10).map((s, i) => `
                    <tr>
                        <td>${i + 1}</td>
                        <td>
                            <div class="song-cell">
                                <img class="song-thumb" src="https://img.youtube.com/vi/${s.yt_id}/default.jpg" alt="">
                                <div class="song-info">
                                    <span class="song-name">${s.title}</span>
                                    <span class="song-artist">${s.artist}</span>
                                </div>
                            </div>
                        </td>
                        <td>${s.plays}</td>
                        <td>${s.likes} ❤️</td>
                    </tr>
                `).join('');
            }
        }

        // Top users
        const userList = document.getElementById('top-users-list');
        if (userList) {
            if (!data.top_users || data.top_users.length === 0) {
                userList.innerHTML = '<div style="text-align: center; color: var(--text-muted); padding: 1rem;">No users active yet</div>';
            } else {
                userList.innerHTML = data.top_users.slice(0, 10).map(u => `
                    <div class="user-item" onclick="viewUser('${u.id}')">
                        <div class="user-avatar">${(u.name || '?').charAt(0)}</div>
                        <div class="user-info">
                            <div class="user-name">${u.name || 'Unknown'}</div>
                            <div class="user-stats">${u.plays || 0} plays • ${u.total_likes || 0} likes</div>
                        </div>
                    </div>
                `).join('');
            }
        }

        // Insights
        const elements = {
            'insight-liked-genre': data.top_liked_genres?.[0]?.name,
            'insight-liked-artist': data.top_liked_artists?.[0]?.name,
            'insight-liked-song': data.top_liked_songs?.[0] ? `${data.top_liked_songs[0].title} by ${data.top_liked_songs[0].artist}` : null,
            'insight-played-genre': data.top_played_genres?.[0]?.name,
            'insight-played-artist': data.top_played_artists?.[0]?.name
        };
        for (const [id, val] of Object.entries(elements)) {
            const el = document.getElementById(id);
            if (el) el.textContent = val || '-';
        }

        const usefulList = document.getElementById('useful-users-list');
        if (usefulList) {
            if (!data.top_useful_users || data.top_useful_users.length === 0) {
                usefulList.innerHTML = '<div style="text-align: center; color: var(--text-muted); padding: 1rem;">No useful activity yet</div>';
            } else {
                usefulList.innerHTML = data.top_useful_users.map(u => `
                    <div class="user-item">
                        <div class="user-avatar" style="background: var(--gradient-2)">${(u.username || '?').charAt(0)}</div>
                        <div class="user-info">
                            <div class="user-name">${u.username || 'Unknown'}</div>
                            <div class="user-stats">${u.score || 0} helpfulness points</div>
                        </div>
                    </div>
                `).join('');
            }
        }
    } catch (e) { console.error('Error updating analytics', e); }
}

async function fetchSongs() {
    try {
        let url = API.songs;
        if (currentScope !== 'global') url += `?guild_id=${currentScope}`;
        const res = await fetch(url);
        const data = await res.json();
        updateSongsList(data.songs || []);
    } catch (e) { console.error(e); }
}

function updateSongsList(songs) {
    const list = document.getElementById('songs-list');
    if (!list) return;

    if (songs.length === 0) {
        list.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--text-muted);">No songs found</td></tr>';
        return;
    }

    list.innerHTML = songs.map(s => {
        let durationStr = '-';
        if (s.duration_seconds) {
            const mins = Math.floor(s.duration_seconds / 60);
            const secs = s.duration_seconds % 60;
            durationStr = `${mins}:${secs.toString().padStart(2, '0')}`;
        }
        let timeStr = s.played_at ? new Date(s.played_at).toLocaleString() : 'Never';

        return `
            <tr>
                <td>${s.title}</td>
                <td>${s.artist_name}</td>
                <td>${durationStr}</td>
                <td>${s.genre || '-'}</td>
                <td><span class="user-list">${s.requested_by || '-'}</span></td>
                <td><span class="user-list liked">${s.liked_by || '-'}</span></td>
                <td><span class="user-list disliked">${s.disliked_by || '-'}</span></td>
                <td>${timeStr}</td>
            </tr>
        `;
    }).join('');
}

async function fetchUsers() {
    try {
        let url = API.users;
        if (currentScope !== 'global') url += `?guild_id=${currentScope}`;
        const res = await fetch(url);
        const data = await res.json();
        updateUserDirectory(data.users || []);
    } catch (e) { console.error(e); }
}

function updateUserDirectory(users) {
    const list = document.getElementById('users-directory');
    if (!list) return;

    if (users.length === 0) {
        list.innerHTML = '<div style="text-align: center; color: var(--text-muted);">No users found</div>';
        return;
    }

    list.innerHTML = users.map(u => `
        <div class="user-item">
            <div class="user-avatar">${(u.username || '?').charAt(0)}</div>
            <div class="user-info">
                <div class="user-name">${u.username || 'Unknown'}</div>
                <div class="user-stats">${u.formatted_id || u.discord_id || 'ID: ' + u.id}</div>
            </div>
            <div class="user-metrics" style="margin-left: auto; text-align: right; font-size: 0.8rem; color: var(--text-muted);">
                <div>${u.reactions || 0} reactions</div>
                <div>${u.playlists || 0} playlists</div>
            </div>
        </div>
    `).join('');
}

async function fetchLibrary() {
    try {
        let url = API.library;
        if (currentScope !== 'global') url += `?guild_id=${currentScope}`;
        const res = await fetch(url);
        const data = await res.json();
        updateLibraryList(data.library || []);
    } catch (e) { console.error(e); }
}

function updateLibraryList(library) {
    const list = document.getElementById('library-list');
    if (!list) return;

    if (library.length === 0) {
        list.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-muted);">Library is empty</td></tr>';
        return;
    }

    list.innerHTML = library.map(s => {
        let dateStr = s.last_added ? new Date(s.last_added).toLocaleDateString() : '-';
        const sourceMap = { 'request': '📨 Request', 'like': '❤️ Like', 'import': '📥 Import' };
        const sourcesFormatted = (s.sources || '').split(',').map(src => sourceMap[src] || src).join(', ');

        return `
            <tr>
                <td>${s.title}</td>
                <td>${s.artist_name}</td>
                <td>${s.genre || '-'}</td>
                <td>${sourcesFormatted}</td>
                <td><span class="user-list">${s.contributors || '-'}</span></td>
                <td>${dateStr}</td>
            </tr>
        `;
    }).join('');
}

function selectGuild(e, id) {
    currentGuild = id;
    document.querySelectorAll('.user-item').forEach(el => el.classList.remove('active'));
    if (e && e.currentTarget) e.currentTarget.classList.add('active');
}

function viewUser(id) { console.log('View user', id); }

function control(action) {
    if (currentGuild) {
        fetch(`/api/guilds/${currentGuild}/control/${action}`, { method: 'POST' })
            .then(() => setTimeout(fetchGuilds, 200));
    }
}

function switchTab(tab) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    const tabBtn = document.querySelector(`[data-tab="${tab}"]`);
    if (tabBtn) tabBtn.classList.add('active');

    document.querySelectorAll('.tab-content').forEach(c => {
        c.style.display = 'none';
        c.classList.remove('active');
    });
    const content = document.getElementById(`tab-${tab}`);
    if (content) {
        content.style.display = tab === 'logs' ? 'flex' : 'block';
        content.classList.add('active');
    }

    if (tab === 'settings') {
        loadSettingsTab();
    }

    // Scroll logs to bottom when switching to logs tab
    if (tab === 'logs' && logState.autoScroll) {
        requestAnimationFrame(scrollLogsToBottom);
    }
}

async function switchScope(scope) {
    currentScope = scope;
    document.querySelectorAll('.server-nav-item').forEach(el => {
        el.classList.remove('active');
        if (scope === 'global' && el.textContent.includes('Global')) el.classList.add('active');
    });

    fetchAnalytics();
    fetchLibrary();

    if (scope === 'global') {
        fetchStatus();
        switchTab('dashboard');
        const gLabel = document.getElementById('stat-guilds');
        if (gLabel) gLabel.nextElementSibling.textContent = 'Servers';
    } else {
        try {
            const res = await fetch(API.guild(scope));
            const data = await res.json();
            const gCard = document.getElementById('stat-guilds');
            if (gCard) {
                gCard.textContent = data.queue_size || 0;
                gCard.nextElementSibling.textContent = 'In Queue';
            }
            fetchSongs();
            switchTab('dashboard');
        } catch (e) { console.error(e); }
    }
}

async function loadSettingsTab() {
    const title = document.getElementById('settings-title');
    const globalBlock = document.getElementById('settings-global');
    const serverBlock = document.getElementById('settings-server');

    if (currentScope === 'global') {
        if (title) title.textContent = '⚙️ Global Settings';
        if (globalBlock) globalBlock.style.display = 'block';
        if (serverBlock) serverBlock.style.display = 'none';

        try {
            const res = await fetch(API.settings_global);
            const data = await res.json();
            const el = document.getElementById('setting-max-servers-tab');
            if (el) el.value = data.max_concurrent_servers || '';
        } catch (e) {
            console.error(e);
        }
    } else {
        if (title) title.textContent = '⚙️ Server Settings';
        if (globalBlock) globalBlock.style.display = 'none';
        if (serverBlock) serverBlock.style.display = 'block';

        try {
            const res = await fetch(API.settings(currentScope));
            const data = await res.json();

            const pb = document.getElementById('setting-pre-buffer');
            if (pb) pb.checked = !!data.pre_buffer;

            const ba = document.getElementById('setting-buffer-amount');
            if (ba) {
                ba.value = data.buffer_amount || 1;
                const val = document.getElementById('buffer-val');
                if (val) val.textContent = ba.value;
            }

            const md = document.getElementById('setting-max-duration');
            if (md) md.value = data.max_song_duration || 6;
        } catch (e) { console.error(e); }
    }
}

async function fetchNotifications() {
    try {
        const res = await fetch(API.notifications);
        const data = await res.json();
        updateNotifications(data.notifications || []);
    } catch (e) { console.error(e); }
}

function updateNotifications(list) {
    const container = document.getElementById('notif-list');
    const dot = document.getElementById('notif-dot');
    if (!container) return;

    if (list.length === 0) {
        container.innerHTML = '<div class="notif-item" style="color: var(--text-muted); text-align: center;">No new notifications</div>';
        if (dot) dot.style.display = 'none';
        return;
    }

    if (dot) dot.style.display = 'block';
    container.innerHTML = list.map(n => `
        <div class="notif-item">
            <div style="font-weight: 500; color: var(--${n.level === 'error' ? 'error' : n.level === 'warning' ? 'warning' : 'text-primary'})">${n.level.toUpperCase()}</div>
            <div>${n.message}</div>
            <div class="notif-time">${new Date(n.created_at * 1000).toLocaleString()}</div>
        </div>
    `).join('');
}

function toggleNotifications() {
    const dd = document.getElementById('notif-dropdown');
    if (dd) dd.classList.toggle('show');
}

async function saveServerSettings() {
    if (!currentGuild) return;

    const preBuffer = document.getElementById('setting-pre-buffer').checked;
    const bufferAmount = document.getElementById('setting-buffer-amount').value;
    const maxDuration = document.getElementById('setting-max-duration').value;

    try {
        const res = await fetch(API.settings(currentGuild), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                pre_buffer: preBuffer,
                buffer_amount: parseInt(bufferAmount),
                max_song_duration: parseInt(maxDuration)
            })
        });

        if (res.ok) alert('Settings saved!');
        else alert('Failed to save settings');
    } catch (e) {
        console.error(e);
        alert('Error saving settings');
    }
}

async function saveSettingsTab() {
    const maxServers = document.getElementById('setting-max-servers-tab').value;

    try {
        const res = await fetch(API.settings_global, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                max_concurrent_servers: parseInt(maxServers)
            })
        });

        if (res.ok) alert('Global settings saved!');
        else alert('Failed to save global settings');
    } catch (e) {
        console.error(e);
        alert('Error saving global settings');
    }
}
