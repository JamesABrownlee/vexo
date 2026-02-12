'use client';

import { Clock, Search, Music2, Calendar, X, CheckCircle2, XCircle, Sparkles, ExternalLink, SkipForward } from 'lucide-react';
import { useState, useMemo } from 'react';

interface HistoryItem {
    played_at: string;
    title: string;
    artist_name: string;
    album: string | null;
    release_year: number | null;
    duration_seconds: number | null;
    yt_id: string | null;
    genre: string | null;
    discovery_source: string | null;
    discovery_reason: string | null;
    completed: number;
    skip_reason: string | null;
    requested_by: string | null;
    liked_by: string | null;
    disliked_by: string | null;
}

interface HistoryPageClientProps {
    initialHistory: HistoryItem[];
}

// Discovery source labels and colors
// Note: backend may use both 'artist' (new) and 'same_artist' (legacy).
const discoveryLabels: Record<string, { label: string; color: string; icon: string }> = {
    user_request: { label: 'Requested', color: 'bg-blue-500/20 text-blue-400', icon: '🎯' },
    similar: { label: 'Similar', color: 'bg-violet-500/20 text-violet-400', icon: '🎵' },
    artist: { label: 'Artist', color: 'bg-pink-500/20 text-pink-400', icon: '🎤' },
    same_artist: { label: 'Artist', color: 'bg-pink-500/20 text-pink-400', icon: '🎤' },
    wildcard: { label: 'Discovery', color: 'bg-green-500/20 text-green-400', icon: '🎲' },
    library: { label: 'Library', color: 'bg-amber-500/20 text-amber-400', icon: '📚' },
};

