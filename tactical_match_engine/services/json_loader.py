"""
Service for loading player and club data from JSON files.

Two data sources are supported:

1. **Engine-format JSONs** (tactical_match_engine/data/players/, .../clubs/)
   – The original hand-crafted example format used by example_runner.py.

2. **SofaScore scraped JSONs** (Coding/output/.../Players/*.json)
   – Real player data produced by the Coding/ scraper.  These are converted
     into a Player-model-compatible dict automatically.

League intensity is resolved from the Opta Power Rankings CVS JSON in Resources/.
"""
import json
import os
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

# ── Paths ─────────────────────────────────────────────────────────────────────
_SERVICES_DIR = os.path.dirname(__file__)
_PACKAGE_ROOT = os.path.normpath(os.path.join(_SERVICES_DIR, ".."))
_WORKSPACE_ROOT = os.environ.get(
    'TACTICAL_BASE_DIR',
    os.path.normpath(os.path.join(_PACKAGE_ROOT, ".."))
)

_PLAYERS_DIR = os.path.join(_PACKAGE_ROOT, "data", "players")
_CLUBS_DIR   = os.path.join(_PACKAGE_ROOT, "data", "clubs")
_OPTA_PATH   = os.path.join(
    _WORKSPACE_ROOT, "Resources", "Top_Rankings",
    "Opta_League_CVS_2026.json"
)

# ── Opta CVS cache ────────────────────────────────────────────────────────────
_iffhs_cache: Optional[Dict] = None
_MAX_CVS = 100.0   # Opta scale: 0-100 (Premier League ≈ 90.9)


def load_iffhs_data() -> Dict:
    """Load (and cache) the Opta league CVS JSON."""
    global _iffhs_cache
    if _iffhs_cache is not None:
        return _iffhs_cache
    with open(_OPTA_PATH, "r", encoding="utf-8") as fh:
        _iffhs_cache = json.load(fh)
    return _iffhs_cache


def get_league_info(country: str,
                    league_name: Optional[str] = None,
                    tier: int = 1) -> Dict[str, Any]:
    """
    Return a dict with full league metadata for *country* / *league_name*.

    Keys returned:
        intensity   – 0-1 CVS fraction (same as get_league_intensity)
        cvs         – raw 0-100 Opta CVS score
        global_rank – Opta global rank of the matched division (1 = strongest)
        name        – matched division name string
    Falls back to neutral values when the country/league is not found.
    """
    try:
        data = load_iffhs_data()
    except FileNotFoundError:
        return {"intensity": 0.5, "cvs": 50.0, "global_rank": None, "name": ""}

    country_lc = country.lower().strip()
    league_lc  = (league_name or "").lower().replace("_", " ").strip()

    for entry in data.get("rankings", []):
        if entry["country"].lower() != country_lc:
            continue
        divisions: List[Dict] = entry.get("divisions", [])
        if not divisions:
            sc = float(entry.get("s_country", 50))
            return {"intensity": min(sc / 100.0, 1.0), "cvs": sc,
                    "global_rank": entry.get("rank"), "name": entry["country"]}

        def _make(div: Dict) -> Dict[str, Any]:
            cvs = float(div["cvs"])
            return {
                "intensity":   min(cvs / _MAX_CVS, 1.0),
                "cvs":         cvs,
                "global_rank": div.get("global_rank"),
                "name":        div.get("name", ""),
            }

        if league_lc:
            for div in divisions:
                if league_lc in div["name"].lower():
                    return _make(div)

        for div in divisions:
            if div.get("tier", 1) == tier:
                return _make(div)

        return _make(divisions[0])

    return {"intensity": 0.5, "cvs": 50.0, "global_rank": None, "name": ""}


def get_league_intensity(country: str,
                         league_name: Optional[str] = None,
                         tier: int = 1) -> float:
    """Return 0-1 league intensity (thin wrapper around get_league_info)."""
    return get_league_info(country, league_name, tier)["intensity"]


# ── Engine-format loader ──────────────────────────────────────────────────────

def get_available_players() -> List[str]:
    """Return stem names of JSONs in tactical_match_engine/data/players/."""
    if not os.path.isdir(_PLAYERS_DIR):
        return []
    return [
        os.path.splitext(f)[0]
        for f in os.listdir(_PLAYERS_DIR)
        if f.endswith(".json")
    ]


def get_available_clubs() -> List[str]:
    """Return stem names of JSONs in tactical_match_engine/data/clubs/."""
    if not os.path.isdir(_CLUBS_DIR):
        return []
    return [
        os.path.splitext(f)[0]
        for f in os.listdir(_CLUBS_DIR)
        if f.endswith(".json")
    ]


def load_engine_player(name: str) -> Dict:
    """Load a player JSON from tactical_match_engine/data/players/<name>.json."""
    path = os.path.join(_PLAYERS_DIR, f"{name}.json")
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_engine_club(name: str) -> Dict:
    """Load a club JSON from tactical_match_engine/data/clubs/<name>.json."""
    path = os.path.join(_CLUBS_DIR, f"{name}.json")
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ── SofaScore player loader ───────────────────────────────────────────────────

def load_sofascore_player(file_path: str) -> Dict[str, Any]:
    """
    Load a SofaScore scraped player JSON and return a dict compatible with
    the ``Player`` model.

    The raw ``statistics.statistics`` dict is stored under both ``stats``
    (used by the Player model) and ``raw_stats`` (used by the role encoder).
    Non-numeric / negative stat values are stripped to satisfy model validation.
    """
    with open(file_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    profile  = data.get("profile", {}).get("player", {})
    raw_stats: Dict = data.get("statistics", {}).get("statistics", {})

    # Filter to only numeric, non-negative values for the Player model
    stats = {
        k: v for k, v in raw_stats.items()
        if isinstance(v, (int, float)) and v >= 0
    }

    age = _compute_age(profile.get("dateOfBirth", ""))

    country     = data.get("competition_country", "")
    competition = data.get("competition_name", "")
    league_info = get_league_info(country, competition)
    league_intensity = league_info["intensity"]

    positions_detailed: List[str] = (
        profile.get("positionsDetailed")
        or data.get("positions", [])
        or ["MC"]
    )
    primary_position = positions_detailed[0] if positions_detailed else "MC"

    return {
        "id":                      str(data.get("player_id", "")),
        "name":                    (data.get("player_name")
                                    or profile.get("name", "Unknown")),
        "age":                     age,
        "position":                primary_position,
        "positions":               positions_detailed,
        "preferred_foot":          profile.get("preferredFoot", "Right"),
        "height_cm":               int(profile.get("height", 0) or 0),
        "weight_kg":               int(profile.get("weight", 0) or 0),
        "current_league_intensity":  league_intensity,
        "current_league_global_rank": league_info.get("global_rank"),
        "current_league_name":       league_info.get("name", competition),
        "tactical_familiarity":    {"formation_experience": {}},
        "stats":                   stats,
        "raw_stats":               raw_stats,
    }


def _compute_age(dob_str: str) -> int:
    """Derive current age from an ISO-8601 date-of-birth string."""
    if not dob_str:
        return 25
    try:
        # Handle both "Z" and "+00:00" suffixes
        dob = datetime.fromisoformat(dob_str.replace("Z", "+00:00"))
        today = datetime.now(tz=timezone.utc)
        age = (today.year - dob.year
               - ((today.month, today.day) < (dob.month, dob.day)))
        return max(15, min(45, age))
    except Exception:
        return 25

