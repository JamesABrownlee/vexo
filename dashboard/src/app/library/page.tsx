import { getLibrary } from '@/lib/db';
import LibraryPageClient from './LibraryPageClient';

export const dynamic = 'force-dynamic';

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

export default async function LibraryPage() {
    let library: LibraryItem[] = [];

    try {
        library = (await getLibrary(500) as unknown) as LibraryItem[];
    } catch (error) {
        console.error('Failed to fetch library:', error);
    }

    return <LibraryPageClient initialLibrary={library} />;
}
