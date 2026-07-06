"""
imported_player.py
==================
In-memory import pipeline for ad-hoc player JSONs (e.g. national-team season
aggregates) so they can be dropped into **Player Scatter** and **Club
Suitability / Compatibility** without writing anything to ``output/``.

Design goals
------------
* No disk writes. Uploaded players live in a process-local dict (``IMPORT_STORE``)
  keyed by a short id, with a soft cap so memory can't grow unbounded.
* The normalized record is byte-for-byte shaped like an on-disk league player
  file, so the existing scatter route reads it via the *same*
  ``raw['profile']['player']`` / ``raw['statistics']['statistics']`` access path.
* ``additionalStatistics`` (tackles / interceptions / touches / xG …) is merged
  INTO ``statistics.statistics`` because the rest of the app only ever looks
  there.
* Every stat is **dual-written** under both naming conventions found in the
  codebase (season-aggregate names like ``assists`` AND export/event names like
  ``goalAssist``) so a value lights up no matter which key a consumer asks for.
* Age is derived from ``dateOfBirth`` OR ``dateOfBirthTimestamp``.
* Single-letter positions ('M'/'D'/'F'/'G') are expanded to representative
  detailed codes so BOTH ``profile_category`` (scatter) and ``_BROAD_GROUP``
  (engine squad matching) resolve them.

Wire-up: call ``register_import_routes(app)`` once after the Flask app exists.
"""

from __future__ import annotations

import io
import json
import time
import uuid
from datetime import datetime, timezone, date

from flask import request, jsonify

# ──────────────────────────────────────────────────────────────────────────────
# In-memory store
# ──────────────────────────────────────────────────────────────────────────────
# id -> {'record': <canonical dict>, 'meta': {...}, 'ts': <epoch>}
IMPORT_STORE: dict = {}
_MAX_IMPORTS = 50          # soft cap; oldest evicted past this
_TTL_SECONDS = 60 * 60 * 6  # 6h; evicted lazily on access/insert


def _evict():
    """Drop expired entries, then trim to the most-recent _MAX_IMPORTS."""
    now = time.time()
    for k in [k for k, v in IMPORT_STORE.items() if now - v['ts'] > _TTL_SECONDS]:
        IMPORT_STORE.pop(k, None)
    if len(IMPORT_STORE) > _MAX_IMPORTS:
        for k, _ in sorted(IMPORT_STORE.items(), key=lambda kv: kv[1]['ts'])[
            : len(IMPORT_STORE) - _MAX_IMPORTS
        ]:
            IMPORT_STORE.pop(k, None)


def store_put(record: dict, meta: dict) -> str:
    _evict()
    pid = uuid.uuid4().hex[:12]
    IMPORT_STORE[pid] = {'record': record, 'meta': meta, 'ts': time.time()}
    return pid


def store_get(pid: str):
    _evict()
    entry = IMPORT_STORE.get(pid)
    return entry['record'] if entry else None


# Token used in the HTML player-path field, e.g. "imported:ab12cd34ef56"
IMPORT_TOKEN_PREFIX = 'imported:'


def is_import_token(path: str) -> bool:
    return isinstance(path, str) and path.startswith(IMPORT_TOKEN_PREFIX)


def token_id(path: str) -> str:
    return path[len(IMPORT_TOKEN_PREFIX):]


# ──────────────────────────────────────────────────────────────────────────────
# Field aliasing  (season-aggregate name  <->  export / event name)
# ──────────────────────────────────────────────────────────────────────────────
# Left = name your uploaded file + data_loader + scatter labels use.
# Right = name the XLSX export's PLAYER_STAT_FIELDS / S2_RAW_ORDER uses.
# We write BOTH so every consumer finds a value.
_ALIAS_PAIRS = [
    ('assists',                       'goalAssist'),
    ('keyPasses',                     'keyPass'),
    ('shotsOnTarget',                 'onTargetScoringAttempt'),
    ('totalDuelsWon',                 'totalWon'),
    ('aerialDuelsWon',                'aerialWon'),
    ('accuratePassesPercentage',      'passAccuracyPercentage'),
    ('accurateCrosses',               'accurateCross'),
    ('accurateCrossesPercentage',     'crossAccuracyPercentage'),
    ('accurateLongBallsPercentage',   'longBallAccuracyPercentage'),
    ('successfulDribblesPercentage',  'dribbleSuccessPercentage'),
    ('totalDuelsWonPercentage',       'duelSuccessPercentage'),
    ('aerialDuelsWonPercentage',      'aerialDuelSuccessPercentage'),
    ('penaltiesTaken',                'penaltyTaken'),
    ('offsides',                      'offsideGiven'),
    ('dribbledPast',                  'dribledPast'),     # note: app uses the misspelling
    ('blockedShots',                  'blockedShotsAll'),
    # ── engine-defensive: event-feed names the role_encoder MAY read. Not in
    #    PLAYER_STAT_FIELDS, so irrelevant to scatter, but harmless extra keys. ──
    ('tacklesWon',                    'wonTackle'),
    ('interceptions',                 'interceptionWon'),
    ('dribbles',                      'dribbleAttempts'),
]

