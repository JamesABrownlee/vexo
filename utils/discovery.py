import logging
import random
from typing import List, Dict, Optional
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

    async def get_next_songs(self, guild_id: int, vc_user_ids: List[int], count: int = 5) -> List[str]:
        """
        Calculate the next songs to play based on users in the voice channel.
        This is a placeholder for a more complex recommendation algorithm.
        For now, it will return a list of 'favored' track IDs or search queries.
        """
        # 1. Aggregate preferences of all users in VC
        aggregated_preferences: Dict[str, float] = {}
        
        last_interactor = self.session_interactors.get(guild_id)
        
        for user_id in vc_user_ids:
            user_prefs = await db.get_user_preferences(user_id)
            
            # Apply interactor influence multiplier
            multiplier = Config.DISCOVERY_INTERACTOR_INFLUENCE if user_id == last_interactor else 1.0
            
            for track_id, score in user_prefs.items():
                aggregated_preferences[track_id] = aggregated_preferences.get(track_id, 0) + (score * multiplier)

        # 2. Filter out recently played songs
        recent_history = await db.get_recent_history(guild_id, limit=100)
        for track_id in recent_history:
            if track_id in aggregated_preferences:
                # Heavily penalize recently played songs instead of outright removal to allow 
                # highly requested ones if there are no other options, or just remove them.
                del aggregated_preferences[track_id]

        # 3. Sort by score
        recommended = sorted(aggregated_preferences.items(), key=lambda x: x[1], reverse=True)
        
        # 4. Return top N track IDs
        results = [track_id for track_id, score in recommended[:count]]
        
        # If we don't have enough recommendations, we might need to fallback to 
        # general 'popular' songs or related artists (this would need external API or larger DB)
        return results

    async def record_interaction(self, discord_id: int, track_id: str, interaction_type: str):
        """Record an interaction and update score."""
        weights = {
            "upvote": Config.DISCOVERY_WEIGHT_UPVOTE,
            "downvote": Config.DISCOVERY_WEIGHT_DOWNVOTE,
            "skip": Config.DISCOVERY_WEIGHT_SKIP,
            "request": Config.DISCOVERY_WEIGHT_REQUEST
        }
        
        delta = weights.get(interaction_type, 0)
        if delta != 0:
            await db.update_user_preference(discord_id, track_id, delta)
            logger.info(f"Recorded {interaction_type} for user {discord_id} on track {track_id}: {delta}")

# Global instance
discovery_engine = DiscoveryEngine()
