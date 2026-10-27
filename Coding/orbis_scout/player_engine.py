"""player_engine.py — position-grouped percentile / z-score radar.

Builds a "z-score radar" for a single player: each metric is normalised against
the player's *same-position peers in the same league*, where:

  * peer position group is the broad GK / DEF / MID / FWD bucket
    (from data_loader.profile_category),
  * the peer pool is gated to players whose minutes are at or above the
    33rd percentile of minutes for that group (drops fringe / low-sample
    players, exactly like the scatter qualifying gate),
  * countable stats are converted to per-90, while percentage / rating stats
    are used as-is,
  * each axis is the z-score (standard deviations from the peer mean), so the
    peer average sits at z = 0 and "further out = stronger".

Public entry point:
    build_radar(player_path, output_dir) -> dict | None

`player_path` is "country/competition/club_slug/filename" (same shape the
percentile endpoint already uses).

This module is intentionally free of Flask / app imports so app.py can import
it without any circular dependency.
"""

from __future__ import annotations

import glob
import json
import os
import re
import statistics
from pathlib import Path


# ── Position group mapping ───────────────────────────────────────────────────
# data_loader.profile_category returns single letters: G / D / M / F
_CAT_TO_GROUP = {'G': 'GK', 'D': 'DEF', 'M': 'MID', 'F': 'FWD'}

# Stats that are already rates / ratios / ratings — never converted to per-90.
_RATE_STATS = frozenset([
    'accuratePassesPercentage', 'accurateLongBallsPercentage',
    'accurateCrossesPercentage', 'successfulDribblesPercentage',
    'totalDuelsWonPercentage', 'aerialDuelsWonPercentage',
    'goalConversionPercentage', 'penaltyConversion', 'rating',
    'tacklesWonPercentage', 'groundDuelsWonPercentage',
])

# ── Per-position radar templates ─────────────────────────────────────────────
# Priority-ordered supersets. The engine keeps only the metrics that actually
# have data (target value + at least 2 peers) and caps the radar at _MAX_AXES,
# walking this list top-to-bottom. So core SofaScore fields (always present)
# lead, and advanced ones (xG, xA, tackles, interceptions, touches — missing in
# lower leagues like the Indonesian top flight) fill in only where available.
# All metrics are "higher = better" so the z-score reads cleanly. Edit freely.
POSITION_TEMPLATES: dict[str, list[tuple[str, str]]] = {
    'FWD': [
        ('goals',                    'Goals'),
        ('shotsOnTarget',            'Shots OT'),
        ('totalShots',               'Shots'),
        ('goalConversionPercentage', 'Conv. %'),
        ('successfulDribbles',       'Dribbles'),
        ('keyPasses',                'Key Passes'),
        ('assists',                  'Assists'),
        ('expectedGoals',            'xG'),          # advanced
        ('expectedAssists',          'xA'),          # advanced
        ('accurateCrosses',          'Acc. Cross'),
        ('touches',                  'Touches'),     # advanced
    ],
    'MID': [
        ('accuratePasses',           'Acc. Passes'),
        ('accuratePassesPercentage', 'Pass %'),
        ('keyPasses',                'Key Passes'),
        ('successfulDribbles',       'Dribbles'),
        ('accurateLongBalls',        'Long Balls'),
        ('ballRecovery',             'Recoveries'),
        ('totalDuelsWon',            'Duels Won'),
        ('assists',                  'Assists'),
        ('expectedAssists',          'xA'),          # advanced
        ('tackles',                  'Tackles'),     # advanced
        ('interceptions',            'Interceptions'),  # advanced
        ('possessionWonAttThird',    'Win Att 3rd'), # advanced
    ],
    'DEF': [
        ('clearances',               'Clearances'),
        ('blockedShots',             'Blocks'),
        ('aerialDuelsWon',           'Aerials Won'),
        ('aerialDuelsWonPercentage', 'Aerial %'),
        ('totalDuelsWon',            'Duels Won'),
        ('totalDuelsWonPercentage',  'Duel %'),
        ('ballRecovery',             'Recoveries'),
        ('accuratePasses',           'Acc. Passes'),
        ('accuratePassesPercentage', 'Pass %'),
        ('interceptions',            'Interceptions'),  # advanced
        ('tackles',                  'Tackles'),     # advanced
        ('accurateLongBalls',        'Long Balls'),
    ],
    'GK': [
        ('saves',                       'Saves'),
        ('accuratePasses',              'Acc. Passes'),
        ('accuratePassesPercentage',    'Pass %'),
        ('accurateLongBalls',           'Long Balls'),
        ('accurateLongBallsPercentage', 'Long %'),
        ('totalLongBalls',              'Long Att.'),
        ('ballRecovery',                'Recoveries'),
        ('clearances',                  'Clearances'),
    ],
}