# Keys pulled out of additionalStatistics and merged into statistics.statistics.
# value = canonical name to store under. These canonical names are the ones in
# app.py's PLAYER_STAT_FIELDS (verified) so the scatter axes pick them up.
_ADDITIONAL_MAP = {
    'touches':                       'touches',
    'dribblesAttempted':             'dribbles',          # PLAYER_STAT_FIELDS uses 'dribbles'
    'tacklesTotal':                  'tackles',           # ← in PLAYER_STAT_FIELDS
    'tacklesWon':                    'tacklesWon',        # ← in PLAYER_STAT_FIELDS (was wrongly 'wonTackle')
    'interceptions':                 'interceptions',     # ← in PLAYER_STAT_FIELDS
    'possessionLost':                'possessionLost',    # ← in PLAYER_STAT_FIELDS
    'dispossessed':                  'dispossessed',      # ← in PLAYER_STAT_FIELDS
    'ownHalfPasses':                 'totalOwnHalfPasses',
    'accurateOwnHalfPasses':         'accurateOwnHalfPasses',
    'oppositionHalfPasses':          'totalOppositionHalfPasses',
    'accurateOppositionHalfPasses':  'accurateOppositionHalfPasses',
    'xG':                            'expectedGoals',     # ← in PLAYER_STAT_FIELDS (null in this file)
    'xA':                            'expectedAssists',   # ← in PLAYER_STAT_FIELDS (null in this file)
    # 'unsuccessfulTouches' intentionally dropped — no consumer
}

# Single-letter -> representative detailed position code(s).
_LETTER_EXPAND = {
    'G': ['GK'],
    'D': ['DC'],
    'M': ['MC'],
    'F': ['ST'],
    'A': ['ST'],
    'W': ['RW'],
}


