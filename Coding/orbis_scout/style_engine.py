"""style_engine.py — Playing style fingerprint scorer.

Reads season_statistics from Club_*.json files (Sofascore format) and
returns Z-score-normalised style confidence scores (0–1) per team
across 10 tactical playing styles.
"""

import math
from statistics import mean, stdev


# ── Metric extractors ─────────────────────────────────────────────────────────

def _pm(field):
    """Per-match extractor for a raw count field."""
    return lambda s, m: float(s[field]) / m if (
        m > 0 and field in s and isinstance(s[field], (int, float))
    ) else None


def _raw(field):
    """Raw-value extractor (percentages, averages — not divided by matches)."""
    return lambda s, m: float(s[field]) if (
        field in s and isinstance(s[field], (int, float))
    ) else None


def _ratio(n_field, d_field):
    """Ratio extractor: n_field / d_field."""
    def _f(s, m):
        n, d = s.get(n_field), s.get(d_field)
        if isinstance(n, (int, float)) and isinstance(d, (int, float)) and d > 0:
            return float(n) / float(d)
        return None
    return _f


METRIC_EXTRACTORS = {
    'possession':        _raw('averageBallPossession'),
    'pass_accuracy':     _raw('accuratePassesPercentage'),
    'pass_volume':       _pm('totalPasses'),
    'directness':        _ratio('totalLongBalls', 'totalPasses'),
    'crossing':          _pm('totalCrosses'),
    'aerial_volume':     _pm('totalAerialDuels'),
    'aerial_win':        _raw('aerialDuelsWonPercentage'),
    'press_proxy':       lambda s, m: (
        ((s.get('interceptions') or 0) + (s.get('fouls') or 0)) / m
        if m > 0 else None
    ),
    'recovery':          _pm('ballRecovery'),
    'offsides_pm':       _pm('offsides'),
    'shots_for':         _pm('shots'),
    'shots_against':     _pm('shotsAgainst'),
    'clean_sheets_rate': lambda s, m: (
        (s.get('cleanSheets') or 0) / m if m > 0 else None
    ),
    'dribbles':          _pm('successfulDribbles'),
    'shot_efficiency':   _ratio('shots', 'totalPasses'),
}

METRIC_KEYS = list(METRIC_EXTRACTORS.keys())

METRIC_LABELS = {
    'possession':        'Possession',
    'pass_accuracy':     'Pass Accuracy',
    'pass_volume':       'Pass Volume',
    'directness':        'Directness',
    'crossing':          'Crossing',
    'aerial_volume':     'Aerial Volume',
    'aerial_win':        'Aerial Win %',
    'press_proxy':       'Press Intensity',
    'recovery':          'Ball Recovery',
    'offsides_pm':       'Offside Traps',
    'shots_for':         'Shots For',
    'shots_against':     'Shots Against',
    'clean_sheets_rate': 'Clean Sheet Rate',
    'dribbles':          'Dribbles',
    'shot_efficiency':   'Shot Efficiency',
}


# ── Style fingerprint weight vectors ─────────────────────────────────────────
# Positive weight → HIGH value of that metric supports this style.
# Negative weight → LOW value of that metric supports this style.
# Weights are applied to Z-scored metric values, then dot-product-scored.

