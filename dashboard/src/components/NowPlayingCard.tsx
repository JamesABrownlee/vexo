'use client';

import { useEffect, useState } from 'react';
import { Music2, RefreshCw, AudioLines, Info } from 'lucide-react';
import ReasoningDialog from './ReasoningDialog';

interface PlayingGuild {
    id: string;
    name: string;
    is_playing: boolean;
    current_song?: string;
    current_artist?: string;
    video_id?: string;
    duration_seconds?: number;
    genre?: string | null;
    discovery_reason?: string | null;
    requested_by?: string | null;
    reasoning?: any;
}

export default function NowPlayingCard() {
    const [playing, setPlaying] = useState<PlayingGuild | null>(null);
    const [loading, setLoading] = useState(true);
    const [isReasoningOpen, setIsReasoningOpen] = useState(false);

    const fetchNowPlaying = async () => {
        try {
            const res = await fetch('/api/bot/guilds');
            if (!res.ok) throw new Error('Failed to fetch');

            const data = await res.json();
            const guilds: PlayingGuild[] = data.guilds || [];
            const active = guilds.find(g => g.is_playing && g.current_song);
            setPlaying(active || null);
        } catch (err) {
            console.error('Error fetching now playing:', err);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchNowPlaying();
        const interval = setInterval(fetchNowPlaying, 5000);
        return () => clearInterval(interval);
    }, []);

    if (loading) {
        return (
            <div className="bento-card animate-pulse h-[140px]">
                <div className="flex items-center gap-2 mb-4">
                    <div className="w-2 h-2 rounded-full bg-zinc-700" />
                    <div className="h-4 w-24 bg-zinc-700 rounded" />
                </div>
                <div className="flex gap-4">
                    <div className="w-16 h-16 rounded-xl bg-zinc-700" />
                    <div className="space-y-2 flex-1">
                        <div className="h-5 w-3/4 bg-zinc-700 rounded" />
                        <div className="h-4 w-1/2 bg-zinc-700 rounded" />
                    </div>
                </div>
            </div>
        );
    }

    if (!playing) {
        return (
            <div className="bento-card bg-gradient-to-br from-zinc-900 to-zinc-950 border-zinc-800 h-[140px] flex flex-col justify-center">
                <div className="flex items-center gap-2 mb-2 text-zinc-500">
                    <div className="w-2 h-2 rounded-full bg-zinc-600" />
                    <span className="text-xs uppercase tracking-wider font-semibold">Idle</span>
                </div>
                <div className="flex items-center gap-4 opacity-50">
                    <div className="w-12 h-12 rounded-xl bg-white/5 flex items-center justify-center">
                        <Music2 className="w-6 h-6 text-zinc-500" />
                    </div>
                    <div>
                        <p className="text-sm font-medium text-zinc-400">Nothing playing</p>
                        <p className="text-xs text-zinc-600">Bot is waiting for commands</p>
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div className="bento-card bg-gradient-to-br from-violet-950/30 to-fuchsia-950/30 border-violet-500/20 relative overflow-hidden group h-[140px]">
            {/* Animated Background Mesh */}
            <div className="absolute inset-0 bg-[url('/noise.png')] opacity-20 mix-blend-overlay pointer-events-none" />
            <div className="absolute -top-10 -right-10 w-40 h-40 bg-violet-500/10 blur-[60px] rounded-full pointer-events-none" />

            {/* Header */}
            <div className="flex items-center justify-between mb-3 relative z-10">
                <div className="flex items-center gap-2">
                    <span className="relative flex h-2 w-2">
                        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"></span>
                        <span className="relative inline-flex rounded-full h-2 w-2 bg-green-500"></span>
                    </span>
                    <span className="text-xs font-semibold text-green-400 tracking-wide uppercase">Now Playing</span>
                </div>
                {playing.name && (
                    <div className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-white/5 border border-white/5">
                        <AudioLines className="w-3 h-3 text-zinc-400" />
                        <span className="text-[10px] font-medium text-zinc-300 max-w-[100px] truncate">
                            {playing.name}
                        </span>
                    </div>
                )}
            </div>

            {/* Content */}
            <div className="flex gap-4 relative z-10">
                {/* Album Art */}
                <div className="relative w-16 h-16 shrink-0 rounded-xl overflow-hidden shadow-lg shadow-black/20 group-hover:scale-105 transition-transform duration-500 border border-white/10">
                    <img
                        src={`https://img.youtube.com/vi/${playing.video_id || 'dQw4w9WgXcQ'}/mqdefault.jpg`}
                        alt="Album Art"
                        className="w-full h-full object-cover"
                    />
                    <div className="absolute inset-0 ring-1 ring-inset ring-black/10 rounded-xl" />
                </div>

                {/* Info */}
                <div className="flex-1 min-w-0 flex flex-col justify-center">
                    <a
                        href={`https://youtu.be/${playing.video_id}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-white font-bold text-base leading-tight truncate hover:text-violet-300 transition-colors mb-0.5 block"
                    >
                        {playing.current_song}
                    </a>
                    <p className="text-zinc-400 text-xs truncate font-medium mb-2">{playing.current_artist}</p>

                    <div className="flex flex-wrap gap-1.5">
                        {playing.requested_by ? (
                            <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-medium bg-blue-500/10 text-blue-300 border border-blue-500/20">
                                👤 {playing.requested_by.split(',')[0]}
                            </span>
                        ) : (
                            <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-medium bg-purple-500/10 text-purple-300 border border-purple-500/20">
                                ✨ Auto Discovery
                            </span>
                        )}

                        {playing.genre && (
                            <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-medium bg-zinc-800/50 text-zinc-400 border border-zinc-700/50 truncate max-w-[80px]">
                                {playing.genre}
                            </span>
                        )}

                        {playing.reasoning && (
                            <button
                                onClick={() => setIsReasoningOpen(true)}
                                className="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-bold bg-violet-500/20 text-violet-400 border border-violet-500/30 hover:bg-violet-500/30 transition-colors gap-1"
                            >
                                <Info className="w-2.5 h-2.5" />
                                WHY?
                            </button>
                        )}
                    </div>
                </div>
            </div>

            {playing.reasoning && (
                <ReasoningDialog
                    isOpen={isReasoningOpen}
                    onClose={() => setIsReasoningOpen(false)}
                    reasoning={playing.reasoning}
                    songTitle={playing.current_song || ''}
                />
            )}
        </div>
    );
}
