"""
YouTube Music API Wrapper
"""
import asyncio
import logging
import re
from dataclasses import dataclass
from functools import partial
from typing import Any

import yt_dlp
from ytmusicapi import YTMusic

import random
import time
from functools import wraps

from src.utils.logging import get_logger, Category, Event

log = get_logger(__name__)


def retry_with_backoff(retries=3, backoff_in_seconds=1):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            x = 0
            while True:
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    if x == retries:
                        log.event(Category.API, Event.API_ERROR, level=logging.ERROR, retries=retries, error=str(e))
                        raise
                    else:
                        sleep = (backoff_in_seconds * 2 ** x + random.uniform(0, 1))
                        log.warning_cat(Category.API, f"Retry {x + 1}/{retries} for {func.__name__}", sleep=f"{sleep:.2f}s", error=str(e))
                        await asyncio.sleep(sleep)
                        x += 1
        return wrapper
    return decorator


@dataclass
class YTTrack:
    """YouTube track info."""
    video_id: str
    title: str
    artist: str
    duration_seconds: int | None = None
    album: str | None = None
    year: int | None = None
    thumbnail_url: str | None = None


class YouTubeService:
    """YouTube Music API wrapper."""
    
    def __init__(self, cookies_path: str | None = None, po_token: str | None = None):
        self.yt = YTMusic()
        # Set a longer timeout on the underlying requests session (default can be too low)
        if hasattr(self.yt, '_session'):
            self.yt._session.timeout = 20
        self.cookies_path = cookies_path
        self.po_token = po_token
        self._ydl_opts = {
            "format": "bestaudio/best",
            "source_address": "0.0.0.0",
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "socket_timeout": 20,
        }
        if cookies_path:
            self._ydl_opts["cookiefile"] = cookies_path
        if po_token:
            self._ydl_opts["extractor_args"] = {"youtube": {"po_token": [po_token]}}
    
    @retry_with_backoff()
    async def search(self, query: str, filter_type: str = "songs", limit: int = 5) -> list[YTTrack]:
        """Search YouTube Music for tracks."""
        loop = asyncio.get_event_loop()
        try:
            results = await loop.run_in_executor(
                None,
                partial(self.yt.search, query, filter=filter_type, limit=limit)
            )
            
            tracks = []
            for r in results:
                if not r.get("videoId"):
                    continue
                
                duration = None
                if r.get("duration_seconds"):
                    duration = r["duration_seconds"]
                elif r.get("duration"):
                    # Parse duration string like "3:45"
                    duration = self._parse_duration(r["duration"])
                
                artist = "Unknown"
                if r.get("artists") and len(r["artists"]) > 0:
                    artist = r["artists"][0].get("name", "Unknown")
                
                tracks.append(YTTrack(
                    video_id=r["videoId"],
                    title=r.get("title", "Unknown"),
                    artist=artist,
                    duration_seconds=duration,
                    album=r.get("album", {}).get("name") if r.get("album") else None,
                    year=r.get("year"),
                    thumbnail_url=r.get("thumbnails", [{}])[-1].get("url"),
                ))
            
            return tracks
        except Exception as e:
            log.event(Category.API, Event.SEARCH_FAILED, level=logging.ERROR, service="youtube", error=str(e))
            return []
    
    @retry_with_backoff()
    async def get_watch_playlist(self, video_id: str, limit: int = 20) -> list[YTTrack]:
        """Get related tracks from a video's watch playlist."""
        loop = asyncio.get_event_loop()
        try:
            results = await loop.run_in_executor(
                None,
                partial(self.yt.get_watch_playlist, videoId=video_id, limit=limit)
            )
            
            tracks = []
            for t in results.get("tracks", []):
                if not t.get("videoId"):
                    continue
                
                artist = "Unknown"
                if t.get("artists") and len(t["artists"]) > 0:
                    artist = t["artists"][0].get("name", "Unknown")
                
                tracks.append(YTTrack(
                    video_id=t["videoId"],
                    title=t.get("title", "Unknown"),
                    artist=artist,
                    duration_seconds=t.get("length_seconds") or t.get("duration_seconds"),
                    year=t.get("year"),
                ))
            
            return tracks
        except Exception as e:
            log.error_cat(Category.API, "Error getting watch playlist", error=str(e))
            return []
    
    @retry_with_backoff()
    async def get_playlist_tracks(self, playlist_id: str, limit: int = 100) -> list[YTTrack]:
        """Get tracks from a YouTube Music playlist."""
        loop = asyncio.get_event_loop()
        try:
            results = await loop.run_in_executor(
                None,
                partial(self.yt.get_playlist, playlist_id, limit=limit)
            )
            
            tracks = []
            for t in results.get("tracks", []):
                if not t.get("videoId"):
                    continue
                
                artist = "Unknown"
                if t.get("artists") and len(t["artists"]) > 0:
                    artist = t["artists"][0].get("name", "Unknown")
                
                tracks.append(YTTrack(
                    video_id=t["videoId"],
                    title=t.get("title", "Unknown"),
                    artist=artist,
                    duration_seconds=t.get("duration_seconds"),
                ))
            
            return tracks
        except Exception as e:
            log.error_cat(Category.API, "Error getting playlist", error=str(e))
            return []
    
    async def get_stream_url(self, video_id: str) -> str | None:
        """Get the audio stream URL for a video using yt-dlp."""
        loop = asyncio.get_event_loop()
        url = f"https://www.youtube.com/watch?v={video_id}"
        
        try:
            def extract():
                with yt_dlp.YoutubeDL(self._ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    return info.get("url")
            
            return await loop.run_in_executor(None, extract)
        except Exception as e:
            log.event(Category.API, Event.API_ERROR, level=logging.ERROR, service="youtube", video_id=video_id, error=str(e))
            return None
    
    @retry_with_backoff()
    async def search_playlists(self, query: str, limit: int = 5) -> list[dict]:
        """Search for playlists."""
        loop = asyncio.get_event_loop()
        try:
            results = await loop.run_in_executor(
                None,
                partial(self.yt.search, query, filter="playlists", limit=limit)
            )
            return [
                {
                    "browse_id": r.get("browseId"),
                    "title": r.get("title"),
                    "author": r.get("author"),
                }
                for r in results if r.get("browseId")
            ]
        except Exception as e:
            log.error_cat(Category.API, "Error searching playlists", error=str(e))
            return []
    
    def _parse_duration(self, duration_str: str) -> int | None:
        """Parse duration string like '3:45' to seconds."""
        if not duration_str:
            return None
        try:
            parts = duration_str.split(":")
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            elif len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        except ValueError:
            pass
        return None
