import logging
import math
import random
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any, Tuple, Set
from database import db
from config import Config
from utils.logger import set_logger

logger = set_logger(logging.getLogger('Vexo.Discovery'))


# ---------------------------------------------------------------------------
# Stop-words that should NEVER count as meaningful keyword matches.
# These appear in thousands of song titles across all genres and produce
# noisy, irrelevant "Similar to …" matches.
# ---------------------------------------------------------------------------
_KEYWORD_STOPWORDS: Set[str] = {
    "feat", "remix", "edit", "version", "live", "radio", "official",
    "audio", "video", "music", "lyric", "lyrics", "visualizer",
    "from", "with", "this", "that", "your", "have", "been", "will",
    "what", "when", "where", "they", "them", "than", "into", "over",
    "just", "about", "also", "back", "only", "more", "some", "like",
    "love", "baby", "yeah", "part", "prod", "remaster", "remastered",
    "deluxe", "bonus", "track", "album", "single", "extended",
}


def _extract_keywords(title: str) -> Set[str]:
    """Extract meaningful keywords from a song title (lowercase, >3 chars, no stop-words)."""
    words = set()
    for w in title.lower().split():
        # Strip common punctuation
        w = w.strip("()[]{}.,!?'\"")
        if len(w) > 3 and w not in _KEYWORD_STOPWORDS:
            words.add(w)
    return words


def _genres_overlap(genres_a: Optional[str], genres_b: Optional[str]) -> bool:
    """Check if two Spotify-style genre strings share at least one genre token."""
    if not genres_a or not genres_b:
        return False
    # Genres are stored as comma-separated or JSON-ish strings
    set_a = {g.strip().lower().strip('"[]') for g in genres_a.replace(",", " ").split()}
    set_b = {g.strip().lower().strip('"[]') for g in genres_b.replace(",", " ").split()}
    return bool(set_a & set_b)


