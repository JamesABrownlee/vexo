'use client';

import { Cog, RotateCcw, CheckCircle, XCircle, Activity, Clock, Bot, Globe, RefreshCw } from 'lucide-react';
import { useState, useEffect, useCallback } from 'react';

interface Service {
    id: string;
    name: string;
    description: string;
    status: 'online' | 'offline' | 'restarting' | 'starting';
    uptime?: string;
    restartable: boolean;
}

const BOT_API_URL = '/api/bot';

function ServiceCard({
    service,
    onRestart,
    isRestarting,
}: {
    service: Service;
    onRestart: () => void;
    isRestarting: boolean;
}) {
    const Icon = service.id === 'bot' ? Bot : Globe;

    const statusColors = {
        online: 'bg-green-500',
        offline: 'bg-red-500',
        restarting: 'bg-yellow-500',
        starting: 'bg-yellow-500',
    };

    const statusText = {
        online: 'Online',
        offline: 'Offline',
        restarting: 'Restarting...',
        starting: 'Starting...',
    };

    const actualStatus = isRestarting ? 'restarting' : service.status;

    return (
        <div className="bento-card">
            <div className="flex items-start justify-between mb-4">
                <div className="flex items-center gap-3">
                    <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-violet-500/20 to-pink-500/20 flex items-center justify-center">
                        <Icon className="w-6 h-6 text-violet-400" />
                    </div>
                    <div>
                        <h3 className="text-lg font-semibold text-white">{service.name}</h3>
                        <p className="text-sm text-zinc-500">{service.description}</p>
                    </div>
                </div>
                <div className="flex items-center gap-2">
                    <div className={`w-2 h-2 rounded-full ${statusColors[actualStatus]} animate-pulse`} />
                    <span className="text-sm text-zinc-400">{statusText[actualStatus]}</span>
                </div>
            </div>

            <div className="flex items-center justify-between pt-4 border-t border-white/[0.08]">
                <div className="flex items-center gap-4 text-sm text-zinc-500">
                    <div className="flex items-center gap-1">
                        <Clock className="w-4 h-4" />
                        <span>{service.uptime || '—'}</span>
                    </div>
                </div>

                <button
                    onClick={onRestart}
                    disabled={isRestarting || !service.restartable}
                    className={`flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium transition-all
            ${isRestarting
                            ? 'bg-yellow-500/20 text-yellow-500 cursor-not-allowed'
                            : !service.restartable
                                ? 'bg-zinc-500/20 text-zinc-500 cursor-not-allowed'
                                : 'bg-violet-500/20 text-violet-400 hover:bg-violet-500/30'
                        }`}
                >
                    <RotateCcw className={`w-4 h-4 ${isRestarting ? 'animate-spin' : ''}`} />
                    {isRestarting ? 'Restarting...' : 'Restart'}
                </button>
            </div>
        </div>
    );
}

