"""
Spotify helpers for playlist retrieval.
"""
from __future__ import annotations

import re
from typing import List, Tuple, Optional, Dict, Any

import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from spotipy.exceptions import SpotifyException

try:
    from requests.exceptions import Timeout as RequestsTimeout
except Exception:  # pragma: no cover - requests may not be importable in some environments
    class RequestsTimeout(Exception):
        pass

from config import Config
from utils.logger import set_logger
import logging

logger = set_logger(logging.getLogger("Vexo.Spotify"))

_spotify_client: Optional[spotipy.Spotify] = None


class SpotifyError(Exception):
    """Raised when Spotify operations fail."""


def _parse_timeout(value: Any) -> Any:
    """
    Parse Spotipy requests_timeout setting.
    Accepts a float/int seconds or a "connect,read" string (seconds).
    """
    if value is None:
        return 5
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return 5
    if "," in s:
        parts = [p.strip() for p in s.split(",") if p.strip()]
        if len(parts) == 2:
            try:
                return (float(parts[0]), float(parts[1]))
            except Exception:
                return 5
    try:
        return float(s)
    except Exception:
        return 5


def _parse_status_forcelist(value: Any) -> List[int]:
    if value is None:
        return [429, 500, 502, 503, 504]
    if isinstance(value, (list, tuple)):
        out: List[int] = []
        for v in value:
            try:
                out.append(int(v))
            except Exception:
                continue
        return out or [429, 500, 502, 503, 504]

    s = str(value).strip()
    if not s:
        return [429, 500, 502, 503, 504]
    out = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except Exception:
            continue
    return out or [429, 500, 502, 503, 504]


def _extract_playlist_id(value: str) -> Optional[str]:
    if not value:
        return None
    uri_match = re.search(r"spotify:playlist:([A-Za-z0-9]+)", value)
    if uri_match:
        return uri_match.group(1)
    url_match = re.search(r"open\.spotify\.com/playlist/([A-Za-z0-9]+)", value)
    if url_match:
        return url_match.group(1)
    return None


def _get_client() -> Optional[spotipy.Spotify]:
    global _spotify_client
    if _spotify_client is not None:
        return _spotify_client
    if not Config.SPOTIFY_CLIENT_ID or not Config.SPOTIFY_CLIENT_SECRET:
        return None
    creds = SpotifyClientCredentials(
        client_id=Config.SPOTIFY_CLIENT_ID,
        client_secret=Config.SPOTIFY_CLIENT_SECRET,
    )

    requests_timeout = _parse_timeout(getattr(Config, "SPOTIFY_REQUEST_TIMEOUT", 5))
    retries = int(getattr(Config, "SPOTIFY_RETRIES", 3))
    status_retries = int(getattr(Config, "SPOTIFY_STATUS_RETRIES", 3))
    backoff_factor = float(getattr(Config, "SPOTIFY_BACKOFF_FACTOR", 0.3))
    status_forcelist = _parse_status_forcelist(getattr(Config, "SPOTIFY_STATUS_FORCELIST", None))

    try:
        _spotify_client = spotipy.Spotify(
            client_credentials_manager=creds,
            requests_timeout=requests_timeout,
            retries=retries,
            status_retries=status_retries,
            backoff_factor=backoff_factor,
            status_forcelist=status_forcelist,
        )
    except TypeError:
        # Older spotipy versions may not support all knobs; fall back to defaults.
        _spotify_client = spotipy.Spotify(client_credentials_manager=creds)
    return _spotify_client


def fetch_playlist_tracks(value: str, limit: Optional[int] = None) -> Tuple[str, List[Tuple[str, str]], int]:
    """
    Fetch playlist name and tracks from a Spotify playlist URL or URI.
    Returns (playlist_name, [(track_title, artist_names), ...], total_tracks).
    """
    client = _get_client()
    if not client:
        raise SpotifyError("Spotify credentials are not configured.")

    playlist_id = _extract_playlist_id(value)
    if not playlist_id:
        raise SpotifyError("Invalid Spotify playlist URL or URI.")

    try:
        playlist = client.playlist(playlist_id)
    except Exception as exc:
        logger.error(f"Failed to fetch Spotify playlist: {exc}")
        raise SpotifyError("Failed to fetch Spotify playlist.")

    name = playlist.get("name") or "Spotify Playlist"
    results = playlist.get("tracks") or {}
    total = results.get("total") or 0
    items = results.get("items") or []
    tracks: List[Tuple[str, str]] = []

    while True:
        for item in items:
            track = (item or {}).get("track") or {}
            title = track.get("name")
            artists = [a.get("name") for a in track.get("artists", []) if a.get("name")]
            artist_str = ", ".join(artists) if artists else ""
            if title:
                tracks.append((title, artist_str))
            if limit and len(tracks) >= limit:
                return name, tracks[:limit], total
        if results.get("next"):
            results = client.next(results)
            items = results.get("items") or []
        else:
            break

    return name, tracks, total