# Radar shape limits.
_MAX_AXES     = 9   # cap so rich leagues don't produce a cluttered radar
_MIN_AXES     = 3   # below this a radar is meaningless
# Minimum peer pool below which results are flagged as a small sample.
_SMALL_SAMPLE = 8


def _percentile(sorted_vals, q):
    """Linear-interpolated percentile. `sorted_vals` ascending, `q` in [0, 1]."""
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    if n == 1:
        return float(sorted_vals[0])
    idx = q * (n - 1)
    lo  = int(idx)
    hi  = min(lo + 1, n - 1)
    return float(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (idx - lo))


def _group_for(raw: dict) -> str:
    """Map a player's positions to a GK/DEF/MID/FWD bucket."""
    from data_loader import profile_category
    profile  = raw.get('profile', {}).get('player', {})
    pos_list = profile.get('positionsDetailed') or raw.get('positions', [])
    fallback = profile.get('position', pos_list[0] if pos_list else '')
    cat      = profile_category(pos_list, fallback)
    return _CAT_TO_GROUP.get(cat, 'MID')


def _stat_value(stats: dict, key: str, minutes: float):
    """Return the radar value for a stat: rate as-is, else per-90. None if absent."""
    v = stats.get(key)
    if v is None or not isinstance(v, (int, float)) or isinstance(v, bool):
        return None
    if key in _RATE_STATS:
        return float(v)
    if minutes and minutes > 0:
        return round((v / minutes) * 90.0, 4)
    return None


def _pct_rank(value: float, pool: list[float]) -> float:
    """Percentile rank of `value` within `pool` (0-100, higher = better)."""
    if not pool:
        return 0.0
    below = sum(1 for v in pool if v < value)
    equal = sum(1 for v in pool if v == value)
    return round((below + 0.5 * equal) / len(pool) * 100.0, 1)


# ── Document source: MongoDB when the app runs on it, else output/ JSON ──────
# A Mongo player doc is the on-disk JSON plus _country/_competition/_club/
# _file/season_id, so both paths hand callers the same shape. On Vercel there
# is no output/ tree, so reading files directly made every radar/percentile
# request 404.

# Fields the radar/percentile pools read — skips the heavy heatmap points.
_POOL_PROJECTION = {'positions': 1, 'profile.player': 1, 'statistics.statistics': 1,
                    '_club': 1, '_file': 1, 'season_id': 1}


def _mongo_db():
    """Handle for the active Mongo data layer, or None on the file-system loader.

    Imported lazily so this module stays free of app imports at load time."""
    try:
        import context as _ctx
        if getattr(_ctx, '_USING_MONGO', False):
            return _ctx._loader._db()
    except Exception:                                 # noqa: BLE001
        pass
    return None


def load_player_raw(output_dir: str, player_path: str):
    """Load a player from a 'Country/Competition/Club/file' path.

    Returns (parts, target_file, raw) or None if the path is bad / missing."""
    parts = player_path.split('/')
    if len(parts) != 4:
        return None
    country, competition, club_slug, filename = parts
    target_file = filename if filename.endswith('.json') else filename + '.json'
    db = _mongo_db()
    if db is not None:
        raw = db.players.find_one({'_country': country, '_competition': competition,
                                   '_club': club_slug, '_file': target_file}, {'_id': 0})
        return (parts, target_file, raw) if raw else None
    full_path = os.path.join(output_dir, country, competition, club_slug, 'Players', target_file)
    if not os.path.exists(full_path):
        return None
    with open(full_path, encoding='utf-8') as f:
        return parts, target_file, json.load(f)


def season_pool_docs(output_dir: str, country: str, competition: str,
                     target_file: str, target_raw: dict):
    """Yield every player doc of the target's league season (target included).

    Mongo keeps a league's seasons side by side, so the pool is filtered on the
    target's season_id; on disk the _Season_<id> filename suffix does the same.
    Disk docs get _club/_file set like Mongo's, so callers can skip the target."""
    db = _mongo_db()
    if db is not None:
        flt = {'_country': country, '_competition': competition}
        if target_raw.get('season_id'):
            flt['season_id'] = str(target_raw['season_id'])
        yield from db.players.find(flt, _POOL_PROJECTION)
        return
    from data_loader import same_season_glob
    comp_dir = os.path.join(output_dir, country, competition)
    for fpath in glob.glob(os.path.join(comp_dir, '*', 'Players', same_season_glob(target_file))):
        try:
            with open(fpath, encoding='utf-8') as f:
                doc = json.load(f)
        except Exception:                             # noqa: BLE001
            continue
        doc.setdefault('_club', os.path.basename(os.path.dirname(os.path.dirname(fpath))))
        doc.setdefault('_file', os.path.basename(fpath))
        yield doc


