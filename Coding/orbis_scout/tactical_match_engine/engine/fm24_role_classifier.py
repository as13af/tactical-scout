"""
fm24_role_classifier.py
────────────────────────────────────────────────────────────────────────────────
Classifies a player into one or more FM24 role archetypes by evaluating per-90
stat thresholds defined in fm24_role_schema_copilot.md.

Usage
-----
    from tactical_match_engine.engine.fm24_role_classifier import classify

    result = classify(raw_stats, positions)
    # result = {
    #   'strict':      [...],   # roles where ALL conditions are met
    #   'near':        [...],   # top near-matches ranked by coverage %
    #   'sample_size': 'ok'|'low',
    # }

Each entry in strict / near:
    id, label, cluster, positions, conditions_met, conditions_total,
    coverage (0-1), is_strict_match, confidence (0-100), confidence_tier,
    cond_details  (per-condition breakdown)
"""

from __future__ import annotations

from typing import Any, Dict, List

# ── Per-90 helper ─────────────────────────────────────────────────────────────

def _p90(raw: Any, minutes: float) -> float:
    """Normalise a raw cumulative stat to a per-90 value."""
    if not minutes or minutes <= 0:
        return 0.0
    return (float(raw or 0) / minutes) * 90.0


# ── Operator evaluator ────────────────────────────────────────────────────────

def _check(value: float, op: str, threshold: float) -> bool:
    if op == '>=': return value >= threshold
    if op == '<=': return value <= threshold
    if op == '>':  return value > threshold
    if op == '<':  return value < threshold
    if op == '==': return value == threshold
    return False


# ── Position group helper ────────────────────────────────────────────────────
# Maps FM24 role position codes → broad G/D/M/F letter.
_POS_GROUP: Dict[str, str] = {
    'GK':  'G',
    'CB':  'D', 'DC':  'D',
    'LB':  'D', 'RB':  'D', 'DL': 'D', 'DR': 'D',
    'LWB': 'D', 'RWB': 'D',
    'DM':  'M', 'CM':  'M', 'AM': 'M', 'ML': 'M', 'MR': 'M',
    'ST':  'F', 'LW':  'F', 'RW': 'F',
}


def _position_group(positions: List[str]) -> str:
    """Return the broad position group letter for the first recognised code."""
    for p in positions:
        g = _POS_GROUP.get(p)
        if g:
            return g
    return '?'


# ── Role catalogue ────────────────────────────────────────────────────────────
# Condition tuple: (sofascore_raw_key, kind, op, threshold)
#   kind = 'p90'  → compute (raw / minutes * 90)
#   kind = 'pct'  → use raw value directly (already 0-100)
# All thresholds and formulas sourced from fm24_role_schema_copilot.md

