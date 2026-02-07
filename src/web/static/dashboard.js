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
    genres: '/api/genres',
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
    categoryFilter: '',    // Filter by category (playback, voice, discovery, api, system, etc)
    searchQuery: '',
    sourceFilter: '',
    guildFilter: '',
    counts: { all: 0, DEBUG: 0, INFO: 0, WARNING: 0, ERROR: 0 },
    knownSources: new Set(),
    knownGuilds: new Map(), // guild_id -> name
    knownCategories: new Set(['playback', 'voice', 'queue', 'discovery', 'api', 'database', 'system', 'user', 'preference', 'import']),
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
    fetchGenres();

    setInterval(fetchStatus, 5000);
    setInterval(fetchGuilds, 10000);
    setInterval(fetchAnalytics, 15000);
    setInterval(fetchSongs, 30000);
    setInterval(fetchLibrary, 30000);
    setInterval(fetchNotifications, 15000);

    // Tab handling - sidebar links
    document.querySelectorAll('.sidebar-link').forEach(link => {
        link.addEventListener('click', (e) => {
            e.preventDefault();
            switchTab(link.dataset.tab);
        });
    });

    // Legacy .tab class support
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', (e) => {
            e.preventDefault();
            switchTab(tab.dataset.tab);
        });
    });

    // Log UI initialization
    initLogControls();

    // Command palette initialization
    initCommandPalette();

    // Global search
    initGlobalSearch();

    // Panel search/filter initialization
    initPanelSearches();
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

    // Category filter
    const categorySelect = document.getElementById('log-filter-category');
    if (categorySelect) {
        categorySelect.addEventListener('change', () => {
            logState.categoryFilter = categorySelect.value;
            applyLogFilters();
        });
        // Populate initial category options
        updateCategoryFilter();
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
function addLogEntry(logData) {
    // Store entry - use category/event directly from WebSocket if available
    const entry = {
        timestamp: logData.timestamp,
        level: logData.level || 'INFO',
        message: logData.message || '',
        logger: logData.logger || '',
        guild_id: logData.guild_id || null,
        category: logData.category || null,
        event: logData.event || null,
        fields: logData.fields || {},
        parsed: parseLogMessage(logData.message || ''),
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
    row.dataset.category = entry.category || '';

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

    // Category badge (shown first if available)
    if (entry.category) {
        const catBadge = document.createElement('span');
        catBadge.className = `log-category log-category-${entry.category}`;
        catBadge.textContent = entry.category;
        body.appendChild(catBadge);
    }

    // Source badge
    const shortSource = shortenLogger(entry.logger);
    if (shortSource) {
        const source = document.createElement('span');
        source.className = 'log-source';
        source.textContent = shortSource;
        body.appendChild(source);
    }

    // Event name (prefer entry.event from WebSocket, fallback to parsed)
    const eventName = entry.event || entry.parsed.event;
    if (eventName) {
        const eventEl = document.createElement('span');
        eventEl.className = 'log-event';
        eventEl.textContent = eventName;
        body.appendChild(eventEl);
    }

    // Key-value pairs from fields (prefer WebSocket fields) or parsed
    const fields = Object.keys(entry.fields || {}).length > 0 ? entry.fields : {};
    const pairs = entry.parsed.pairs || [];

    // Render fields from WebSocket
    for (const [key, val] of Object.entries(fields)) {
        if (key === 'category') continue; // Already shown as badge
        const kv = document.createElement('span');
        kv.className = 'log-kv';
        kv.innerHTML = `<span class="log-kv-key">${escapeHtml(key)}</span><span class="log-kv-eq">=</span><span class="log-kv-val">${escapeHtml(String(val))}</span>`;
        body.appendChild(kv);
    }

    // Render parsed pairs (if no WebSocket fields)
    if (Object.keys(fields).length === 0 && pairs.length > 0) {
        pairs.forEach(({ key, val }) => {
            if (key === 'category') return; // Already shown as badge
            const kv = document.createElement('span');
            kv.className = 'log-kv';
            kv.innerHTML = `<span class="log-kv-key">${escapeHtml(key)}</span><span class="log-kv-eq">=</span><span class="log-kv-val">${escapeHtml(val)}</span>`;
            body.appendChild(kv);
        });
    }

    // Remaining text (if any)
    const parsed = entry.parsed;
    if (parsed.text) {
        const msgEl = document.createElement('span');
        msgEl.className = 'log-msg';
        msgEl.textContent = (eventName || Object.keys(fields).length > 0 || pairs.length > 0) ? ' ' + parsed.text : parsed.text;
        body.appendChild(msgEl);
    }

    // If no structured content was generated, show raw message
    if (!eventName && Object.keys(fields).length === 0 && pairs.length === 0 && !parsed.text) {
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
        ${entry.category ? `<div class="log-detail-line"><span class="log-detail-label">Category</span> <span class="log-detail-value">${entry.category}</span></div>` : ''}
        ${eventName ? `<div class="log-detail-line"><span class="log-detail-label">Event</span> <span class="log-detail-value">${eventName}</span></div>` : ''}
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

    // Category filter
    if (logState.categoryFilter && entry.category !== logState.categoryFilter) {
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

function updateCategoryFilter() {
    const select = document.getElementById('log-filter-category');
    if (!select) return;
    const current = select.value;
    const sorted = [...logState.knownCategories].sort();
    select.innerHTML = '<option value="">All Categories</option>' + sorted.map(c =>
        `<option value="${c}"${c === current ? ' selected' : ''}>${c.charAt(0).toUpperCase() + c.slice(1)}</option>`
    ).join('');
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
        const dot = document.querySelector('.status-indicator');
        const text = document.getElementById('status-text');
        const guildCount = document.getElementById('stat-guilds');
        const voiceCount = document.getElementById('stat-voice');
        const latency = document.getElementById('stat-latency-val');
        const sidebarLatency = document.getElementById('stat-latency');
        const cpu = document.getElementById('stat-cpu');
        const ram = document.getElementById('stat-ram');
        const cpuBar = document.getElementById('cpu-bar');
        const ramBar = document.getElementById('ram-bar');

        if (dot) {
            dot.classList.toggle('online', data.status === 'online');
            dot.classList.toggle('offline', data.status !== 'online');
        }
        if (text) text.textContent = data.status === 'online' ? 'Connected' : 'Offline';
        if (guildCount) guildCount.textContent = data.guilds || 0;
        if (voiceCount) voiceCount.textContent = data.voice_connections || 0;
        if (latency) latency.textContent = `${data.latency_ms || 0}ms`;
        if (sidebarLatency) sidebarLatency.textContent = `${data.latency_ms || 0}ms`;
        if (cpu) cpu.textContent = data.cpu_percent || 0;
        if (ram) ram.textContent = data.ram_percent || 0;

        // Update progress bars
        if (cpuBar) cpuBar.style.width = `${data.cpu_percent || 0}%`;
        if (ramBar) ramBar.style.width = `${data.ram_percent || 0}%`;
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

    let html = `<button class="nav-pill ${currentScope === 'global' ? 'active' : ''}" onclick="switchScope('global')">
        <span class="nav-pill-dot"></span>
        Global
    </button>`;

    html += guilds.map(g => `
        <button class="nav-pill ${currentScope === g.id ? 'active' : ''}" onclick="switchScope('${g.id}')">
            ${g.is_playing ? '<span class="nav-pill-dot" style="background: var(--success);"></span>' : ''}
            ${g.name || 'Server'}
        </button>
    `).join('');

    nav.innerHTML = html;
}

function updateGuildList(guilds) {
    const list = document.getElementById('guild-list');
    if (!list) return;

    if (!guilds || guilds.length === 0) {
        list.innerHTML = '<div class="list-empty">No servers connected</div>';
        return;
    }

    list.innerHTML = guilds.map(g => `
        <div class="server-card ${g.id === currentGuild ? 'active' : ''}" onclick="selectGuild(event, '${g.id}')">
            <div class="server-card-icon">${(g.name || '?').charAt(0).toUpperCase()}</div>
            <div class="server-card-info">
                <div class="server-card-name">${g.name || 'Unknown Server'}</div>
                <div class="server-card-stats">${g.member_count || 0} members ${g.is_playing ? '• ▶ Playing' : ''}</div>
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
        let progressPercent = 0;
        if (playing.duration_seconds) {
            const mins = Math.floor(playing.duration_seconds / 60);
            const secs = playing.duration_seconds % 60;
            durationStr = `${mins}:${secs.toString().padStart(2, '0')}`;
            // Calculate progress if we have position
            if (playing.position_seconds && playing.duration_seconds > 0) {
                progressPercent = (playing.position_seconds / playing.duration_seconds) * 100;
            }
        }

        np.innerHTML = `
            <div class="np-active">
                <div class="np-artwork">
                    <img src="https://img.youtube.com/vi/${playing.video_id || 'dQw4w9WgXcQ'}/hqdefault.jpg" alt="">
                </div>
                <div class="np-info">
                    <div class="np-title">${playing.current_song || 'Unknown'}</div>
                    <div class="np-artist">${playing.current_artist || 'Unknown Artist'}</div>
                    <div class="np-progress">
                        <div class="np-progress-fill" style="width: ${progressPercent}%"></div>
                    </div>
                    <div class="np-time">
                        <span>${playing.position_seconds ? formatTime(playing.position_seconds) : '0:00'}</span>
                        <span>${durationStr || '--:--'}</span>
                    </div>
                    <div class="np-meta">
                        ${playing.genre ? `<span class="np-meta-item">🏷️ ${playing.genre}</span>` : ''}
                        ${playing.discovery_reason ? `<span class="np-meta-item">✨ ${playing.discovery_reason}</span>` : ''}
                    </div>
                </div>
            </div>
        `;
    } else {
        np.innerHTML = `
            <div class="np-idle">
                <div class="np-idle-icon">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="48" height="48">
                        <circle cx="12" cy="12" r="10"/>
                        <polygon points="10 8 16 12 10 16 10 8" fill="currentColor" stroke="none"/>
                    </svg>
                </div>
                <span class="np-idle-text">Nothing playing</span>
                <span class="np-idle-hint">Use /play in Discord to start</span>
            </div>
        `;
    }
}

function formatTime(seconds) {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
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

        // Top songs (now using div-based song-list instead of table)
        const songList = document.getElementById('top-songs-table');
        if (songList) {
            if (!data.top_songs || data.top_songs.length === 0) {
                songList.innerHTML = '<div class="list-empty">No songs played yet</div>';
            } else {
                songList.innerHTML = data.top_songs.slice(0, 8).map((s, i) => `
                    <div class="song-item">
                        <div class="song-rank">${i + 1}</div>
                        <div class="song-info">
                            <div class="song-name">${s.title}</div>
                            <div class="song-artist">${s.artist || 'Unknown'}</div>
                        </div>
                        <div class="song-stats">
                            <span>${s.plays} plays</span>
                            <span>${s.likes || 0} ❤️</span>
                        </div>
                    </div>
                `).join('');
            }
        }

        // Top users
        const userList = document.getElementById('top-users-list');
        if (userList) {
            if (!data.top_users || data.top_users.length === 0) {
                userList.innerHTML = '<div class="list-empty">No users active yet</div>';
            } else {
                userList.innerHTML = data.top_users.slice(0, 6).map(u => `
                    <div class="user-item" onclick="viewUser('${u.id}')">
                        <div class="user-avatar">${(u.name || '?').charAt(0).toUpperCase()}</div>
                        <div class="user-info">
                            <div class="user-name">${u.name || 'Unknown'}</div>
                            <div class="user-stat">${u.plays || 0} plays • ${u.total_likes || 0} likes</div>
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
                usefulList.innerHTML = '<div class="list-empty">No useful activity yet</div>';
            } else {
                usefulList.innerHTML = data.top_useful_users.slice(0, 5).map(u => `
                    <div class="user-item">
                        <div class="user-avatar">${(u.username || '?').charAt(0).toUpperCase()}</div>
                        <div class="user-info">
                            <div class="user-name">${u.username || 'Unknown'}</div>
                            <div class="user-stat">${u.score || 0} points</div>
                        </div>
                    </div>
                `).join('');
            }
        }

        // Charts
        if (typeof Chart !== 'undefined') {
            updateCharts(data);
        }

    } catch (e) { console.error('Error updating analytics', e); }
}

let genreChartInstance = null;
let discoveryChartInstance = null;

function updateCharts(data) {
    const genreCtx = document.getElementById('genreChart');
    const discoveryCtx = document.getElementById('discoveryChart');

    // Genre Chart - Pie/Doughnut
    if (genreCtx && data.genre_distribution) {
        const labels = data.genre_distribution.map(d => d.name);
        const values = data.genre_distribution.map(d => d.plays);

        if (genreChartInstance) {
            genreChartInstance.data.labels = labels;
            genreChartInstance.data.datasets[0].data = values;
            genreChartInstance.update();
        } else {
            genreChartInstance = new Chart(genreCtx, {
                type: 'doughnut',
                data: {
                    labels: labels,
                    datasets: [{
                        label: 'Plays',
                        data: values,
                        backgroundColor: [
                            '#8b5cf6', '#ec4899', '#3b82f6', '#10b981', '#f59e0b',
                            '#6366f1', '#ef4444', '#14b8a6', '#f97316', '#84cc16'
                        ],
                        borderWidth: 0
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { position: 'right', labels: { color: '#9ca3af' } }
                    }
                }
            });
        }
    }

    // Discovery Chart - Bar/Pie
    if (discoveryCtx && data.discovery_breakdown) {
        const labels = data.discovery_breakdown.map(d => d.discovery_source.replace('_', ' '));
        const values = data.discovery_breakdown.map(d => d.count);

        if (discoveryChartInstance) {
            discoveryChartInstance.data.labels = labels;
            discoveryChartInstance.data.datasets[0].data = values;
            discoveryChartInstance.update();
        } else {
            discoveryChartInstance = new Chart(discoveryCtx, {
                type: 'bar',
                data: {
                    labels: labels,
                    datasets: [{
                        label: 'Plays by Source',
                        data: values,
                        backgroundColor: '#6366f1',
                        borderRadius: 4
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false },
                    },
                    scales: {
                        y: {
                            beginAtZero: true,
                            grid: { color: 'rgba(255, 255, 255, 0.1)' },
                            ticks: { color: '#9ca3af' }
                        },
                        x: {
                            grid: { display: false },
                            ticks: { color: '#9ca3af' }
                        }
                    }
                }
            });
        }
    }
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

    filterLibrary();
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

// switchTab function moved to end of file

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

// Updates settings panel visibility based on scope
function updateSettingsPanel() {
    const globalBlock = document.getElementById('settings-global');
    const serverBlock = document.getElementById('settings-server');

    if (currentScope === 'global') {
        if (globalBlock) globalBlock.style.display = 'block';
        if (serverBlock) serverBlock.style.display = 'none';
    } else {
        if (globalBlock) globalBlock.style.display = 'none';
        if (serverBlock) serverBlock.style.display = 'block';
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
    if (dd) dd.classList.toggle('open');
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

// ============================================================
// COMMAND PALETTE
// ============================================================
function initCommandPalette() {
    const modal = document.getElementById('search-modal');
    const input = document.getElementById('cp-input');
    const results = document.getElementById('cp-results');
    const globalSearch = document.getElementById('global-search');

    if (!modal || !input) return;

    // Open command palette with Cmd/Ctrl+K
    document.addEventListener('keydown', (e) => {
        if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
            e.preventDefault();
            openCommandPalette();
        }
        if (e.key === 'Escape' && modal.classList.contains('open')) {
            closeCommandPalette();
        }
    });

    // Click global search to open
    if (globalSearch) {
        globalSearch.addEventListener('click', openCommandPalette);
        globalSearch.addEventListener('focus', openCommandPalette);
    }

    // Close on overlay click
    modal.addEventListener('click', (e) => {
        if (e.target === modal) closeCommandPalette();
    });

    // Handle input
    input.addEventListener('input', () => {
        const query = input.value.toLowerCase().trim();
        filterCommandResults(query);
    });

    // Handle result clicks
    if (results) {
        results.addEventListener('click', (e) => {
            const item = e.target.closest('.cp-item');
            if (item) {
                const action = item.dataset.action;
                if (action) {
                    switchTab(action);
                    closeCommandPalette();
                }
            }
        });
    }
}

function openCommandPalette() {
    const modal = document.getElementById('search-modal');
    const input = document.getElementById('cp-input');
    if (modal) {
        modal.classList.add('open');
        if (input) {
            input.value = '';
            input.focus();
        }
    }
}

function closeCommandPalette() {
    const modal = document.getElementById('search-modal');
    if (modal) modal.classList.remove('open');
}

function filterCommandResults(query) {
    const results = document.getElementById('cp-results');
    if (!results) return;

    // Default quick actions
    const actions = [
        { action: 'dashboard', icon: '📊', label: 'Go to Dashboard' },
        { action: 'library', icon: '📚', label: 'Open Library' },
        { action: 'songs', icon: '🕒', label: 'View History' },
        { action: 'users', icon: '👥', label: 'View Users' },
        { action: 'servers', icon: '🏠', label: 'View Servers' },
        { action: 'logs', icon: '📜', label: 'View Logs' },
        { action: 'settings', icon: '⚙️', label: 'Open Settings' },
    ];

    const filtered = query
        ? actions.filter(a => a.label.toLowerCase().includes(query))
        : actions;

    results.innerHTML = `
        <div class="cp-section">
            <div class="cp-section-title">Quick Actions</div>
            ${filtered.map(a => `
                <div class="cp-item" data-action="${a.action}">
                    <span class="cp-icon">${a.icon}</span> ${a.label}
                </div>
            `).join('')}
            ${filtered.length === 0 ? '<div class="cp-item" style="color: var(--text-muted);">No results found</div>' : ''}
        </div>
    `;
}

// ============================================================
// GLOBAL SEARCH
// ============================================================
function initGlobalSearch() {
    const searchInput = document.getElementById('global-search');
    if (searchInput) {
        searchInput.addEventListener('focus', openCommandPalette);
    }
}

// ============================================================
// PANEL SEARCHES (Library, History, Users, Servers)
// ============================================================
function initPanelSearches() {
    // Library search & filter
    const librarySearch = document.getElementById('library-search');
    const genreFilter = document.getElementById('library-filter-genre');

    const debouncedLibraryFilter = debounce(filterLibrary, 200);

    if (librarySearch) librarySearch.addEventListener('input', debouncedLibraryFilter);
    if (genreFilter) genreFilter.addEventListener('change', debouncedLibraryFilter);

    // History search
    const historySearch = document.getElementById('history-search');
    if (historySearch) {
        historySearch.addEventListener('input', debounce(() => {
            filterTable('songs-list', historySearch.value);
        }, 200));
    }

    // Users search
    const usersSearch = document.getElementById('users-search');
    if (usersSearch) {
        usersSearch.addEventListener('input', debounce(() => {
            filterGrid('users-directory', usersSearch.value);
        }, 200));
    }

    // Servers search
    const serversSearch = document.getElementById('servers-search');
    if (serversSearch) {
        serversSearch.addEventListener('input', debounce(() => {
            filterGrid('guild-list', serversSearch.value);
        }, 200));
    }
}

async function fetchGenres() {
    try {
        const res = await fetch(API.genres);
        const data = await res.json();
        const select = document.getElementById('library-filter-genre');
        if (select && data.genres) {
            const current = select.value;
            select.innerHTML = '<option value="">All Genres</option>' +
                data.genres.map(g => `<option value="${g}">${g}</option>`).join('');
            select.value = current;
        }
    } catch (e) { console.error('Failed to fetch genres', e); }
}

function filterLibrary() {
    const searchInput = document.getElementById('library-search');
    const genreSelect = document.getElementById('library-filter-genre');
    const table = document.getElementById('library-list');

    if (!table) return;

    const query = searchInput ? searchInput.value.toLowerCase().trim() : '';
    const genre = genreSelect ? genreSelect.value.toLowerCase() : '';

    const rows = table.querySelectorAll('tr');

    rows.forEach(row => {
        if (row.querySelector('.table-empty')) return;

        // Col indices: 0=Title, 1=Artist, 2=Genre
        const cols = row.querySelectorAll('td');
        if (cols.length < 3) return;

        const title = cols[0].textContent.toLowerCase();
        const artist = cols[1].textContent.toLowerCase();
        const rowGenre = cols[2].textContent.toLowerCase();

        const matchesSearch = !query || title.includes(query) || artist.includes(query);
        const matchesGenre = !genre || rowGenre === genre || (genre === '-' && (rowGenre === '-' || rowGenre === ''));

        row.style.display = (matchesSearch && matchesGenre) ? '' : 'none';
    });
}


function filterTable(tableId, query) {
    const table = document.getElementById(tableId);
    if (!table) return;

    const rows = table.querySelectorAll('tr');
    const q = query.toLowerCase().trim();

    rows.forEach(row => {
        if (row.querySelector('.table-empty')) return;
        const text = row.textContent.toLowerCase();
        row.style.display = !q || text.includes(q) ? '' : 'none';
    });
}

function filterGrid(gridId, query) {
    const grid = document.getElementById(gridId);
    if (!grid) return;

    const items = grid.querySelectorAll('.user-card, .server-card, .user-item');
    const q = query.toLowerCase().trim();

    items.forEach(item => {
        const text = item.textContent.toLowerCase();
        item.style.display = !q || text.includes(q) ? '' : 'none';
    });
}

function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// ============================================================
// UPDATED SWITCH TAB
// ============================================================
function switchTab(tabName) {
    // Update sidebar links
    document.querySelectorAll('.sidebar-link').forEach(link => {
        link.classList.toggle('active', link.dataset.tab === tabName);
    });

    // Legacy .tab support
    document.querySelectorAll('.tab').forEach(t => {
        t.classList.toggle('active', t.dataset.tab === tabName);
    });

    // Show/hide panels
    document.querySelectorAll('.tab-panel, .tab-content').forEach(panel => {
        const panelId = panel.id.replace('tab-', '');
        const isActive = panelId === tabName;
        panel.classList.toggle('active', isActive);

        // Handle display - logs needs flex, others block
        if (isActive) {
            panel.style.display = tabName === 'logs' ? 'flex' : 'block';
        } else {
            panel.style.display = 'none';
        }
    });

    // Load settings if needed
    if (tabName === 'settings') {
        updateSettingsPanel();
        if (typeof loadSettingsTab === 'function') {
            loadSettingsTab();
        }
    }

    // Scroll logs to bottom when switching to logs tab
    if (tabName === 'logs' && logState.autoScroll) {
        requestAnimationFrame(scrollLogsToBottom);
    }
}