def build_radar(player_path: str, output_dir: str) -> dict | None:
    """Build the z-score radar payload for one player.

    Returns None if the path is malformed or the player file is missing.
    Returns a dict with ``error`` set when the player has no usable stats.
    """
    loaded = load_player_raw(output_dir, player_path)
    if loaded is None:
        return None
    (country, competition, _, _), target_file, target_raw = loaded

    target_stats = target_raw.get('statistics', {}).get('statistics', {})
    if not isinstance(target_stats, dict) or not target_stats:
        return {'error': 'No season statistics for this player.'}

    target_minutes = target_stats.get('minutesPlayed')
    if not isinstance(target_minutes, (int, float)) or target_minutes <= 0:
        return {'error': 'Player has no recorded minutes.'}

    group    = _group_for(target_raw)
    template = POSITION_TEMPLATES.get(group, POSITION_TEMPLATES['MID'])

    # ── Pass 1: every same-group player in the league season who appeared ─────
    peers = []   # list of (minutes, stats-dict)
    for pd in season_pool_docs(output_dir, country, competition, target_file, target_raw):
        try:
            if _group_for(pd) != group:
                continue
            ps = pd.get('statistics', {}).get('statistics', {})
            mins = ps.get('minutesPlayed')
            if not isinstance(mins, (int, float)) or mins <= 0:
                continue
            peers.append((float(mins), ps))
        except Exception:
            continue

    # ── Qualifying gate: 33rd percentile of minutes within the group ──────────
    all_minutes       = sorted(m for m, _ in peers)
    minutes_threshold = _percentile(all_minutes, 0.33)
    qualified = [ps for m, ps in peers if m >= minutes_threshold]

    # ── Per-metric distribution + target z-score ──────────────────────────────
    # Walk the template in priority order, keeping only axes that have real data
    # (target value present + >= 2 qualified peers). This makes the radar adapt
    # to each league: lower leagues without xG / tackles simply show fewer axes
    # rather than misleading "average" points.
    metrics = []
    for key, label in template:
        if len(metrics) >= _MAX_AXES:
            break

        target_val = _stat_value(target_stats, key, target_minutes)
        if target_val is None:
            continue

        pool = []
        for ps in qualified:
            mins = ps.get('minutesPlayed') or 0
            val = _stat_value(ps, key, mins)
            if val is not None:
                pool.append(val)
        if len(pool) < 2:
            continue

        mean = statistics.fmean(pool)
        std  = statistics.pstdev(pool)
        z    = (target_val - mean) / std if std > 0 else 0.0
        metrics.append({
            'key':        key,
            'label':      label,
            'value':      round(target_val, 2),
            'mean':       round(mean, 2),
            'std':        round(std, 2),
            'z':          round(z, 2),
            'percentile': _pct_rank(target_val, pool),
        })

    if len(metrics) < _MIN_AXES:
        return {'error': 'Not enough comparable data for a radar '
                         f'(only {len(metrics)} metric(s) with peers).'}

    return {
        'player_path':       player_path,
        'player_name':       target_raw.get('player_name',
                              target_raw.get('profile', {}).get('player', {}).get('name', '')),
        'group':             group,
        'competition':       competition.replace('_', ' '),
        'peer_count':        len(qualified),
        'minutes_threshold': round(minutes_threshold, 1),
        'small_sample':      len(qualified) < _SMALL_SAMPLE,
        'metrics':           metrics,
    }


# ── Compare radar: two players + league average, as percentiles ──────────────

def _pos_codes(raw: dict) -> list[str]:
    """Upper-cased position codes, same source order as get_league_position_avg."""
    profile = raw.get('profile', {}).get('player', {})
    return [str(p).upper()
            for p in (profile.get('positionsDetailed') or
                      raw.get('positions', [profile.get('position', '')]))
            if p]


def _pos_match(target_pos: list[str], other_pos: list[str], pos_mode: str) -> bool:
    """Exact = share a position code; grouped = share a GK/DEF/MID/FWD bucket."""
    if pos_mode == 'grouped':
        from data_loader import _BROAD_GROUP
        t = {_BROAD_GROUP.get(p) for p in target_pos} - {None}
        o = {_BROAD_GROUP.get(p) for p in other_pos} - {None}
        return bool(t & o)
    return any(p in target_pos for p in other_pos)


