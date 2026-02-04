import logging
import random
from typing import List, Dict, Optional, Any, Tuple
from database import db
from config import Config
from utils.logger import set_logger

logger = set_logger(logging.getLogger('Vexo.Discovery'))


class DiscoveryEngine:
    """
    Smart discovery engine for Vexo.
    
    Each user in voice gets 2 public + 2 hidden slots:
    - 2 slots from songs they actually liked
    - 2 slots from similar artists/keywords (discovery)
    
    Users with no preferences are skipped.
    """
    
    def __init__(self):
        self.session_interactors: Dict[int, int] = {}  # guild_id -> last interactor

    def set_interactor(self, guild_id: int, discord_id: int):
        """Set the last person who interacted with the bot in this guild."""
        self.session_interactors[guild_id] = discord_id

    async def build_user_slots(self, user_id: int, guild_id: int, count: int = 4) -> List[Dict[str, Any]]:
        """
        Build slots for a single user.
        
        Returns up to `count` slots:
        - First half: Songs they actually liked
        - Second half: Discovery (similar artists/keywords)
        
        Returns empty list if user has no preferences.
        """
        # Get user preferences
        prefs = await db.get_user_preferences(user_id)
        liked_songs = [p for p in prefs if p.get('score', 0) > 0]
        
        if not liked_songs:
            logger.debug(f"User {user_id} has no liked songs, skipping.")
            return []
        
        slots = []
        used_urls = set()
        
        # 1. Pick from directly liked songs (up to count/2)
        liked_count = count // 2
        random.shuffle(liked_songs)
        
        for pick in liked_songs[:liked_count]:
            url = pick.get('url')
            if url and url not in used_urls:
                # Check if recently played
                if await db.is_recently_played(guild_id, url, minutes=30):
                    continue
                    
                slots.append({
                    'artist': pick.get('artist', 'Unknown'),
                    'song': pick.get('liked_song', 'Unknown'),
                    'url': url,
                    'user_id': user_id,
                    'slot_type': 'liked'
                })
                used_urls.add(url)
        
        # 2. Fill remaining with discovery (similar artists + keyword matching)
        discovery_needed = count - len(slots)
        if discovery_needed > 0:
            pool = await db.get_autoplay_pool(0)  # Global pool
            liked_artists = {p.get('artist', '').lower() for p in liked_songs if p.get('artist')}
            
            # Extract keywords from liked song titles
            liked_keywords = set()
            for p in liked_songs:
                title = p.get('liked_song', '')
                if title:
                    # Split into words, filter short words
                    words = [w.lower() for w in title.split() if len(w) > 3]
                    liked_keywords.update(words)
            
            # Score pool tracks for discovery
            discovery_candidates = []
            for track in pool:
                url = track.get('url')
                if not url or url in used_urls:
                    continue
                    
                if await db.is_recently_played(guild_id, url, minutes=30):
                    continue
                
                artist = track.get('artist', '').lower()
                title = track.get('song', '').lower()
                
                score = 0
                
                # Same artist = strong match
                if artist in liked_artists:
                    score += 5
                
                # Keyword match in title
                title_words = set(title.split())
                keyword_matches = len(liked_keywords & title_words)
                score += keyword_matches * 2
                
                if score > 0:
                    discovery_candidates.append({
                        'track': track,
                        'score': score
                    })
            
            # Sort by score, add randomness
            discovery_candidates.sort(key=lambda x: x['score'] + random.random(), reverse=True)
            
            for item in discovery_candidates[:discovery_needed]:
                track = item['track']
                slots.append({
                    'artist': track.get('artist', 'Unknown'),
                    'song': track.get('song', 'Unknown'),
                    'url': track.get('url'),
                    'user_id': user_id,
                    'slot_type': 'discovery'
                })
                used_urls.add(track.get('url'))
        
        logger.info(f"Built {len(slots)} slots for user {user_id}: {len([s for s in slots if s['slot_type']=='liked'])} liked, {len([s for s in slots if s['slot_type']=='discovery'])} discovery")
        return slots

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
        
        public_by_user = {}
        hidden_by_user = {}
        
        for user_id in user_ids:
            # Skip users without preferences
            if not await db.has_preferences(user_id):
                logger.debug(f"User {user_id} has no preferences, skipping.")
                continue
            
            # Build 4 slots for this user
            slots = await self.build_user_slots(user_id, guild_id, count=4)
            
            if not slots:
                continue
            
            # First 2 → public, next 2 → hidden
            public_by_user[user_id] = slots[:2]
            hidden_by_user[user_id] = slots[2:4]
        
        # Interleave for fairness (round-robin by user)
        public = self._interleave_slots(public_by_user)
        hidden = self._interleave_slots(hidden_by_user)
        
        logger.info(f"Allocation complete: {len(public)} public, {len(hidden)} hidden")
        return public, hidden

    def _interleave_slots(self, slots_by_user: Dict[int, List[Dict]]) -> List[Dict]:
        """Round-robin interleave slots from different users for fair representation."""
        if not slots_by_user:
            return []
        
        result = []
        user_ids = list(slots_by_user.keys())
        random.shuffle(user_ids)  # Randomize starting order
        
        max_slots = max(len(slots) for slots in slots_by_user.values())
        
        for i in range(max_slots):
            for user_id in user_ids:
                user_slots = slots_by_user.get(user_id, [])
                if i < len(user_slots):
                    result.append(user_slots[i])
        
        return result

    async def get_next_songs(self, guild_id: int, vc_user_ids: List[int], count: int = 10) -> List[Dict[str, Any]]:
        """
        Legacy method - now wraps allocate_queue for backward compatibility.
        Returns tracks for autoplay buffer.
        """
        public, hidden = await self.allocate_queue(guild_id, vc_user_ids)
        return (public + hidden)[:count]

    async def get_mood_recommendation(self, guild_id: int, seed_song_title: str, artist: str) -> Optional[Dict[str, Any]]:
        """Find a song similar to the seed song for mood refresh."""
        logger.debug(f"Mood Search: Finding tracks similar to '{seed_song_title}' by {artist}")
        
        pool = await db.get_autoplay_pool(0)
        
        # Extract keywords from seed
        seed_words = set(w.lower() for w in seed_song_title.split() if len(w) > 3)
        artist_lower = artist.lower()
        
        matches = []
        for t in pool:
            score = 0
            if t['artist'].lower() == artist_lower:
                score += 5
            title_words = set(t['song'].lower().split())
            score += len(seed_words & title_words) * 2
            
            if score > 0 and not await db.is_recently_played(guild_id, t['url']):
                matches.append({'track': t, 'score': score})
        
        if matches:
            matches.sort(key=lambda x: x['score'], reverse=True)
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
