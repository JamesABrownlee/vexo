import { createClient } from '@libsql/client';
import { drizzle } from 'drizzle-orm/libsql';
import path from 'path';
import fs from 'fs';

// Path to the bot's SQLite database
const dockerPath = '/app/data/musicbot.db';
const localPath = path.resolve(process.cwd(), '../src/database/musicbot.db');
const dbPath = process.env.DATABASE_PATH || (fs.existsSync(dockerPath) ? dockerPath : localPath);
const DB_URL = `file:${dbPath}`;

// Create singleton client
let client: ReturnType<typeof createClient> | null = null;
let db: ReturnType<typeof drizzle> | null = null;

function getDatabase() {
  if (!client) {
    try {
      client = createClient({ url: DB_URL, intMode: 'bigint' });
      db = drizzle(client);
      console.log(`Database connected: ${DB_URL}`);
    } catch (error) {
      console.error(`Failed to connect to database at ${DB_URL}:`, error);
      throw error;
    }
  }
  return { client: client!, db: db! };
}

// Async query helper using libSQL
async function queryAll<T = Record<string, unknown>>(sqlQuery: string, params: unknown[] = []): Promise<T[]> {
  try {
    const { client } = getDatabase();
    const result = await client.execute({ sql: sqlQuery, args: params as any[] });
    // Convert Row objects to plain objects for client component serialization
    // And handle BigInt serialization
    return result.rows.map(row => {
      const plainRow: Record<string, unknown> = {};
      for (const [key, value] of Object.entries(row)) {
        if (typeof value === 'bigint') {
          // If it's a small enough BigInt, convert to Number so math works in the UI
          // If it's larger (like a Discord ID), keep as string for precision
          plainRow[key] = value <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(value) : value.toString();
        } else {
          plainRow[key] = value;
        }
      }
      return plainRow;
    }) as T[];
  } catch (error) {
    console.error('Query error:', error);
    return [];
  }
}

async function queryOne<T = Record<string, unknown>>(sqlQuery: string, params: unknown[] = []): Promise<T | null> {
  const results = await queryAll<T>(sqlQuery, params);
  return results[0] || null;
}

// Export types
export interface Song {
  id: number;
  canonical_yt_id: string;
  title: string;
  artist_name: string;
  album?: string;
  release_year?: number;
  duration_seconds?: number;
  spotify_id?: string;
  is_ephemeral: boolean;
  created_at: string;
}

export interface User {
  id: number;
  username: string;
  created_at: string;
  last_active: string;
  is_banned: boolean;
  opted_out: boolean;
}

// Async query functions
export async function getTopSongs(limit = 10, guildId?: number) {
  if (guildId) {
    return queryAll(`
      SELECT 
        s.title, 
        s.canonical_yt_id as yt_id,
        s.artist_name as artist, 
        COUNT(*) as plays,
        (SELECT COUNT(*) FROM song_reactions r WHERE r.song_id = s.id AND r.reaction = 'like') as likes
      FROM playback_history ph
      JOIN songs s ON ph.song_id = s.id
      JOIN playback_sessions ps ON ph.session_id = ps.id
      WHERE ps.guild_id = ?
      GROUP BY s.id
      ORDER BY plays DESC
      LIMIT ?
    `, [guildId, limit]);
  }

  return queryAll(`
    SELECT 
      s.title, 
      s.canonical_yt_id as yt_id,
      s.artist_name as artist, 
      COUNT(*) as plays,
      (SELECT COUNT(*) FROM song_reactions r WHERE r.song_id = s.id AND r.reaction = 'like') as likes
    FROM playback_history ph
    JOIN songs s ON ph.song_id = s.id
    GROUP BY s.id
    ORDER BY plays DESC
    LIMIT ?
  `, [limit]);
}

