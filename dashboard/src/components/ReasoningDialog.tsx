'use client';

import { X, Sparkles, Brain, ListMusic, User } from 'lucide-react';

interface Candidate {
    title: string;
    artist: string;
    score: number;
    source: string;
    video_id: string;
}

interface ReasoningData {
    user_vector_debug: string;
    candidates: Candidate[];
    temperature: number;
    top_k: number;
}

interface ReasoningDialogProps {
    isOpen: boolean;
    onClose: () => void;
    reasoning: ReasoningData;
    songTitle: string;
}

export default function ReasoningDialog({ isOpen, onClose, reasoning, songTitle }: ReasoningDialogProps) {
    if (!isOpen) return null;

    return (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 sm:p-6">
            <div
                className="absolute inset-0 bg-black/60 backdrop-blur-sm transition-opacity"
                onClick={onClose}
            />

            <div className="relative w-full max-w-2xl bg-zinc-900 border border-zinc-800 rounded-2xl shadow-2xl overflow-hidden flex flex-col max-h-[90vh]">
                {/* Header */}
                <div className="flex items-center justify-between p-4 border-b border-zinc-800 bg-zinc-900/50">
                    <div className="flex items-center gap-2">
                        <Sparkles className="w-5 h-5 text-violet-400" />
                        <h2 className="text-lg font-semibold text-white">Discovery Reasoning</h2>
                    </div>
                    <button
                        onClick={onClose}
                        className="p-1 hover:bg-white/10 rounded-lg transition-colors text-zinc-400 hover:text-white"
                    >
                        <X className="w-6 h-6" />
                    </button>
                </div>

                {/* Content */}
                <div className="flex-1 overflow-y-auto p-6 space-y-6">
                    {/* User Profile Summary */}
                    <section className="space-y-3">
                        <div className="flex items-center gap-2 text-zinc-400">
                            <Brain className="w-4 h-4" />
                            <h3 className="text-sm font-medium uppercase tracking-wider">User Taste Profile</h3>
                        </div>
                        <div className="p-4 bg-zinc-950 border border-zinc-800 rounded-xl font-mono text-xs text-violet-300 leading-relaxed">
                            {reasoning.user_vector_debug}
                        </div>
                    </section>

                    {/* Winning Song Explanation */}
                    <section className="space-y-3">
                        <div className="flex items-center gap-2 text-zinc-400">
                            <ListMusic className="w-4 h-4" />
                            <h3 className="text-sm font-medium uppercase tracking-wider">Scoring Breakdown (Top 10)</h3>
                        </div>
                        <p className="text-xs text-zinc-500 italic">
                            Scored via 128-dimensional vector cosine similarity + softmax selection (temp={reasoning.temperature})
                        </p>

                        <div className="space-y-2">
                            {reasoning.candidates.map((cand, idx) => {
                                const isWinner = cand.title === songTitle;
                                return (
                                    <div
                                        key={idx}
                                        className={`flex items-center justify-between p-3 rounded-lg border transition-all ${isWinner
                                                ? 'bg-violet-500/10 border-violet-500/30'
                                                : 'bg-zinc-950/50 border-zinc-800/50 hover:bg-zinc-800/30'
                                            }`}
                                    >
                                        <div className="flex-1 min-w-0 pr-4">
                                            <div className="flex items-center gap-2">
                                                <p className={`text-sm font-medium truncate ${isWinner ? 'text-violet-300' : 'text-zinc-200'}`}>
                                                    {cand.title}
                                                </p>
                                                {isWinner && (
                                                    <span className="px-1.5 py-0.5 rounded text-[9px] font-bold bg-violet-500 text-white uppercase">Picked</span>
                                                )}
                                            </div>
                                            <p className="text-xs text-zinc-500 truncate">{cand.artist} • <span className="capitalize">{cand.source}</span></p>
                                        </div>
                                        <div className="text-right">
                                            <div className={`text-sm font-mono font-bold ${isWinner ? 'text-violet-400' : 'text-zinc-400'}`}>
                                                {(cand.score * 100).toFixed(1)}%
                                            </div>
                                            <div className="w-16 h-1 bg-zinc-800 rounded-full mt-1 overflow-hidden">
                                                <div
                                                    className={`h-full rounded-full ${isWinner ? 'bg-violet-500' : 'bg-zinc-600'}`}
                                                    style={{ width: `${Math.max(0, Math.min(100, cand.score * 100))}%` }}
                                                />
                                            </div>
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    </section>
                </div>

                {/* Footer */}
                <div className="p-4 border-t border-zinc-800 bg-zinc-900/50 text-center">
                    <p className="text-[10px] text-zinc-500">Live vector reasoning data from Discovery Engine V2. No data is being stored.</p>
                </div>
            </div>
        </div>
    );
}
