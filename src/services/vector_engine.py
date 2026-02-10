"""
128-Dimensional Vector Discovery Engine

Encodes songs and user profiles as 128-dim float vectors.
Uses cosine similarity to score candidates against user taste.

Vector Layout (128 dimensions):
  [0:64]    Genre embedding space
  [64:80]   Artist fingerprint (hash-based)
  [80:96]   Temporal / era features
  [96:112]  Popularity & energy signals
  [112:128] Source affinity & novelty
"""
import hashlib
import math
import random
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

VECTOR_DIM = 128

# Dimension ranges
GENRE_START, GENRE_END = 0, 64
ARTIST_START, ARTIST_END = 64, 80
TEMPORAL_START, TEMPORAL_END = 80, 96
ENERGY_START, ENERGY_END = 96, 112
NOVELTY_START, NOVELTY_END = 112, 128

# ─── Genre Vocabulary ───────────────────────────────────────────────
# Maps genre strings to fixed dimensions in [0:64).
# Aliases point to the same index.
GENRE_MAP: dict[str, int] = {
    # Pop / mainstream
    "pop": 0, "dance pop": 0, "electropop": 0,
    "rock": 1, "classic rock": 1,
    "hip hop": 2, "hip-hop": 2, "hiphop": 2,
    "rap": 3,
    "r&b": 4, "rnb": 4, "r and b": 4, "rhythm and blues": 4,
    # Electronic
    "electronic": 5, "electronica": 5,
    "dance": 6,
    "edm": 7,
    "house": 8, "deep house": 8, "tech house": 8,
    "techno": 9,
    # Jazz / Blues / Soul
    "jazz": 10, "smooth jazz": 10,
    "blues": 11,
    "soul": 12, "neo-soul": 12, "neo soul": 12,
    "funk": 13,
    "reggae": 14,
    # Roots / Country
    "country": 15,
    "folk": 16, "indie folk": 16,
    "indie": 17, "indie rock": 17, "indie pop": 17,
    "alternative": 18, "alt rock": 18,
    "punk": 19, "pop punk": 19,
    # Heavy
    "metal": 20, "heavy metal": 20,
    "classical": 21,
    "latin": 22, "latin pop": 22,
    "k-pop": 23, "kpop": 23, "k pop": 23,
    "j-pop": 24, "jpop": 24, "j pop": 24,
    # Urban / modern
    "trap": 25,
    "drill": 26,
    "afrobeats": 27, "afrobeat": 27,
    "reggaeton": 28,
    "disco": 29,
    # Mood / texture
    "ambient": 30,
    "lo-fi": 31, "lofi": 31, "lo fi": 31,
    "chill": 32, "chillout": 32,
    "acoustic": 33,
    "singer-songwriter": 34, "singer songwriter": 34,
    # Sub-genres
    "grunge": 35,
    "emo": 36,
    "ska": 37,
    "dub": 38,
    "dubstep": 39,
    "trance": 40,
    "drum and bass": 41, "dnb": 41, "d&b": 41,
    "garage": 42, "uk garage": 42,
    "grime": 43,
    "gospel": 44,
    "opera": 45,
    "new wave": 46,
    "synthpop": 47, "synth-pop": 47, "synth pop": 47,
    "post-punk": 48, "post punk": 48,
    "shoegaze": 49,
    "psychedelic": 50, "psychedelic rock": 50,
    "progressive rock": 51, "prog rock": 51, "prog": 51,
    "hard rock": 52,
    "soft rock": 53,
    "new age": 54,
    "world": 55, "world music": 55,
    "celtic": 56,
    "bossa nova": 57,
    "salsa": 58,
    "cumbia": 59,
    "bachata": 60,
    "dancehall": 61,
    "soca": 62,
    "highlife": 63,
}

# ─── Decade Mapping ─────────────────────────────────────────────────
# Maps decade strings to indices within the temporal space [0:8).
DECADE_MAP: dict[str, int] = {
    "1950s": 0, "50s": 0,
    "1960s": 1, "60s": 1,
    "1970s": 2, "70s": 2,
    "1980s": 3, "80s": 3,
    "1990s": 4, "90s": 4,
    "2000s": 5, "00s": 5,
    "2010s": 6, "10s": 6,
    "2020s": 7, "20s": 7,
}

# ─── Source affinity dims within [112:128) ───────────────────────────
SOURCE_DIM_MAP: dict[str, tuple[int, float]] = {
    "library":  (0, 1.0),
    "similar":  (1, 0.85),
    "artist":   (2, 0.75),
    "wildcard":  (3, 0.5),
    "chart":    (4, 0.6),
    "related":  (5, 0.7),
}


# ════════════════════════════════════════════════════════════════════
#  Core Vector Math (pure Python, no numpy needed for 128 dims)
# ════════════════════════════════════════════════════════════════════

def zero_vector() -> list[float]:
    return [0.0] * VECTOR_DIM


def magnitude(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v))


