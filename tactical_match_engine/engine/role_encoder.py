"""
Role encoding utilities for tactical analysis.

Loads Position Line role-profile JSONs from Resources/ and scores a player
against the 10-metric model defined there.

Computation formulas recognised (no eval, explicit pattern matching):
  "direct"                           – use the stat value as-is
  "X / (minutesPlayed / 90)"         – per-90 single key
  "(X + Y) / (minutesPlayed / 90)"   – per-90 sum of two keys
  "X / Y * 100"                      – ratio × 100   (produces 0-100)

Scoring flow per metric
  1. Compute raw value from the formula.
  2. If a population list is provided → percentile_score (0-100).
  3. If the formula already produces a 0-100 percentage → use directly.
  4. Otherwise → range_score against a per-metric reference maximum.

Category score = mean of metric scores within that category.
Role fitness   = Σ (category_score * weight).            (0-100)
"""
import json
import os
import re
from typing import Dict, List, Optional, Any

from tactical_match_engine.engine.normalization import (
    percentile_score, range_score
)

# ── Paths ────────────────────────────────────────────────────────────────────
_ENGINE_DIR = os.path.dirname(__file__)
_base = os.environ.get(
    'TACTICAL_BASE_DIR',
    os.path.normpath(os.path.join(_ENGINE_DIR, "..", ".."))
)
_RESOURCES_DIR = os.path.join(_base, "Resources", "Position Line")

# ── SofaScore position code → Role-profile relative path ─────────────────────
POSITION_CODE_TO_ROLE_FILE: Dict[str, str] = {
    # Back-Line
    "DC":  "1. Back-Line/A_Central_Defend.json",
    "CB":  "1. Back-Line/A_Central_Defend.json",   # FBRef alias
    "DL":  "1. Back-Line/D_Full_Back.json",
    "DR":  "1. Back-Line/D_Full_Back.json",
    "LB":  "1. Back-Line/D_Full_Back.json",        # FBRef alias
    "RB":  "1. Back-Line/D_Full_Back.json",        # FBRef alias
    "WBL": "1. Back-Line/C_Wing_Back.json",
    "WBR": "1. Back-Line/C_Wing_Back.json",
    # Mid-Line
    "DM":  "2. Mid-Line/G_Defensive_Midfielder.json",
    "CDM": "2. Mid-Line/G_Defensive_Midfielder.json",  # FBRef alias
    "MC":  "2. Mid-Line/I_Central_Midfielder.json",
    "CM":  "2. Mid-Line/I_Central_Midfielder.json",    # FBRef alias
    "ML":  "2. Mid-Line/I_Central_Midfielder.json",
    "MR":  "2. Mid-Line/I_Central_Midfielder.json",
    "AM":  "2. Mid-Line/L_Attacking_Midfielder.json",
    "CAM": "2. Mid-Line/L_Attacking_Midfielder.json",  # FBRef alias
    "AMC": "2. Mid-Line/L_Attacking_Midfielder.json",
    # Front-Line
    "LW":  "3. Front-Line/M_Winger.json",
    "RW":  "3. Front-Line/M_Winger.json",
    "ST":  "3. Front-Line/O_Advanced_Forward.json",
    "FW":  "3. Front-Line/O_Advanced_Forward.json",
    "CF":  "3. Front-Line/O_Advanced_Forward.json",    # FBRef alias
    "SS":  "3. Front-Line/Q_False_Nine.json",
    # GK has no profile yet; will return None gracefully
}

# ── Reference "elite" per-90 values used when no population is available ─────
# A player hitting this benchmark scores ~100; values above are clamped at 100.
_PER90_REF_MAX: Dict[str, float] = {
    "ballRecovery":              12.0,
    "clearances":                10.0,
    "blockedShots":               1.5,
    "totalPasses":               80.0,
    "accurateFinalThirdPasses":  20.0,
    "keyPasses":                  4.0,
    "goals":                      1.2,
    "assists":                    0.8,
    "shotsOnTarget":              3.5,
    "totalShots":                 5.0,
    "successfulDribbles":         5.0,
    "passToAssist":               0.7,
    "wasFouled":                  6.0,
    "fouls":                      4.0,        # lower is better
    "dribbledPast":               3.5,        # lower is better
    "totalCross":                 5.5,
    "totalLongBalls":             6.0,
    # composite (key1+key2) keys for per-90 of two-key sums
    "totalDuelsWon+duelLost":    22.0,
    "aerialDuelsWon+aerialLost": 10.0,
}

# ── Profile cache ─────────────────────────────────────────────────────────────
_profile_cache: Dict[str, Optional[Dict]] = {}