export default function ServicesPage() {
    const [services, setServices] = useState<Service[]>([]);
    const [restartingServices, setRestartingServices] = useState<Set<string>>(new Set());
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const fetchServices = useCallback(async () => {
        try {
            const response = await fetch(`${BOT_API_URL}/services`);
            if (!response.ok) {
                throw new Error(`Failed to fetch services: ${response.status}`);
            }
            const data = await response.json();
            setServices(data.services || []);
            setError(null);
        } catch (err) {
            console.error('Failed to fetch services:', err);
            setError('Failed to connect to bot API');
            // Set fallback services when API is unavailable
            setServices([
                {
                    id: 'bot',
                    name: 'Discord Bot',
                    description: 'Core Discord bot handling commands and audio playback',
                    status: 'offline',
                    uptime: '—',
                    restartable: true,
                },
                {
                    id: 'dashboard',
                    name: 'Dashboard',
                    description: 'This Next.js web dashboard',
                    status: 'online',
                    uptime: 'Running',
                    restartable: false,
                },
            ]);
        } finally {
            setLoading(false);
        }
    }, [setServices, setError, setLoading]);

    useEffect(() => {
        fetchServices();
        // Refresh every 30 seconds
        const interval = setInterval(fetchServices, 30000);
        return () => clearInterval(interval);
    }, [fetchServices]);

    const handleRestart = async (serviceId: string) => {
        setRestartingServices(prev => new Set(prev).add(serviceId));

        try {
            const response = await fetch(`${BOT_API_URL}/services/${serviceId}/restart`, {
                method: 'POST',
            });

            if (!response.ok) {
                const data = await response.json().catch(() => ({}));
                throw new Error(data.error || 'Restart failed');
            }

            // Wait for the service to restart
            await new Promise(resolve => setTimeout(resolve, 5000));

            // Refresh services list
            await fetchServices();

        } catch (error) {
            console.error('Failed to restart service:', error);
        } finally {
            setRestartingServices(prev => {
                const next = new Set(prev);
                next.delete(serviceId);
                return next;
            });
        }
    };

    const onlineCount = services.filter(s => s.status === 'online').length;
    const offlineCount = services.filter(s => s.status === 'offline').length;

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-white flex items-center gap-3">
                        <Cog className="w-7 h-7 text-violet-500" />
                        Services
                    </h1>
                    <p className="text-sm text-zinc-500 mt-1">
                        Manage and monitor running services
                    </p>
                </div>
                <button
                    onClick={fetchServices}
                    disabled={loading}
                    className="flex items-center gap-2 px-3 py-2 rounded-xl bg-white/[0.04] border border-white/[0.08] text-sm text-zinc-400 hover:text-white hover:border-white/[0.16] transition-colors"
                >
                    <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
                    Refresh
                </button>
            </div>

            {/* Error Banner */}
            {error && (
                <div className="bento-card bg-red-500/10 border-red-500/30">
                    <div className="flex items-center gap-3">
                        <XCircle className="w-5 h-5 text-red-500" />
                        <div>
                            <p className="text-sm font-medium text-red-400">{error}</p>
                            <p className="text-xs text-zinc-500">Make sure the bot is running and accessible</p>
                        </div>
                    </div>
                </div>
            )}

            {/* Overview Cards */}
            <div className="grid grid-cols-3 gap-4">
                <div className="bento-card flex items-center gap-4">
                    <div className="w-12 h-12 rounded-xl bg-green-500/20 flex items-center justify-center">
                        <CheckCircle className="w-6 h-6 text-green-500" />
                    </div>
                    <div>
                        <p className="text-2xl font-bold text-white">{onlineCount}</p>
                        <p className="text-sm text-zinc-500">Services Online</p>
                    </div>
                </div>

                <div className="bento-card flex items-center gap-4">
                    <div className="w-12 h-12 rounded-xl bg-red-500/20 flex items-center justify-center">
                        <XCircle className="w-6 h-6 text-red-500" />
                    </div>
                    <div>
                        <p className="text-2xl font-bold text-white">{offlineCount}</p>
                        <p className="text-sm text-zinc-500">Services Offline</p>
                    </div>
                </div>

                <div className="bento-card flex items-center gap-4">
                    <div className="w-12 h-12 rounded-xl bg-violet-500/20 flex items-center justify-center">
                        <Activity className="w-6 h-6 text-violet-500" />
                    </div>
                    <div>
                        <p className="text-2xl font-bold text-white">{services.length}</p>
                        <p className="text-sm text-zinc-500">Total Services</p>
                    </div>
                </div>
            </div>

            {/* Service Cards */}
            {loading ? (
                <div className="space-y-4">
                    {[1, 2].map((i) => (
                        <div key={i} className="bento-card animate-pulse">
                            <div className="flex items-center gap-4">
                                <div className="w-12 h-12 rounded-xl bg-white/[0.08]" />
                                <div className="flex-1 space-y-2">
                                    <div className="h-5 w-32 bg-white/[0.08] rounded" />
                                    <div className="h-4 w-64 bg-white/[0.08] rounded" />
                                </div>
                            </div>
                        </div>
                    ))}
                </div>
            ) : (
                <div className="space-y-4">
                    {services.map((service) => (
                        <ServiceCard
                            key={service.id}
                            service={service}
                            onRestart={() => handleRestart(service.id)}
                            isRestarting={restartingServices.has(service.id)}
                        />
                    ))}
                </div>
            )}

            {/* Info */}
            <div className="bento-card bg-blue-500/5 border-blue-500/20">
                <div className="flex items-start gap-3">
                    <Activity className="w-5 h-5 text-blue-400 mt-0.5" />
                    <div>
                        <p className="text-sm font-medium text-white">Service Restart Note</p>
                        <p className="text-sm text-zinc-400 mt-1">
                            Restarting the Discord bot will disconnect all active voice channels.
                            Users will need to rejoin or use /play again after the bot reconnects.
                        </p>
                    </div>
                </div>
            </div>
        </div>
    );
}
