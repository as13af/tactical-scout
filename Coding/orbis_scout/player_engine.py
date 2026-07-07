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


def build_radar(player_path: str, output_dir: str) -> dict | None:
    """Build the z-score radar payload for one player.

    Returns None if the path is malformed or the player file is missing.
    Returns a dict with ``error`` set when the player has no usable stats.
    """
    parts = player_path.split('/')
    if len(parts) != 4:
        return None
    country, competition, club_slug, filename = parts
    target_file = filename if filename.endswith('.json') else filename + '.json'
    full_path = os.path.join(output_dir, country, competition, club_slug, 'Players', target_file)
    if not os.path.exists(full_path):
        return None

    with open(full_path, encoding='utf-8') as f:
        target_raw = json.load(f)

    target_stats = target_raw.get('statistics', {}).get('statistics', {})
    if not isinstance(target_stats, dict) or not target_stats:
        return {'error': 'No season statistics for this player.'}

    target_minutes = target_stats.get('minutesPlayed')
    if not isinstance(target_minutes, (int, float)) or target_minutes <= 0:
        return {'error': 'Player has no recorded minutes.'}

    group    = _group_for(target_raw)
    template = POSITION_TEMPLATES.get(group, POSITION_TEMPLATES['MID'])

    # ── Pass 1: every same-group player in the league who appeared ────────────
    comp_dir = os.path.join(output_dir, country, competition)
    peers = []   # list of (minutes, stats-dict)
    for fpath in glob.glob(os.path.join(comp_dir, '*', 'Players', '*.json')):
        try:
            with open(fpath, encoding='utf-8') as f:
                pd = json.load(f)
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