def _temporal_weight(last_interaction: str, half_life_days: int) -> float:
    """
    Apply exponential temporal decay to a score.
    Returns a multiplier between 0.0 and 1.0.
    More recent interactions → closer to 1.0.
    """
    try:
        ts = datetime.strptime(last_interaction, '%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError):
        # Fallback: treat as very old
        return 0.1
    age_days = max((datetime.now() - ts).total_seconds() / 86400, 0)
    return math.pow(0.5, age_days / max(half_life_days, 1))


class DiscoveryEngine:
    """
    Improved discovery engine for Vexo.

    Philosophy — the "addictive radio" model:

    Each user's allocation is split into three tiers:
      • COMFORT  (default 50%) — songs you already liked, weighted by
        score × temporal decay so recent favorites surface more.
      • ADJACENT (default 35%) — new songs from artists/genres you like,
        boosted by collaborative filtering and genre matching. This is the
        bridge that keeps things fresh without feeling alien.
      • WILDCARD (default 15%) — surprising picks from the broader pool
        that scored zero on direct matching. This is the variable-reward
        dopamine hit. Sometimes you discover your new favourite artist here.

    Every slot carries a human-readable `reason` explaining *why* it was
    chosen, so users can understand and trust the algorithm.
    """

    def __init__(self):
        self.session_interactors: Dict[int, int] = {}  # guild_id -> last interactor

    def set_interactor(self, guild_id: int, discord_id: int):
        """Set the last person who interacted with the bot in this guild."""
        self.session_interactors[guild_id] = discord_id

    # ------------------------------------------------------------------
    # MAIN ENTRY POINT
    # ------------------------------------------------------------------

    async def build_user_slots(
        self,
        user_id: int,
        guild_id: int,
        count: int = 4,
        disallow_urls: Optional[Set[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Build up to `count` slots for a single user across three tiers.

        Tier allocation (rounded):
          comfort_count  = round(count × COMFORT_RATIO)
          adjacent_count = round(count × ADJACENT_RATIO)
          wildcard_count = count - comfort - adjacent

        Returns empty list if user has no positive preferences.
        """
        dedup_minutes = Config.DISCOVERY_DEDUP_MINUTES
        half_life = Config.DISCOVERY_DECAY_HALF_LIFE_DAYS

        # --- 1. Load user preferences with timestamps ---
        prefs = await db.get_user_preferences_with_timestamps(user_id)
        liked_songs = [p for p in prefs if p.get('score', 0) > 0]

        if not liked_songs:
            logger.debug(f"User {user_id} has no liked songs, skipping.")
            return []

        used_urls: Set[str] = set(disallow_urls or set())
        slots: List[Dict[str, Any]] = []

        # --- 2. Compute effective scores (score × temporal decay) ---
        for p in liked_songs:
            raw_score = p.get('score', 1)
            decay = _temporal_weight(p.get('last_interaction', '2020-01-01 00:00:00'), half_life)
            p['effective_score'] = raw_score * decay

        # Sort by effective score descending for comfort picks
        liked_songs.sort(key=lambda p: p['effective_score'], reverse=True)

        # --- 3. Determine tier counts ---
        comfort_count = max(1, round(count * Config.DISCOVERY_RATIO_COMFORT))
        adjacent_count = max(1, round(count * Config.DISCOVERY_RATIO_ADJACENT))
        wildcard_count = max(0, count - comfort_count - adjacent_count)

        logger.info(
            f"Building {count} slots for user {user_id} "
            f"(comfort={comfort_count}, adjacent={adjacent_count}, wildcard={wildcard_count}) | "
            f"{len(liked_songs)} liked songs in profile"
        )

        # --- 4. COMFORT TIER — weighted by effective score ---
        comfort_slots = await self._pick_comfort(
            liked_songs, guild_id, user_id, comfort_count, used_urls, dedup_minutes
        )
        slots.extend(comfort_slots)
        for s in comfort_slots:
            if s.get('url'):
                used_urls.add(s['url'])

        # --- 5. Prepare shared data for adjacent + wildcard tiers ---
        pool = await db.get_autoplay_pool(0)

        # Build artist -> liked-song map
        artist_to_liked: Dict[str, Dict] = {}
        for p in liked_songs:
            key = p.get('artist', '').lower()
            if key and key not in artist_to_liked:
                artist_to_liked[key] = p
        liked_artist_set = set(artist_to_liked.keys())

        # Build keyword -> liked-song map (filtered stop-words)
        keyword_to_liked: Dict[str, Dict] = {}
        for p in liked_songs:
            for kw in _extract_keywords(p.get('liked_song', '')):
                if kw not in keyword_to_liked:
                    keyword_to_liked[kw] = p

        # Look up genres for liked artists (batch)
        liked_artist_genres = await db.get_genres_for_artists(list(liked_artist_set))

        # Get collaborative recommendations
        collab_urls: Set[str] = set()
        collab_map: Dict[str, int] = {}  # url -> supporter_count
        try:
            collab_songs = await db.get_collaborative_songs(user_id, guild_id, limit=50)
            for cs in collab_songs:
                collab_urls.add(cs['url'])
                collab_map[cs['url']] = cs.get('supporter_count', 1)
        except Exception as e:
            logger.debug(f"Collaborative filtering query failed (non-critical): {e}")

        # Get last played song for momentum
        last_played = await db.get_last_played_song(guild_id)
        last_artist = (last_played.get('artist', '') if last_played else '').lower()
        last_genre = None
        if last_artist:
            last_genre = await db.get_genre_for_artist(last_artist)

        # --- 6. Score ALL pool tracks once ---
        scored_pool = await self._score_pool(
            pool, used_urls, guild_id, dedup_minutes,
            liked_artist_set, artist_to_liked,
            keyword_to_liked,
            liked_artist_genres,
            collab_urls, collab_map,
            last_artist, last_genre,
            user_id
        )

        # --- 7. ADJACENT TIER — tracks with score > 0 ---
        adjacent_candidates = [s for s in scored_pool if s['score'] > 0]
        # Sort by score with a touch of randomness to avoid identical queues
        adjacent_candidates.sort(key=lambda x: x['score'] + random.random() * 2, reverse=True)

        adjacent_slots = []
        for item in adjacent_candidates:
            if len(adjacent_slots) >= adjacent_count:
                break
            url = item['track'].get('url')
            if url in used_urls:
                continue
            slot = self._make_slot(item, user_id, 'adjacent')
            reasons_str = " + ".join(item.get('reasons_detail', []))
            logger.info(
                f"  [ADJACENT] '{slot['song']}' by {slot['artist']} | "
                f"score={item['score']} | {reasons_str}"
            )
            adjacent_slots.append(slot)
            used_urls.add(url)
        slots.extend(adjacent_slots)

        # --- 8. WILDCARD TIER — tracks with score == 0 (the surprise element) ---
        wildcard_candidates = [s for s in scored_pool if s['score'] == 0 and s['track'].get('url') not in used_urls]
        random.shuffle(wildcard_candidates)

        wildcard_slots = []
        for item in wildcard_candidates:
            if len(wildcard_slots) >= wildcard_count:
                break
            url = item['track'].get('url')
            if url in used_urls:
                continue
            # Give it a wildcard reason
            item['reason'] = "Wildcard pick — something new for you"
            item['matched_song'] = None
            slot = self._make_slot(item, user_id, 'wildcard')
            logger.info(
                f"  [WILDCARD] '{slot['song']}' by {slot['artist']} | "
                f"no direct match — random discovery pick"
            )
            wildcard_slots.append(slot)
            used_urls.add(url)
        slots.extend(wildcard_slots)

        # --- 9. If we still have room, backfill from adjacent overflow ---
        remaining = count - len(slots)
        if remaining > 0:
            for item in adjacent_candidates:
                if remaining <= 0:
                    break
                url = item['track'].get('url')
                if url in used_urls:
                    continue
                slots.append(self._make_slot(item, user_id, 'adjacent'))
                used_urls.add(url)
                remaining -= 1

        tier_counts = {}
        for s in slots:
            t = s.get('slot_type', '?')
            tier_counts[t] = tier_counts.get(t, 0) + 1
        logger.info(
            f"Built {len(slots)} slots for user {user_id}: "
            f"{tier_counts.get('comfort', 0)} comfort, "
            f"{tier_counts.get('adjacent', 0)} adjacent, "
            f"{tier_counts.get('wildcard', 0)} wildcard"
        )
        return slots

    # ------------------------------------------------------------------
    # COMFORT picks
    # ------------------------------------------------------------------

    async def _pick_comfort(
        self,
        liked_songs: List[Dict],
        guild_id: int,
        user_id: int,
        count: int,
        used_urls: Set[str],
        dedup_minutes: int
    ) -> List[Dict[str, Any]]:
        """
        Pick comfort-tier songs from the user's liked history.
        Uses weighted random selection based on effective_score so higher-scored
        songs appear more often, but lower-scored ones still have a chance.
        """
        # Build a weighted pool of eligible liked songs
        eligible = []
        for p in liked_songs:
            url = p.get('url')
            if not url or url in used_urls:
                continue
            if await db.is_recently_played(guild_id, url, minutes=dedup_minutes):
                continue
            eligible.append(p)

        if not eligible:
            return []

        # Weighted random sampling (without replacement)
        selected = []
        remaining = list(eligible)
        for _ in range(min(count, len(remaining))):
            weights = [max(p['effective_score'], 0.1) for p in remaining]
            total = sum(weights)
            r = random.random() * total
            cumulative = 0
            pick_idx = 0
            for i, w in enumerate(weights):
                cumulative += w
                if cumulative >= r:
                    pick_idx = i
                    break

            pick = remaining.pop(pick_idx)
            raw_score = pick.get('score', 0)
            decay = round(pick['effective_score'] / max(raw_score, 1), 2)
            eff = round(pick['effective_score'], 1)
            song_name = pick.get('liked_song', 'Unknown')
            artist_name = pick.get('artist', 'Unknown')

            logger.info(
                f"  [COMFORT] '{song_name}' by {artist_name} | "
                f"raw={raw_score} x decay={decay} = eff={eff}"
            )

            selected.append({
                'artist': artist_name,
                'song': song_name,
                'url': pick.get('url'),
                'user_id': user_id,
                'slot_type': 'comfort',
                'reason': f"From your likes (score {eff})",
                'matched_song': None
            })

        return selected

    # ------------------------------------------------------------------
    # Pool scoring (shared by adjacent + wildcard tiers)
    # ------------------------------------------------------------------

    async def _score_pool(
        self,
        pool: List[Dict],
        used_urls: Set[str],
        guild_id: int,
        dedup_minutes: int,
        liked_artist_set: Set[str],
        artist_to_liked: Dict[str, Dict],
        keyword_to_liked: Dict[str, Dict],
        liked_artist_genres: Dict[str, str],
        collab_urls: Set[str],
        collab_map: Dict[str, int],
        last_artist: str,
        last_genre: Optional[str],
        user_id: int
    ) -> List[Dict[str, Any]]:
        """
        Score every track in the global pool for the adjacent/wildcard tiers.
        Returns a list of {track, score, reason, matched_song, reasons_detail}.
        """
        results = []
        for track in pool:
            url = track.get('url')
            if not url or url in used_urls:
                continue
            if await db.is_recently_played(guild_id, url, minutes=dedup_minutes):
                continue

            artist_lower = track.get('artist', '').lower()
            title_lower = track.get('song', '').lower()

            score = 0
            reasons: List[str] = []
            matched_song = None

            # --- A. Artist match (strongest signal) ---
            if artist_lower in liked_artist_set:
                liked_info = artist_to_liked[artist_lower]
                matched_song = liked_info.get('liked_song', 'Unknown')
                # Weight by how much the user likes this artist
                artist_bonus = 5
                effective = liked_info.get('effective_score', 5)
                if effective > 10:
                    artist_bonus = 7  # Favourite artist gets a boost
                score += artist_bonus
                reasons.append(f"Same artist as '{matched_song}'")

            # --- B. Keyword match (filtered stop-words) ---
            track_keywords = _extract_keywords(track.get('song', ''))
            matching_kws = set(keyword_to_liked.keys()) & track_keywords
            if matching_kws:
                kw_score = len(matching_kws) * 2
                score += kw_score
                if not matched_song:
                    sample_kw = next(iter(matching_kws))
                    liked_info = keyword_to_liked[sample_kw]
                    matched_song = liked_info.get('liked_song', 'Unknown')
                reasons.append(f"Title keywords match '{matched_song}'")

            # --- C. Genre match ---
            track_genre = await db.get_genre_for_artist(artist_lower) if artist_lower else None
            if track_genre:
                for liked_artist, liked_genre_str in liked_artist_genres.items():
                    if _genres_overlap(track_genre, liked_genre_str):
                        score += Config.DISCOVERY_GENRE_MATCH_SCORE
                        if not matched_song:
                            liked_info = artist_to_liked.get(liked_artist)
                            if liked_info:
                                matched_song = liked_info.get('liked_song', 'Unknown')
                        reasons.append(f"Genre match ({track_genre.split(',')[0].strip().strip('\"[]')})")
                        break  # Count genre match once

            # --- D. Collaborative filtering ---
            if url in collab_urls:
                supporters = collab_map.get(url, 1)
                collab_bonus = min(Config.DISCOVERY_COLLAB_SCORE * supporters, 9)
                score += collab_bonus
                reasons.append(f"Liked by {supporters} listener{'s' if supporters > 1 else ''} with similar taste")

            # --- E. Momentum (match the vibe of what just played) ---
            if last_artist and artist_lower == last_artist:
                score += Config.DISCOVERY_MOMENTUM_SCORE
                reasons.append("Keeps the current vibe going")
            elif last_genre and track_genre and _genres_overlap(last_genre, track_genre):
                score += max(1, Config.DISCOVERY_MOMENTUM_SCORE - 1)
                reasons.append("Matches the current mood")

            # Build the primary reason string
            primary_reason = reasons[0] if reasons else "Discovery"

            # Log score breakdown for every candidate that scored > 0
            if score > 0:
                track_label = f"'{track.get('song', '?')}' by {track.get('artist', '?')}"
                reasons_str = " + ".join(reasons)
                logger.debug(
                    f"  [SCORE] {track_label} => {score} pts ({reasons_str})"
                )

            results.append({
                'track': track,
                'score': score,
                'reason': primary_reason,
                'reasons_detail': reasons,
                'matched_song': matched_song
            })

        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_slot(scored_item: Dict, user_id: int, slot_type: str) -> Dict[str, Any]:
        """Convert a scored pool item into a queue slot dict."""
        track = scored_item['track']
        return {
            'artist': track.get('artist', 'Unknown'),
            'song': track.get('song', 'Unknown'),
            'url': track.get('url'),
            'user_id': user_id,
            'slot_type': slot_type,
            'reason': scored_item.get('reason', 'Discovery'),
            'matched_song': scored_item.get('matched_song')
        }

    # ------------------------------------------------------------------
    # Queue allocation (multi-user fairness) — unchanged logic
    # ------------------------------------------------------------------

    async def allocate_queue(self, guild_id: int, user_ids: List[int]) -> Tuple[List[Dict], List[Dict]]:
        """
        Build public and hidden queues with fair per-user allocation.

        Each user with preferences gets:
        - 2 songs in public queue (committed)
        - 2 songs in hidden queue (dynamic)

        Returns (public_queue, hidden_queue).
        """
        logger.info(f"--- Allocating Queue (Guild: {guild_id}) ---")
        logger.info(f"Users in voice: {user_ids}")

        slots_per_user = Config.DISCOVERY_SLOTS_PER_USER
        public_by_user: Dict[int, List[Dict]] = {}
        hidden_by_user: Dict[int, List[Dict]] = {}
        global_used: Set[str] = set()

        for user_id in user_ids:
            if not await db.has_preferences(user_id):
                logger.debug(f"User {user_id} has no preferences, skipping.")
                continue

            slots = await self.build_user_slots(
                user_id, guild_id, count=slots_per_user, disallow_urls=global_used
            )
            if not slots:
                continue

            half = slots_per_user // 2
            public_by_user[user_id] = slots[:half]
            hidden_by_user[user_id] = slots[half:]
            for s in slots:
                url = s.get('url')
                if url:
                    global_used.add(url)

        public = self._interleave_slots(public_by_user)
        hidden = self._interleave_slots(hidden_by_user)

        # Log the final ordered queue with full reasoning
        logger.info(f"=== Final Queue (Guild {guild_id}) ===")
        for i, slot in enumerate(public):
            logger.info(
                f"  PUBLIC [{i+1}] '{slot['song']}' by {slot['artist']} "
                f"[{slot['slot_type']}] — {slot.get('reason', '?')} "
                f"(user {slot.get('user_id', '?')})"
            )
        for i, slot in enumerate(hidden):
            logger.info(
                f"  HIDDEN [{i+1}] '{slot['song']}' by {slot['artist']} "
                f"[{slot['slot_type']}] — {slot.get('reason', '?')} "
                f"(user {slot.get('user_id', '?')})"
            )
        logger.info(f"Allocation complete: {len(public)} public, {len(hidden)} hidden")
        return public, hidden

    def _interleave_slots(self, slots_by_user: Dict[int, List[Dict]]) -> List[Dict]:
        """Round-robin interleave slots from different users for fair representation."""
        if not slots_by_user:
            return []

        result = []
        user_ids = list(slots_by_user.keys())
        random.shuffle(user_ids)

        max_slots = max(len(slots) for slots in slots_by_user.values())
        for i in range(max_slots):
            for user_id in user_ids:
                user_slots = slots_by_user.get(user_id, [])
                if i < len(user_slots):
                    result.append(user_slots[i])

        return result

    async def get_next_songs(self, guild_id: int, vc_user_ids: List[int], count: int = 10) -> List[Dict[str, Any]]:
        """
        Legacy method — wraps allocate_queue for backward compatibility.
        Returns tracks for autoplay buffer.
        """
        public, hidden = await self.allocate_queue(guild_id, vc_user_ids)
        return (public + hidden)[:count]

    async def get_mood_recommendation(self, guild_id: int, seed_song_title: str, artist: str) -> Optional[Dict[str, Any]]:
        """Find a song similar to the seed song for mood refresh."""
        logger.debug(f"Mood Search: Finding tracks similar to '{seed_song_title}' by {artist}")

        pool = await db.get_autoplay_pool(0)
        dedup_minutes = Config.DISCOVERY_DEDUP_MINUTES

        seed_words = _extract_keywords(seed_song_title)
        artist_lower = artist.lower()

        # Also try genre matching for mood recommendations
        seed_genre = await db.get_genre_for_artist(artist_lower)

        matches = []
        for t in pool:
            score = 0
            t_artist = t.get('artist', '').lower()

            if t_artist == artist_lower:
                score += 5
            title_words = _extract_keywords(t.get('song', ''))
            score += len(seed_words & title_words) * 2

            # Genre bonus for mood
            if seed_genre and t_artist != artist_lower:
                t_genre = await db.get_genre_for_artist(t_artist)
                if t_genre and _genres_overlap(seed_genre, t_genre):
                    score += 3

            if score > 0 and not await db.is_recently_played(guild_id, t['url'], minutes=dedup_minutes):
                matches.append({'track': t, 'score': score})

        if matches:
            matches.sort(key=lambda x: x['score'] + random.random(), reverse=True)
            chosen = matches[0]['track']
            logger.debug(f"Mood Result: Selected '{chosen['song']}' as similar vibe.")
            return chosen

        logger.debug("Mood Result: No similar tracks found in pool.")
        return None

    async def record_interaction(self, user_id: int, artist: str, song_title: str, url: str, interaction_type: str):
        """Record an interaction and update score."""
        weights = {
            "upvote": Config.DISCOVERY_WEIGHT_UPVOTE,
            "downvote": Config.DISCOVERY_WEIGHT_DOWNVOTE,
            "skip": Config.DISCOVERY_WEIGHT_SKIP,
            "request": Config.DISCOVERY_WEIGHT_REQUEST
        }

        delta = weights.get(interaction_type, 0)
        if delta != 0:
            logger.info(f"Interaction: {interaction_type} by {user_id} for '{song_title}' (Delta: {delta})")
            await db.update_user_preference(user_id, artist, song_title, url, delta)
            # Also ensure it's in the global autoplay pool
            await db.add_to_autoplay_pool(0, artist, song_title, url)


# Global instance
discovery_engine = DiscoveryEngine()
