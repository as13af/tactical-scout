import os
import json
import re
import unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path
from functools import lru_cache

# ── Simple module-level result cache (key → value) ────────────────────────────
# Populated on first call, reused for all subsequent requests in the same process.
_cache: dict = {}


def _nrm(s: str) -> str:
    """NFC-normalize, strip combining diacritics (Mn), then lowercase.
    Enables accent-insensitive search: 'Muller' matches 'Müller'.
    """
    nfc = unicodedata.normalize('NFC', str(s))
    return ''.join(
        c for c in unicodedata.normalize('NFD', nfc)
        if unicodedata.category(c) != 'Mn'
    ).lower()


def _cached(key, fn):
    """Return cached result for *key*, or call *fn()* and cache the result."""
    if key not in _cache:
        _cache[key] = fn()
    return _cache[key]


def _load_json(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _parse_player_row(json_path, country, competition, club_name):
    """Parse a player JSON into a flat summary dict for listings."""
    data    = _load_json(json_path)
    profile = data.get('profile', {}).get('player', {})
    stats   = data.get('statistics', {}).get('statistics', {})
    # positionsDetailed from profile is the authoritative source; fall back to top-level positions
    pos_list = profile.get('positionsDetailed') or data.get('positions', [])

    age = profile.get('age', '')
    dob = profile.get('dateOfBirth', '')
    if not age and dob:
        from datetime import date, datetime
        try:
            today      = date.today()
            birth_date = datetime.fromisoformat(dob).date()
            age        = today.year - birth_date.year - (
                (today.month, today.day) < (birth_date.month, birth_date.day)
            )
        except Exception:
            age = ''

    # ── Contract expiry ────────────────────────────────────────────────────────
    _cts = int(profile.get('contractUntilTimestamp') or 0)
    contract_until = ''
    contract_badge = ''
    if _cts:
        _dt = datetime.fromtimestamp(_cts, tz=timezone.utc)
        contract_until = _dt.strftime('%b %Y')
        _now_ts = datetime.now(timezone.utc).timestamp()
        _remaining = (_cts - _now_ts) / 86400
        if _cts < _now_ts:
            contract_badge = 'expired'
        elif _remaining <= 182:
            contract_badge = 'red'
        elif _remaining <= 365:
            contract_badge = 'yellow'
        else:
            contract_badge = 'green'

    return {
        'name':              data.get('player_name', profile.get('name', '')),
        'player_id':         data.get('player_id', ''),
        'team':              data.get('team', club_name),
        'team_id':           data.get('team_id', ''),
        'competition':       competition,
        'country':           country,
        'club_slug':         club_name,
        'position':          profile.get('position', pos_list[0] if pos_list else ''),
        'positions':         pos_list,
        'nationality':       (profile.get('country') or {}).get('name', '') or profile.get('nationality', ''),
        'age':               age,
        'height':            profile.get('height', ''),
        'preferred_foot':    profile.get('preferredFoot', ''),
        'shirt_number':      profile.get('shirtNumber', ''),
        'stats':             stats,
        'file':              Path(json_path).name,
        'path':              f"{country}/{competition}/{club_name}/{Path(json_path).name}",
        'contract_until_ts': _cts,
        'contract_until':    contract_until,
        'contract_badge':    contract_badge,
    }


# ── Home dashboard stats ──────────────────────────────────────────────────────
def get_home_stats(output_dir):
    """Return aggregate counts and top-rated players for the home page."""
    return _cached(f'home_stats:{output_dir}', lambda: _get_home_stats(output_dir))


def _get_home_stats(output_dir):
    base = Path(output_dir)
    if not base.exists():
        return {'competitions': 0, 'clubs': 0, 'players': 0, 'top_players': []}

    total_competitions = 0
    total_clubs        = 0
    total_players      = 0
    top_players        = []

    for country_dir in sorted(base.iterdir()):
        if not country_dir.is_dir():
            continue
        for comp_dir in sorted(country_dir.iterdir()):
            if not comp_dir.is_dir():
                continue
            total_competitions += 1
            for club_dir in sorted(comp_dir.iterdir()):
                if not club_dir.is_dir():
                    continue
                total_clubs += 1
                players_dir = club_dir / 'Players'
                if not players_dir.exists():
                    continue
                for f in players_dir.glob('*.json'):
                    total_players += 1
                    try:
                        d     = _load_json(f)
                        stats = d.get('statistics', {}).get('statistics', {})
                        rating = float(stats.get('rating', 0) or 0)
                        apps   = int(stats.get('appearances', 0) or 0)
                        if rating >= 6.5 and apps >= 5:
                            profile = d.get('profile', {}).get('player', {})
                            pos_list = profile.get('positionsDetailed') or d.get('positions', [])
                            top_players.append({
                                'name':        d.get('player_name', ''),
                                'team':        d.get('team', ''),
                                'competition': comp_dir.name.replace('_', ' '),
                                'country':     country_dir.name.replace('_', ' '),
                                'position':    pos_list[0] if pos_list else profile.get('position', ''),
                                'rating':      round(rating, 2),
                                'goals':       int(stats.get('goals', 0) or 0),
                                'assists':     int(stats.get('assists', 0) or 0),
                                'appearances': apps,
                                'path':        f"{country_dir.name}/{comp_dir.name}/{club_dir.name}/{f.name}",
                            })
                    except Exception:
                        continue

    top_players.sort(key=lambda x: x['rating'], reverse=True)

    return {
        'competitions': total_competitions,
        'clubs':        total_clubs,
        'players':      total_players,
        'top_players':  top_players[:10],
    }


# ── Competition / League overview ─────────────────────────────────────────────
def get_all_competitions(output_dir):
    """Walk output/ and return list of {country, competition, clubs, standings_path}."""
    return _cached(f'all_competitions:{output_dir}', lambda: _get_all_competitions(output_dir))


def _get_all_competitions(output_dir):
    result = []
    base   = Path(output_dir)
    if not base.exists():
        return result

    for country_dir in sorted(base.iterdir()):
        if not country_dir.is_dir():
            continue
        for comp_dir in sorted(country_dir.iterdir()):
            if not comp_dir.is_dir():
                continue
            clubs     = [d.name for d in sorted(comp_dir.iterdir()) if d.is_dir()]
            standings = next(comp_dir.glob('Standings_*.json'), None)
            result.append({
                'country':        country_dir.name,
                'competition':    comp_dir.name,
                'club_count':     len(clubs),
                'standings_path': str(standings) if standings else None,
            })
    return result


def get_competition(output_dir, country, competition):
    """Return standings + club list for a competition."""
    base         = Path(output_dir) / country / competition
    standings_f  = next(base.glob('Standings_*.json'), None)
    standings    = _load_json(standings_f) if standings_f else {}

    # ── Extract scraping metadata ──────────────────────────────────────────────
    tid = ''
    season_id = ''
    uniq_tid = ''

    if standings_f:
        # Filename: Standings_{name}_{tid}_Season_{season_id}.json
        m = re.search(r'_(\d+)_Season_(\d+)$', standings_f.stem)
        if m:
            tid       = m.group(1)
            season_id = m.group(2)

    for group in standings.get('standings', []):
        tourney = group.get('tournament', {}).get('uniqueTournament', {})
        if tourney.get('id'):
            uniq_tid = str(tourney['id'])
            break

    clubs = []
    competition_colors = None
    for club_dir in sorted(base.iterdir()):
        if not club_dir.is_dir():
            continue
        club_json  = next(club_dir.glob('Club_*.json'), None)
        club_data  = _load_json(club_json) if club_json else {}
        profile    = club_data.get('profile', {}).get('team', {})
        season_stats = club_data.get('season_statistics', {}).get('statistics', {})
        player_count = len(list((club_dir / 'Players').glob('*.json'))) if (club_dir / 'Players').exists() else 0

        # Extract competition colors from the first club that has them
        if competition_colors is None:
            ut = profile.get('tournament', {}).get('uniqueTournament', {})
            if ut.get('primaryColorHex'):
                competition_colors = {
                    'primary':   ut['primaryColorHex'],
                    'secondary': ut.get('secondaryColorHex', '#D9D9D9'),
                }

        # Extract team colors
        tc = profile.get('teamColors', {})
        team_colors = {
            'primary':   tc.get('primary',   '#6E6E6E'),
            'secondary': tc.get('secondary', '#D9D9D9'),
        }

        clubs.append({
            'slug':         club_dir.name,
            'name':         club_data.get('team_name', club_dir.name),
            'team_id':      club_data.get('team_id', ''),
            'player_count': player_count,
            'stats':        season_stats,
            'profile':      profile,
            'team_colors':  team_colors,
        })

    # Build flat standings (backward-compat) AND phase-aware list
    rows = []
    standings_phases = []
    for group in standings.get('standings', []):
        # phase_name is injected by scraper_cli; fall back to tournament.name for old files
        phase_name = group.get('phase_name') or group.get('tournament', {}).get('name', 'League')
        t_id = group.get('tournament', {}).get('id')
        phase_rows = group.get('rows', [])
        for row in phase_rows:
            rows.append(row)
        standings_phases.append({
            'phase_name':    phase_name,
            'tournament_id': t_id,
            'rows':          phase_rows,
        })

    # Load cup trees (playoff brackets)
    cuptrees_f = next(base.glob('CupTrees_*.json'), None)
    cup_trees = _load_json(cuptrees_f).get('cupTrees', []) if cuptrees_f else []

    # Load season info
    info_f = next(base.glob('SeasonInfo_*.json'), None)
    season_info = _load_json(info_f).get('info', {}) if info_f else {}

    # Derive competition type from stored field or infer from shape
    comp_type = standings.get("competition_type") or (
        "cup_knockout" if (not standings.get("standings") and cup_trees) else
        "cup_groups"   if (len(standings.get("standings", [])) > 1 or
                           (len(standings.get("standings", [])) == 1 and cup_trees)) else
        "league"
    )

    return {
        'competition':        competition,
        'country':            country,
        'clubs':              clubs,
        'standings':          rows,
        'standings_phases':   standings_phases,
        'cup_trees':          cup_trees,
        'season_info':        season_info,
        'tid':                tid,
        'uniq_tid':           uniq_tid,
        'season_id':          season_id,
        'competition_colors': competition_colors or {'primary': '#6E6E6E', 'secondary': '#D9D9D9'},
        'competition_type':   comp_type,
    }


# ── Club ──────────────────────────────────────────────────────────────────────
def get_club(output_dir, country, competition, club):
    base      = Path(output_dir) / country / competition / club
    club_json = next(base.glob('Club_*.json'), None)
    data      = _load_json(club_json) if club_json else {}
    return data


def get_club_players(output_dir, country, competition, club):
    players_dir = Path(output_dir) / country / competition / club / 'Players'
    if not players_dir.exists():
        return []
    players = []
    for f in sorted(players_dir.glob('*.json')):
        players.append(_parse_player_row(f, country, competition, club))
    return players


# ── Player ────────────────────────────────────────────────────────────────────
def get_player(output_dir, country, competition, club, player_file):
    path = Path(output_dir) / country / competition / club / 'Players' / player_file
    return _load_json(path)


def get_heatmap_path(output_dir, country, competition, club, player_file):
    """Return relative URL path for heatmap image if it exists."""
    base    = Path(output_dir) / country / competition / club / 'Heatmaps'
    stem    = Path(player_file).stem
    png     = base / f"{stem}_heatmap.png"
    if png.exists():
        rel = png.relative_to(Path(output_dir))
        return str(rel).replace('\\', '/')
    return None


_POS_CATEGORY = {
    'GK': 'G',
    'DC': 'D', 'DL': 'D', 'DR': 'D',
    'DM': 'M', 'MC': 'M', 'AM': 'M', 'ML': 'M', 'MR': 'M',
    'LW': 'F', 'RW': 'F', 'ST': 'F',
}

def profile_category(positions, fallback_position):
    """Return broad category letter (G/D/M/F) from positionsDetailed list."""
    for pos in positions:
        cat = _POS_CATEGORY.get(str(pos).upper())
        if cat:
            return cat
    return str(fallback_position or '').upper()


# ── Player search ─────────────────────────────────────────────────────────────
def search_players(output_dir, filters):
    base  = Path(output_dir)
    rows  = []

    # Pre-compute contract expiry cutoff once (avoids per-row branching)
    _contract_expiry = filters.get('contract_expiry', '')
    contract_cutoff_ts = None
    if _contract_expiry:
        _now = datetime.now(timezone.utc)
        if _contract_expiry in ('6', '12', '18'):
            contract_cutoff_ts = (_now + timedelta(days=int(_contract_expiry) * 30.44)).timestamp()
        else:
            try:
                contract_cutoff_ts = datetime.fromisoformat(_contract_expiry).replace(
                    tzinfo=timezone.utc).timestamp()
            except Exception:
                pass

    # Walk all Players/ folders
    for country_dir in base.iterdir():
        if not country_dir.is_dir(): continue
        comp_filter = filters.get('competition', '')
        for comp_dir in country_dir.iterdir():
            if not comp_dir.is_dir(): continue
            if comp_filter and comp_filter.lower() not in comp_dir.name.lower():
                continue
            club_filter = filters.get('club', '')
            for club_dir in comp_dir.iterdir():
                if not club_dir.is_dir(): continue
                if club_filter and club_filter.lower() not in club_dir.name.lower():
                    continue
                players_dir = club_dir / 'Players'
                if not players_dir.exists(): continue
                for f in players_dir.glob('*.json'):
                    p = _parse_player_row(f, country_dir.name, comp_dir.name, club_dir.name)
                    rows.append(p)

    # Apply filters
    def _f(val, key, cast=str):
        v = filters.get(key, '')
        if not v: return True
        try:
            return cast(val or 0) >= cast(v)
        except Exception:
            return True

    def _contains(val, key):
        v = _nrm(filters.get(key, ''))
        if not v: return True
        return v in _nrm(str(val or ''))

    filtered = []
    for p in rows:
        s = p.get('stats', {})
        # Category filter (G/D/M/F) — match against broad SofaScore letter
        cat_filter = filters.get('position', '').upper()
        if cat_filter:
            broad = profile_category(p.get('positions', []), p.get('position', ''))
            if not broad.upper().startswith(cat_filter):
                continue
        # specific_pos: exact match against positions list (e.g. "DC", "AM")
        sp_raw  = filters.get('specific_pos', '').upper()
        sp_list = [x.strip() for x in sp_raw.split(',') if x.strip()]
        if sp_list:
            player_pos = [str(x).upper() for x in p.get('positions', [])]
            if not any(sp in player_pos for sp in sp_list):
                continue
        if not _contains(p.get('nationality',''), 'nationality'):  continue
        if not _f(p.get('age',''),           'age_min',    int):   continue
        if not _f(s.get('appearances', 0),   'apps_min',   int):   continue
        if not _f(s.get('rating', 0),        'rating_min', float): continue
        if not _f(s.get('goals', 0),         'goals_min',  int):   continue
        if not _f(s.get('assists', 0),       'assists_min',int):   continue
        # Advanced
        if not _f(s.get('accuratePassesPercentage', 0), 'pass_acc_min', float): continue
        if not _f(s.get('totalDuelsWon', 0),           'duels_min',    int):   continue
        if not _f(s.get('aerialDuelsWon', 0),          'aerial_min',   int):   continue
        if not _f(s.get('successfulDribbles', 0),      'dribbles_min', int):   continue
        if not _f(s.get('tackles', 0),                 'tackles_min',  int):   continue
        if not _f(s.get('keyPasses', 0),               'key_passes_min',int):  continue
        # Age max
        age_max = filters.get('age_max', '')
        if age_max:
            try:
                if int(p.get('age') or 999) > int(age_max): continue
            except Exception:
                pass
        # Contract expiry filter
        if contract_cutoff_ts is not None:
            p_ts = p.get('contract_until_ts', 0)
            if not (0 < p_ts <= contract_cutoff_ts):
                continue
        filtered.append(p)

    # Sort
    sort_by  = filters.get('sort_by', 'rating')
    sort_dir = filters.get('sort_dir', 'desc')
    sort_map = {
        'rating':      lambda p: float(p['stats'].get('rating', 0) or 0),
        'goals':       lambda p: int(p['stats'].get('goals', 0) or 0),
        'assists':     lambda p: int(p['stats'].get('assists', 0) or 0),
        'appearances': lambda p: int(p['stats'].get('appearances', 0) or 0),
        'age':         lambda p: int(p.get('age') or 0),
        'name':        lambda p: p.get('name', '').lower(),
    }
    key_fn = sort_map.get(sort_by, sort_map['rating'])
    filtered.sort(key=key_fn, reverse=(sort_dir == 'desc'))

    # Paginate
    page     = int(filters.get('page', 1))
    per_page = int(filters.get('per_page', 50))
    total    = len(filtered)
    start    = (page - 1) * per_page
    paginated = filtered[start:start + per_page]

    return {
        'players':    paginated,
        'total':      total,
        'page':       page,
        'per_page':   per_page,
        'total_pages': (total + per_page - 1) // per_page,
    }


def get_all_players_flat(output_dir, query='', limit=20):
    """Quick search by name for compare autocomplete."""
    base   = Path(output_dir)
    result = []
    for country_dir in base.iterdir():
        if not country_dir.is_dir(): continue
        for comp_dir in country_dir.iterdir():
            if not comp_dir.is_dir(): continue
            for club_dir in comp_dir.iterdir():
                if not club_dir.is_dir(): continue
                players_dir = club_dir / 'Players'
                if not players_dir.exists(): continue
                for f in players_dir.glob('*.json'):
                    try:
                        d    = _load_json(f)
                        name = d.get('player_name', '')
                        if query and _nrm(query) not in _nrm(name):
                            continue
                        _prof = d.get('profile', {}).get('player', {})
                        _pos  = _prof.get('positionsDetailed') or d.get('positions', [])
                        result.append({
                            'name': name,
                            'team': d.get('team', ''),
                            'position': _prof.get('position') or (_pos[0] if _pos else ''),
                            'competition': comp_dir.name.replace('_', ' '),
                            'path': f"{country_dir.name}/{comp_dir.name}/{club_dir.name}/{f.name}",
                        })
                    except Exception:
                        continue
    result.sort(key=lambda x: x['name'].lower())
    return result[:limit]


# ── League positional average ─────────────────────────────────────────────────

_P90_INELIGIBLE_SUFFIXES = ('Percentage', 'Pct', 'Rate')
_P90_INELIGIBLE_KEYS = frozenset({
    'rating', 'totalRating', 'countRating', 'id', 'type', 'statisticsType',
    'scoringFrequency', 'setPieceConversion', 'totwAppearances',
    'minutesPlayed', 'appearances', 'matchesStarted',
    'substitutionsIn', 'substitutionsOut',
})
_NON_STAT_META_KEYS = frozenset({'id', 'type', 'statisticsType'})

# Broad position groups for 'grouped' pos_mode
_BROAD_GROUP = {
    'GK': 'GK',
    'DC': 'DEF', 'DL': 'DEF', 'DR': 'DEF', 'WBL': 'DEF', 'WBR': 'DEF',
    'DM': 'MID', 'MC': 'MID', 'AM': 'MID', 'ML': 'MID', 'MR': 'MID', 'CM': 'MID',
    'LW': 'FWD', 'RW': 'FWD', 'ST': 'FWD', 'SS': 'FWD', 'CF': 'FWD',
}


def _is_p90_eligible_key(key: str) -> bool:
    if key in _P90_INELIGIBLE_KEYS:
        return False
    for suffix in _P90_INELIGIBLE_SUFFIXES:
        if key.endswith(suffix):
            return False
    return True


def get_league_position_avg(output_dir, player_path, pos_mode='exact'):
    """
    Compute league-wide average stats for players sharing the same position as
    the given player, excluding the player themselves.

    player_path : "Country/Competition/Club/filename.json"
    pos_mode    : 'exact'   – must share at least one exact position code
                  'grouped' – share a broad group (GK / DEF / MID / FWD)

    Returns a dict with 'averages' (raw totals averaged), 'p90_averages'
    (per-90 rates averaged across the qualifying pool), pool metadata, and small_pool flag.
    Minutes threshold: players must meet the 33rd-percentile minutes cutoff.
    Stat keys are collected dynamically from the competition pool.
    """
    parts = player_path.split('/')
    if len(parts) != 4:
        return None

    country, competition, club_slug, filename = parts
    target_file = filename if filename.endswith('.json') else filename + '.json'
    target_full = Path(output_dir) / country / competition / club_slug / 'Players' / target_file
    target_data = _load_json(str(target_full))
    if not target_data:
        return None

    # Determine target player's position codes
    t_profile = target_data.get('profile', {}).get('player', {})
    pos_list  = [
        str(p).upper()
        for p in (t_profile.get('positionsDetailed') or
                  target_data.get('positions', [t_profile.get('position', '')]))
        if p
    ]
    if not pos_list:
        return {'error': 'could not determine player position'}

    target_broad = {_BROAD_GROUP.get(p) for p in pos_list} - {None}

    comp_dir = Path(output_dir) / country / competition
    if not comp_dir.is_dir():
        return None

    # First pass: gather all position-matching candidates with stats + minutes
    candidates = []
    for club_dir in comp_dir.iterdir():
        if not club_dir.is_dir():
            continue
        players_dir = club_dir / 'Players'
        if not players_dir.exists():
            continue
        for f in players_dir.glob('*.json'):
            if f.name == target_file and club_dir.name == club_slug:
                continue
            try:
                d      = _load_json(str(f))
                p_prof = d.get('profile', {}).get('player', {})
                p_pos  = [
                    str(p).upper()
                    for p in (p_prof.get('positionsDetailed') or
                              d.get('positions', [p_prof.get('position', '')]))
                    if p
                ]
                if not p_pos:
                    continue

                if pos_mode == 'grouped':
                    p_broad = {_BROAD_GROUP.get(p) for p in p_pos} - {None}
                    if not target_broad.intersection(p_broad):
                        continue
                else:
                    if not any(p in pos_list for p in p_pos):
                        continue

                s    = d.get('statistics', {}).get('statistics', {})
                mins = float(s.get('minutesPlayed', 0) or 0)
                candidates.append((mins, s))
            except Exception:
                continue

    if not candidates:
        return {
            'averages': {}, 'p90_averages': {}, 'pool_size': 0,
            'positions_used': pos_list, 'pos_mode': pos_mode,
            'competition': competition, 'country': country, 'small_pool': True,
        }

    # Apply 33rd-percentile minutes threshold
    all_mins  = sorted(c[0] for c in candidates)
    idx_33    = max(0, int(len(all_mins) * 0.33) - 1)
    threshold = all_mins[idx_33] if all_mins else 0
    pool      = [(mins, s) for mins, s in candidates if mins >= threshold]
    if not pool:
        pool = candidates  # fallback: keep all
    pool_size = len(pool)

    # Dynamically collect all numeric stat keys from the qualified pool
    all_keys: set = set()
    for _, s in pool:
        for k, v in s.items():
            if k in _NON_STAT_META_KEYS:
                continue
            try:
                float(v)
                all_keys.add(k)
            except (TypeError, ValueError):
                continue

    raw_buckets: dict = {k: [] for k in all_keys}
    p90_buckets: dict = {k: [] for k in all_keys if _is_p90_eligible_key(k)}

    for mins, s in pool:
        m90 = mins / 90.0 if mins > 0 else None
        for k in all_keys:
            raw_val = s.get(k)
            if raw_val is None:
                continue
            try:
                v = float(raw_val)
            except (TypeError, ValueError):
                continue
            raw_buckets[k].append(v)
            if k in p90_buckets and m90:
                p90_buckets[k].append(v / m90)

    averages     = {k: round(sum(v) / len(v), 4) if v else 0.0 for k, v in raw_buckets.items()}
    p90_averages = {k: round(sum(v) / len(v), 4) if v else 0.0 for k, v in p90_buckets.items()}

    return {
        'averages':       averages,
        'p90_averages':   p90_averages,
        'pool_size':      pool_size,
        'positions_used': pos_list,
        'pos_mode':       pos_mode,
        'competition':    competition,
        'country':        country,
        'small_pool':     pool_size < 5,
    }


def get_all_clubs_flat(output_dir, query='', limit=20, league_filter=''):
    """Quick search by club name for club-compatibility autocomplete.

    Args:
        league_filter: optional "Country/Competition" to restrict results to one league.
    """
    base   = Path(output_dir)
    result = []

    # Pre-parse the league filter so we can skip dirs early
    lf_parts  = league_filter.split('/') if league_filter else []
    lf_country = lf_parts[0] if len(lf_parts) >= 1 else ''
    lf_comp    = lf_parts[1] if len(lf_parts) >= 2 else ''

    for country_dir in sorted(base.iterdir()):
        if not country_dir.is_dir(): continue
        if lf_country and country_dir.name != lf_country: continue
        for comp_dir in sorted(country_dir.iterdir()):
            if not comp_dir.is_dir(): continue
            if lf_comp and comp_dir.name != lf_comp: continue
            for club_dir in sorted(comp_dir.iterdir()):
                if not club_dir.is_dir(): continue
                display = club_dir.name.replace('_', ' ')
                if query and _nrm(query) not in _nrm(display) \
                          and _nrm(query) not in _nrm(comp_dir.name.replace('_', ' ')) \
                          and _nrm(query) not in _nrm(country_dir.name):
                    continue
                result.append({
                    'name':        display,
                    'competition': comp_dir.name.replace('_', ' '),
                    'country':     country_dir.name.replace('_', ' '),
                    'path':        f"{country_dir.name}/{comp_dir.name}/{club_dir.name}",
                })
    result.sort(key=lambda x: x['name'].lower())
    return result[:limit]


def resolve_player_paths(output_dir: str, player_paths: list) -> list:
    """Resolve a list of player path strings to minimal display dicts.

    Each path must be in the form ``"Country/Competition/Club/filename"``.
    Invalid or missing entries are silently skipped.

    Returns:
        List of dicts with keys: ``path``, ``name``, ``position``, ``team``,
        ``competition``, ``country``.
    """
    base    = Path(output_dir)
    results = []
    seen    = set()
    for raw_path in player_paths:
        raw_path = str(raw_path).strip()
        if raw_path in seen:
            continue
        seen.add(raw_path)
        parts = raw_path.split('/')
        if len(parts) != 4:
            continue
        country, competition, club, filename = parts
        fname    = filename if filename.endswith('.json') else filename + '.json'
        full     = base / country / competition / club / 'Players' / fname
        if not full.exists():
            continue
        try:
            with open(full, encoding='utf-8') as f:
                raw = json.load(f)
            profile   = raw.get('profile', {}).get('player', {})
            name      = profile.get('name', '') or raw.get('name', filename.replace('_', ' '))
            position  = profile.get('position', '') or raw.get('position', '')
            team_name = club.replace('_', ' ')
        except Exception:
            continue
        results.append({
            'path':        raw_path,
            'name':        name,
            'position':    position,
            'team':        team_name,
            'competition': competition.replace('_', ' '),
            'country':     country.replace('_', ' '),
        })
    return results

