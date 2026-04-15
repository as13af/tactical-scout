import os
import json
import re
from pathlib import Path


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

    return {
        'name':           data.get('player_name', profile.get('name', '')),
        'player_id':      data.get('player_id', ''),
        'team':           data.get('team', club_name),
        'team_id':        data.get('team_id', ''),
        'competition':    competition,
        'country':        country,
        'club_slug':      club_name,
        'position':       profile.get('position', pos_list[0] if pos_list else ''),
        'positions':      pos_list,
        'nationality':    (profile.get('country') or {}).get('name', '') or profile.get('nationality', ''),
        'age':            age,
        'height':         profile.get('height', ''),
        'preferred_foot': profile.get('preferredFoot', ''),
        'shirt_number':   profile.get('shirtNumber', ''),
        'stats':          stats,
        'file':           Path(json_path).name,
        'path':           f"{country}/{competition}/{club_name}/{Path(json_path).name}",
    }


# ── Home dashboard stats ──────────────────────────────────────────────────────
def get_home_stats(output_dir):
    """Return aggregate counts and top-rated players for the home page."""
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
    for club_dir in sorted(base.iterdir()):
        if not club_dir.is_dir():
            continue
        club_json  = next(club_dir.glob('Club_*.json'), None)
        club_data  = _load_json(club_json) if club_json else {}
        profile    = club_data.get('profile', {}).get('team', {})
        season_stats = club_data.get('season_statistics', {}).get('statistics', {})
        player_count = len(list((club_dir / 'Players').glob('*.json'))) if (club_dir / 'Players').exists() else 0

        clubs.append({
            'slug':         club_dir.name,
            'name':         club_data.get('team_name', club_dir.name),
            'team_id':      club_data.get('team_id', ''),
            'player_count': player_count,
            'stats':        season_stats,
            'profile':      profile,
        })

    rows = []
    for group in standings.get('standings', []):
        for row in group.get('rows', []):
            rows.append(row)

    return {
        'competition': competition,
        'country':     country,
        'clubs':       clubs,
        'standings':   rows,
        'tid':         tid,
        'uniq_tid':    uniq_tid,
        'season_id':   season_id,
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
        v = filters.get(key, '').lower()
        if not v: return True
        return v in str(val or '').lower()

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
                        if query and query not in name.lower():
                            continue
                        result.append({
                            'name': name,
                            'team': d.get('team', ''),
                            'competition': comp_dir.name.replace('_', ' '),
                            'path': f"{country_dir.name}/{comp_dir.name}/{club_dir.name}/{f.name}",
                        })
                    except Exception:
                        continue
    result.sort(key=lambda x: x['name'].lower())
    return result[:limit]


# ── League positional average ─────────────────────────────────────────────────

# Mirrors compare.html COMPARE_STATS: (key, canBeP90, isPercent)
_COMPARE_STAT_META = [
    ('appearances',              False, False),
    ('goals',                    True,  False),
    ('assists',                  True,  False),
    ('minutesPlayed',            False, False),
    ('rating',                   False, True ),
    ('totalShots',               True,  False),
    ('shotsOnTarget',            True,  False),
    ('keyPasses',                True,  False),
    ('accuratePassesPercentage', False, True ),
    ('accuratePasses',           True,  False),
    ('successfulDribbles',       True,  False),
    ('totalDuelsWon',            True,  False),
    ('totalDuelsWonPercentage',  False, True ),
    ('aerialDuelsWon',           True,  False),
    ('aerialDuelsWonPercentage', False, True ),
    ('clearances',               True,  False),
    ('ballRecovery',             True,  False),
    ('yellowCards',              False, False),
    ('redCards',                 False, False),
]

# Broad position groups for 'grouped' pos_mode
_BROAD_GROUP = {
    'GK': 'GK',
    'DC': 'DEF', 'DL': 'DEF', 'DR': 'DEF', 'WBL': 'DEF', 'WBR': 'DEF',
    'DM': 'MID', 'MC': 'MID', 'AM': 'MID', 'ML': 'MID', 'MR': 'MID', 'CM': 'MID',
    'LW': 'FWD', 'RW': 'FWD', 'ST': 'FWD', 'SS': 'FWD', 'CF': 'FWD',
}


def get_league_position_avg(output_dir, player_path, pos_mode='exact'):
    """
    Compute league-wide average stats for players sharing the same position as
    the given player, excluding the player themselves.

    player_path : "Country/Competition/Club/filename.json"
    pos_mode    : 'exact'   – must share at least one exact position code
                  'grouped' – share a broad group (GK / DEF / MID / FWD)

    Returns a dict with 'averages' (raw totals averaged), 'p90_averages'
    (per-90 rates averaged across the pool), pool metadata, and small_pool flag.
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

    # Walk all clubs in the same competition
    comp_dir = Path(output_dir) / country / competition
    if not comp_dir.is_dir():
        return None

    raw_buckets = {key: [] for key, _, _ in _COMPARE_STAT_META}
    p90_buckets = {
        key: []
        for key, can_p90, is_pct in _COMPARE_STAT_META
        if can_p90 and not is_pct
    }
    pool_size = 0

    for club_dir in comp_dir.iterdir():
        if not club_dir.is_dir():
            continue
        players_dir = club_dir / 'Players'
        if not players_dir.exists():
            continue
        for f in players_dir.glob('*.json'):
            # Exclude self
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

                # Position matching
                if pos_mode == 'grouped':
                    p_broad = {_BROAD_GROUP.get(p) for p in p_pos} - {None}
                    if not target_broad.intersection(p_broad):
                        continue
                else:
                    if not any(p in pos_list for p in p_pos):
                        continue

                s     = d.get('statistics', {}).get('statistics', {})
                mins  = float(s.get('minutesPlayed', 0) or 0)
                m90   = mins / 90.0 if mins > 0 else None

                for key, can_p90, is_pct in _COMPARE_STAT_META:
                    val = float(s.get(key, 0) or 0)
                    raw_buckets[key].append(val)
                    if can_p90 and not is_pct and m90:
                        p90_buckets[key].append(val / m90)

                pool_size += 1
            except Exception:
                continue

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
                if query and query not in display.lower() \
                          and query not in comp_dir.name.lower().replace('_', ' ') \
                          and query not in country_dir.name.lower():
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

