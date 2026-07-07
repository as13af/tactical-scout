"""routes/core.py -- core blueprint (auto-extracted from the original app.py)."""

import os, io, csv, json, glob, re, uuid, threading
import time as _time
from flask import Blueprint, render_template, request, jsonify, send_file, abort

import context as ctx
from context import (
    _logger,
    OUTPUT_DIR, MATCH_OUTPUT_DIR, _WEBAPP_DIR,
    get_all_competitions, get_competition, get_club, get_club_players,
    get_player, search_players, get_all_players_flat, get_all_clubs_flat,
    get_heatmap_path, get_home_stats,
    store_get, is_import_token, token_id, build_engine_candidate,
    get_all_matches, get_match, get_player_match_history, compute_per90,
    MATCH_STATS_META, _DERIVED_STATS,
    _home_stats_cache, _HOME_STATS_TTL, _player_id_cache,
    _scrape_jobs, _scrape_jobs_lock,
)
from services.positions import (
    get_position_line, POSITION_LINE, _LINE_BROAD_POS,
    _pos_codes_for_role, _build_role_pos_codes, _get_squad_role_analysis,
    _load_player_for_engine,
)
from services.scatter import (
    CLUB_STAT_FIELDS, _CLUB_P90_EXCLUDE, PLAYER_STAT_FIELDS,
    _P90_EXCLUDE, _CAT_TO_GROUP, _percentile,
)

bp = Blueprint('core', __name__)