def normalize(v: list[float]) -> list[float]:
    mag = magnitude(v)
    if mag < 1e-9:
        return v[:]
    return [x / mag for x in v]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two 128-dim vectors. Returns [-1, 1]."""
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = magnitude(a)
    mag_b = magnitude(b)
    if mag_a < 1e-9 or mag_b < 1e-9:
        return 0.0
    return dot / (mag_a * mag_b)


def vector_add(a: list[float], b: list[float], scale_b: float = 1.0) -> list[float]:
    """a + b * scale_b"""
    return [x + y * scale_b for x, y in zip(a, b)]


# ════════════════════════════════════════════════════════════════════
#  Artist Fingerprinting
# ════════════════════════════════════════════════════════════════════

def _artist_hash_dims(artist_name: str, n_dims: int = 4) -> list[int]:
    """
    Hash an artist name into N dimension indices within the artist space [64:80).
    Acts like a bloom-filter fingerprint: similar hashes = partial overlap.
    """
    h = hashlib.sha256(artist_name.lower().strip().encode()).hexdigest()
    dims = []
    artist_range = ARTIST_END - ARTIST_START  # 16
    for i in range(n_dims):
        chunk = h[i * 8 : (i + 1) * 8]
        dim = int(chunk, 16) % artist_range
        dims.append(ARTIST_START + dim)
    return dims


# ════════════════════════════════════════════════════════════════════
#  Genre Encoding Helpers
# ════════════════════════════════════════════════════════════════════

def _encode_genre(v: list[float], genre: str, weight: float = 1.0) -> None:
    """Set genre dimensions in-place for a single genre string."""
    g = genre.lower().strip()
    # Direct match
    if g in GENRE_MAP:
        idx = GENRE_START + GENRE_MAP[g]
        v[idx] = max(v[idx], weight)
        return
    # Substring match (e.g., "canadian pop" matches "pop")
    for key, idx in GENRE_MAP.items():
        if key in g or g in key:
            dim = GENRE_START + idx
            v[dim] = max(v[dim], weight * 0.7)
            return
    # Hash fallback for unknown genres — still gets a stable dimension
    idx = int(hashlib.md5(g.encode()).hexdigest()[:8], 16) % (GENRE_END - GENRE_START)
    v[GENRE_START + idx] = max(v[GENRE_START + idx], weight * 0.4)


# ════════════════════════════════════════════════════════════════════
#  Song Encoding
# ════════════════════════════════════════════════════════════════════

@dataclass
class SongCandidate:
    """A candidate song with its vector and metadata."""
    video_id: str
    title: str
    artist: str
    source: str  # 'library', 'similar', 'artist', 'wildcard'
    vector: list[float]
    duration_seconds: int | None = None
    year: int | None = None
    genres: list[str] | None = None
    popularity: float = 0.5


def encode_song(
    genres: list[str] | None = None,
    artist: str | None = None,
    year: int | None = None,
    popularity: float = 0.5,
    source: str = "unknown",
) -> list[float]:
    """
    Encode a song's features into a 128-dimensional vector.

    Handles missing data gracefully — dimensions without data stay at 0.
    Songs with richer metadata get more accurate vectors.
    """
    v = zero_vector()

    # ── Genre space [0:64) ──
    if genres:
        for genre in genres:
            _encode_genre(v, genre, weight=1.0)

    # ── Artist fingerprint [64:80) ──
    if artist:
        for dim in _artist_hash_dims(artist):
            v[dim] = 1.0

    # ── Temporal features [80:96) ──
    if year is not None:
        try:
            year = int(year)
        except (ValueError, TypeError):
            year = None
    if year and 1950 <= year <= 2030:
        # Coarse: decade bucket
        decade_idx = min((year - 1950) // 10, 7)
        v[TEMPORAL_START + decade_idx] = 1.0
        # Fine: position within decade (0.0 → 1.0)
        year_frac = (year % 10) / 10.0
        v[TEMPORAL_START + 8 + decade_idx] = year_frac

    # ── Popularity / energy [96:112) ──
    try:
        popularity = float(popularity)
    except (ValueError, TypeError):
        popularity = 0.5
    pop = max(0.0, min(1.0, popularity))
    v[ENERGY_START] = pop
    # Popularity bucket one-hot
    if pop > 0.8:
        v[ENERGY_START + 1] = 1.0
    elif pop > 0.6:
        v[ENERGY_START + 2] = 1.0
    elif pop > 0.3:
        v[ENERGY_START + 3] = 1.0
    else:
        v[ENERGY_START + 4] = 1.0

    # ── Source / novelty [112:128) ──
    if source in SOURCE_DIM_MAP:
        offset, val = SOURCE_DIM_MAP[source]
        v[NOVELTY_START + offset] = val

    return v


# ════════════════════════════════════════════════════════════════════
#  User Profile Vector
# ════════════════════════════════════════════════════════════════════

def build_user_profile(
    genre_prefs: dict[str, float],
    artist_prefs: dict[str, float],
    decade_prefs: dict[str, float],
    liked_song_vectors: list[list[float]] | None = None,
) -> list[float]:
    """
    Build a 128-dimensional taste profile for a user.

    Combines:
      - Genre affinity scores from user_preferences
      - Artist fingerprints weighted by affinity
      - Decade preferences
      - Centroid of liked song vectors (optional reinforcement)
    """
    v = zero_vector()

    # ── Genre preferences → dims [0:64) ──
    for genre, score in genre_prefs.items():
        if score > 0:
            _encode_genre(v, genre, weight=score)

    # ── Artist preferences → dims [64:80) ──
    # Blend all liked artists' fingerprints, weighted by affinity
    for artist, score in artist_prefs.items():
        if score > 0:
            for dim in _artist_hash_dims(artist):
                v[dim] = max(v[dim], score)

    # ── Decade preferences → dims [80:96) ──
    for decade, score in decade_prefs.items():
        d = decade.lower().strip()
        if d in DECADE_MAP and score > 0:
            v[TEMPORAL_START + DECADE_MAP[d]] = score

    # ── Liked song centroid reinforcement ──
    # Blend in the average of all liked song vectors at 30% weight
    # This captures patterns the explicit preferences might miss
    if liked_song_vectors:
        n = len(liked_song_vectors)
        for song_vec in liked_song_vectors:
            for i in range(VECTOR_DIM):
                v[i] += song_vec[i] / n * 0.3

    # ── Source affinity: user prefers library and similar ──
    # Slight bias toward familiar sources
    v[NOVELTY_START + 0] = 0.4  # library affinity
    v[NOVELTY_START + 1] = 0.3  # similar affinity

    return v


# ════════════════════════════════════════════════════════════════════
#  Scoring & Selection
# ════════════════════════════════════════════════════════════════════

def score_candidates(
    user_vector: list[float],
    candidates: list[SongCandidate],
    temperature: float = 0.15,
) -> list[tuple[SongCandidate, float]]:
    """
    Score all candidates against the user profile vector.

    Returns list of (candidate, score) sorted by score descending.
    Temperature adds controlled noise for exploration (0 = greedy, 1 = random).
    """
    user_norm = normalize(user_vector)
    scored = []

    for candidate in candidates:
        sim = cosine_similarity(user_norm, normalize(candidate.vector))
        # Controlled exploration noise
        noise = random.random() * temperature
        final_score = sim + noise
        scored.append((candidate, final_score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def softmax_select(
    scored: list[tuple[SongCandidate, float]],
    top_k: int = 8,
    temperature: float = 0.5,
) -> SongCandidate | None:
    """
    Select from top-K candidates using softmax-weighted random selection.

    Not pure greedy — gives lower-ranked candidates a fair chance.
    Temperature controls spread: low = almost greedy, high = more random.
    """
    top = scored[:top_k]
    if not top:
        return None
    if len(top) == 1:
        return top[0][0]

    # Softmax with temperature scaling
    scores = [s for _, s in top]
    max_score = max(scores)
    temp = max(temperature, 0.01)
    exp_scores = [math.exp((s - max_score) / temp) for s in scores]
    total = sum(exp_scores)
    probs = [e / total for e in exp_scores]

    # Weighted random pick
    r = random.random()
    cumulative = 0.0
    for (candidate, _), prob in zip(top, probs):
        cumulative += prob
        if r <= cumulative:
            return candidate

    return top[-1][0]


def debug_vector(v: list[float], label: str = "") -> str:
    """Format a vector for debug logging. Shows active dimensions."""
    parts = []
    if label:
        parts.append(f"[{label}]")

    # Genre dims
    active_genres = []
    reverse_genre = {}
    for name, idx in GENRE_MAP.items():
        if idx not in reverse_genre or len(name) < len(reverse_genre[idx]):
            reverse_genre[idx] = name
    for i in range(GENRE_START, GENRE_END):
        if v[i] > 0.01:
            name = reverse_genre.get(i, f"g{i}")
            active_genres.append(f"{name}={v[i]:.2f}")
    if active_genres:
        parts.append(f"genres=[{', '.join(active_genres[:8])}]")

    # Artist dims
    active_artist = sum(1 for i in range(ARTIST_START, ARTIST_END) if v[i] > 0.01)
    if active_artist:
        parts.append(f"artist_dims={active_artist}")

    # Temporal
    active_temporal = []
    reverse_decade = {}
    for name, idx in DECADE_MAP.items():
        if len(name) == 3:  # prefer short names like "80s"
            reverse_decade[idx] = name
    for i in range(TEMPORAL_START, TEMPORAL_START + 8):
        if v[i] > 0.01:
            name = reverse_decade.get(i - TEMPORAL_START, f"t{i}")
            active_temporal.append(f"{name}={v[i]:.2f}")
    if active_temporal:
        parts.append(f"era=[{', '.join(active_temporal)}]")

    # Popularity
    pop = v[ENERGY_START]
    if pop > 0.01:
        parts.append(f"pop={pop:.2f}")

    mag = magnitude(v)
    parts.append(f"||v||={mag:.3f}")

    return " ".join(parts)