def _coerce_num(v):
    """Return v if it's a real (non-bool) number, else None."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    return None


def _derive_age(player: dict):
    """Age from dateOfBirth (ISO) or dateOfBirthTimestamp (epoch seconds)."""
    if player.get('age'):
        try:
            return int(player['age'])
        except Exception:
            pass
    dob = player.get('dateOfBirth')
    if dob:
        try:
            bd = datetime.fromisoformat(dob).date()
            t = date.today()
            return t.year - bd.year - ((t.month, t.day) < (bd.month, bd.day))
        except Exception:
            pass
    ts = player.get('dateOfBirthTimestamp')
    if ts:
        try:
            bd = datetime.fromtimestamp(int(ts), tz=timezone.utc).date()
            t = date.today()
            return t.year - bd.year - ((t.month, t.day) < (bd.month, bd.day))
        except Exception:
            pass
    return None


def _expand_positions(positions, fallback):
    """Return (positions_detailed_list, primary_position)."""
    out = []
    for p in (positions or []):
        code = str(p).upper().strip()
        if not code:
            continue
        if code in _LETTER_EXPAND:
            out.extend(_LETTER_EXPAND[code])
        else:
            out.append(code)
    if not out and fallback:
        fb = str(fallback).upper().strip()
        out = _LETTER_EXPAND.get(fb, [fb] if fb else [])
    if not out:
        out = ['MC']
    # de-dup preserving order
    seen, dedup = set(), []
    for c in out:
        if c not in seen:
            seen.add(c)
            dedup.append(c)
    return dedup, dedup[0]


def normalize_uploaded_player(raw: dict) -> dict:
    """Convert an uploaded player JSON into a canonical on-disk-shaped record.

    The result has the same access paths the rest of the app expects:
    ``rec['profile']['player']`` and ``rec['statistics']['statistics']``.
    """
    profile_in = (raw.get('profile') or {}).get('player') or {}
    stats_in   = (raw.get('statistics') or {}).get('statistics') or {}
    addl_in    = raw.get('additionalStatistics') or {}

    # ── Build merged stat block ────────────────────────────────────────────
    stats = {}
    for k, v in stats_in.items():
        n = _coerce_num(v)
        if n is not None:
            stats[k] = n

    # Merge additionalStatistics under canonical names
    for src, dst in _ADDITIONAL_MAP.items():
        n = _coerce_num(addl_in.get(src))
        if n is not None and dst not in stats:
            stats[dst] = n

    # Dual-write naming aliases (only fill the missing side)
    for a, b in _ALIAS_PAIRS:
        if a in stats and b not in stats:
            stats[b] = stats[a]
        elif b in stats and a not in stats:
            stats[a] = stats[b]

    # ── Profile / bio ──────────────────────────────────────────────────────
    age = _derive_age(profile_in)

    # nationality: a national-team file stores the country as the team name
    nationality = ''
    country_obj = profile_in.get('country')
    if isinstance(country_obj, dict):
        nationality = country_obj.get('name', '') or ''
    if not nationality:
        nationality = profile_in.get('nationality', '') or raw.get('team', '') or ''

    positions_detailed, primary_pos = _expand_positions(
        raw.get('positions') or profile_in.get('positionsDetailed'),
        profile_in.get('position'),
    )

    market_value = (
        profile_in.get('proposedMarketValue')
        or (profile_in.get('proposedMarketValueRaw') or {}).get('value')
        or profile_in.get('marketValue')
        or 0
    )

    player_obj = {
        **profile_in,                       # keep originals (slug, height, jersey…)
        'name':               raw.get('player_name', profile_in.get('name', '')),
        'position':           primary_pos,
        'positionsDetailed':  positions_detailed,
        'age':                age,
        'nationality':        nationality,
        'country':            {'name': nationality} if nationality else None,
        'height':             profile_in.get('height', '') or '',
        'proposedMarketValue': market_value,
        'contractUntilTimestamp': int(profile_in.get('contractUntilTimestamp') or 0),
        'slug':               profile_in.get('slug', ''),
    }

    # Give the engine loader an ISO dateOfBirth it can parse (it ignores the
    # timestamp), so age is correct on BOTH the in-memory and loader paths.
    if not player_obj.get('dateOfBirth') and profile_in.get('dateOfBirthTimestamp'):
        try:
            player_obj['dateOfBirth'] = datetime.fromtimestamp(
                int(profile_in['dateOfBirthTimestamp']), tz=timezone.utc
            ).date().isoformat()
        except Exception:
            pass

    record = {
        'player_id':            raw.get('player_id', ''),
        'player_name':          raw.get('player_name', profile_in.get('name', '')),
        'team':                 raw.get('team', ''),
        'team_id':              raw.get('team_id', ''),
        'positions':            positions_detailed,
        'competition_name':     raw.get('competition_name', 'Imported'),
        'competition_country':  raw.get('competition_country') or '',   # never None: loader calls .lower()
        'profile':              {'player': player_obj},
        'statistics':           {'statistics': stats},
        '_imported':            True,           # marker the scatter route can pass through
        '_meta_src':            raw.get('_meta', {}),
    }
    return record


def import_meta(record: dict) -> dict:
    """Small preview payload for the UI after a successful upload."""
    p = record['profile']['player']
    s = record['statistics']['statistics']
    return {
        'name':         record['player_name'],
        'team':         record['team'],
        'position':     p.get('position', ''),
        'positions':    record['positions'],
        'age':          p.get('age'),
        'nationality':  p.get('nationality', ''),
        'minutes':      s.get('minutesPlayed', 0),
        'appearances':  s.get('appearances', 0),
        'goals':        s.get('goals', 0),
        'assists':      s.get('assists', 0),
        'rating':       s.get('rating'),
        'stat_count':   len(s),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Engine candidate builder (for /api/club_suitability + /api/club_compatibility)
# ──────────────────────────────────────────────────────────────────────────────
def build_engine_candidate(record: dict, source_league: str | None,
                           get_league_info_fn):
    """Return the dict shape that ``_load_player_for_engine`` returns.

    source_league : "Country/Competition" chosen in the UI, used purely to
                    supply the player's *current* league intensity + global
                    rank for the physical-adaptation calc and move-label.
                    If absent/unknown, a neutral baseline is used.
    """
    p = record['profile']['player']
    s = record['statistics']['statistics']

    intensity   = None
    global_rank = None
    league_name = ''
    if source_league and '/' in source_league:
        c, comp = source_league.split('/', 1)
        try:
            info        = get_league_info_fn(c.replace('_', ' '), comp.replace('_', ' '))
            intensity   = info.get('intensity')
            global_rank = info.get('global_rank')
            league_name = comp.replace('_', ' ')
        except Exception:
            pass

    if intensity is None:
        intensity = 0.5          # neutral baseline → adaptation ~ centred
        league_name = league_name or 'Imported (neutral baseline)'

    positions = [str(x).upper() for x in record['positions']]
    # Mirror load_sofascore_player's filtered `stats` (numeric, non-negative)
    filtered_stats = {k: v for k, v in s.items()
                      if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0}
    return {
        # keys load_sofascore_player returns — full superset, drop-in compatible
        'id':                                 str(record.get('player_id', '')),
        'name':                               record['player_name'],
        'age':                                p.get('age') if p.get('age') is not None else 25,
        'position':                           p.get('position', positions[0] if positions else 'MC'),
        'positions':                          positions,
        'preferred_foot':                     p.get('preferredFoot') or 'Right',
        'height_cm':                          int(p.get('height') or 0),
        'weight_kg':                          int(p.get('weight') or 0),
        'current_league_intensity':           intensity,
        'current_league_intensity_estimated': intensity == 0.5 and not source_league,
        'current_league_global_rank':         global_rank,
        'current_league_name':                league_name,
        'tactical_familiarity':               {'formation_experience': {}},
        'stats':                              filtered_stats,
        'raw_stats':                          s,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Flask route registration
# ──────────────────────────────────────────────────────────────────────────────
def register_import_routes(app):
    """Adds POST /api/import_player to the given Flask app."""

    @app.route('/api/import_player', methods=['POST'])
    def api_import_player():
        # Accept either a multipart file field named "file" or a raw JSON body.
        raw = None
        if 'file' in request.files:
            try:
                raw = json.loads(request.files['file'].read().decode('utf-8'))
            except Exception as e:
                return jsonify({'error': f'invalid JSON file: {e}'}), 400
        else:
            raw = request.get_json(silent=True)
        if not isinstance(raw, dict):
            return jsonify({'error': 'no player JSON provided'}), 400

        try:
            record = normalize_uploaded_player(raw)
        except Exception as e:
            return jsonify({'error': f'could not normalize player: {e}'}), 400

        s = record['statistics']['statistics']
        mins = s.get('minutesPlayed')
        if not isinstance(mins, (int, float)) or mins <= 0:
            return jsonify({'error': 'player has no minutesPlayed; cannot import'}), 400

        pid  = store_put(record, import_meta(record))
        return jsonify({
            'id':      pid,
            'token':   IMPORT_TOKEN_PREFIX + pid,
            'preview': import_meta(record),
        })


# ──────────────────────────────────────────────────────────────────────────────
# CLI self-test:  python imported_player.py <path-to-player.json>
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else 'season_Marselino_Ferdinan.json'
    with open(path, encoding='utf-8') as f:
        raw = json.load(f)
    rec = normalize_uploaded_player(raw)
    meta = import_meta(rec)
    print('── preview ──────────────────────────────')
    for k, v in meta.items():
        print(f'{k:>14}: {v}')
    s = rec['statistics']['statistics']
    print('\n── alias sanity check ──────────────────')
    for a, b in _ALIAS_PAIRS:
        print(f'{a:>30} = {s.get(a)!s:>8}   |   {b:<28} = {s.get(b)}')
    print('\n── merged-from-additionalStatistics ───')
    for key in ('tackles', 'wonTackle', 'interceptions', 'interceptionWon',
                'touches', 'possessionLost', 'dispossessed'):
        print(f'{key:>16}: {s.get(key)}')
    print(f'\ntotal stat keys: {len(s)}')