@bp.route('/api/import_player_positions')
def api_import_player_positions():
    """
    Return position metadata for an already-imported player (token in memory).
    Called by club_compatibility.html immediately after /api/import_player
    to enable role auto-suggestion for imported players.

    Query params:
      token – the "imported:<id>" token returned by /api/import_player

    Returns:
      { "positions": ["LW","RW"], "position": "LW", "position_line": "Front-Line", "is_gk": false }
    """
    token = request.args.get('token', '').strip()
    if not token or not is_import_token(token):
        return jsonify({'error': 'valid import token required'}), 400

    rec = store_get(token_id(token))
    if rec is None:
        return jsonify({'error': 'imported player expired — re-upload'}), 404

    try:
        profile   = rec.get('profile', {}).get('player', {})
        positions = profile.get('positionsDetailed') or rec.get('positions', [])
        positions = [str(p).upper() for p in positions if p]
        if not positions:
            broad = profile.get('position', '')
            positions = [broad.upper()] if broad else []

        primary  = positions[0] if positions else ''
        is_gk    = primary == 'GK'
        pos_line = get_position_line(positions)

        return jsonify({
            'positions':     positions,
            'position':      primary,
            'position_line': pos_line,
            'is_gk':         is_gk,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/')
def home():
    now = _time.time()
    if _home_stats_cache['data'] is None or now - _home_stats_cache['ts'] > _HOME_STATS_TTL:
        _logger.info(f"home_stats_cache MISS — scanning output/ directory")
        _home_stats_cache['data'] = get_home_stats(OUTPUT_DIR)
        _home_stats_cache['ts']   = now
        _logger.debug(f"home_stats_cache LOADED — {_home_stats_cache['data']}")
    else:
        age = now - _home_stats_cache['ts']
        _logger.debug(f"home_stats_cache HIT (age={age:.1f}s)")
    return render_template('home.html', stats=_home_stats_cache['data'])


@bp.route('/competitions')
def index():
    _logger.debug("Fetching all competitions")
    competitions = get_all_competitions(OUTPUT_DIR)
    _logger.debug(f"Loaded {len(competitions)} competitions")
    return render_template('index.html', competitions=competitions)


@bp.route('/competition/<country>/<competition>')
def competition(country, competition):
    data = get_competition(OUTPUT_DIR, country, competition)
    from tactical_match_engine.services.json_loader import get_league_intensity
    cvs_score = round(get_league_intensity(country.replace('_', ' '), competition.replace('_', ' ')) * 100, 1)
    comp_type = data.get('competition_type', 'league')
    return render_template('competition.html', data=data, country=country, competition=competition,
                           cvs_score=cvs_score, comp_type=comp_type)


@bp.route('/competition/<country>/<competition>/playing-styles')
def playing_styles(country, competition):
    _logger.info(f"playing_styles route: country={country}, competition={competition}")
    country_name = country.replace('_', ' ')
    competition_name = competition.replace('_', ' ')
    return render_template('team_playing_style.html',
                         country=country,
                         competition=competition,
                         country_name=country_name,
                         competition_name=competition_name)


@bp.route('/competition/<country>/<competition>/<club>')
def club(country, competition, club):
    club = club.replace('-', '_')
    data    = get_club(OUTPUT_DIR, country, competition, club)
    players = get_club_players(OUTPUT_DIR, country, competition, club)
    return render_template('club.html', data=data, players=players,
                           country=country, competition=competition, club=club)


# ── Team contribution breakdown ───────────────────────────────────────────────
# Each entry: (display label, player field, team field). Grouped into the three
# categories rendered on player.html. Crosses live under Possession by design.
_CONTRIB_CATEGORIES = [
    ('Possession', [
        ('Total Passes',        'totalPasses',       'totalPasses'),
        ('Accurate Passes',     'accuratePasses',    'accuratePasses'),
        ('Dribbles',            'successfulDribbles','successfulDribbles'),
        ('Long Balls',          'totalLongBalls',    'totalLongBalls'),
        ('Accurate Long Balls', 'accurateLongBalls', 'accurateLongBalls'),
        ('Crosses',             'totalCross',        'totalCrosses'),
        ('Accurate Crosses',    'accurateCrosses',   'accurateCrosses'),
    ]),
    ('Attack', [
        ('Goals',         'goals',        'goalsScored'),
        ('Assists',       'assists',      'assists'),
        ('Shots',         'totalShots',   'shots'),
        ('Penalty Goals', 'penaltyGoals', 'penaltyGoals'),
    ]),
    ('Defense', [
        ('Aerial Duels Won', 'aerialDuelsWon', 'aerialDuelsWon'),
        ('Duels Won',        'totalDuelsWon',  'duelsWon'),
        ('Ball Recoveries',  'ballRecovery',   'ballRecovery'),
        ('Fouls',            'fouls',          'fouls'),
    ]),
]


def build_team_contribution(player_stats, team_stats):
    """Compute the player's share of team season totals, grouped by category.

    Returns None when team data is unavailable so the card can hide entirely.
    Contribution % = player_total ÷ team_total × 100 (raw, no per-90).
    Rows are kept only when both totals are present and the team total > 0;
    rows within a category are sorted by contribution % descending.
    """
    if not player_stats or not team_stats:
        return None

    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    # Minutes / matches context for per-90 conversion.
    # Player per-90 = player_total ÷ minutes × 90 (the player's own rate).
    # Team  per-90 = team_total ÷ matches (a team plays 90 min per match, so
    # this is the team's per-match output — the natural "per 90" for a side).
    p_mins    = _num(player_stats.get('minutesPlayed'))
    t_matches = _num(team_stats.get('matches'))

    categories = []
    any_rows = False
    for cat_label, metrics in _CONTRIB_CATEGORIES:
        rows = []
        p_sum = 0.0
        t_sum = 0.0
        for label, p_key, t_key in metrics:
            p_val = _num(player_stats.get(p_key))
            t_val = _num(team_stats.get(t_key))
            if p_val is None or t_val is None or t_val <= 0:
                continue
            pct = p_val / t_val * 100.0
            tier = 'high' if pct > 25 else ('mid' if pct >= 10 else 'low')
            rows.append({
                'label':      label,
                'player':     int(p_val) if p_val == int(p_val) else round(p_val, 1),
                'team':       int(t_val) if t_val == int(t_val) else round(t_val, 1),
                'pct':        round(pct, 1),
                'tier':       tier,
                'p90_player': round(p_val / p_mins * 90.0, 2) if p_mins and p_mins > 0 else None,
                'p90_team':   round(t_val / t_matches, 2) if t_matches and t_matches > 0 else None,
            })
            p_sum += p_val
            t_sum += t_val
        if not rows:
            continue
        rows.sort(key=lambda r: r['pct'], reverse=True)
        agg = round(p_sum / t_sum * 100.0, 1) if t_sum > 0 else None
        categories.append({'label': cat_label, 'rows': rows, 'aggregate': agg})
        any_rows = True

    if not any_rows:
        return None

    # Minutes context: share of available minutes (team matches × 90)
    minutes_share = None
    if p_mins is not None and t_matches and t_matches > 0:
        minutes_share = round(p_mins / (t_matches * 90.0) * 100.0, 1)

    return {
        'categories':      categories,
        'minutes_played':  int(p_mins) if p_mins is not None else None,
        'minutes_share':   minutes_share,
    }


@bp.route('/player/<country>/<competition>/<club>/<player_file>')
def player(country, competition, club, player_file):
    club = club.replace('-', '_')
    data         = get_player(OUTPUT_DIR, country, competition, club, player_file)
    heatmap_path = get_heatmap_path(OUTPUT_DIR, country, competition, club, player_file)
    from tactical_match_engine.services.json_loader import get_league_intensity
    cvs_score = round(get_league_intensity(country.replace('_', ' '), competition.replace('_', ' ')) * 100, 1)
    # Build player path for quick-links
    player_path = f"{country}/{competition}/{club}/{player_file}"
    # Match data enrichment
    player_id = data.get('player_id', '')
    match_per90  = None
    match_count  = 0
    if player_id:
        try:
            _appearances = get_player_match_history(int(player_id), MATCH_OUTPUT_DIR)
            match_count  = len(_appearances)
            if match_count > 0:
                match_per90 = compute_per90(_appearances)
        except Exception:
            pass
    # Team contribution breakdown (player season total ÷ team season total)
    contribution = None
    try:
        club_data  = get_club(OUTPUT_DIR, country, competition, club)
        team_stats = (club_data or {}).get('season_statistics', {}).get('statistics', {})
        contribution = build_team_contribution(
            data.get('statistics', {}).get('statistics', {}), team_stats)
    except Exception:
        contribution = None
    return render_template('player.html', data=data, heatmap=heatmap_path,
                           country=country, competition=competition, club=club,
                           cvs_score=cvs_score, player_path=player_path,
                           match_per90=match_per90, match_count=match_count,
                           contribution=contribution)


@bp.route('/players')
def players():
    competitions = get_all_competitions(OUTPUT_DIR)
    return render_template('players.html', competitions=competitions)


@bp.route('/api/players')
def api_players():
    filters = {
        'competition': request.args.get('competition', ''),
        'club':        request.args.get('club', ''),
        'position':    request.args.get('position', ''),
        'specific_pos': request.args.get('specific_pos', ''),
        'nationality': request.args.get('nationality', ''),
        'age_min':     request.args.get('age_min', ''),
        'age_max':     request.args.get('age_max', ''),
        'apps_min':    request.args.get('apps_min', ''),
        'rating_min':  request.args.get('rating_min', ''),
        'goals_min':   request.args.get('goals_min', ''),
        'assists_min': request.args.get('assists_min', ''),
        # Advanced
        'pass_acc_min':    request.args.get('pass_acc_min', ''),
        'duels_min':       request.args.get('duels_min', ''),
        'aerial_min':      request.args.get('aerial_min', ''),
        'dribbles_min':    request.args.get('dribbles_min', ''),
        'tackles_min':     request.args.get('tackles_min', ''),
        'key_passes_min':  request.args.get('key_passes_min', ''),
        'sort_by':         request.args.get('sort_by', 'rating'),
        'sort_dir':        request.args.get('sort_dir', 'desc'),
        'page':            int(request.args.get('page', 1)),
        'per_page':        int(request.args.get('per_page', 50)),
    }
    result = search_players(OUTPUT_DIR, filters)
    return jsonify(result)


@bp.route('/api/search')
def api_search():
    q = request.args.get('q', '').strip().lower()
    if not q:
        return jsonify({'players': [], 'clubs': [], 'leagues': []})

    results = {'players': [], 'clubs': [], 'leagues': []}
    max_results = 5

    try:
        # Search players by name. Let the loader filter across ALL players
        # (query=q) and build the correct path itself — previously this called
        # get_all_players_flat() with no query, so only the first 20 players
        # (alphabetically) were ever considered, and the result mapping used
        # keys the loader doesn't return (blank position/club, broken path).
        for player in get_all_players_flat(OUTPUT_DIR, query=q, limit=max_results):
            results['players'].append({
                'name':        player.get('name', ''),
                'position':    player.get('position', ''),
                'club':        player.get('team', ''),       # loader's field is 'team'
                'competition': player.get('competition', ''),
                'path':        player.get('path', ''),        # already built by the loader
            })

        # Search clubs by name
        all_clubs = get_all_clubs_flat(OUTPUT_DIR, query=q, limit=max_results)
        if isinstance(all_clubs, dict) and 'clubs' in all_clubs:
            results['clubs'] = all_clubs['clubs'][:max_results]

        # Search competitions by name
        all_competitions = get_all_competitions(OUTPUT_DIR)
        for comp in all_competitions:
            if len(results['leagues']) >= max_results:
                break
            comp_name = comp.get('competition', '').lower()
            country_name = comp.get('country', '').lower()
            if q in comp_name or q in country_name:
                results['leagues'].append({
                    'name': f"{comp.get('competition', '')} ({comp.get('country', '')})",
                    'competition': comp.get('competition', ''),
                    'country': comp.get('country', ''),
                    'path': f"{comp.get('country_slug', '')}/{comp.get('competition_slug', '')}"
                })

    except Exception as e:
        _logger.warning(f"Search error for query '{q}': {e}")

    return jsonify(results)


@bp.route('/api/player_detail')
def api_player_detail():
    path = request.args.get('path', '')
    parts = path.split('/')
    if len(parts) == 4:
        data = get_player(OUTPUT_DIR, parts[0], parts[1], parts[2], parts[3])
        heatmap = get_heatmap_path(OUTPUT_DIR, parts[0], parts[1], parts[2], parts[3])
        return jsonify({'data': data, 'heatmap': heatmap})
    return jsonify({'error': 'invalid path'}), 400


@bp.route('/api/player_info')
def api_player_info():
    """
    Return lightweight position metadata for a single player by path.
    Used by club_compatibility.html to auto-suggest the role profile
    immediately after a player is selected from the search dropdown.

    Query params:
      path – "Country/Competition/Club/filename.json"

    Returns:
      {
        "positions":     ["LW", "RW"],   // positionsDetailed codes
        "position":      "LW",           // primary (first) code
        "position_line": "Front-Line",   // Back-Line | Mid-Line | Front-Line | Unknown
        "is_gk":         false
      }
    """
    path = request.args.get('path', '').strip()
    if not path:
        return jsonify({'error': 'path is required'}), 400

    parts = path.split('/')
    if len(parts) != 4:
        return jsonify({'error': 'invalid player path — expected Country/Competition/Club/filename'}), 400

    try:
        from tactical_match_engine.services.json_loader import load_sofascore_player
        full = os.path.join(
            OUTPUT_DIR,
            parts[0], parts[1], parts[2], 'Players',
            parts[3] if parts[3].endswith('.json') else parts[3] + '.json'
        )
        if not os.path.exists(full):
            return jsonify({'error': 'player file not found'}), 404

        pd        = load_sofascore_player(full)
        positions = [str(p).upper() for p in pd.get('positions', [pd.get('position', '')])]
        primary   = positions[0] if positions else ''
        is_gk     = primary == 'GK'
        pos_line  = get_position_line(positions)

        return jsonify({
            'positions':     positions,
            'position':      primary,
            'position_line': pos_line,
            'is_gk':         is_gk,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/league_position_avg')
def api_league_position_avg():
    """
    Return league-wide average stats for players sharing the same position as
    the given player (excluding the player themselves).

    Query params:
      player   – "Country/Competition/Club/filename.json"
      pos_mode – 'exact' (default) | 'grouped'
    """
    player_path = request.args.get('player', '').strip()
    pos_mode    = request.args.get('pos_mode', 'exact')
    if pos_mode not in ('exact', 'grouped'):
        pos_mode = 'exact'
    if not player_path:
        return jsonify({'error': 'player path required'}), 400

    from data_loader import get_league_position_avg
    result = get_league_position_avg(OUTPUT_DIR, player_path, pos_mode)
    if result is None:
        return jsonify({'error': 'player not found or invalid path'}), 404
    if 'error' in result:
        return jsonify(result), 400
    return jsonify(result)


@bp.route('/api/player_search')
def api_player_search():
    q      = request.args.get('q', '').lower()
    result = get_all_players_flat(OUTPUT_DIR, query=q, limit=20)
    return jsonify(result)


@bp.route('/compare')
def compare():
    return render_template('compare.html')
