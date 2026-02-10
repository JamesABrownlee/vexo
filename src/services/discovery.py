"""
Vector Discovery Engine - 128-dimensional smart song selection

Replaces the old 4-strategy if/elif router with a unified vector scoring pipeline:
  1. Build user taste profile as 128-dim vector from preferences + liked songs
  2. Gather candidates from ALL sources (library, similar, artist, wildcard)
  3. Encode each candidate as 128-dim vector
  4. Score all candidates via cosine similarity against user profile
  5. Softmax-select winner from top-K
"""
import asyncio
import logging
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.services.youtube import YouTubeService, YTTrack
from src.services.spotify import SpotifyService
from src.services.normalizer import SongNormalizer
from src.services.vector_engine import (
    SongCandidate,
    build_user_profile,
    encode_song,
    score_candidates,
    softmax_select,
    cosine_similarity,
    normalize,
    debug_vector,
)

if TYPE_CHECKING:
    from src.database.crud import PreferenceCRUD, PlaybackCRUD, ReactionCRUD, SongCRUD

logger = logging.getLogger(__name__)


@dataclass
class DiscoveredSong:
    """A discovered song with metadata."""
    video_id: str
    title: str
    artist: str
    strategy: str  # 'similar', 'artist', 'wildcard', 'library'
    reason: str  # Human-readable discovery reason
    for_user_id: int  # The user this song was picked for
    duration_seconds: int | None = None
    genre: str | None = None
    year: int | None = None
    score: float = 0.0  # Vector similarity score


class TurnTracker:
    """Tracks democratic turn-based song selection per guild."""

    def __init__(self):
        self.guild_members: dict[int, list[int]] = {}  # guild_id -> ordered [user_ids]
        self.guild_index: dict[int, int] = {}  # guild_id -> current index

    def update_members(self, guild_id: int, member_ids: list[int]) -> None:
        """Update member list, preserving order for existing members."""
        if guild_id not in self.guild_members:
            self.guild_members[guild_id] = list(member_ids)
            self.guild_index[guild_id] = 0
            return

        current = self.guild_members[guild_id]

        # Keep existing members in order, add new ones at end
        new_list = [m for m in current if m in member_ids]
        for m in member_ids:
            if m not in new_list:
                new_list.append(m)

        self.guild_members[guild_id] = new_list

        # Adjust index if members left
        if self.guild_index[guild_id] >= len(new_list):
            self.guild_index[guild_id] = 0

    def get_current_user(self, guild_id: int) -> int | None:
        """Get the user whose turn it is."""
        if guild_id not in self.guild_members or not self.guild_members[guild_id]:
            return None

        idx = self.guild_index.get(guild_id, 0)
        return self.guild_members[guild_id][idx]

    def advance(self, guild_id: int) -> None:
        """Move to the next user."""
        if guild_id not in self.guild_members or not self.guild_members[guild_id]:
            return

        self.guild_index[guild_id] = (self.guild_index[guild_id] + 1) % len(self.guild_members[guild_id])