def build_compare_radar(path_a: str, path_b: str, output_dir: str,
                        pos_mode: str = 'exact') -> dict | None:
    """Percentile radar for two players plus the league average.

    The peer pool is Player A's league season, filtered to A's position
    (``pos_mode`` exact / grouped, mirroring /api/league_position_avg) and
    gated at the 33rd percentile of minutes. Both players are ranked against
    that one pool so the three shapes share a scale; when B comes from a
    different league or position, ``same_pool`` is False and the UI says so.
    Axes come from A's position-group template (POSITION_TEMPLATES).
    Countable stats are per-90; rate stats are used as-is.
    """
    loaded_a = load_player_raw(output_dir, path_a)
    loaded_b = load_player_raw(output_dir, path_b)
    if loaded_a is None or loaded_b is None:
        return None
    (country, competition, _, _), file_a, raw_a = loaded_a
    (country_b, competition_b, _, _), file_b, raw_b = loaded_b

    stats_a = raw_a.get('statistics', {}).get('statistics', {}) or {}
    stats_b = raw_b.get('statistics', {}).get('statistics', {}) or {}
    mins_a  = stats_a.get('minutesPlayed') or 0
    mins_b  = stats_b.get('minutesPlayed') or 0
    if not mins_a or not mins_b:
        return {'error': 'Both players need recorded minutes for a radar.'}

    pos_a = _pos_codes(raw_a)
    if not pos_a:
        return {'error': "Could not determine Player A's position."}

    # ── Pool: same league season + position as A, minutes-gated ──────────────
    from data_loader import same_season_glob
    peers = []   # list of (minutes, stats-dict)
    for pd in season_pool_docs(output_dir, country, competition, file_a, raw_a):
        try:
            if not _pos_match(pos_a, _pos_codes(pd), pos_mode):
                continue
            ps = pd.get('statistics', {}).get('statistics', {})
            mins = ps.get('minutesPlayed')
            if not isinstance(mins, (int, float)) or mins <= 0:
                continue
            peers.append((float(mins), ps))
        except Exception:
            continue

    minutes_threshold = _percentile(sorted(m for m, _ in peers), 0.33)
    qualified = [ps for m, ps in peers if m >= minutes_threshold]

    same_pool = (country_b == country and competition_b == competition
                 and same_season_glob(file_b) == same_season_glob(file_a)
                 and _pos_match(pos_a, _pos_codes(raw_b), pos_mode))

    group    = _group_for(raw_a)
    template = POSITION_TEMPLATES.get(group, POSITION_TEMPLATES['MID'])

    # Keep an axis only when both players have the stat and the pool has data.
    metrics = []
    for key, label in template:
        if len(metrics) >= _MAX_AXES:
            break
        val_a = _stat_value(stats_a, key, mins_a)
        val_b = _stat_value(stats_b, key, mins_b)
        if val_a is None or val_b is None:
            continue
        pool = []
        for ps in qualified:
            val = _stat_value(ps, key, ps.get('minutesPlayed') or 0)
            if val is not None:
                pool.append(val)
        if len(pool) < 2:
            continue
        mean = statistics.fmean(pool)
        metrics.append({
            'key':     key,
            'label':   label,
            'is_rate': key in _RATE_STATS,
            'a':   {'value': round(val_a, 2), 'pct': _pct_rank(val_a, pool)},
            'b':   {'value': round(val_b, 2), 'pct': _pct_rank(val_b, pool)},
            'avg': {'value': round(mean, 2),  'pct': _pct_rank(mean, pool)},
        })

    if len(metrics) < _MIN_AXES:
        return {'error': 'Not enough comparable data for a radar '
                         f'(only {len(metrics)} metric(s) shared by both players).'}

    def _name(raw):
        return raw.get('player_name', raw.get('profile', {}).get('player', {}).get('name', ''))

    return {
        'player_a':          _name(raw_a),
        'player_b':          _name(raw_b),
        'group':             group,
        'positions_used':    pos_a,
        'pos_mode':          pos_mode,
        'competition':       competition.replace('_', ' '),
        'peer_count':        len(qualified),
        'minutes_threshold': round(minutes_threshold, 1),
        'small_sample':      len(qualified) < _SMALL_SAMPLE,
        'same_pool':         same_pool,
        'metrics':           metrics,
    }
