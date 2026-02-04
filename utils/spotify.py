"""
Spotify helpers for playlist retrieval.
"""
from __future__ import annotations

import re
from typing import List, Tuple, Optional, Dict, Any

import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from spotipy.exceptions import SpotifyException

from config import Config
from utils.logger import set_logger
import logging

logger = set_logger(logging.getLogger("Vexo.Spotify"))

_spotify_client: Optional[spotipy.Spotify] = None


class SpotifyError(Exception):
    """Raised when Spotify operations fail."""


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
    except Exception as exc:
        logger.error(f"Spotify connectivity check failed: {exc}")
        return {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}