def load_role_profile(position_code: str) -> Optional[Dict]:
    """
    Load (and cache) the Position Line JSON for *position_code*.
    Returns None for unknown positions (e.g. GK which has no profile).
    """
    if position_code in _profile_cache:
        return _profile_cache[position_code]

    rel_path = POSITION_CODE_TO_ROLE_FILE.get(position_code)
    if rel_path is None:
        _profile_cache[position_code] = None
        return None

    full_path = os.path.join(_RESOURCES_DIR, rel_path)
    try:
        with open(full_path, "r", encoding="utf-8") as fh:
            profile = json.load(fh)
        _profile_cache[position_code] = profile
        return profile
    except FileNotFoundError:
        _profile_cache[position_code] = None
        return None


def load_role_profile_by_path(relative_path: str) -> Optional[Dict]:
    """Load a role profile by its path relative to Resources/Position Line/."""
    full_path = os.path.join(_RESOURCES_DIR, relative_path)
    try:
        with open(full_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None


# ── Formula parser ────────────────────────────────────────────────────────────

def compute_metric_value(metric: Dict, raw_stats: Dict[str, Any]) -> float:
    """
    Safely evaluate a metric computation formula against *raw_stats*.

    Returns the computed float value, or 0.0 when data is missing / minutes = 0.
    """
    computation: str = metric.get("computation", "direct")
    sofascore_key = metric.get("sofascore_key", "")
    return _parse_and_compute(computation, sofascore_key, raw_stats)


def _parse_and_compute(computation: str, sofascore_key,
                       raw_stats: Dict[str, Any]) -> float:
    minutes = float(raw_stats.get("minutesPlayed", 0) or 0)

    # ── 1. direct ─────────────────────────────────────────────────────────────
    if computation == "direct":
        key = sofascore_key if isinstance(sofascore_key, str) else sofascore_key[0]
        return float(raw_stats.get(key, 0.0) or 0.0)

    # ── 2. (X + Y) / (minutesPlayed / 90) ────────────────────────────────────
    m = re.match(
        r'^\((\w+)\s*\+\s*(\w+)\)\s*/\s*\(minutesPlayed\s*/\s*90\)$',
        computation.strip()
    )
    if m:
        if minutes <= 0:
            return 0.0
        v1 = float(raw_stats.get(m.group(1), 0.0) or 0.0)
        v2 = float(raw_stats.get(m.group(2), 0.0) or 0.0)
        return (v1 + v2) / (minutes / 90.0)

    # ── 3. X / (minutesPlayed / 90) ───────────────────────────────────────────
    m = re.match(
        r'^(\w+)\s*/\s*\(minutesPlayed\s*/\s*90\)$',
        computation.strip()
    )
    if m:
        if minutes <= 0:
            return 0.0
        return float(raw_stats.get(m.group(1), 0.0) or 0.0) / (minutes / 90.0)

    # ── 4. X / Y * 100 (e.g. shotsOnTarget / totalShots * 100) ───────────────
    m = re.match(r'^(\w+)\s*/\s*(\w+)\s*\*\s*100$', computation.strip())
    if m:
        num = float(raw_stats.get(m.group(1), 0.0) or 0.0)
        den = float(raw_stats.get(m.group(2), 0.0) or 0.0)
        if den == 0.0:
            return 0.0
        return num / den * 100.0

    return 0.0


def _is_percentage_output(computation: str) -> bool:
    """True when the formula already produces a 0-100 value (no ref-max needed)."""
    if computation == "direct":
        return True
    # X / Y * 100 produces 0-100
    if re.match(r'^(\w+)\s*/\s*(\w+)\s*\*\s*100$', computation.strip()):
        return True
    return False


def _get_ref_max(computation: str) -> float:
    """Return the per-90 reference maximum for normalisation."""
    # Composite: (X + Y) / (minutesPlayed / 90)
    m = re.match(r'^\((\w+)\s*\+\s*(\w+)\)', computation.strip())
    if m:
        composite_key = f"{m.group(1)}+{m.group(2)}"
        return _PER90_REF_MAX.get(composite_key, 10.0)
    # Single key per-90
    m = re.match(r'^(\w+)\s*/\s*\(minutesPlayed', computation.strip())
    if m:
        return _PER90_REF_MAX.get(m.group(1), 10.0)
    return 10.0


def _score_metric(raw_value: float, computation: str,
                  higher_is_better: bool,
                  population: Optional[List[float]] = None) -> float:
    """Convert a raw metric value to a 0-100 score."""
    if population:
        return percentile_score(raw_value, population, higher_is_better)
    if _is_percentage_output(computation):
        clamped = max(0.0, min(100.0, raw_value))
        return clamped if higher_is_better else (100.0 - clamped)
    ref_max = _get_ref_max(computation)
    return range_score(raw_value, ref_max, higher_is_better)


# ── Public API ────────────────────────────────────────────────────────────────

def get_role_fitness_vector(
    raw_stats: Dict[str, Any],
    position_code: str,
    population_stats_by_metric: Optional[Dict[str, List[float]]] = None,
    role_path: Optional[str] = None,
    flat_weights: bool = False,
) -> Optional[Dict]:
    """
    Score a player (given their raw SofaScore *raw_stats*) against the role
    profile for *position_code*.

    Parameters
    ----------
    raw_stats :
        The ``statistics.statistics`` dict from a SofaScore player JSON.
    position_code :
        SofaScore position code, e.g. ``"DC"``, ``"ST"``.
    population_stats_by_metric :
        Optional mapping ``{metric_name: [values_for_all_players]}``.
        When provided, real percentile scoring is used instead of range scoring.
    role_path :
        Override the automatic file lookup with an explicit path relative to
        ``Resources/Position Line/``.  Useful when running against non-primary
        role profiles (e.g. scoring a DC against B_Ball_Playing_Defend.json).
    flat_weights :
        When *True*, replace the role-profile weight distribution with equal
        weights across all categories.  The profile weights are still returned
        in ``weights`` for reference but are not used for ``overall_score``.
        Use this in squad-benchmarking contexts (e.g. Club Fit) so scores
        reflect raw attribute level rather than role archetype fit.

    Returns
    -------
    dict with keys:
        ``category_scores``  – ``{category_name: 0-100 float}``
        ``weights``          – ``{category_name: weight float}``
        ``overall_score``    – weighted sum, 0-100 float
        ``role_profile``     – the full loaded JSON dict
        ``metric_details``   – list of per-metric detail dicts
    Returns *None* if no role profile exists for the position.
    """
    if role_path:
        profile = load_role_profile_by_path(role_path)
    else:
        profile = load_role_profile(position_code)

    if profile is None:
        return None

    model = profile["core_10_model"]
    weights: Dict[str, float] = model["weight_distribution"]
    metrics: List[Dict] = model["metrics"]

    # ── compute per-metric scores ─────────────────────────────────────────────
    category_buckets: Dict[str, List[float]] = {}
    metric_details: List[Dict] = []

    for m in metrics:
        comp = m["computation"]
        hib = bool(m.get("higher_is_better", True))
        category = m["category"]

        raw_val = compute_metric_value(m, raw_stats)

        pop = None
        if population_stats_by_metric:
            pop = population_stats_by_metric.get(m["name"])

        score = _score_metric(raw_val, comp, hib, pop)

        category_buckets.setdefault(category, []).append(score)
        metric_details.append({
            "name":          m["name"],
            "category":      category,
            "raw_value":     round(raw_val, 4),
            "score":         round(score, 2),
            "higher_is_better": hib,
        })

    # ── category averages ─────────────────────────────────────────────────────
    category_scores: Dict[str, float] = {
        cat: round(sum(scores) / len(scores), 2)
        for cat, scores in category_buckets.items()
    }
    for cat in weights:
        category_scores.setdefault(cat, 0.0)

    # ── weighted overall score (0-100) ────────────────────────────────────────
    if flat_weights:
        n = len(category_scores)
        overall_score = (sum(category_scores.values()) / n) if n else 0.0
    else:
        overall_score = sum(
            (category_scores.get(cat, 0.0) / 100.0) * w
            for cat, w in weights.items()
        ) * 100.0

    return {
        "category_scores": category_scores,
        "weights":         weights,
        "overall_score":   round(overall_score, 2),
        "role_profile":    profile,
        "metric_details":  metric_details,
    }


def get_ordered_vectors(
    raw_stats: Dict[str, Any],
    position_code: str,
    population_stats_by_metric: Optional[Dict[str, List[float]]] = None,
    role_path: Optional[str] = None,
) -> Optional[tuple]:
    """
    Convenience wrapper that returns (player_vector, role_weight_vector) as
    two aligned lists, ready for cosine-similarity comparison.

    player_vector      – category scores [0,100] ordered alphabetically by name
    role_weight_vector – category weights [0,100] in the same order
    """
    result = get_role_fitness_vector(
        raw_stats, position_code,
        population_stats_by_metric, role_path
    )
    if result is None:
        return None
    ordered_cats = sorted(result["weights"].keys())
    player_vec = [result["category_scores"].get(c, 0.0) for c in ordered_cats]
    weight_vec = [result["weights"].get(c, 0.0) * 100.0 for c in ordered_cats]
    return player_vec, weight_vec

