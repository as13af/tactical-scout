"""
services/positions.py
---------------------
Position-line classification, role <-> position-code mapping, engine player
loading, and squad role analysis.  These are the shared "business logic"
helpers used across the compatibility, export, scatter and rankings routes.
"""

import os

import context as ctx
from context import OUTPUT_DIR

def _load_player_for_engine(country: str, competition: str, club: str, fname: str):
    """Load an engine-ready player dict for one player.

    Reads from MongoDB when available (Vercel / Mongo mode), otherwise from the
    output/ JSON files. Returns None if the player is not found.
    """
    fname = fname if fname.endswith('.json') else fname + '.json'
    if ctx._USING_MONGO:
        doc = ctx._loader._db().players.find_one({
            '_country': country, '_competition': competition,
            '_club': club, '_file': fname,
        })
        return ctx._loader.load_sofascore_player_for_engine(doc) if doc else None
    from tactical_match_engine.services.json_loader import load_sofascore_player
    full = os.path.join(OUTPUT_DIR, country, competition, club, 'Players', fname)
    return load_sofascore_player(full) if os.path.exists(full) else None


POSITION_LINE = {
    "GK": "Back-Line",  "DC": "Back-Line",  "DL": "Back-Line",
    "DR": "Back-Line",  "WBL": "Back-Line", "WBR": "Back-Line",
    "DM": "Mid-Line",   "MC": "Mid-Line",   "ML": "Mid-Line",
    "MR": "Mid-Line",   "AM": "Mid-Line",
    "LW": "Front-Line", "RW": "Front-Line", "ST": "Front-Line",
    "SS": "Front-Line",
}


def get_position_line(positions: list) -> str:
    """Return the dominant position line (Back-Line / Mid-Line / Front-Line) for a position list."""
    counts = {}
    for pos in positions:
        line = POSITION_LINE.get(str(pos).upper())
        if line:
            counts[line] = counts.get(line, 0) + 1
    if not counts:
        return "Unknown"
    keys = list(counts.keys())
    return max(counts, key=lambda k: (counts[k], -keys.index(k)))


_LINE_BROAD_POS = {
    '1. Back-Line':  {'DC', 'DL', 'DR', 'WBL', 'WBR', 'CB'},
    '2. Mid-Line':   {'DM', 'MC', 'AM', 'ML', 'MR', 'CM'},
    '3. Front-Line': {'LW', 'RW', 'ST', 'CF', 'SS', 'FW'},
}


def _build_role_pos_codes():
    from tactical_match_engine.engine.role_encoder import POSITION_CODE_TO_ROLE_FILE
    mapping = {}
    for code, rel_path in POSITION_CODE_TO_ROLE_FILE.items():
        mapping.setdefault(rel_path, set()).add(code.upper())
    return mapping


_ROLE_TO_POS_CODES = None  # lazy-initialised


def _pos_codes_for_role(role_path):
    """Return the set of position codes (e.g. {'DC'}) whose default role is role_path."""
    global _ROLE_TO_POS_CODES
    if _ROLE_TO_POS_CODES is None:
        _ROLE_TO_POS_CODES = _build_role_pos_codes()
    # Normalise slashes for lookup
    normalised = role_path.replace('\\', '/')
    return _ROLE_TO_POS_CODES.get(normalised, set())


def _get_squad_role_analysis(country, competition, club, role_path, cand_line=None, exclude_path=None):
    """
    Load every player at *club* whose position line matches *cand_line*,
    score each against *role_path*, and return aggregated squad stats.
    Uses MongoDB when available, falls back to file-system scan.
    """
    from tactical_match_engine.engine.role_encoder import get_role_fitness_vector

    empty = {'players': [], 'avg_overall': 0.0,
             'avg_category_scores': {}, 'avg_metric_scores': {}, 'avg_metric_raw': {}, 'player_count': 0}
    role_codes = None if cand_line else _pos_codes_for_role(role_path)

    def _score_doc(pd):
        player_positions = [str(p).upper() for p in pd.get('positions', [pd.get('position', '')])]
        if cand_line:
            if get_position_line(player_positions) != cand_line:
                return None
        else:
            if role_codes and not any(p in role_codes for p in player_positions):
                return None
        res = get_role_fitness_vector(pd['raw_stats'], pd['position'], role_path=role_path, flat_weights=True)
        if res is None:
            return None
        return {
            'name':            pd['name'],
            'position':        pd['position'],
            'positions':       pd.get('positions', []),
            'age':             pd['age'],
            'overall_score':   res['overall_score'],
            'category_scores': res['category_scores'],
            'metric_details':  res['metric_details'],
            'rating':          round(float(pd['raw_stats'].get('rating', 0) or 0), 2),
            'appearances':     int(pd['raw_stats'].get('appearances', 0) or 0),
        }

    squad = []
    if ctx._USING_MONGO:
        exclude_file = os.path.basename(exclude_path) if exclude_path else None
        for doc in ctx._loader._db().players.find({
            '_country': country, '_competition': competition, '_club': club,
        }):
            if exclude_file and doc.get('_file') == exclude_file:
                continue
            try:
                pd  = ctx._loader.load_sofascore_player_for_engine(doc)
                row = _score_doc(pd)
                if row:
                    squad.append(row)
            except Exception:
                continue
    else:
        import glob as _glob
        from tactical_match_engine.services.json_loader import load_sofascore_player
        players_dir = os.path.join(OUTPUT_DIR, country, competition, club, 'Players')
        if not os.path.isdir(players_dir):
            return empty
        for fpath in sorted(_glob.glob(os.path.join(players_dir, '*.json'))):
            if exclude_path and os.path.normpath(fpath) == os.path.normpath(exclude_path):
                continue
            try:
                pd  = load_sofascore_player(fpath)
                row = _score_doc(pd)
                if row:
                    squad.append(row)
            except Exception:
                continue

    if not squad:
        return empty

    avg_overall = sum(p['overall_score'] for p in squad) / len(squad)

    cat_buckets = {}
    for p in squad:
        for cat, score in p['category_scores'].items():
            cat_buckets.setdefault(cat, []).append(score)
    avg_cat = {cat: round(sum(v) / len(v), 2) for cat, v in cat_buckets.items()}

    metric_score_buckets = {}
    metric_raw_buckets   = {}
    for p in squad:
        for m in p['metric_details']:
            metric_score_buckets.setdefault(m['name'], []).append(m['score'])
            metric_raw_buckets.setdefault(m['name'],  []).append(m['raw_value'])
    avg_metric_scores = {k: round(sum(v) / len(v), 2) for k, v in metric_score_buckets.items()}
    avg_metric_raw    = {k: round(sum(v) / len(v), 4) for k, v in metric_raw_buckets.items()}

    return {
        'players':             sorted(squad, key=lambda x: x['overall_score'], reverse=True),
        'avg_overall':         round(avg_overall, 2),
        'avg_category_scores': avg_cat,
        'avg_metric_scores':   avg_metric_scores,
        'avg_metric_raw':      avg_metric_raw,
        'player_count':        len(squad),
    }