STYLE_WEIGHTS = {
    'Control Possession': {
        'possession':     2.0,
        'pass_accuracy':  2.0,
        'pass_volume':    1.5,
        'directness':    -1.5,
        'aerial_volume': -1.0,
        'press_proxy':    0.5,
        'shots_against': -0.5,
    },
    'Tiki-Taka': {
        'possession':      2.5,
        'pass_accuracy':   3.0,
        'pass_volume':     2.0,
        'directness':     -2.0,
        'aerial_volume':  -2.0,
        'press_proxy':     1.0,
        'recovery':        1.0,
        'dribbles':       -0.5,
        'shot_efficiency': -0.5,
    },
    'Vertical Tiki-Taka': {
        'possession':      2.0,
        'pass_accuracy':   2.0,
        'pass_volume':     1.5,
        'directness':     -1.0,
        'shot_efficiency': 1.5,
        'dribbles':        1.5,
        'shots_for':       1.0,
        'press_proxy':     1.0,
    },
    'Gegenpress': {
        'press_proxy':  3.0,
        'recovery':     2.5,
        'shots_for':    1.0,
        'possession':   0.5,
        'directness':  -0.5,
    },
    'Wing Play': {
        'crossing':      3.0,
        'dribbles':      1.5,
        'aerial_volume': 0.5,
    },
    'Route One': {
        'directness':    3.0,
        'aerial_volume': 2.5,
        'aerial_win':    1.5,
        'possession':   -2.0,
        'pass_accuracy': -1.5,
        'pass_volume':  -1.5,
    },
    'Fluid Counter-Attack': {
        'dribbles':        2.0,
        'shot_efficiency': 2.0,
        'recovery':        0.5,
        'possession':     -0.5,
        'directness':     -0.5,
        'shots_against':  -0.5,
    },
    'Direct Counter-Attack': {
        'directness':      1.5,
        'shot_efficiency': 1.0,
        'shots_against':   0.5,
        'possession':     -1.5,
        'pass_volume':    -1.5,
        'dribbles':        0.5,
    },
    'Catenaccio': {
        'clean_sheets_rate': 2.0,
        'pass_accuracy':     0.5,
        'shots_for':        -2.0,
        'possession':       -0.5,
        'directness':       -0.5,
        'dribbles':         -0.5,
    },
    'Park The Bus': {
        'shots_against':     2.0,
        'aerial_volume':     1.5,
        'clean_sheets_rate': 1.5,
        'possession':       -3.0,
        'shots_for':        -2.5,
        'pass_volume':      -2.0,
    },
}

STYLE_KEYS = list(STYLE_WEIGHTS.keys())


# ── Display stats table definition ───────────────────────────────────────────
# (sofascore_field, display_label, is_raw_percentage, lower_is_better)
DISPLAY_STATS = [
    ('goals',                       'Goals',            False, False),
    ('goalsConceded',               'Goals Conceded',   False, True),
    ('shots',                       'Shots',            False, False),
    ('shotsAgainst',                'Shots Against',    False, True),
    ('averageBallPossession',       'Possession %',     True,  False),
    ('totalPasses',                 'Passes',           False, False),
    ('accuratePassesPercentage',    'Pass Accuracy %',  True,  False),
    ('totalLongBalls',              'Long Balls',       False, False),
    ('accurateLongBallsPercentage', 'Long Ball Acc %',  True,  False),
    ('totalCrosses',                'Crosses',          False, False),
    ('accurateCrossesPercentage',   'Cross Acc %',      True,  False),
    ('successfulDribbles',          'Succ. Dribbles',   False, False),
    ('corners',                     'Corners',          False, False),
    ('interceptions',               'Interceptions',    False, False),
    ('totalAerialDuels',            'Aerial Duels',     False, False),
    ('aerialDuelsWonPercentage',    'Aerial Win %',     True,  False),
    ('offsides',                    'Offsides',         False, True),
    ('fouls',                       'Fouls',            False, True),
    ('ballRecovery',                'Ball Recovery',    False, False),
    ('cleanSheets',                 'Clean Sheets',     False, False),
    ('saves',                       'Saves',            False, False),
]


def compute_stats_table(clubs_raw):
    """Compute per-match stats, league averages, and ranks for the stats table.

    Args:
        clubs_raw: same list passed to compute_style_scores

    Returns:
        {
            'stat_defs':    [{field, label, is_raw, lower_is_better}, ...],
            'league_avg':   {field: float, ...},
            'total_teams':  int,
            'teams':        {slug: {field: {value, rank}, ...}, ...},
        }
    """
    # Step 1 — per-match values for each club
    team_vals = {}
    for club in clubs_raw:
        s = club['season_stats']
        m = club['matches']
        vals = {}
        for field, _label, is_raw, _lib in DISPLAY_STATS:
            v = s.get(field)
            if isinstance(v, (int, float)):
                vals[field] = float(v) if is_raw else (float(v) / m if m > 0 else None)
            else:
                vals[field] = None
        team_vals[club['slug']] = vals

    # Step 2 — league average per stat
    league_avg = {}
    for field, _label, _ir, _lib in DISPLAY_STATS:
        valid = [team_vals[slug][field] for slug in team_vals
                 if team_vals[slug][field] is not None]
        league_avg[field] = round(mean(valid), 2) if valid else None

    # Step 3 — rank per stat (rank 1 = best)
    team_ranks = {slug: {} for slug in team_vals}
    for field, _label, _ir, lower_is_better in DISPLAY_STATS:
        pairs = [(slug, team_vals[slug][field]) for slug in team_vals
                 if team_vals[slug][field] is not None]
        pairs.sort(key=lambda x: x[1], reverse=not lower_is_better)
        for rank_idx, (slug, _) in enumerate(pairs, 1):
            team_ranks[slug][field] = rank_idx

    # Step 4 — package per-team output
    teams_out = {}
    for slug in team_vals:
        stats = {}
        for field, _label, _ir, _lib in DISPLAY_STATS:
            v = team_vals[slug].get(field)
            stats[field] = {
                'value': round(v, 2) if v is not None else None,
                'rank':  team_ranks[slug].get(field),
            }
        teams_out[slug] = stats

    return {
        'stat_defs': [
            {'field': f, 'label': l, 'is_raw': ir, 'lower_is_better': lib}
            for f, l, ir, lib in DISPLAY_STATS
        ],
        'league_avg':  {f: league_avg[f] for f, *_ in DISPLAY_STATS},
        'total_teams': len(clubs_raw),
        'teams':       teams_out,
    }


