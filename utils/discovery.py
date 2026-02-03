import logging
import random
from typing import List, Dict, Optional, Any
from database import db
from config import Config

logger = logging.getLogger('Vexo.Discovery')

class DiscoveryEngine:
    """The smart discovery logic for Vexo."""
    
    def __init__(self):
        self.session_interactors: Dict[int, int] = {} # guild_id -> discord_id of last interactor

    def set_interactor(self, guild_id: int, discord_id: int):
        """Set the last person who interacted with the bot in this guild."""
        self.session_interactors[guild_id] = discord_id

    async def get_next_songs(self, guild_id: int, vc_user_ids: List[int], count: int = 10) -> List[Dict[str, Any]]:
        """
        Calculate the next songs to play based on users in the voice channel.
        Returns a list of track dicts with artist, song, and url.
        """
        logger.info(f"--- Discovery Engine Run (Guild: {guild_id}) ---")
        logger.info(f"Analyzing preferences for users: {vc_user_ids}")
        
        # 1. Aggregate all user preferences
        all_user_prefs = []
        for user_id in vc_user_ids:
            prefs = await db.get_user_preferences(user_id)
            all_user_prefs.extend(prefs)
            logger.debug(f"User {user_id} has {len(prefs)} preference entries.")

        # 2. Get the global autoplay pool (stored with guild_id=0)
        pool = await db.get_autoplay_pool(0)  # Query global pool
        logger.info(f"Global pool size: {len(pool)} tracks.")

        # 3. Score each track in the pool
        scored_tracks = []
        
        # Build a map of liked artists and songs for fast lookup
        liked_artists = {}
        liked_urls = {}
        for pref in all_user_prefs:
            url = pref.get('url')
            if not url: continue
            
            artist = pref.get('artist', '').lower()
            score = pref.get('score', 0)
            
            liked_urls[url] = max(liked_urls.get(url, 0), score)
            if artist:
                liked_artists[artist] = max(liked_artists.get(artist, 0), score)

        for track in pool:
            url = track.get('url')
            if not url: continue
            
            artist = track.get('artist', '').lower()
            
            # Enforce 120-minute lockout
            if await db.is_recently_played(guild_id, url, minutes=120):
                logger.debug(f"Skipping '{track['song']}' - Played within last 120m.")
                continue

            score = 0
            reasons = []

            # Rule 1: Direct Like (+10)
            if url in liked_urls:
                bonus = 10 if liked_urls[url] > 0 else -10
                score += bonus
                reasons.append(f"Direct Like ({bonus})")

            # Rule 2: Artist Affinity (+5)
            if artist in liked_artists:
                bonus = 5 if liked_artists[artist] > 0 else -5
                score += bonus
                reasons.append(f"Artist Affinity ({bonus})")

            # Rule 3: Random Factor (+1 to +3)
            random_bonus = random.randint(1, 3)
            score += random_bonus
            # reasons.append(f"Organic Variance (+{random_bonus})")

            scored_tracks.append({
                "track": track,
                "score": score,
                "reasons": reasons
            })

        # 4. Sort and select top tracks
        scored_tracks.sort(key=lambda x: x['score'], reverse=True)
        
        selection = []
        for item in scored_tracks[:count]:
            selection.append(item['track'])
            logger.info(f"Selected: '{item['track']['song']}' by {item['track']['artist']} (Score: {item['score']}, Factors: {', '.join(item['reasons'])})")

        if not selection:
            logger.warning("Discovery Engine found 0 matches in pool. This usually happens if the pool is empty or history lockout is full.")
            
        return selection

    async def get_mood_recommendation(self, guild_id: int, seed_song_title: str, artist: str) -> Optional[Dict[str, Any]]:
        """Find a song similar to the seed song."""
        logger.info(f"Mood Search: Finding tracks similar to '{seed_song_title}' by {artist}")
        
        pool = await db.get_autoplay_pool(0)  # Query global pool
        # Simple keyword matching for now - can be expanded to external APIs
        matches = [t for t in pool if t['artist'].lower() == artist.lower() or any(word in t['song'].lower() for word in seed_song_title.lower().split())]
        
        # Filter out recently played
        matches = [t for t in matches if not await db.is_recently_played(guild_id, t['url'])]

        if matches:
            chosen = random.choice(matches)
            logger.info(f"Mood Result: Selected '{chosen['song']}' as a similar vibe.")
            return chosen
        
        logger.info("Mood Result: No similar tracks found in pool.")
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
            # Also ensure it's in the global autoplay pool for future discovery
            await db.add_to_autoplay_pool(0, artist, song_title, url) # 0 for global or specific guild

# Global instance
discovery_engine = DiscoveryEngine()