def fetch_playlist_tracks_detailed(value: str, limit: Optional[int] = None) -> Tuple[str, List[Dict[str, Any]], int]:
    """
    Fetch playlist name and detailed tracks from a Spotify playlist URL or URI.
    Returns (playlist_name, [{spotify_id, title, artists, position}, ...], total_tracks).

    Notes:
    - Local/unavailable tracks (no Spotify track id) are skipped.
    - 'position' is the playlist order (0-based) among returned tracks.
    """
    client = _get_client()
    if not client:
        raise SpotifyError("Spotify credentials are not configured.")

    playlist_id = _extract_playlist_id(value)
    if not playlist_id:
        raise SpotifyError("Invalid Spotify playlist URL or URI.")

    try:
        playlist = client.playlist(playlist_id)
    except Exception as exc:
        logger.error(f"Failed to fetch Spotify playlist: {exc}")
        raise SpotifyError("Failed to fetch Spotify playlist.")

    name = playlist.get("name") or "Spotify Playlist"
    results = playlist.get("tracks") or {}
    total = results.get("total") or 0
    items = results.get("items") or []

    tracks: List[Dict[str, Any]] = []
    position = 0

    while True:
        for item in items:
            track = (item or {}).get("track") or {}
            if (track or {}).get("is_local"):
                continue

            spotify_id = (track or {}).get("id")
            title = (track or {}).get("name")
            artist_objs = (track or {}).get("artists", []) or []
            artists = [a.get("name") for a in artist_objs if a.get("name")]
            artist_ids = [a.get("id") for a in artist_objs if a.get("id")]
            artist_str = ", ".join(artists) if artists else ""

            album = ((track or {}).get("album") or {}).get("name")
            release_date = ((track or {}).get("album") or {}).get("release_date")
            popularity = (track or {}).get("popularity")
            duration_ms = (track or {}).get("duration_ms")

            if not spotify_id or not title:
                continue

            tracks.append(
                {
                    "spotify_id": spotify_id,
                    "title": title,
                    "artists": artist_str,
                    "artist_ids": artist_ids,
                    "album": album,
                    "release_date": release_date,
                    "popularity": popularity,
                    "duration_ms": duration_ms,
                    "position": position,
                }
            )
            position += 1

            if limit and len(tracks) >= limit:
                return name, tracks[:limit], total

        if results.get("next"):
            results = client.next(results)
            items = results.get("items") or []
        else:
            break

    return name, tracks, total


def check_connectivity(query: str = "vexo") -> Dict[str, Any]:
    """
    Lightweight connectivity test for Spotify using client credentials.
    Returns a dict suitable for JSON responses: {ok: bool, error?: str, sample?: {...}}
    """
    client = _get_client()
    if not client:
        return {"ok": False, "error": "Spotify credentials are not configured."}

    try:
        result = client.search(q=query, type="track", limit=1)
        items = ((result or {}).get("tracks") or {}).get("items") or []
        sample = None
        if items:
            track = items[0] or {}
            artists = track.get("artists") or []
            sample = {
                "track": track.get("name"),
                "artist": (artists[0] or {}).get("name") if artists else None,
            }
        return {"ok": True, "sample": sample}
    except SpotifyException as exc:
        status = getattr(exc, "http_status", None)
        msg = getattr(exc, "msg", None) or str(exc)
        logger.error(f"Spotify connectivity check failed: {status} {msg}")
        if status:
            return {"ok": False, "error": f"Spotify API error {status}: {msg}"}
        return {"ok": False, "error": f"Spotify API error: {msg}"}
    except RequestsTimeout as exc:
        logger.warning(f"Spotify connectivity check timed out: {exc}")
        return {"ok": False, "error": "Spotify request timed out."}
    except Exception as exc:
        logger.error(f"Spotify connectivity check failed: {exc}")
        return {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}