# ── Core scoring functions ────────────────────────────────────────────────────

def _zscore_across_pool(teams):
    """Z-score normalise each metric across all teams in the pool."""
    zscores = {t['slug']: {} for t in teams}

    for metric in METRIC_KEYS:
        pairs = [(t['slug'], t['metrics'].get(metric)) for t in teams]
        valid = [(slug, v) for slug, v in pairs if v is not None]

        if len(valid) < 2:
            for slug, _ in pairs:
                zscores[slug][metric] = 0.0
            continue

        vals = [v for _, v in valid]
        mu    = mean(vals)
        sigma = stdev(vals)

        for slug, v in pairs:
            if v is None:
                zscores[slug][metric] = 0.0
            elif sigma < 1e-9:
                zscores[slug][metric] = 0.0
            else:
                zscores[slug][metric] = (v - mu) / sigma

    return zscores


def compute_style_scores(clubs_raw):
    """Score each team in clubs_raw against all 10 playing styles.

    Args:
        clubs_raw: list of dicts, each with:
            {
                'name':         str,
                'slug':         str,
                'logo_url':     str,
                'season_stats': dict,   # season_statistics.statistics from Club JSON
                'matches':      int,
            }

    Returns:
        list of dicts, one per team:
            {
                'name':          str,
                'slug':          str,
                'logo_url':      str,
                'style_scores':  {style_name: float (0–1), ...},
                'primary_style': str,
                'radar':         {metric_key: float (0–100), ...},
            }
    """
    # Step 1 — compute raw metric values per team
    teams = []
    for club in clubs_raw:
        s = club['season_stats']
        m = club['matches']
        metrics = {}
        for key, extractor in METRIC_EXTRACTORS.items():
            try:
                val = extractor(s, m)
                if val is not None and math.isfinite(val):
                    metrics[key] = float(val)
            except Exception:
                pass
        teams.append({
            'name':        club['name'],
            'slug':        club['slug'],
            'logo_url':    club.get('logo_url', ''),
            'team_colors': club.get('team_colors', {'primary': '#6E6E6E', 'secondary': '#D9D9D9'}),
            'metrics':     metrics,
        })

    # Step 2 — Z-score normalise across pool
    zscores = _zscore_across_pool(teams)

    # Step 3 — dot-product score against each style fingerprint
    results = []
    for t in teams:
        z = zscores[t['slug']]

        style_scores = {}
        for style_name, weights in STYLE_WEIGHTS.items():
            raw_dot = sum(z.get(metric, 0.0) * w for metric, w in weights.items())
            # Normalise by L2-norm of weight vector for comparable scale
            weight_norm = math.sqrt(sum(w * w for w in weights.values()))
            normalized  = raw_dot / weight_norm if weight_norm > 0 else 0.0
            # tanh maps (−∞,+∞) → (−1,+1); shift to (0,1)
            score = (math.tanh(normalized * 0.8) + 1.0) / 2.0
            style_scores[style_name] = round(score, 3)

        primary = max(style_scores, key=style_scores.get)

        # Radar values: Z-score mapped to 0–100 centred at 50 (±1σ = ±15 pts)
        radar = {
            k: round(max(0.0, min(100.0, 50.0 + z.get(k, 0.0) * 15.0)), 1)
            for k in METRIC_KEYS
        }

        results.append({
            'name':          t['name'],
            'slug':          t['slug'],
            'logo_url':      t['logo_url'],
            'style_scores':  style_scores,
            'primary_style': primary,
            'radar':         radar,
            'team_colors':   t.get('team_colors', {'primary': '#6E6E6E', 'secondary': '#D9D9D9'}),
        })

    return results