export async function getTopUsers(limit = 10, guildId?: number) {
  if (guildId) {
    return queryAll(`
      SELECT 
        u.id,
        u.username,
        COUNT(DISTINCT ph.id) as plays,
        (SELECT COUNT(*) FROM song_reactions r WHERE r.user_id = u.id) as reactions,
        (SELECT COUNT(*) FROM imported_playlists ip WHERE ip.user_id = u.id) as playlists
      FROM users u
      LEFT JOIN playback_history ph ON ph.for_user_id = u.id
      LEFT JOIN playback_sessions ps ON ph.session_id = ps.id
      WHERE ps.guild_id = ?
      GROUP BY u.id
      ORDER BY plays DESC
      LIMIT ?
    `, [guildId, limit]);
  }

  return queryAll(`
    SELECT 
      u.id,
      u.username,
      COUNT(DISTINCT ph.id) as plays,
      (SELECT COUNT(*) FROM song_reactions r WHERE r.user_id = u.id) as reactions,
      (SELECT COUNT(*) FROM imported_playlists ip WHERE ip.user_id = u.id) as playlists
    FROM users u
    LEFT JOIN playback_history ph ON ph.for_user_id = u.id
    GROUP BY u.id
    ORDER BY plays DESC
    LIMIT ?
  `, [limit]);
}

export async function getTotalStats(guildId?: number) {
  let totalPlays = 0;
  let totalSongs = 0;
  let totalUsers = 0;

  if (guildId) {
    const playsRow = await queryOne<{ count: number }>(`
      SELECT COUNT(*) as count 
      FROM playback_history ph
      JOIN playback_sessions ps ON ph.session_id = ps.id
      WHERE ps.guild_id = ?
    `, [guildId]);
    totalPlays = Number(playsRow?.count) || 0;

    const songsRow = await queryOne<{ count: number }>(`
      SELECT COUNT(DISTINCT song_id) as count 
      FROM playback_history ph
      JOIN playback_sessions ps ON ph.session_id = ps.id
      WHERE ps.guild_id = ?
    `, [guildId]);
    totalSongs = Number(songsRow?.count) || 0;

    const usersRow = await queryOne<{ count: number }>(`
      SELECT COUNT(DISTINCT ph.for_user_id) as count
      FROM playback_history ph
      JOIN playback_sessions ps ON ph.session_id = ps.id
      WHERE ps.guild_id = ?
    `, [guildId]);
    totalUsers = Number(usersRow?.count) || 0;
  } else {
    const playsRow = await queryOne<{ count: number }>(`SELECT COUNT(*) as count FROM playback_history`);
    totalPlays = Number(playsRow?.count) || 0;

    const songsRow = await queryOne<{ count: number }>(`SELECT COUNT(*) as count FROM songs WHERE is_ephemeral = 0`);
    totalSongs = Number(songsRow?.count) || 0;

    const usersRow = await queryOne<{ count: number }>(`SELECT COUNT(*) as count FROM users`);
    totalUsers = Number(usersRow?.count) || 0;
  }

  return { totalPlays, totalSongs, totalUsers };
}

export async function getTopLikedGenres(limit = 5) {
  return queryAll(`
    SELECT g.genre as name, COUNT(r.song_id) as likes
    FROM song_genres g
    JOIN song_reactions r ON g.song_id = r.song_id
    WHERE r.reaction = 'like'
    GROUP BY g.genre
    ORDER BY likes DESC
    LIMIT ?
  `, [limit]);
}

export async function getTopLikedArtists(limit = 5) {
  return queryAll(`
    SELECT s.artist_name as name, COUNT(r.song_id) as likes
    FROM songs s
    JOIN song_reactions r ON s.id = r.song_id
    WHERE r.reaction = 'like'
    GROUP BY s.artist_name
    ORDER BY likes DESC
    LIMIT ?
  `, [limit]);
}

export async function getTopLikedSongs(limit = 5) {
  return queryAll(`
    SELECT s.title, s.artist_name as artist, COUNT(r.song_id) as likes
    FROM songs s
    JOIN song_reactions r ON s.id = r.song_id
    WHERE r.reaction = 'like'
    GROUP BY s.id
    ORDER BY likes DESC
    LIMIT ?
  `, [limit]);
}

export async function getDiscoveryBreakdown(guildId?: number) {
  if (guildId) {
    return queryAll(`
      SELECT ph.discovery_source as source, COUNT(*) as count
      FROM playback_history ph
      JOIN playback_sessions ps ON ph.session_id = ps.id
      WHERE ps.guild_id = ?
      GROUP BY ph.discovery_source
      ORDER BY count DESC
    `, [guildId]);
  }

  return queryAll(`
    SELECT discovery_source as source, COUNT(*) as count
    FROM playback_history
    GROUP BY discovery_source
    ORDER BY count DESC
  `);
}

