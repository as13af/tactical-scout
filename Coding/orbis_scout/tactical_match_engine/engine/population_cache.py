"""
Population cache for context-aware percentile scoring.

Scans the entire OUTPUT_DIR tree once (lazily, on first call) to build
per-metric raw-value distributions grouped by
    (country_folder, competition_folder, position_category)

where *position_category* is SofaScore's own single-letter code read directly
from ``profile.player.position`` in each JSON:

    "D" — Defender
    "M" — Midfielder
    "F" — Forward
    "G" — Goalkeeper  (excluded — no role profiles exist)

The resulting dict is passed as *population_stats_by_metric* to
``get_role_fitness_vector()`` so metric scores become league-relative
percentiles instead of comparisons against a static reference maximum.

Keys use the raw folder names from OUTPUT_DIR (same convention as the
Flask route path-parts) so look-ups are a simple dict.get() with no
normalisation needed.
"""
import json
import logging
import os
import threading
from typing import Dict, List, Tuple

from tactical_match_engine.engine.role_encoder import compute_metric_value

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
_ENGINE_DIR = os.path.dirname(__file__)
_BASE = os.environ.get(
    'TACTICAL_BASE_DIR',
    os.path.normpath(os.path.join(_ENGINE_DIR, '..', '..'))
)
_ROLES_BASE = os.path.join(_BASE, 'Resources', 'Position Line')

# SofaScore position category letters — read directly from profile.player.position.
# "G" (Goalkeeper) is deliberately excluded — no role profiles exist for GKs.
_VALID_CATEGORIES = {"D", "M", "F"}

# Representative role-profile file per SofaScore position category
# (path relative to _ROLES_BASE)
_CATEGORY_REPR_ROLE: Dict[str, str] = {
    "D": "1. Back-Line/A_Central_Defend.json",
    "M": "2. Mid-Line/I_Central_Midfielder.json",
    "F": "3. Front-Line/O_Advanced_Forward.json",
}

# Minimum minutes played to include a player in the population
_MIN_MINUTES = 450

# ── Module-level cache ────────────────────────────────────────────────────────
_CACHE: Dict[Tuple[str, str, str], Dict[str, List[float]]] = {}
_BUILT: bool = False
_LOCK = threading.Lock()


def _load_category_metrics() -> Dict[str, List[Dict]]:
    """Load the representative role-profile metrics list for each position category."""
    category_metrics: Dict[str, List[Dict]] = {}
    for cat, rel_path in _CATEGORY_REPR_ROLE.items():
        full_path = os.path.join(_ROLES_BASE, rel_path)
        try:
            with open(full_path, encoding='utf-8') as fh:
                profile = json.load(fh)
            category_metrics[cat] = profile['core_10_model']['metrics']
        except Exception as exc:
            logger.warning(
                'population_cache: could not load role profile %s: %s',
                full_path, exc,
            )
    return category_metrics


def _scan_and_build(output_dir: str) -> Dict[Tuple[str, str, str], Dict[str, List[float]]]:
    """
    Walk OUTPUT_DIR/{country}/{competition}/{club}/Players/*.json and build
    per-group metric value distributions.

    Returns
    -------
    dict keyed by (country_folder, competition_folder, position_category)
    mapping to {metric_name: [float, ...]}
    """
    category_metrics = _load_category_metrics()
    if not category_metrics:
        logger.warning('population_cache: no role profiles loaded — cache will be empty')
        return {}

    groups: Dict[Tuple[str, str, str], Dict[str, List[float]]] = {}

    if not os.path.isdir(output_dir):
        logger.warning('population_cache: output_dir not found: %s', output_dir)
        return {}

    try:
        country_entries = [e for e in os.scandir(output_dir) if e.is_dir(follow_symlinks=False)]
    except OSError as exc:
        logger.warning('population_cache: cannot scan output_dir %s: %s', output_dir, exc)
        return {}

    total_players = 0
    skipped = 0

    for country_entry in country_entries:
        try:
            comp_entries = [e for e in os.scandir(country_entry.path) if e.is_dir(follow_symlinks=False)]
        except OSError:
            continue

        for comp_entry in comp_entries:
            try:
                club_entries = [e for e in os.scandir(comp_entry.path) if e.is_dir(follow_symlinks=False)]
            except OSError:
                continue

            for club_entry in club_entries:
                players_dir = os.path.join(club_entry.path, 'Players')
                if not os.path.isdir(players_dir):
                    continue

                try:
                    file_entries = [
                        e for e in os.scandir(players_dir)
                        if e.is_file(follow_symlinks=False)
                        and e.name.endswith('.json')
                        and not e.name.startswith('Club_')
                    ]
                except OSError:
                    continue

                for file_entry in file_entries:
                    try:
                        with open(file_entry.path, encoding='utf-8') as fh:
                            data = json.load(fh)
                    except Exception as exc:
                        logger.debug(
                            'population_cache: skip %s (%s)', file_entry.path, exc
                        )
                        skipped += 1
                        continue

                    raw_stats: Dict = (
                        data.get('statistics', {}).get('statistics', {}) or {}
                    )
                    minutes = float(raw_stats.get('minutesPlayed', 0) or 0)
                    if minutes < _MIN_MINUTES:
                        skipped += 1
                        continue

                    # Read SofaScore's own position category letter directly.
                    # profile.player.position is already "G", "D", "M", or "F".
                    profile_data = data.get('profile', {}).get('player', {}) or {}
                    category = profile_data.get('position', '').strip().upper()

                    if category not in _VALID_CATEGORIES or category not in category_metrics:
                        skipped += 1
                        continue

                    key = (country_entry.name, comp_entry.name, category)
                    if key not in groups:
                        groups[key] = {
                            m['name']: [] for m in category_metrics[category]
                        }

                    bucket = groups[key]
                    for metric in category_metrics[category]:
                        name = metric['name']
                        try:
                            val = compute_metric_value(metric, raw_stats)
                            bucket[name].append(val)
                        except Exception:
                            pass

                    total_players += 1

    logger.info(
        'population_cache: built %d groups from %d players (%d skipped)',
        len(groups), total_players, skipped,
    )
    return groups