function HistoryRow({ item, expanded, onToggle }: { item: HistoryItem; expanded: boolean; onToggle: () => void }) {
    const formatTime = (dateStr: string) => {
        const date = new Date(dateStr);
        return date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
    };

    const formatDate = (dateStr: string) => {
        const date = new Date(dateStr);
        return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    };

    const formatDuration = (seconds: number | null) => {
        if (!seconds) return '--:--';
        const mins = Math.floor(seconds / 60);
        const secs = seconds % 60;
        return `${mins}:${secs.toString().padStart(2, '0')}`;
    };

    const genres = item.genre?.split(',').slice(0, 2) || [];
    const likeCount = item.liked_by?.split(',').length || 0;
    const dislikeCount = item.disliked_by?.split(',').length || 0;
    const discovery = discoveryLabels[item.discovery_source || ''] || { label: 'Unknown', color: 'bg-zinc-500/20 text-zinc-400', icon: '❓' };
    const wasCompleted = item.completed === 1;

    return (
        <div className="group">
            {/* Main Row */}
            <div
                onClick={onToggle}
                className={`flex items-center gap-4 p-3 rounded-xl transition-all cursor-pointer ${expanded ? 'bg-white/[0.06]' : 'hover:bg-white/[0.04]'
                    }`}
            >
                {/* Status Icon */}
                <div className="w-6 shrink-0">
                    {wasCompleted ? (
                        <CheckCircle2 className="w-5 h-5 text-green-500" />
                    ) : (
                        <SkipForward className="w-5 h-5 text-orange-500" />
                    )}
                </div>

                {/* Time */}
                <div className="w-20 text-center shrink-0">
                    <p className="text-sm font-medium text-white">{formatTime(item.played_at)}</p>
                    <p className="text-xs text-zinc-600">{formatDate(item.played_at)}</p>
                </div>

                {/* Song Info */}
                <div className="flex items-center gap-3 flex-1 min-w-0">
                    <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-violet-500/20 to-pink-500/20 flex items-center justify-center shrink-0 group-hover:from-violet-500/30 group-hover:to-pink-500/30 transition-colors overflow-hidden">
                        {item.yt_id ? (
                            <img
                                src={`https://i.ytimg.com/vi/${item.yt_id}/default.jpg`}
                                alt=""
                                className="w-full h-full object-cover"
                            />
                        ) : (
                            <Music2 className="w-5 h-5 text-violet-400" />
                        )}
                    </div>
                    <div className="min-w-0">
                        <p className="text-sm font-medium text-white truncate group-hover:text-violet-400 transition-colors">
                            {item.title}
                        </p>
                        <p className="text-xs text-zinc-500 truncate">
                            {item.artist_name}
                            {item.album && <span className="text-zinc-600"> · {item.album}</span>}
                            {item.release_year && <span className="text-zinc-600"> ({item.release_year})</span>}
                        </p>
                    </div>
                </div>

                {/* Genre */}
                <div className="w-36 shrink-0 flex gap-1 flex-wrap">
                    {genres.map((genre, i) => (
                        <span key={i} className="text-xs px-2 py-0.5 rounded-full bg-violet-500/20 text-violet-400 truncate max-w-[70px]">
                            {genre.trim()}
                        </span>
                    ))}
                    {genres.length === 0 && <span className="text-xs text-zinc-600">—</span>}
                </div>

                {/* Duration */}
                <div className="w-14 text-center shrink-0">
                    <span className="text-sm text-zinc-400 font-mono">{formatDuration(item.duration_seconds)}</span>
                </div>

                {/* Discovery Source */}
                <div className="w-28 shrink-0">
                    <span className={`text-xs px-2 py-1 rounded-full ${discovery.color} flex items-center gap-1 justify-center`}>
                        <span>{discovery.icon}</span>
                        {discovery.label}
                    </span>
                </div>

                {/* Reactions */}
                <div className="w-20 text-right shrink-0 flex items-center justify-end gap-2">
                    {likeCount > 0 && (
                        <span className="text-xs text-green-500 flex items-center gap-1">
                            ❤️ {likeCount}
                        </span>
                    )}
                    {dislikeCount > 0 && (
                        <span className="text-xs text-red-500 flex items-center gap-1">
                            👎 {dislikeCount}
                        </span>
                    )}
                    {likeCount === 0 && dislikeCount === 0 && (
                        <span className="text-xs text-zinc-600">—</span>
                    )}
                </div>
            </div>

            {/* Expanded Details */}
            {expanded && (
                <div className="px-4 py-3 mx-3 mb-2 rounded-xl bg-white/[0.02] border border-white/[0.05] space-y-2">
                    {/* Discovery Reason */}
                    {item.discovery_reason && (
                        <div className="flex items-start gap-2">
                            <Sparkles className="w-4 h-4 text-green-500 mt-0.5 shrink-0" />
                            <div>
                                <p className="text-xs text-zinc-500">Discovery Reason</p>
                                <p className="text-sm text-white">{item.discovery_reason}</p>
                            </div>
                        </div>
                    )}

                    {/* Requested By */}
                    {item.requested_by && (
                        <div className="flex items-start gap-2">
                            <span className="text-sm">🎯</span>
                            <div>
                                <p className="text-xs text-zinc-500">Requested By</p>
                                <p className="text-sm text-blue-400">{item.requested_by}</p>
                            </div>
                        </div>
                    )}

                    {/* Skip Reason */}
                    {!wasCompleted && item.skip_reason && (
                        <div className="flex items-start gap-2">
                            <XCircle className="w-4 h-4 text-orange-500 mt-0.5 shrink-0" />
                            <div>
                                <p className="text-xs text-zinc-500">Skip Reason</p>
                                <p className="text-sm text-orange-400 capitalize">{item.skip_reason}</p>
                            </div>
                        </div>
                    )}

                    {/* Liked By */}
                    {item.liked_by && (
                        <div className="flex items-start gap-2">
                            <span className="text-sm">❤️</span>
                            <div>
                                <p className="text-xs text-zinc-500">Liked By</p>
                                <p className="text-sm text-green-400">{item.liked_by}</p>
                            </div>
                        </div>
                    )}

                    {/* YouTube Link */}
                    {item.yt_id && (
                        <a
                            href={`https://youtube.com/watch?v=${item.yt_id}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center gap-1 text-xs text-zinc-400 hover:text-white transition-colors mt-2"
                            onClick={e => e.stopPropagation()}
                        >
                            <ExternalLink className="w-3 h-3" />
                            Open on YouTube
                        </a>
                    )}
                </div>
            )}
        </div>
    );
}

export default function HistoryPageClient({ initialHistory }: HistoryPageClientProps) {
    const [searchQuery, setSearchQuery] = useState('');
    const [genreFilter, setGenreFilter] = useState<string>('all');
    const [sourceFilter, setSourceFilter] = useState<
        'all' | 'user_request' | 'similar' | 'artist' | 'same_artist' | 'wildcard' | 'library'
    >('all');
    const [statusFilter, setStatusFilter] = useState<'all' | 'completed' | 'skipped'>('all');
    const [dateFilter, setDateFilter] = useState<'all' | 'today' | 'week' | 'month'>('all');
    const [expandedIndex, setExpandedIndex] = useState<number | null>(null);

    // Get unique genres
    const allGenres = useMemo(() => {
        const genres = new Set<string>();
        initialHistory.forEach(item => {
            item.genre?.split(',').forEach(g => genres.add(g.trim()));
        });
        return Array.from(genres).sort();
    }, [initialHistory]);

    // Filter history
    const filteredHistory = useMemo(() => {
        let result = [...initialHistory];

        // Search filter
        if (searchQuery) {
            const query = searchQuery.toLowerCase();
            result = result.filter(item =>
                item.title?.toLowerCase().includes(query) ||
                item.artist_name?.toLowerCase().includes(query) ||
                item.album?.toLowerCase().includes(query)
            );
        }

        // Genre filter
        if (genreFilter !== 'all') {
            result = result.filter(item =>
                item.genre?.toLowerCase().includes(genreFilter.toLowerCase())
            );
        }

        // Source filter
        if (sourceFilter !== 'all') {
            result = result.filter(item => item.discovery_source === sourceFilter);
        }

        // Status filter
        if (statusFilter === 'completed') {
            result = result.filter(item => item.completed === 1);
        } else if (statusFilter === 'skipped') {
            result = result.filter(item => item.completed !== 1);
        }

        // Date filter
        const now = new Date();
        if (dateFilter === 'today') {
            const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
            result = result.filter(item => new Date(item.played_at) >= today);
        } else if (dateFilter === 'week') {
            const weekAgo = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
            result = result.filter(item => new Date(item.played_at) >= weekAgo);
        } else if (dateFilter === 'month') {
            const monthAgo = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000);
            result = result.filter(item => new Date(item.played_at) >= monthAgo);
        }

        return result;
    }, [initialHistory, searchQuery, genreFilter, sourceFilter, statusFilter, dateFilter]);

    // Stats
    const totalDuration = initialHistory.reduce((sum, item) => sum + (item.duration_seconds || 0), 0);
    const totalHours = Math.floor(totalDuration / 3600);
    const totalMins = Math.floor((totalDuration % 3600) / 60);
    const completedCount = initialHistory.filter(h => h.completed === 1).length;
    const skippedCount = initialHistory.length - completedCount;
    const discoveryCount = initialHistory.filter(h => h.discovery_source !== 'user_request').length;

    const hasActiveFilters = searchQuery || genreFilter !== 'all' || sourceFilter !== 'all' || statusFilter !== 'all' || dateFilter !== 'all';

    const clearFilters = () => {
        setSearchQuery('');
        setGenreFilter('all');
        setSourceFilter('all');
        setStatusFilter('all');
        setDateFilter('all');
    };

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-white flex items-center gap-3">
                        <Clock className="w-7 h-7 text-violet-500" />
                        Playback History
                    </h1>
                    <p className="text-sm text-zinc-500 mt-1">
                        {initialHistory.length} tracks · {totalHours}h {totalMins}m total playtime
                    </p>
                </div>
            </div>

            {/* Quick Stats */}
            <div className="grid grid-cols-5 gap-4">
                <div className="bento-card text-center">
                    <p className="text-3xl font-bold text-white">{initialHistory.length}</p>
                    <p className="text-sm text-zinc-500">Total Plays</p>
                </div>
                <div className="bento-card text-center">
                    <p className="text-3xl font-bold text-green-400">{completedCount}</p>
                    <p className="text-sm text-zinc-500 flex items-center justify-center gap-1">
                        <CheckCircle2 className="w-3 h-3" />
                        Completed
                    </p>
                </div>
                <div className="bento-card text-center">
                    <p className="text-3xl font-bold text-orange-400">{skippedCount}</p>
                    <p className="text-sm text-zinc-500 flex items-center justify-center gap-1">
                        <SkipForward className="w-3 h-3" />
                        Skipped
                    </p>
                </div>
                <div className="bento-card text-center">
                    <p className="text-3xl font-bold text-blue-400">{discoveryCount}</p>
                    <p className="text-sm text-zinc-500 flex items-center justify-center gap-1">
                        <Sparkles className="w-3 h-3" />
                        Discovery
                    </p>
                </div>
                <div className="bento-card text-center">
                    <p className="text-3xl font-bold text-violet-400">{totalHours}h {totalMins}m</p>
                    <p className="text-sm text-zinc-500">Total Duration</p>
                </div>
            </div>

            {/* Filters */}
            <div className="flex items-center gap-3 flex-wrap">
                {/* Search */}
                <div className="flex items-center gap-2 px-4 py-2 rounded-xl bg-white/[0.04] border border-white/[0.08] flex-1 max-w-sm">
                    <Search className="w-4 h-4 text-zinc-400" />
                    <input
                        type="text"
                        placeholder="Search songs, artists, albums..."
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        className="bg-transparent text-sm text-white placeholder-zinc-500 outline-none w-full"
                    />
                </div>

                {/* Genre Filter */}
                <select
                    value={genreFilter}
                    onChange={(e) => setGenreFilter(e.target.value)}
                    className="px-3 py-2 rounded-xl bg-white/[0.04] border border-white/[0.08] text-sm text-white outline-none cursor-pointer"
                >
                    <option value="all">All Genres</option>
                    {allGenres.slice(0, 20).map(genre => (
                        <option key={genre} value={genre}>{genre}</option>
                    ))}
                </select>

                {/* Source Filter */}
                <select
                    value={sourceFilter}
                    onChange={(e) => setSourceFilter(e.target.value as typeof sourceFilter)}
                    className="px-3 py-2 rounded-xl bg-white/[0.04] border border-white/[0.08] text-sm text-white outline-none cursor-pointer"
                >
                    <option value="all">All Sources</option>
                    <option value="user_request">🎯 Requested</option>
                    <option value="similar">🎵 Similar</option>
                    <option value="artist">🎤 Artist</option>
                    <option value="same_artist">🎤 Artist (legacy)</option>
                    <option value="wildcard">🎲 Discovery</option>
                    <option value="library">📚 Library</option>
                </select>

                {/* Status Filter */}
                <select
                    value={statusFilter}
                    onChange={(e) => setStatusFilter(e.target.value as typeof statusFilter)}
                    className="px-3 py-2 rounded-xl bg-white/[0.04] border border-white/[0.08] text-sm text-white outline-none cursor-pointer"
                >
                    <option value="all">All Status</option>
                    <option value="completed">✓ Completed</option>
                    <option value="skipped">⏭ Skipped</option>
                </select>

                {/* Date Filter */}
                <select
                    value={dateFilter}
                    onChange={(e) => setDateFilter(e.target.value as typeof dateFilter)}
                    className="px-3 py-2 rounded-xl bg-white/[0.04] border border-white/[0.08] text-sm text-white outline-none cursor-pointer"
                >
                    <option value="all">All Time</option>
                    <option value="today">Today</option>
                    <option value="week">This Week</option>
                    <option value="month">This Month</option>
                </select>

                {/* Clear Filters */}
                {hasActiveFilters && (
                    <button
                        onClick={clearFilters}
                        className="px-3 py-2 rounded-xl bg-red-500/20 text-red-400 text-sm flex items-center gap-1 hover:bg-red-500/30 transition-colors"
                    >
                        <X className="w-4 h-4" />
                        Clear
                    </button>
                )}
            </div>

            {/* Results Count */}
            {hasActiveFilters && (
                <p className="text-sm text-zinc-500">
                    Showing {filteredHistory.length} of {initialHistory.length} tracks
                </p>
            )}

            {/* Table Header */}
            <div className="flex items-center gap-4 px-3 py-2 text-xs text-zinc-500 uppercase tracking-wider border-b border-white/[0.08]">
                <div className="w-6"></div>
                <div className="w-20 text-center">Time</div>
                <div className="flex-1">Song / Album</div>
                <div className="w-36">Genre</div>
                <div className="w-14 text-center">Duration</div>
                <div className="w-28 text-center">Source</div>
                <div className="w-20 text-right">Reactions</div>
            </div>

            {/* History List */}
            <div className="space-y-1 max-h-[600px] overflow-y-auto">
                {filteredHistory.length === 0 ? (
                    <div className="bento-card text-center py-12">
                        <Clock className="w-12 h-12 text-zinc-600 mx-auto mb-4" />
                        <p className="text-lg font-medium text-zinc-400">
                            {hasActiveFilters ? 'No tracks match your filters' : 'No playback history'}
                        </p>
                        <p className="text-sm text-zinc-600">
                            {hasActiveFilters ? 'Try adjusting your filters' : 'Songs will appear here after they\'re played'}
                        </p>
                    </div>
                ) : (
                    filteredHistory.map((item, i) => (
                        <HistoryRow
                            key={i}
                            item={item}
                            expanded={expandedIndex === i}
                            onToggle={() => setExpandedIndex(expandedIndex === i ? null : i)}
                        />
                    ))
                )}
            </div>
        </div>
    );
}