export async function getGenreDistribution(limit = 10, guildId?: number) {
  if (guildId) {
    return queryAll(`
      SELECT g.genre as name, COUNT(ph.id) as plays
      FROM song_genres g
      JOIN playback_history ph ON g.song_id = ph.song_id
      JOIN playback_sessions ps ON ph.session_id = ps.id
      WHERE ps.guild_id = ?
      GROUP BY g.genre
      ORDER BY plays DESC
      LIMIT ?
    `, [guildId, limit]);
  }

  return queryAll(`
    SELECT g.genre as name, COUNT(ph.id) as plays
    FROM song_genres g
    JOIN playback_history ph ON g.song_id = ph.song_id
    GROUP BY g.genre
    ORDER BY plays DESC
    LIMIT ?
  `, [limit]);
}

export async function getRecentHistory(limit = 100, guildId?: number) {
  if (guildId) {
    return queryAll(`
      SELECT 
        ph.played_at,
        s.title,
        s.artist_name,
        s.album,
        s.release_year,
        s.duration_seconds,
        s.canonical_yt_id as yt_id,
        (SELECT GROUP_CONCAT(DISTINCT sg.genre) FROM song_genres sg WHERE sg.song_id = s.id) as genre,
        ph.discovery_source,
        ph.discovery_reason,
        ph.completed,
        ph.skip_reason,
        CASE WHEN ph.discovery_source = 'user_request' THEN u.username ELSE NULL END as requested_by,
        (SELECT GROUP_CONCAT(DISTINCT u2.username) 
         FROM song_reactions sr 
         JOIN users u2 ON sr.user_id = u2.id 
         WHERE sr.song_id = s.id AND sr.reaction = 'like') as liked_by,
        (SELECT GROUP_CONCAT(DISTINCT u2.username) 
         FROM song_reactions sr 
         JOIN users u2 ON sr.user_id = u2.id 
         WHERE sr.song_id = s.id AND sr.reaction = 'dislike') as disliked_by
      FROM playback_history ph
      JOIN songs s ON ph.song_id = s.id
      JOIN playback_sessions ps ON ph.session_id = ps.id
      LEFT JOIN users u ON ph.for_user_id = u.id
      WHERE ps.guild_id = ?
      ORDER BY ph.played_at DESC
      LIMIT ?
    `, [guildId, limit]);
  }

  return queryAll(`
    SELECT 
      ph.played_at,
      s.title,
      s.artist_name,
      s.album,
      s.release_year,
      s.duration_seconds,
      s.canonical_yt_id as yt_id,
      (SELECT GROUP_CONCAT(DISTINCT sg.genre) FROM song_genres sg WHERE sg.song_id = s.id) as genre,
      ph.discovery_source,
      ph.discovery_reason,
      ph.completed,
      ph.skip_reason,
      CASE WHEN ph.discovery_source = 'user_request' THEN u.username ELSE NULL END as requested_by,
      (SELECT GROUP_CONCAT(DISTINCT u2.username) 
       FROM song_reactions sr 
       JOIN users u2 ON sr.user_id = u2.id 
       WHERE sr.song_id = s.id AND sr.reaction = 'like') as liked_by,
      (SELECT GROUP_CONCAT(DISTINCT u2.username) 
       FROM song_reactions sr 
       JOIN users u2 ON sr.user_id = u2.id 
       WHERE sr.song_id = s.id AND sr.reaction = 'dislike') as disliked_by
    FROM playback_history ph
    JOIN songs s ON ph.song_id = s.id
    LEFT JOIN users u ON ph.for_user_id = u.id
    ORDER BY ph.played_at DESC
    LIMIT ?
  `, [limit]);
}

export async function getLibrary(limit = 200) {
  return queryAll(`
    SELECT 
      s.id,
      s.title,
      s.artist_name,
      s.album,
      s.release_year,
      s.duration_seconds,
      s.canonical_yt_id as yt_id,
      s.spotify_id,
      (SELECT GROUP_CONCAT(DISTINCT sg.genre) FROM song_genres sg WHERE sg.song_id = s.id) as genre,
      GROUP_CONCAT(DISTINCT u.username) as contributors,
      GROUP_CONCAT(DISTINCT l.source) as sources,
      MAX(l.added_at) as last_added,
      (SELECT COUNT(*) FROM playback_history ph WHERE ph.song_id = s.id) as play_count,
      (SELECT COUNT(*) FROM song_reactions sr WHERE sr.song_id = s.id AND sr.reaction = 'like') as like_count,
      (SELECT COUNT(*) FROM song_reactions sr WHERE sr.song_id = s.id AND sr.reaction = 'dislike') as dislike_count
    FROM songs s
    JOIN song_library_entries l ON s.id = l.song_id
    JOIN users u ON l.user_id = u.id
    GROUP BY s.id
    ORDER BY last_added DESC
    LIMIT ?
  `, [limit]);
}

