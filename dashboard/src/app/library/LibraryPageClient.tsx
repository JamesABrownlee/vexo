'use client';

import { Library as LibraryIcon, Search, Music2, User, Calendar, X, Heart, Play, Clock, ExternalLink } from 'lucide-react';
import { useState, useMemo } from 'react';

interface LibraryItem {
    id: number;
    title: string;
    artist_name: string;
    album: string | null;
    release_year: number | null;
    duration_seconds: number | null;
    yt_id: string | null;
    spotify_id: string | null;
    genre: string | null;
    contributors: string | null;
    sources: string | null;
    last_added: string;
    play_count: number;
    like_count: number;
    dislike_count: number;
}

interface LibraryPageClientProps {
    initialLibrary: LibraryItem[];
}

function formatDuration(seconds: number | null): string {
    if (!seconds) return '--:--';
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}:${secs.toString().padStart(2, '0')}`;
}

function LibraryCard({ item }: { item: LibraryItem }) {
    const formatDate = (dateStr: string) => {
        if (!dateStr) return '—';
        const date = new Date(dateStr);
        return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    };

    const getSourceBadge = (source: string) => {
        const badges: Record<string, { color: string; label: string }> = {
            'spotify_import': { color: 'bg-green-500/20 text-green-400', label: 'Spotify' },
            'youtube_import': { color: 'bg-red-500/20 text-red-400', label: 'YouTube' },
            'user_request': { color: 'bg-blue-500/20 text-blue-400', label: 'Request' },
            'request': { color: 'bg-blue-500/20 text-blue-400', label: 'Request' },
            'reaction': { color: 'bg-pink-500/20 text-pink-400', label: 'Liked' },
            'like': { color: 'bg-pink-500/20 text-pink-400', label: 'Liked' },
            'import': { color: 'bg-green-500/20 text-green-400', label: 'Import' },
        };
        return badges[source] || { color: 'bg-zinc-500/20 text-zinc-400', label: source };
    };

    const genres = item.genre?.split(',').slice(0, 2) || [];
    const sources = item.sources?.split(',') || [];
    const contributorCount = item.contributors?.split(',').length || 0;

    return (
        <div className="bento-card flex items-center gap-4 group hover:border-violet-500/30 transition-all">
            {/* Thumbnail */}
            <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-violet-500/20 to-pink-500/20 flex items-center justify-center shrink-0 group-hover:from-violet-500/30 group-hover:to-pink-500/30 transition-colors overflow-hidden">
                {item.yt_id ? (
                    <img
                        src={`https://i.ytimg.com/vi/${item.yt_id}/default.jpg`}
                        alt=""
                        className="w-full h-full object-cover"
                    />
                ) : (
                    <Music2 className="w-6 h-6 text-violet-400" />
                )}
            </div>

            {/* Song Info */}
            <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                    <p className="text-base font-medium text-white truncate group-hover:text-violet-400 transition-colors">
                        {item.title}
                    </p>
                    {item.yt_id && (
                        <a
                            href={`https://youtube.com/watch?v=${item.yt_id}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            onClick={e => e.stopPropagation()}
                            className="opacity-0 group-hover:opacity-100 transition-opacity"
                        >
                            <ExternalLink className="w-3 h-3 text-zinc-500 hover:text-white" />
                        </a>
                    )}
                </div>
                <p className="text-sm text-zinc-500 truncate">
                    {item.artist_name}
                    {item.album && <span className="text-zinc-600"> · {item.album}</span>}
                    {item.release_year && <span className="text-zinc-600"> ({item.release_year})</span>}
                </p>
            </div>

            {/* Duration */}
            <div className="w-14 text-center shrink-0">
                <span className="text-sm text-zinc-400 font-mono">{formatDuration(item.duration_seconds)}</span>
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

            {/* Sources */}
            <div className="w-28 shrink-0 flex gap-1 flex-wrap">
                {sources.slice(0, 2).map((source, i) => {
                    const badge = getSourceBadge(source.trim());
                    return (
                        <span key={i} className={`text-xs px-2 py-0.5 rounded-full ${badge.color}`}>
                            {badge.label}
                        </span>
                    );
                })}
            </div>

            {/* Stats */}
            <div className="w-24 shrink-0 flex items-center justify-center gap-3">
                <span className="text-xs text-zinc-400 flex items-center gap-1">
                    <Play className="w-3 h-3" />
                    {item.play_count || 0}
                </span>
                <span className="text-xs text-pink-400 flex items-center gap-1">
                    <Heart className="w-3 h-3" />
                    {item.like_count || 0}
                </span>
            </div>

            {/* Contributors */}
            <div className="w-16 shrink-0 flex items-center gap-1 justify-center">
                <User className="w-4 h-4 text-zinc-600" />
                <span className="text-sm text-zinc-400">{contributorCount}</span>
            </div>

            {/* Date Added */}
            <div className="w-20 text-right shrink-0 flex items-center gap-1 justify-end">
                <Calendar className="w-4 h-4 text-zinc-600" />
                <span className="text-sm text-zinc-400">{formatDate(item.last_added)}</span>
            </div>
        </div>
    );
}

export default function LibraryPageClient({ initialLibrary }: LibraryPageClientProps) {
    const [searchQuery, setSearchQuery] = useState('');
    const [genreFilter, setGenreFilter] = useState<string>('all');
    const [sourceFilter, setSourceFilter] = useState<string>('all');
    const [sortBy, setSortBy] = useState<'recent' | 'title' | 'artist' | 'plays' | 'likes'>('recent');

    // Get unique genres and sources
    const { allGenres, allSources } = useMemo(() => {
        const genres = new Set<string>();
        const sources = new Set<string>();

        initialLibrary.forEach(item => {
            item.genre?.split(',').forEach(g => genres.add(g.trim()));
            item.sources?.split(',').forEach(s => sources.add(s.trim()));
        });

        return {
            allGenres: Array.from(genres).sort(),
            allSources: Array.from(sources).sort(),
        };
    }, [initialLibrary]);

    // Filter and sort library
    const filteredLibrary = useMemo(() => {
        let result = [...initialLibrary];

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
            result = result.filter(item =>
                item.sources?.toLowerCase().includes(sourceFilter.toLowerCase())
            );
        }

        // Sort
        result.sort((a, b) => {
            switch (sortBy) {
                case 'title':
                    return (a.title || '').localeCompare(b.title || '');
                case 'artist':
                    return (a.artist_name || '').localeCompare(b.artist_name || '');
                case 'plays':
                    return (b.play_count || 0) - (a.play_count || 0);
                case 'likes':
                    return (b.like_count || 0) - (a.like_count || 0);
                case 'recent':
                default:
                    return new Date(b.last_added).getTime() - new Date(a.last_added).getTime();
            }
        });

        return result;
    }, [initialLibrary, searchQuery, genreFilter, sourceFilter, sortBy]);

    // Stats
    const uniqueArtists = new Set(initialLibrary.map(i => i.artist_name)).size;
    const uniqueGenres = allGenres.length;
    const totalPlays = initialLibrary.reduce((sum, i) => sum + (i.play_count || 0), 0);
    const totalLikes = initialLibrary.reduce((sum, i) => sum + (i.like_count || 0), 0);
    const totalDuration = initialLibrary.reduce((sum, i) => sum + (i.duration_seconds || 0), 0);
    const totalHours = Math.floor(totalDuration / 3600);
    const totalMins = Math.floor((totalDuration % 3600) / 60);

    const hasActiveFilters = searchQuery || genreFilter !== 'all' || sourceFilter !== 'all';

    const clearFilters = () => {
        setSearchQuery('');
        setGenreFilter('all');
        setSourceFilter('all');
    };

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-white flex items-center gap-3">
                        <LibraryIcon className="w-7 h-7 text-violet-500" />
                        Song Library
                    </h1>
                    <p className="text-sm text-zinc-500 mt-1">
                        {initialLibrary.length} songs from {uniqueArtists} artists · {totalHours}h {totalMins}m total
                    </p>
                </div>
            </div>

            {/* Quick Stats */}
            <div className="grid grid-cols-5 gap-4">
                <div className="bento-card text-center">
                    <p className="text-3xl font-bold text-white">{initialLibrary.length}</p>
                    <p className="text-sm text-zinc-500">Songs</p>
                </div>
                <div className="bento-card text-center">
                    <p className="text-3xl font-bold text-violet-400">{uniqueArtists}</p>
                    <p className="text-sm text-zinc-500">Artists</p>
                </div>
                <div className="bento-card text-center">
                    <p className="text-3xl font-bold text-pink-400">{uniqueGenres}</p>
                    <p className="text-sm text-zinc-500">Genres</p>
                </div>
                <div className="bento-card text-center">
                    <p className="text-3xl font-bold text-blue-400">{totalPlays.toLocaleString()}</p>
                    <p className="text-sm text-zinc-500 flex items-center justify-center gap-1">
                        <Play className="w-3 h-3" />
                        Total Plays
                    </p>
                </div>
                <div className="bento-card text-center">
                    <p className="text-3xl font-bold text-green-400">{totalLikes}</p>
                    <p className="text-sm text-zinc-500 flex items-center justify-center gap-1">
                        <Heart className="w-3 h-3" />
                        Total Likes
                    </p>
                </div>
            </div>

            {/* Filters */}
            <div className="flex items-center gap-4 flex-wrap">
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
                    className="px-4 py-2 rounded-xl bg-white/[0.04] border border-white/[0.08] text-sm text-white outline-none cursor-pointer"
                >
                    <option value="all">All Genres</option>
                    {allGenres.slice(0, 20).map(genre => (
                        <option key={genre} value={genre}>{genre}</option>
                    ))}
                </select>

                {/* Source Filter */}
                <select
                    value={sourceFilter}
                    onChange={(e) => setSourceFilter(e.target.value)}
                    className="px-4 py-2 rounded-xl bg-white/[0.04] border border-white/[0.08] text-sm text-white outline-none cursor-pointer"
                >
                    <option value="all">All Sources</option>
                    {allSources.map(source => (
                        <option key={source} value={source}>{source.replace('_', ' ')}</option>
                    ))}
                </select>

                {/* Sort */}
                <select
                    value={sortBy}
                    onChange={(e) => setSortBy(e.target.value as typeof sortBy)}
                    className="px-4 py-2 rounded-xl bg-white/[0.04] border border-white/[0.08] text-sm text-white outline-none cursor-pointer"
                >
                    <option value="recent">Recently Added</option>
                    <option value="title">Title A-Z</option>
                    <option value="artist">Artist A-Z</option>
                    <option value="plays">Most Played</option>
                    <option value="likes">Most Liked</option>
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
                    Showing {filteredLibrary.length} of {initialLibrary.length} songs
                </p>
            )}

            {/* Table Header */}
            <div className="flex items-center gap-4 px-3 py-2 text-xs text-zinc-500 uppercase tracking-wider border-b border-white/[0.08]">
                <div className="w-12"></div>
                <div className="flex-1">Song / Artist</div>
                <div className="w-14 text-center">Duration</div>
                <div className="w-36">Genre</div>
                <div className="w-28">Source</div>
                <div className="w-24 text-center">Stats</div>
                <div className="w-16 text-center">Users</div>
                <div className="w-20 text-right">Added</div>
            </div>

            {/* Library Grid */}
            <div className="space-y-2 max-h-[600px] overflow-y-auto">
                {filteredLibrary.length === 0 ? (
                    <div className="bento-card text-center py-12">
                        <LibraryIcon className="w-12 h-12 text-zinc-600 mx-auto mb-4" />
                        <p className="text-lg font-medium text-zinc-400">
                            {hasActiveFilters ? 'No songs match your filters' : 'Library is empty'}
                        </p>
                        <p className="text-sm text-zinc-600">
                            {hasActiveFilters ? 'Try adjusting your filters' : 'Songs will appear here after they\'re added to libraries'}
                        </p>
                    </div>
                ) : (
                    filteredLibrary.map((item) => (
                        <LibraryCard key={item.id} item={item} />
                    ))
                )}
            </div>
        </div>
    );
}