def get_population_cache(
    output_dir: str,
) -> Dict[Tuple[str, str, str], Dict[str, List[float]]]:
    """
    Return the population cache, building it lazily on first call.

    The cache is keyed by *(country_folder, competition_folder, position_category)*
    where folder names are the raw ``os.scandir`` names from OUTPUT_DIR and
    *position_category* is SofaScore's single-letter code: ``"D"``, ``"M"``,
    or ``"F"``.

    Parameters
    ----------
    output_dir :
        Absolute path to the root of the scraped player data
        (e.g. ``Coding/output/``).

    Returns
    -------
    Nested dict:
        ``{(country, competition, category): {metric_name: [float, ...]}}``
    """
    global _CACHE, _BUILT
    if _BUILT:
        return _CACHE
    with _LOCK:
        if _BUILT:          # double-checked locking
            return _CACHE
        logger.info('population_cache: building from %s …', output_dir)
        _CACHE = _scan_and_build(output_dir)
        _BUILT = True
    return _CACHE


# ── MongoDB-backed population cache ──────────────────────────────────────────

_DB_CACHE: Dict[Tuple[str, str, str], Dict[str, List[float]]] = {}
_DB_BUILT: bool = False
_DB_LOCK = threading.Lock()


def _scan_and_build_from_db(db) -> Dict[Tuple[str, str, str], Dict[str, List[float]]]:
    """Build population distributions by querying MongoDB instead of scanning disk."""
    category_metrics = _load_category_metrics()
    if not category_metrics:
        logger.warning("population_cache (db): no role profiles loaded — cache will be empty")
        return {}

    groups: Dict[Tuple[str, str, str], Dict[str, List[float]]] = {}
    total_players = 0
    skipped = 0

    cursor = db.players.find(
        {},
        {
            "_country": 1, "_competition": 1,
            "statistics.statistics": 1,
            "profile.player.position": 1,
        },
    )

    for doc in cursor:
        raw_stats: Dict = doc.get("statistics", {}).get("statistics", {}) or {}
        minutes = float(raw_stats.get("minutesPlayed", 0) or 0)
        if minutes < _MIN_MINUTES:
            skipped += 1
            continue

        category = (
            (doc.get("profile", {}).get("player", {}).get("position") or "")
            .strip()
            .upper()
        )
        if category not in _VALID_CATEGORIES or category not in category_metrics:
            skipped += 1
            continue

        country     = doc.get("_country", "")
        competition = doc.get("_competition", "")
        if not country or not competition:
            skipped += 1
            continue

        key = (country, competition, category)
        if key not in groups:
            groups[key] = {m["name"]: [] for m in category_metrics[category]}

        bucket = groups[key]
        for metric in category_metrics[category]:
            name = metric["name"]
            try:
                val = compute_metric_value(metric, raw_stats)
                bucket[name].append(val)
            except Exception:
                pass

        total_players += 1

    logger.info(
        "population_cache (db): built %d groups from %d players (%d skipped)",
        len(groups), total_players, skipped,
    )
    return groups


def get_population_cache_from_db(db) -> Dict[Tuple[str, str, str], Dict[str, List[float]]]:
    """
    Return the MongoDB-backed population cache, building it lazily on first call.

    Same key / value structure as get_population_cache() but reads from the
    ``players`` MongoDB collection instead of the output/ file tree.
    """
    global _DB_CACHE, _DB_BUILT
    if _DB_BUILT:
        return _DB_CACHE
    with _DB_LOCK:
        if _DB_BUILT:
            return _DB_CACHE
        logger.info("population_cache: building from MongoDB…")
        _DB_CACHE = _scan_and_build_from_db(db)
        _DB_BUILT = True
    return _DB_CACHE