export async function getAllUsers(limit = 50) {
  return queryAll(`
    SELECT 
      CAST(u.id AS TEXT) as id,
      u.username,
      u.created_at,
      u.last_active,
      (SELECT COUNT(*) FROM playback_history ph WHERE ph.for_user_id = u.id) as plays,
      (SELECT COUNT(*) FROM song_reactions r WHERE r.user_id = u.id) as reactions,
      (SELECT COUNT(*) FROM imported_playlists ip WHERE ip.user_id = u.id) as playlists
    FROM users u
    ORDER BY last_active DESC
    LIMIT ?
  `, [limit]);
}

export async function getUserDetail(userId: string) {
  const user = await queryOne<{
    id: number;
    username: string;
    created_at: string;
    last_active: string;
    is_banned: number;
    opted_out: number;
  }>(`
    SELECT id, username, created_at, last_active, is_banned, opted_out 
    FROM users WHERE id = ?
  `, [userId]);

  if (!user) return null;

  const plays = await queryOne<{ count: number }>(`SELECT COUNT(*) as count FROM playback_history WHERE for_user_id = ?`, [userId]);
  const reactions = await queryOne<{ count: number }>(`SELECT COUNT(*) as count FROM song_reactions WHERE user_id = ?`, [userId]);
  const playlists = await queryOne<{ count: number }>(`SELECT COUNT(*) as count FROM imported_playlists WHERE user_id = ?`, [userId]);

  const recentSongs = await queryAll(`
    SELECT s.title, s.artist_name, ph.played_at, ph.discovery_source
    FROM playback_history ph
    JOIN songs s ON ph.song_id = s.id
    WHERE ph.for_user_id = ?
    ORDER BY ph.played_at DESC LIMIT 10
  `, [userId]);

  const likedSongs = await queryAll(`
    SELECT s.title, s.artist_name, sr.reaction
    FROM song_reactions sr
    JOIN songs s ON sr.song_id = s.id
    WHERE sr.user_id = ?
    ORDER BY sr.created_at DESC LIMIT 20
  `, [userId]);

  const preferences = await queryAll(`
    SELECT preference_type, preference_key, affinity_score 
    FROM user_preferences WHERE user_id = ? ORDER BY affinity_score DESC
  `, [userId]);

  const importedPlaylists = await queryAll(`
    SELECT platform, name as playlist_name, track_count, imported_at 
    FROM imported_playlists WHERE user_id = ? ORDER BY imported_at DESC LIMIT 10
  `, [userId]);

  return {
    user,
    stats: {
      plays: Number(plays?.count) || 0,
      reactions: Number(reactions?.count) || 0,
      playlists: Number(playlists?.count) || 0,
    },
    recentSongs,
    likedSongs,
    preferences,
    importedPlaylists,
  };
}

export async function getUsefulUsers(limit = 5) {
  return queryAll(`
    SELECT u.username, COUNT(r.song_id) as score
    FROM users u
    JOIN playback_history ph ON ph.for_user_id = u.id
    JOIN song_reactions r ON ph.song_id = r.song_id
    WHERE ph.discovery_source = 'user_request' 
      AND r.reaction = 'like'
      AND r.user_id != u.id
    GROUP BY u.id
    ORDER BY score DESC
    LIMIT ?
  `, [limit]);
}

export async function getGuilds() {
  return queryAll(`
    SELECT 
      CAST(id AS TEXT) as id,
      name,
      (SELECT COUNT(*) FROM playback_sessions ps WHERE ps.guild_id = g.id AND ps.ended_at IS NULL) > 0 as isPlaying,
      (SELECT COUNT(*) FROM session_listeners sl 
       JOIN playback_sessions ps ON sl.session_id = ps.id 
       WHERE ps.guild_id = g.id AND ps.ended_at IS NULL AND sl.left_at IS NULL) as listeners
    FROM guilds g
    ORDER BY name ASC
  `);
}