def get_artist_genres(artist_id: str) -> List[str]:
    """Return Spotify genres for an artist id (sync)."""
    client = _get_client()
    if not client:
        raise SpotifyError("Spotify credentials are not configured.")
    if not artist_id:
        return []
    try:
        artist = client.artist(artist_id)
        genres = (artist or {}).get("genres") or []
        return [g for g in genres if isinstance(g, str) and g.strip()]
    except SpotifyException as exc:
        status = getattr(exc, "http_status", None)
        msg = getattr(exc, "msg", None) or str(exc)
        logger.error(f"Spotify artist fetch failed: {status} {msg}")
        return []
    except Exception as exc:
        logger.error(f"Spotify artist fetch failed: {exc}")
        return []


def search_track_genres(title: str, artist: Optional[str] = None) -> List[str]:
    """
    Search Spotify for a track and return the primary artist's genres (sync).
    Spotify doesn't provide per-track genres directly; artist genres are used.
    """
    client = _get_client()
    if not client:
        raise SpotifyError("Spotify credentials are not configured.")

    if not title or not title.strip():
        return []

    q = f"track:{title}"
    if artist and artist.strip():
        q += f" artist:{artist}"

    try:
        result = client.search(q=q, type="track", limit=1)
        items = (((result or {}).get("tracks") or {}).get("items") or [])
        if not items:
            return []
        t = items[0] or {}
        artists = t.get("artists") or []
        artist_id = (artists[0] or {}).get("id") if artists else None
        if not artist_id:
            return []
        return get_artist_genres(artist_id)
    except SpotifyException as exc:
        status = getattr(exc, "http_status", None)
        msg = getattr(exc, "msg", None) or str(exc)
        logger.error(f"Spotify search failed: {status} {msg}")
        return []
    except RequestsTimeout as exc:
        logger.warning(f"Spotify search timed out: {exc}")
        return []
    except Exception as exc:
        logger.error(f"Spotify search failed: {exc}")
        return []


def search_track_enrichment(title: str, artist: Optional[str] = None) -> Dict[str, Any]:
    """
    Best-effort Spotify enrichment for a track search.

    Returns:
      {genres: [..], release_year: int|None, release_date: str|None, album: str|None, popularity: int|None}

    Notes:
    - Spotify doesn't provide per-track genres; this uses the primary artist's genres.
    """
    client = _get_client()
    if not client:
        raise SpotifyError("Spotify credentials are not configured.")

    if not title or not title.strip():
        return {"genres": [], "release_year": None, "release_date": None, "album": None, "popularity": None}

    q = f"track:{title}"
    if artist and artist.strip():
        q += f" artist:{artist}"

    try:
        result = client.search(q=q, type="track", limit=1)
        items = (((result or {}).get("tracks") or {}).get("items") or [])
        if not items:
            return {"genres": [], "release_year": None, "release_date": None, "album": None, "popularity": None}

        t = items[0] or {}
        album_obj = (t.get("album") or {})
        release_date = album_obj.get("release_date")
        album = album_obj.get("name")
        popularity = t.get("popularity")

        release_year = None
        if isinstance(release_date, str) and len(release_date) >= 4 and release_date[:4].isdigit():
            try:
                release_year = int(release_date[:4])
            except Exception:
                release_year = None

        artists = t.get("artists") or []
        artist_id = (artists[0] or {}).get("id") if artists else None
        genres = get_artist_genres(artist_id) if artist_id else []

        return {
            "genres": genres,
            "release_year": release_year,
            "release_date": release_date,
            "album": album,
            "popularity": popularity,
        }
    except SpotifyException as exc:
        status = getattr(exc, "http_status", None)
        msg = getattr(exc, "msg", None) or str(exc)
        logger.error(f"Spotify search failed: {status} {msg}")
        return {"genres": [], "release_year": None, "release_date": None, "album": None, "popularity": None}
    except RequestsTimeout as exc:
        logger.warning(f"Spotify search timed out: {exc}")
        return {"genres": [], "release_year": None, "release_date": None, "album": None, "popularity": None}
    except Exception as exc:
        logger.error(f"Spotify search failed: {exc}")
        return {"genres": [], "release_year": None, "release_date": None, "album": None, "popularity": None}