_ROLES: List[Dict] = [
    # ── Creative attacker ─────────────────────────────────────────────────────
    {
        'id': 'ap', 'label': 'Advanced Playmaker', 'cluster': 'Creative',
        'positions': ['AM', 'CM'],
        'confidence': 85,
        'conditions': [
            ('keyPasses',                   'p90', '>=', 1.5),
            ('accuratePassesPercentage',     'pct', '>=', 75.0),
            ('accurateFinalThirdPasses',     'p90', '>=', 1.5),
        ],
    },
    {
        'id': 'shadow', 'label': 'Shadow Striker', 'cluster': 'Creative',
        'positions': ['AM'],
        'confidence': 78,
        'conditions': [
            ('goals',       'p90', '>=', 0.4),
            ('keyPasses',   'p90', '>=', 0.8),
            ('totalShots',  'p90', '>=', 2.0),
        ],
    },
    {
        'id': 'enganche', 'label': 'Enganche', 'cluster': 'Creative',
        'positions': ['AM'],
        'confidence': 72,
        'conditions': [
            ('keyPasses',                    'p90', '>=', 2.0),
            ('accuratePassesPercentage',      'pct', '>=', 78.0),
            ('successfulDribblesPercentage',  'pct', '>=', 60.0),
        ],
    },
    {
        'id': 'trequartista', 'label': 'Trequartista', 'cluster': 'Creative',
        'positions': ['AM'],
        'confidence': 70,
        'conditions': [
            ('keyPasses',          'p90', '>=', 1.8),
            ('successfulDribbles', 'p90', '>=', 1.0),
            ('fouls',              'p90', '<',  1.2),
        ],
    },
    # ── Goalscorer ────────────────────────────────────────────────────────────
    {
        'id': 'af', 'label': 'Advanced Forward', 'cluster': 'Goalscorer',
        'positions': ['ST'],
        'confidence': 88,
        'conditions': [
            ('goals',                    'p90', '>=', 0.5),
            ('goalConversionPercentage', 'pct', '>=', 20.0),
            ('shotsOnTarget',            'p90', '>=', 1.5),
        ],
    },
    {
        'id': 'poacher', 'label': 'Poacher', 'cluster': 'Goalscorer',
        'positions': ['ST'],
        'confidence': 80,
        'conditions': [
            ('goalConversionPercentage', 'pct', '>=', 30.0),
            ('goals',                   'p90', '>=', 0.5),
            ('successfulDribbles',      'p90', '<',  0.5),
        ],
    },
    {
        'id': 'completefwd', 'label': 'Complete Forward', 'cluster': 'Goalscorer',
        'positions': ['ST'],
        'confidence': 82,
        'conditions': [
            ('goals',                    'p90', '>=', 0.35),
            ('assists',                  'p90', '>=', 0.1),
            ('aerialDuelsWonPercentage', 'pct', '>=', 35.0),
            ('keyPasses',               'p90', '>=', 0.5),
        ],
    },
    {
        'id': 'falseni', 'label': 'False 9', 'cluster': 'Goalscorer',
        'positions': ['ST'],
        'confidence': 73,
        'conditions': [
            ('keyPasses',               'p90', '>=', 1.2),
            ('accuratePassesPercentage','pct', '>=', 75.0),
            ('goals',                   'p90', '>=', 0.2),
        ],
    },
    # ── Wide player ───────────────────────────────────────────────────────────
    {
        'id': 'if_role', 'label': 'Inside Forward', 'cluster': 'Wide',
        'positions': ['LW', 'RW', 'AM'],
        'confidence': 83,
        'conditions': [
            ('successfulDribbles', 'p90', '>=', 1.0),
            ('totalShots',         'p90', '>=', 2.0),
            ('goals',              'p90', '>=', 0.3),
        ],
    },
    {
        'id': 'winger', 'label': 'Winger', 'cluster': 'Wide',
        'positions': ['LW', 'RW'],
        'confidence': 79,
        'conditions': [
            ('accurateCrosses',    'p90', '>=', 0.6),
            ('successfulDribbles', 'p90', '>=', 0.8),
        ],
    },
    {
        'id': 'widecm', 'label': 'Wide Midfielder', 'cluster': 'Wide',
        'positions': ['ML', 'MR', 'CM'],
        'confidence': 74,
        'conditions': [
            ('accuratePassesPercentage', 'pct', '>=', 75.0),
            ('accurateCrosses',          'p90', '>=', 0.4),
            ('ballRecovery',             'p90', '>=', 2.0),
        ],
    },
    {
        'id': 'wb_attack', 'label': 'Wing-back (Attack)', 'cluster': 'Wide',
        'positions': ['LWB', 'RWB', 'LB', 'RB'],
        'confidence': 76,
        'conditions': [
            ('accurateCrosses',    'p90', '>=', 0.5),
            ('successfulDribbles', 'p90', '>=', 0.6),
        ],
    },
    {
        'id': 'wb_support', 'label': 'Wing-back (Support)', 'cluster': 'Wide',
        'positions': ['LWB', 'RWB', 'LB', 'RB'],
        'confidence': 76,
        'conditions': [
            ('accuratePassesPercentage', 'pct', '>=', 76.0),
            ('clearances',               'p90', '>=', 1.0),
            ('accurateCrosses',          'p90', '>=', 0.3),
        ],
    },
    # ── Ball-winner ───────────────────────────────────────────────────────────
    {
        'id': 'bwm', 'label': 'Ball-winning Midfielder', 'cluster': 'Ball-winner',
        'positions': ['CM', 'DM'],
        'confidence': 85,
        'conditions': [
            ('totalDuelsWonPercentage', 'pct', '>=', 45.0),
            ('fouls',                   'p90', '>=', 1.5),
            ('ballRecovery',            'p90', '>=', 3.0),
        ],
    },
    {
        'id': 'dm', 'label': 'Defensive Midfielder', 'cluster': 'Ball-winner',
        'positions': ['DM'],
        'confidence': 83,
        'conditions': [
            ('totalDuelsWonPercentage', 'pct', '>=', 40.0),
            ('accuratePassesPercentage','pct', '>=', 78.0),
            ('ballRecovery',            'p90', '>=', 2.5),
        ],
    },
    {
        'id': 'halfback', 'label': 'Half-back', 'cluster': 'Ball-winner',
        'positions': ['DM'],
        'confidence': 71,
        'conditions': [
            ('clearances',               'p90', '>=', 2.0),
            ('aerialDuelsWonPercentage', 'pct', '>=', 40.0),
            ('accuratePassesPercentage', 'pct', '>=', 76.0),
        ],
    },
    # ── Deep creator ──────────────────────────────────────────────────────────
    {
        'id': 'dlp', 'label': 'Deep-lying Playmaker', 'cluster': 'Deep Creator',
        'positions': ['CM', 'DM'],
        'confidence': 82,
        'conditions': [
            ('accuratePassesPercentage',    'pct', '>=', 80.0),
            ('accurateLongBalls',           'p90', '>=', 1.0),
            ('accurateLongBallsPercentage', 'pct', '>=', 65.0),
        ],
    },
    {
        'id': 'regista', 'label': 'Regista', 'cluster': 'Deep Creator',
        'positions': ['DM', 'CM'],
        'confidence': 74,
        'conditions': [
            ('keyPasses',               'p90', '>=', 1.5),
            ('accuratePassesPercentage','pct', '>=', 80.0),
            ('accurateLongBalls',       'p90', '>=', 1.2),
        ],
    },
    {
        'id': 'roaming', 'label': 'Roaming Playmaker', 'cluster': 'Deep Creator',
        'positions': ['CM'],
        'confidence': 70,
        'conditions': [
            ('keyPasses',          'p90', '>=', 1.2),
            ('successfulDribbles', 'p90', '>=', 0.8),
            ('ballRecovery',       'p90', '>=', 2.0),
        ],
    },
    # ── Box-to-box ────────────────────────────────────────────────────────────
    {
        'id': 'b2b', 'label': 'Box-to-box Midfielder', 'cluster': 'Box-to-box',
        'positions': ['CM'],
        'confidence': 80,
        'conditions': [
            ('goals',                   'p90', '>=', 0.15),
            ('ballRecovery',            'p90', '>=', 2.0),
            ('totalDuelsWonPercentage', 'pct', '>=', 38.0),
        ],
    },
    {
        'id': 'mezzala', 'label': 'Mezzala', 'cluster': 'Box-to-box',
        'positions': ['CM'],
        'confidence': 79,
        'conditions': [
            ('successfulDribbles', 'p90', '>=', 0.8),
            ('totalShots',         'p90', '>=', 1.2),
            ('keyPasses',          'p90', '>=', 0.8),
        ],
    },
    {
        'id': 'carrilero', 'label': 'Carrilero', 'cluster': 'Box-to-box',
        'positions': ['CM'],
        'confidence': 72,
        'conditions': [
            ('accuratePassesPercentage', 'pct', '>=', 78.0),
            ('totalDuelsWonPercentage',  'pct', '>=', 38.0),
            ('clearances',              'p90', '>=', 0.8),
        ],
    },
    # ── Defender ──────────────────────────────────────────────────────────────
    {
        'id': 'cb_defend', 'label': 'Central Defender (Defend)', 'cluster': 'Defender',
        'positions': ['CB', 'DC'],
        'confidence': 86,
        'conditions': [
            ('clearances',               'p90', '>=', 3.0),
            ('aerialDuelsWonPercentage', 'pct', '>=', 50.0),
            ('totalDuelsWonPercentage',  'pct', '>=', 45.0),
        ],
    },
    {
        'id': 'cb_stopper', 'label': 'Central Defender (Stopper)', 'cluster': 'Defender',
        'positions': ['CB', 'DC'],
        'confidence': 78,
        'conditions': [
            ('totalDuelsWonPercentage',  'pct', '>=', 48.0),
            ('fouls',                   'p90', '>=', 1.2),
            ('aerialDuelsWonPercentage','pct', '>=', 45.0),
        ],
    },
    {
        'id': 'cb_cover', 'label': 'Central Defender (Cover)', 'cluster': 'Defender',
        'positions': ['CB', 'DC'],
        'confidence': 74,
        'conditions': [
            ('clearances',               'p90', '>=', 3.5),
            ('aerialDuelsWonPercentage', 'pct', '>=', 45.0),
            ('accuratePassesPercentage', 'pct', '>=', 72.0),
        ],
    },
    {
        'id': 'bpd', 'label': 'Ball-playing Defender', 'cluster': 'Defender',
        'positions': ['CB', 'DC'],
        'confidence': 76,
        'conditions': [
            ('accuratePassesPercentage',    'pct', '>=', 78.0),
            ('accurateLongBallsPercentage', 'pct', '>=', 60.0),
            ('clearances',                 'p90', '>=', 2.0),
        ],
    },
    {
        'id': 'libero', 'label': 'Libero', 'cluster': 'Defender',
        'positions': ['CB', 'DC'],
        'confidence': 68,
        'conditions': [
            ('accuratePassesPercentage', 'pct', '>=', 80.0),
            ('accurateLongBalls',        'p90', '>=', 1.5),
            ('clearances',              'p90', '>=', 2.0),
        ],
    },
    {
        'id': 'lb_defend', 'label': 'Full-back (Defend)', 'cluster': 'Defender',
        'positions': ['LB', 'RB', 'DL', 'DR'],
        'confidence': 81,
        'conditions': [
            ('clearances',              'p90', '>=', 1.5),
            ('totalDuelsWonPercentage', 'pct', '>=', 42.0),
            ('accurateCrosses',         'p90', '<',  0.4),
        ],
    },
    {
        'id': 'lb_support', 'label': 'Full-back (Support)', 'cluster': 'Defender',
        'positions': ['LB', 'RB', 'DL', 'DR'],
        'confidence': 78,
        'conditions': [
            ('accuratePassesPercentage', 'pct', '>=', 76.0),
            ('clearances',              'p90', '>=', 1.2),
            ('accurateCrosses',         'p90', '>=', 0.3),
        ],
    },
    {
        'id': 'inverted_fb', 'label': 'Inverted Full-back', 'cluster': 'Defender',
        'positions': ['LB', 'RB', 'DL', 'DR'],
        'confidence': 71,
        'conditions': [
            ('accuratePassesPercentage', 'pct', '>=', 78.0),
            ('keyPasses',               'p90', '>=', 0.5),
            ('successfulDribbles',      'p90', '>=', 0.4),
        ],
    },
    # ── Target / support striker ──────────────────────────────────────────────
    {
        'id': 'targetman', 'label': 'Target Man', 'cluster': 'Target Striker',
        'positions': ['ST'],
        'confidence': 84,
        'conditions': [
            ('aerialDuelsWonPercentage', 'pct', '>=', 50.0),
            ('aerialDuelsWon',           'p90', '>=', 2.5),
            ('fouls',                   'p90', '>=', 1.0),
        ],
    },
    {
        'id': 'dlf', 'label': 'Deep-lying Forward', 'cluster': 'Target Striker',
        'positions': ['ST'],
        'confidence': 75,
        'conditions': [
            ('keyPasses',               'p90', '>=', 0.8),
            ('assists',                 'p90', '>=', 0.1),
            ('accuratePassesPercentage','pct', '>=', 70.0),
        ],
    },
    {
        'id': 'pressingfwd', 'label': 'Pressing Forward', 'cluster': 'Target Striker',
        'positions': ['ST', 'LW', 'RW'],
        'confidence': 77,
        'conditions': [
            ('fouls',                   'p90', '>=', 1.5),
            ('ballRecovery',            'p90', '>=', 2.0),
            ('totalDuelsWonPercentage', 'pct', '>=', 35.0),
        ],
    },
    # ── Goalkeeper ────────────────────────────────────────────────────────────
    {
        'id': 'gk_defend', 'label': 'Goalkeeper (Defend)', 'cluster': 'Goalkeeper',
        'positions': ['GK'],
        'confidence': 80,
        'conditions': [
            ('saves',             'p90', '>=', 3.0),
            ('crossesNotClaimed', 'p90', '<',  1.0),
        ],
    },
    {
        'id': 'sweeperkeeper', 'label': 'Sweeper Keeper', 'cluster': 'Goalkeeper',
        'positions': ['GK'],
        'confidence': 74,
        'conditions': [
            ('saves',             'p90', '>=', 2.5),
            ('accurateLongBalls', 'p90', '>=', 1.5),
            ('crossesNotClaimed', 'p90', '<',  1.5),
        ],
    },
]