class DiscoveryEngine:
    """
    128-Dimensional Vector Discovery Engine.

    Every song and user gets encoded as a 128-dim float vector.
    Cosine similarity replaces the old random.choices() dice roll.
    Candidates from ALL sources compete in a single scoring round.
    """

    DEFAULT_WEIGHTS = {"similar": 25, "artist": 25, "wildcard": 25, "library": 25}

    def __init__(
        self,
        youtube: YouTubeService,
        spotify: SpotifyService,
        normalizer: SongNormalizer,
        preference_crud: "PreferenceCRUD",
        playback_crud: "PlaybackCRUD",
        reaction_crud: "ReactionCRUD",
        song_crud: "SongCRUD | None" = None,
    ):
        self.youtube = youtube
        self.spotify = spotify
        self.normalizer = normalizer
        self.preferences = preference_crud
        self.playback = playback_crud
        self.reactions = reaction_crud
        self.songs = song_crud
        self.turn_tracker = TurnTracker()

    # ════════════════════════════════════════════════════════════════
    #  Main Entry Point
    # ════════════════════════════════════════════════════════════════

    async def get_next_song(
        self,
        guild_id: int,
        voice_member_ids: list[int],
        weights: dict[str, int] | None = None,
        cooldown_seconds: int = 7200,
    ) -> DiscoveredSong | None:
        """
        Get the next song using 128-dim vector scoring.

        Flow:
          1. Determine whose turn it is (democratic rotation)
          2. Build 128-dim user taste profile from preferences + liked songs
          3. Gather candidates from all sources (weighted pool sizes)
          4. Encode each candidate as 128-dim vector
          5. Score ALL candidates via cosine similarity
          6. Softmax-select winner from top-K
        """
        if not voice_member_ids:
            return None

        # ── Turn tracking ──
        self.turn_tracker.update_members(guild_id, voice_member_ids)
        turn_user_id = self.turn_tracker.get_current_user(guild_id)
        if not turn_user_id:
            return None

        # ── Weights ──
        weights = weights or self.DEFAULT_WEIGHTS
        if "library" not in weights:
            weights = self.DEFAULT_WEIGHTS

        # ── Cooldown set ──
        recent_yt_ids = set(await self.playback.get_recent_history_window(guild_id, cooldown_seconds))
        recent_by_count = await self.playback.get_recent_history(guild_id, limit=20)
        recent_yt_ids.update(r["canonical_yt_id"] for r in recent_by_count)

        # ── Step 1: Build user profile vector ──
        user_vector = await self._build_user_vector(turn_user_id)
        logger.info(
            f"Discovery for user {turn_user_id} | "
            f"profile: {debug_vector(user_vector, 'user')} | "
            f"cooldown: {len(recent_yt_ids)} songs"
        )

        # ── Step 2: Gather candidates from ALL sources ──
        candidates = await self._gather_all_candidates(
            turn_user_id, recent_yt_ids, weights
        )

        if not candidates:
            logger.warning(f"No candidates found for user {turn_user_id}")
            self.turn_tracker.advance(guild_id)
            return None

        logger.info(
            f"Gathered {len(candidates)} candidates: "
            + ", ".join(
                f"{src}={count}"
                for src, count in _count_sources(candidates).items()
            )
        )

        # ── Step 3: Score all candidates against user vector ──
        scored = score_candidates(user_vector, candidates, temperature=0.15)

        # Log top 5 for debugging
        for i, (cand, sc) in enumerate(scored[:5]):
            logger.debug(
                f"  #{i+1} [{cand.source}] {cand.artist} - {cand.title} "
                f"(score={sc:.4f})"
            )

        # ── Step 4: Softmax-select winner ──
        winner = softmax_select(scored, top_k=8, temperature=0.5)

        self.turn_tracker.advance(guild_id)

        if not winner:
            return None

        # Fill missing duration
        if winner.duration_seconds is None:
            details = await self.youtube.get_track_info(winner.video_id)
            if details and details.duration_seconds:
                winner.duration_seconds = details.duration_seconds

        # Find the score for logging
        winner_score = next(
            (sc for cand, sc in scored if cand.video_id == winner.video_id), 0.0
        )

        logger.info(
            f"Selected [{winner.source}] {winner.artist} - {winner.title} "
            f"(score={winner_score:.4f}) for user {turn_user_id}"
        )

        return DiscoveredSong(
            video_id=winner.video_id,
            title=winner.title,
            artist=winner.artist,
            strategy=winner.source,
            reason=self._generate_reason(winner),
            for_user_id=turn_user_id,
            duration_seconds=winner.duration_seconds,
            year=winner.year,
            genre=winner.genres[0] if winner.genres else None,
            score=winner_score,
        )

    # ════════════════════════════════════════════════════════════════
    #  User Profile Vector
    # ════════════════════════════════════════════════════════════════

    async def _build_user_vector(self, user_id: int) -> list[float]:
        """Build the 128-dim taste profile for a user from their DB preferences."""
        # Fetch all preference types
        all_prefs = await self.preferences.get_all_preferences(user_id)
        genre_prefs = all_prefs.get("genre", {})
        artist_prefs = all_prefs.get("artist", {})
        decade_prefs = all_prefs.get("decade", {})

        # Build vectors from liked songs for centroid reinforcement
        liked_song_vectors = []
        liked = await self.reactions.get_liked_songs(user_id, limit=30)
        for song in liked:
            genres = await self._get_song_genres(song.get("id"))
            sv = encode_song(
                genres=genres,
                artist=song.get("artist_name"),
                year=song.get("release_year"),
                popularity=0.7,  # liked songs are implicitly valued
                source="library",
            )
            liked_song_vectors.append(sv)

        return build_user_profile(
            genre_prefs=genre_prefs,
            artist_prefs=artist_prefs,
            decade_prefs=decade_prefs,
            liked_song_vectors=liked_song_vectors if liked_song_vectors else None,
        )

    async def _get_song_genres(self, song_id: int | None) -> list[str]:
        """Get genres for a song from DB, with fallback."""
        if not song_id or not self.songs:
            return []
        try:
            return await self.songs.get_genres(song_id)
        except Exception:
            return []

    # ════════════════════════════════════════════════════════════════
    #  Candidate Pool Gathering
    # ════════════════════════════════════════════════════════════════

    async def _gather_all_candidates(
        self,
        user_id: int,
        recent_yt_ids: set[str],
        weights: dict[str, int],
    ) -> list[SongCandidate]:
        """
        Gather candidates from ALL active sources in parallel.

        Weights control pool sizes: higher weight = more candidates from that source.
        All candidates then compete in a single vector scoring round.
        """
        total_weight = sum(weights.values()) or 1
        seen_ids: set[str] = set(recent_yt_ids)
        all_candidates: list[SongCandidate] = []

        # Determine which pools to query based on weights > 0
        tasks = []
        if weights.get("library", 0) > 0:
            tasks.append(self._pool_library(user_id, seen_ids))
        if weights.get("similar", 0) > 0:
            tasks.append(self._pool_similar(user_id, seen_ids))
        if weights.get("artist", 0) > 0:
            tasks.append(self._pool_artist(user_id, seen_ids))
        if weights.get("wildcard", 0) > 0:
            tasks.append(self._pool_wildcard(seen_ids))

        # Gather all pools in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Pool gather error: {result}")
                continue
            if result:
                all_candidates.extend(result)

        # Deduplicate by video_id (keep first occurrence)
        deduped: list[SongCandidate] = []
        dedup_ids: set[str] = set()
        for c in all_candidates:
            if c.video_id not in dedup_ids and c.video_id not in recent_yt_ids:
                deduped.append(c)
                dedup_ids.add(c.video_id)

        return deduped

    async def _pool_library(
        self, user_id: int, seen_ids: set[str]
    ) -> list[SongCandidate]:
        """Gather candidates from user's liked library."""
        candidates = []
        liked = await self.reactions.get_liked_songs(user_id, limit=100)

        for song in liked:
            vid = song.get("canonical_yt_id")
            if not vid or vid in seen_ids:
                continue

            genres = await self._get_song_genres(song.get("id"))
            vec = encode_song(
                genres=genres,
                artist=song.get("artist_name"),
                year=song.get("release_year"),
                popularity=0.7,
                source="library",
            )
            candidates.append(SongCandidate(
                video_id=vid,
                title=song["title"],
                artist=song["artist_name"],
                source="library",
                vector=vec,
                duration_seconds=song.get("duration_seconds"),
                year=song.get("release_year"),
                genres=genres,
            ))

        return candidates

    async def _pool_similar(
        self, user_id: int, seen_ids: set[str]
    ) -> list[SongCandidate]:
        """Gather candidates from YouTube watch playlist (related songs)."""
        candidates = []
        liked = await self.reactions.get_liked_songs(user_id, limit=20)
        if not liked:
            return []

        # Pick up to 2 random seed songs for broader coverage
        seeds = random.sample(liked, min(2, len(liked)))

        for seed_song in seeds:
            seed_yt_id = seed_song.get("canonical_yt_id")
            if not seed_yt_id:
                continue

            # Get related tracks from YouTube's watch playlist
            related = await self.youtube.get_watch_playlist(seed_yt_id, limit=15)

            seed_artist = seed_song.get("artist_name", "").lower()
            seed_genres = await self._get_song_genres(seed_song.get("id"))

            for track in related:
                if track.video_id in seen_ids:
                    continue
                # Skip same artist for diversity
                if track.artist.lower() == seed_artist:
                    continue

                # Related songs inherit seed's genres at reduced weight
                # (YouTube related tracks likely share genre characteristics)
                vec = encode_song(
                    genres=seed_genres,  # inferred from seed
                    artist=track.artist,
                    year=track.year,
                    popularity=0.5,
                    source="similar",
                )
                candidates.append(SongCandidate(
                    video_id=track.video_id,
                    title=track.title,
                    artist=track.artist,
                    source="similar",
                    vector=vec,
                    duration_seconds=track.duration_seconds,
                    year=track.year,
                    genres=seed_genres,
                ))

        return candidates

    async def _pool_artist(
        self, user_id: int, seen_ids: set[str]
    ) -> list[SongCandidate]:
        """Gather candidates from top preferred artists via Spotify."""
        candidates = []
        top_artists = await self.preferences.get_top_preferences(user_id, "artist", limit=8)
        if not top_artists:
            return []

        # Sample a few artists to query
        sample = random.sample(top_artists, min(3, len(top_artists)))

        for artist_name, affinity in sample:
            sp_result = await self.spotify.search_artist(artist_name)
            if not sp_result:
                continue

            artist_genres = sp_result.genres or []
            top_tracks = await self.spotify.get_artist_top_tracks(sp_result.artist_id)

            for track in random.sample(top_tracks, min(5, len(top_tracks))):
                # Normalize to YouTube ID
                normalized = await self.normalizer.normalize(track.title, track.artist)
                if not normalized or normalized.canonical_yt_id in seen_ids:
                    continue

                vec = encode_song(
                    genres=artist_genres,
                    artist=track.artist,
                    year=track.release_year,
                    popularity=track.popularity / 100.0 if track.popularity else 0.5,
                    source="artist",
                )
                candidates.append(SongCandidate(
                    video_id=normalized.canonical_yt_id,
                    title=normalized.clean_title,
                    artist=normalized.clean_artist,
                    source="artist",
                    vector=vec,
                    duration_seconds=track.duration_seconds,
                    year=track.release_year,
                    genres=artist_genres,
                    popularity=track.popularity / 100.0 if track.popularity else 0.5,
                ))

        return candidates

    async def _pool_wildcard(self, seen_ids: set[str]) -> list[SongCandidate]:
        """Gather candidates from chart playlists."""
        candidates = []

        region = random.choice(["US", "UK"])
        query = f"Top 100 Songs {region} 2024"
        playlists = await self.youtube.search_playlists(query, limit=3)

        tracks: list[YTTrack] = []
        if playlists:
            playlist = random.choice(playlists)
            tracks = await self.youtube.get_playlist_tracks(
                playlist["browse_id"], limit=40
            )
        else:
            # Fallback: direct search
            tracks = await self.youtube.search(
                "top hits 2024", filter_type="songs", limit=20
            )

        for track in tracks:
            if track.video_id in seen_ids:
                continue

            vec = encode_song(
                genres=None,  # chart songs: no genre data
                artist=track.artist,
                year=track.year,
                popularity=0.7,  # charts = popular
                source="wildcard",
            )
            candidates.append(SongCandidate(
                video_id=track.video_id,
                title=track.title,
                artist=track.artist,
                source="wildcard",
                vector=vec,
                duration_seconds=track.duration_seconds,
                year=track.year,
            ))

        return candidates

    # ════════════════════════════════════════════════════════════════
    #  Reason Generation
    # ════════════════════════════════════════════════════════════════

    def _generate_reason(self, candidate: SongCandidate) -> str:
        """Generate a human-readable discovery reason based on source."""
        reasons = {
            "similar": "Similar to songs you like",
            "artist": f"From artist you enjoy: {candidate.artist}",
            "wildcard": "Popular track you might like",
            "library": "From your saved library",
        }
        return reasons.get(candidate.source, "Discovered for you")


def _count_sources(candidates: list[SongCandidate]) -> dict[str, int]:
    """Count candidates by source for logging."""
    counts: dict[str, int] = {}
    for c in candidates:
        counts[c.source] = counts.get(c.source, 0) + 1
    return counts