# ── Public API ────────────────────────────────────────────────────────────────

def classify(
    raw_stats: Dict[str, Any],
    positions: List[str],
    top_n: int = 3,
) -> Dict[str, Any]:
    """
    Classify a player into FM24 role archetypes.

    Parameters
    ----------
    raw_stats  : ``statistics.statistics`` dict from a SofaScore player JSON.
    positions  : Player position code list from the JSON (e.g. ['ST']).
    top_n      : Max number of near-matches to return (default 3).

    Returns
    -------
    dict:
        ``strict``      – Roles where ALL conditions are met (sorted by confidence desc).
        ``near``        – Top near-matches when strict is empty; next-closest otherwise.
                          Sorted by coverage % desc, then confidence desc.
        ``sample_size`` – ``"low"`` when minutesPlayed < 270, else ``"ok"``.
    """
    minutes = float(raw_stats.get('minutesPlayed', 0) or 0)
    sample_size = 'low' if minutes < 270 else 'ok'

    player_pos_set = set(positions)

    strict_matches: List[Dict] = []
    near_matches:   List[Dict] = []

    for role in _ROLES:
        # Hard filter: skip roles with no position overlap
        if not (set(role['positions']) & player_pos_set):
            continue
        conditions = role['conditions']
        n_total    = len(conditions)
        n_met      = 0
        cond_details = []

        for (stat_key, kind, op, threshold) in conditions:
            if kind == 'p90':
                raw_val = float(raw_stats.get(stat_key, 0) or 0)
                value   = _p90(raw_val, minutes)
            else:  # 'pct' — used raw
                value = float(raw_stats.get(stat_key, 0) or 0)

            met = _check(value, op, threshold)
            if met:
                n_met += 1

            cond_details.append({
                'stat':      stat_key,
                'kind':      kind,
                'op':        op,
                'threshold': threshold,
                'value':     round(value, 3),
                'met':       met,
            })

        coverage = n_met / n_total if n_total else 0.0
        conf     = role['confidence']

        result = {
            'id':               role['id'],
            'label':            role['label'],
            'cluster':          role['cluster'],
            'positions':        role['positions'],
            'position_group':   _position_group(role['positions']),
            'conditions_met':   n_met,
            'conditions_total': n_total,
            'coverage':         round(coverage, 3),
            'is_strict_match':  n_met == n_total,
            'confidence':       conf,
            'confidence_tier':  (
                'high'   if conf >= 85 else
                'medium' if conf >= 72 else
                'low'
            ),
            'cond_details':     cond_details,
        }

        if n_met == n_total:
            strict_matches.append(result)
        else:
            near_matches.append(result)

    # Sort: strict by confidence desc; near by (coverage, confidence) desc
    strict_matches.sort(key=lambda r: r['confidence'], reverse=True)
    near_matches.sort(key=lambda r: (r['coverage'], r['confidence']), reverse=True)

    return {
        'strict':      strict_matches,
        'near':        near_matches[:top_n],
        'sample_size': sample_size,
    }
