import sys
import os
import logging
import csv
import io

# ── Logging: stdout only (no file writes on Vercel) ────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)-8s] %(name)s | %(message)s',
    stream=sys.stdout,
)
logging.getLogger('werkzeug').setLevel(logging.WARNING)
logging.getLogger('pymongo').setLevel(logging.WARNING)
_logger = logging.getLogger(__name__)

# ── Path setup ─────────────────────────────────────────────────────────────────
# api/index.py  →  vercel_app/api/  →  vercel_app/  →  Coding/  →  project root
_API_DIR        = os.path.dirname(os.path.abspath(__file__))
_VERCEL_DIR     = os.path.dirname(_API_DIR)
_CODING_DIR     = os.path.dirname(_VERCEL_DIR)
_WEBAPP_PATH    = os.path.join(_CODING_DIR, 'webapp')
_WORKSPACE_ROOT = os.path.dirname(_CODING_DIR)

for _p in [_WEBAPP_PATH, _CODING_DIR, _WORKSPACE_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for
from werkzeug.exceptions import HTTPException
import mongo_loader as _loader

# Bind data-layer functions
get_all_competitions = _loader.get_all_competitions
get_competition      = _loader.get_competition
get_club             = _loader.get_club
get_club_players     = _loader.get_club_players
get_player           = _loader.get_player
search_players       = _loader.search_players
get_all_players_flat = _loader.get_all_players_flat
get_all_clubs_flat   = _loader.get_all_clubs_flat
get_heatmap_path     = _loader.get_heatmap_path
get_home_stats       = _loader.get_home_stats

# ── Path resolution ────────────────────────────────────────────────────────────
def _resolve_dir(env_key: str, *rel_parts) -> str:
    v = os.environ.get(env_key)
    return v if v else os.path.normpath(os.path.join(_VERCEL_DIR, *rel_parts))

_WEBAPP_STATIC    = _resolve_dir('TACTICAL_WEBAPP_DIR', '..', 'webapp')
OUTPUT_DIR        = _resolve_dir('TACTICAL_OUTPUT_DIR', '..', 'output')

app = Flask(
    __name__,
    template_folder=os.path.join(_WEBAPP_STATIC, 'templates'),
    static_folder=os.path.join(_WEBAPP_STATIC, 'static'),
)

# ── Ad-hoc player import ───────────────────────────────────────────────────────
from imported_player import (
    register_import_routes, store_get, is_import_token, token_id,
    build_engine_candidate,
)
register_import_routes(app)

# ── Request logging ────────────────────────────────────────────────────────────
import time as _timing

@app.before_request
def _log_request():
    request.start_time = _timing.time()
    _logger.info(f"[REQ] {request.method} {request.path}")

@app.after_request
def _log_response(response):
    elapsed = _timing.time() - getattr(request, 'start_time', _timing.time())
    _logger.info(f"[RES] {request.method} {request.path} → {response.status_code} ({elapsed:.3f}s)")
    return response

@app.errorhandler(Exception)
def _log_error(error):
    # Preserve normal HTTP errors (404, 405, ...) with their own status codes.
    if isinstance(error, HTTPException):
        return error

    _logger.error(f"[ERR] {error}", exc_info=True)

    # Most failures here are the data backend (MongoDB) being unreachable on a
    # frontend-only deploy. Serve a friendly page for page routes, JSON for APIs,
    # so the frontend stays browsable instead of showing a raw 500.
    wants_json = (
        request.path.startswith('/api')
        or 'application/json' in (request.headers.get('Accept') or '')
    )
    if wants_json:
        return jsonify({'error': 'Backend data service unavailable'}), 503
    try:
        return render_template('backend_unavailable.html'), 503
    except Exception:
        return jsonify({'error': 'Internal server error'}), 500

@app.context_processor
def _inject_globals():
    return {'using_mongo': True}

# ── Auth stubs (frontend-only deploy) ──────────────────────────────────────────
# base.html references url_for('signin'|'signup'|'logout'). The real auth routes
# are MongoDB-backed and disabled here, so register no-op stubs that render the
# forms and keep every page's navbar resolvable. No accounts are created.
@app.route('/signin', methods=['GET', 'POST'])
def signin():
    if request.method == 'POST':
        return redirect(url_for('home'))
    return render_template('signin.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        return redirect(url_for('home'))
    return render_template('signup.html')

@app.route('/logout')
def logout():
    return redirect(url_for('home'))

# ── Position helpers ───────────────────────────────────────────────────────────
POSITION_LINE = {
    "GK": "Back-Line",  "DC": "Back-Line",  "DL": "Back-Line",
    "DR": "Back-Line",  "WBL": "Back-Line", "WBR": "Back-Line",
    "DM": "Mid-Line",   "MC": "Mid-Line",   "ML": "Mid-Line",
    "MR": "Mid-Line",   "AM": "Mid-Line",
    "LW": "Front-Line", "RW": "Front-Line", "ST": "Front-Line",
    "SS": "Front-Line",
}

_LINE_BROAD_POS = {
    '1. Back-Line':  {'DC', 'DL', 'DR', 'WBL', 'WBR', 'CB'},
    '2. Mid-Line':   {'DM', 'MC', 'AM', 'ML', 'MR', 'CM'},
    '3. Front-Line': {'LW', 'RW', 'ST', 'CF', 'SS', 'FW'},
}

def get_position_line(positions: list) -> str:
    counts = {}
    for pos in positions:
        line = POSITION_LINE.get(str(pos).upper())
        if line:
            counts[line] = counts.get(line, 0) + 1
    if not counts:
        return 'Unknown'
    keys = list(counts.keys())
    return max(counts, key=lambda k: (counts[k], -keys.index(k)))

# ── MongoDB-backed engine helpers ──────────────────────────────────────────────

def _load_player_for_engine_mongo(country: str, competition: str, club: str, fname: str):
    """Load a player from MongoDB and return an engine-compatible dict."""
    fname = fname if fname.endswith('.json') else fname + '.json'
    doc = _loader._db().players.find_one({
        '_country': country, '_competition': competition,
        '_club': club, '_file': fname,
    })
    return _loader.load_sofascore_player_for_engine(doc) if doc else None


def _get_squad_role_analysis_mongo(country, competition, club, role_path,
                                   cand_line=None, exclude_file=None):
    """Score every squad player at *club* against *role_path* using MongoDB."""
    from tactical_match_engine.engine.role_encoder import get_role_fitness_vector

    empty = {
        'players': [], 'avg_overall': 0.0,
        'avg_category_scores': {}, 'avg_metric_scores': {},
        'avg_metric_raw': {}, 'player_count': 0,
    }

    query = {'_country': country, '_competition': competition, '_club': club}
    if exclude_file:
        query['_file'] = {'$ne': exclude_file}

    squad = []
    for doc in _loader._db().players.find(query):
        try:
            pd = _loader.load_sofascore_player_for_engine(doc)
        except Exception:
            continue

        player_positions = [str(p).upper() for p in pd.get('positions', [pd.get('position', '')])]
        if cand_line and get_position_line(player_positions) != cand_line:
            continue

        res = get_role_fitness_vector(pd['raw_stats'], pd['position'],
                                      role_path=role_path, flat_weights=True)
        if res is None:
            continue

        squad.append({
            'name':            pd['name'],
            'position':        pd['position'],
            'positions':       pd.get('positions', []),
            'age':             pd['age'],
            'overall_score':   res['overall_score'],
            'category_scores': res['category_scores'],
            'metric_details':  res['metric_details'],
            'rating':          round(float(pd['raw_stats'].get('rating', 0) or 0), 2),
            'appearances':     int(pd['raw_stats'].get('appearances', 0) or 0),
        })

    if not squad:
        return empty

    avg_overall = sum(p['overall_score'] for p in squad) / len(squad)

    cat_buckets = {}
    for p in squad:
        for cat, score in p['category_scores'].items():
            cat_buckets.setdefault(cat, []).append(score)
    avg_cat = {cat: round(sum(v) / len(v), 2) for cat, v in cat_buckets.items()}

    ms_buckets, mr_buckets = {}, {}
    for p in squad:
        for m in p['metric_details']:
            ms_buckets.setdefault(m['name'], []).append(m['score'])
            mr_buckets.setdefault(m['name'], []).append(m['raw_value'])

    return {
        'players':             sorted(squad, key=lambda x: x['overall_score'], reverse=True),
        'avg_overall':         round(avg_overall, 2),
        'avg_category_scores': avg_cat,
        'avg_metric_scores':   {k: round(sum(v) / len(v), 2) for k, v in ms_buckets.items()},
        'avg_metric_raw':      {k: round(sum(v) / len(v), 4) for k, v in mr_buckets.items()},
        'player_count':        len(squad),
    }


def _build_role_pos_codes():
    from tactical_match_engine.engine.role_encoder import POSITION_CODE_TO_ROLE_FILE
    mapping = {}
    for code, rel_path in POSITION_CODE_TO_ROLE_FILE.items():
        mapping.setdefault(rel_path, set()).add(code.upper())
    return mapping

_ROLE_TO_POS_CODES = None

def _pos_codes_for_role(role_path):
    global _ROLE_TO_POS_CODES
    if _ROLE_TO_POS_CODES is None:
        _ROLE_TO_POS_CODES = _build_role_pos_codes()
    return _ROLE_TO_POS_CODES.get(role_path.replace('\\', '/'), set())

# ── Home ───────────────────────────────────────────────────────────────────────
import time as _time
_home_stats_cache = {'data': None, 'ts': 0}
_HOME_STATS_TTL = 300

@app.route('/')
def home():
    now = _time.time()
    if _home_stats_cache['data'] is None or now - _home_stats_cache['ts'] > _HOME_STATS_TTL:
        try:
            _home_stats_cache['data'] = get_home_stats(OUTPUT_DIR)
            _home_stats_cache['ts'] = now
        except Exception as e:
            # Backend unavailable — render the landing shell with empty stats
            # rather than 500. Don't cache, so it recovers once the DB is up.
            _logger.warning(f"home stats unavailable: {e}")
            return render_template(
                'home.html',
                stats={'competitions': 0, 'clubs': 0, 'players': 0, 'top_players': []},
            )
    return render_template('home.html', stats=_home_stats_cache['data'])

# ── Competitions ───────────────────────────────────────────────────────────────
@app.route('/competitions')
def index():
    competitions = get_all_competitions(OUTPUT_DIR)
    return render_template('index.html', competitions=competitions)

@app.route('/competition/<country>/<competition>')
def competition(country, competition):
    data = get_competition(OUTPUT_DIR, country, competition)
    from tactical_match_engine.services.json_loader import get_league_intensity
    cvs_score = round(get_league_intensity(country.replace('_', ' '), competition.replace('_', ' ')) * 100, 1)
    return render_template('competition.html', data=data, country=country,
                           competition=competition, cvs_score=cvs_score)

@app.route('/competition/<country>/<competition>/playing-styles')
def playing_styles(country, competition):
    return render_template('team_playing_style.html',
                           country=country, competition=competition,
                           country_name=country.replace('_', ' '),
                           competition_name=competition.replace('_', ' '))

# ── Club & Player ──────────────────────────────────────────────────────────────
@app.route('/competition/<country>/<competition>/<club>')
def club(country, competition, club):
    club = club.replace('-', '_')
    data    = get_club(OUTPUT_DIR, country, competition, club)
    players = get_club_players(OUTPUT_DIR, country, competition, club)
    return render_template('club.html', data=data, players=players,
                           country=country, competition=competition, club=club)

@app.route('/player/<country>/<competition>/<club>/<player_file>')
def player(country, competition, club, player_file):
    club = club.replace('-', '_')
    data         = get_player(OUTPUT_DIR, country, competition, club, player_file)
    heatmap_path = get_heatmap_path(OUTPUT_DIR, country, competition, club, player_file)
    from tactical_match_engine.services.json_loader import get_league_intensity
    cvs_score  = round(get_league_intensity(country.replace('_', ' '), competition.replace('_', ' ')) * 100, 1)
    player_path = f"{country}/{competition}/{club}/{player_file}"

    player_id = data.get('player_id', '')
    match_count, match_per90 = 0, None
    if player_id:
        try:
            matches = list(_loader._db().matches.find(
                {'$or': [{'home_players.player_id': int(player_id)},
                         {'away_players.player_id': int(player_id)}]},
                {'_id': 0, 'match_date': 1}
            ).sort('match_date', -1).limit(30))
            match_count = len(matches)
        except Exception:
            pass

    return render_template('player.html', data=data, heatmap=heatmap_path,
                           country=country, competition=competition, club=club,
                           cvs_score=cvs_score, player_path=player_path,
                           match_per90=match_per90, match_count=match_count)

# ── Heatmaps from MongoDB ──────────────────────────────────────────────────────
@app.route('/heatmap_db/<country>/<competition>/<club>/<stem>')
def heatmap_db(country, competition, club, stem):
    data = _loader.get_heatmap_bytes(country, competition, club, stem)
    if data:
        return send_file(io.BytesIO(data), mimetype='image/png')
    return '', 404

# ── Players search ─────────────────────────────────────────────────────────────
@app.route('/players')
def players():
    competitions = get_all_competitions(OUTPUT_DIR)
    return render_template('players.html', competitions=competitions)

@app.route('/api/players')
def api_players():
    filters = {
        'competition':    request.args.get('competition', ''),
        'club':           request.args.get('club', ''),
        'position':       request.args.get('position', ''),
        'specific_pos':   request.args.get('specific_pos', ''),
        'nationality':    request.args.get('nationality', ''),
        'age_min':        request.args.get('age_min', ''),
        'age_max':        request.args.get('age_max', ''),
        'apps_min':       request.args.get('apps_min', ''),
        'rating_min':     request.args.get('rating_min', ''),
        'goals_min':      request.args.get('goals_min', ''),
        'assists_min':    request.args.get('assists_min', ''),
        'pass_acc_min':   request.args.get('pass_acc_min', ''),
        'duels_min':      request.args.get('duels_min', ''),
        'aerial_min':     request.args.get('aerial_min', ''),
        'dribbles_min':   request.args.get('dribbles_min', ''),
        'tackles_min':    request.args.get('tackles_min', ''),
        'key_passes_min': request.args.get('key_passes_min', ''),
        'sort_by':        request.args.get('sort_by', 'rating'),
        'sort_dir':       request.args.get('sort_dir', 'desc'),
        'page':           int(request.args.get('page', 1)),
        'per_page':       int(request.args.get('per_page', 50)),
    }
    return jsonify(search_players(OUTPUT_DIR, filters))

# ── Compatibility ──────────────────────────────────────────────────────────────
@app.route('/compatibility')
def compatibility():
    return render_template('compatibility.html')

@app.route('/api/compatibility')
def api_compatibility():
    player_path = request.args.get('player', '')
    role_file   = request.args.get('role', '')
    if not player_path or not role_file:
        return jsonify({'error': 'player and role are required'}), 400

    parts = player_path.split('/')
    if len(parts) != 4:
        return jsonify({'error': 'invalid player path'}), 400

    try:
        from tactical_match_engine.engine.role_encoder import get_role_fitness_vector
        player_data = _load_player_for_engine_mongo(parts[0], parts[1], parts[2], parts[3])
        if player_data is None:
            return jsonify({'error': 'player not found'}), 404
        result = get_role_fitness_vector(
            player_data['raw_stats'], player_data['position'], role_path=role_file
        )
        if result is None:
            return jsonify({'error': 'no role profile for this position'}), 404

        return jsonify({
            'player_name':      player_data['name'],
            'player_position':  player_data['position'],
            'player_positions': player_data.get('positions', []),
            'player_age':       player_data['age'],
            'player_team':      parts[2].replace('_', ' '),
            'league_intensity': round(player_data['current_league_intensity'], 4),
            'role_name':        result['role_profile']['position'],
            'overall_score':    result['overall_score'],
            'category_scores':  result['category_scores'],
            'weights':          result['weights'],
            'metric_details':   result['metric_details'],
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/compatibility_avg')
def api_compatibility_avg():
    player_path = request.args.get('player', '').strip()
    role_file   = request.args.get('role', '').strip()
    if not player_path or not role_file:
        return jsonify({'error': 'player and role are required'}), 400

    parts = player_path.split('/')
    if len(parts) != 4:
        return jsonify({'error': 'invalid player path'}), 400

    country, competition, club_slug, filename = parts
    target_file = filename if filename.endswith('.json') else filename + '.json'
    role_line   = role_file.split('/')[0]
    line_codes  = _LINE_BROAD_POS.get(role_line, set())

    try:
        from tactical_match_engine.engine.role_encoder import get_role_fitness_vector

        pool_results = []
        for doc in _loader._db().players.find({'_country': country, '_competition': competition}):
            if doc.get('_club') == club_slug and doc.get('_file') == target_file:
                continue
            try:
                pd = _loader.load_sofascore_player_for_engine(doc)
            except Exception:
                continue
            pos = [str(p).upper() for p in pd.get('positions', [pd.get('position', '')])]
            if line_codes and not any(p in line_codes for p in pos):
                continue
            res = get_role_fitness_vector(pd['raw_stats'], pd['position'], role_path=role_file)
            if res is not None:
                pool_results.append(res)

        pool_size = len(pool_results)
        if pool_size == 0:
            return jsonify({'avg_pool_size': 0, 'overall_score': 0,
                            'category_scores': {}, 'weights': {}, 'metric_details': []})

        avg_overall = round(sum(r['overall_score'] for r in pool_results) / pool_size, 2)
        cat_buckets = {}
        for r in pool_results:
            for cat, sc in r['category_scores'].items():
                cat_buckets.setdefault(cat, []).append(sc)
        avg_cats = {cat: round(sum(v) / len(v), 2) for cat, v in cat_buckets.items()}

        ms_buckets, mr_buckets = {}, {}
        for r in pool_results:
            for m in r['metric_details']:
                ms_buckets.setdefault(m['name'], []).append(m['score'])
                mr_buckets.setdefault(m['name'], []).append(m['raw_value'])

        avg_metrics = [
            {
                'name':             m['name'],
                'category':         m['category'],
                'higher_is_better': m['higher_is_better'],
                'raw_value':        round(sum(mr_buckets[m['name']]) / len(mr_buckets[m['name']]), 4),
                'score':            round(sum(ms_buckets[m['name']]) / len(ms_buckets[m['name']]), 2),
            }
            for m in pool_results[0]['metric_details']
        ]

        return jsonify({'avg_pool_size': pool_size, 'overall_score': avg_overall,
                        'category_scores': avg_cats, 'weights': pool_results[0]['weights'],
                        'metric_details': avg_metrics})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── Club Compatibility ─────────────────────────────────────────────────────────
@app.route('/club_compatibility')
def club_compatibility():
    return render_template('club_compatibility.html',
                           competitions=get_all_competitions(OUTPUT_DIR))

@app.route('/api/club_search')
def api_club_search():
    q      = request.args.get('q', '').lower()
    league = request.args.get('league', '')
    return jsonify(get_all_clubs_flat(OUTPUT_DIR, query=q, limit=20, league_filter=league))

# ── Unified search ─────────────────────────────────────────────────────────────
@app.route('/api/search')
def api_search():
    q = request.args.get('q', '').strip().lower()
    if not q:
        return jsonify({'players': [], 'clubs': [], 'leagues': []})

    results = {'players': [], 'clubs': [], 'leagues': []}
    max_r = 5
    try:
        for p in get_all_players_flat(OUTPUT_DIR):
            if len(results['players']) >= max_r:
                break
            if q in p.get('name', '').lower():
                results['players'].append({
                    'name': p['name'], 'position': p.get('position', ''),
                    'club': p.get('club', ''), 'competition': p.get('competition', ''),
                    'country': p.get('country', ''),
                    'path': p.get('path', ''),
                })
        clubs = get_all_clubs_flat(OUTPUT_DIR, query=q, limit=max_r)
        results['clubs'] = clubs[:max_r] if isinstance(clubs, list) else []
        for comp in get_all_competitions(OUTPUT_DIR):
            if len(results['leagues']) >= max_r:
                break
            if q in comp.get('competition', '').lower() or q in comp.get('country', '').lower():
                results['leagues'].append({
                    'name': f"{comp.get('competition','')} ({comp.get('country','')})",
                    'competition': comp.get('competition', ''),
                    'country': comp.get('country', ''),
                    'path': f"{comp.get('country','')}_{comp.get('competition','')}",
                })
    except Exception as e:
        _logger.warning(f"Search error for '{q}': {e}")
    return jsonify(results)

# ── League Suitability ─────────────────────────────────────────────────────────
@app.route('/api/league_suitability')
def api_league_suitability():
    player_path   = request.args.get('player', '').strip()
    role_file     = request.args.get('role', '').strip()
    source_league = request.args.get('source_league', '').strip()

    if not player_path or not role_file:
        return jsonify({'error': 'player and role are required'}), 400

    _is_import = is_import_token(player_path)
    if not _is_import:
        p_parts = player_path.split('/')
        if len(p_parts) != 4:
            return jsonify({'error': 'invalid player path'}), 400
        player_fname = p_parts[3] if p_parts[3].endswith('.json') else p_parts[3] + '.json'

    try:
        from tactical_match_engine.services.json_loader import get_league_info
        from tactical_match_engine.engine.role_encoder import get_role_fitness_vector
        from tactical_match_engine.engine.physical_adaptation import calculate_physical_adaptation

        if _is_import:
            rec = store_get(token_id(player_path))
            if rec is None:
                return jsonify({'error': 'imported player expired — re-upload'}), 404
            cand = build_engine_candidate(rec, source_league, get_league_info)
        else:
            cand = _load_player_for_engine_mongo(p_parts[0], p_parts[1], p_parts[2], player_fname)
            if cand is None:
                return jsonify({'error': 'player not found'}), 404

        cand_result = get_role_fitness_vector(
            cand['raw_stats'], cand['position'], role_path=role_file, flat_weights=True
        )
        if cand_result is None:
            return jsonify({'error': 'no role profile available for this position'}), 404

        role_fitness       = cand_result['overall_score']
        player_intensity   = cand['current_league_intensity']
        player_global_rank = cand.get('current_league_global_rank')
        player_league_name = cand.get('current_league_name', '')
        role_name          = cand_result.get('role_profile', {}).get('position', role_file)
        cand_positions     = [str(p).upper() for p in cand.get('positions', [cand['position']])]
        cand_line          = get_position_line(cand_positions)

        # Get distinct leagues from MongoDB standings
        leagues = []
        db = _loader._db()
        seen_leagues = set()
        for std in db.standings.find({}, {'_country': 1, '_competition': 1}):
            c_name    = std.get('_country', '')
            comp_name = std.get('_competition', '')
            key = (c_name, comp_name)
            if key in seen_leagues:
                continue
            seen_leagues.add(key)

            t_info           = get_league_info(c_name.replace('_', ' '), comp_name.replace('_', ' '))
            target_intensity = t_info['intensity']
            target_rank      = t_info.get('global_rank')

            player_count = db.players.count_documents({
                '_country': c_name, '_competition': comp_name,
            })

            league_adaptation = round(
                calculate_physical_adaptation(player_intensity, target_intensity) * 100.0, 2
            )
            suitability = round(0.6 * role_fitness + 0.4 * league_adaptation, 2)

            if player_global_rank and target_rank:
                rank_diff = player_global_rank - target_rank
                if rank_diff > 5:    move_label = f'+{rank_diff} ranks up'
                elif rank_diff < -5: move_label = f'{abs(rank_diff)} ranks down'
                else:                move_label = 'lateral move'
            else:
                move_label = ''

            leagues.append({
                'league':             comp_name.replace('_', ' '),
                'league_key':         f"{c_name}/{comp_name}",
                'country':            c_name.replace('_', ' '),
                'suitability_score':  suitability,
                'league_intensity':   round(target_intensity, 4),
                'global_rank':        target_rank,
                'role_fitness':       round(role_fitness, 2),
                'league_adaptation':  league_adaptation,
                'move_label':         move_label,
                'player_count':       player_count,
            })

        leagues.sort(key=lambda x: x['suitability_score'], reverse=True)
        return jsonify({
            'player_name':              cand['name'],
            'role_name':                role_name,
            'player_league_name':       player_league_name,
            'player_global_rank':       player_global_rank,
            'player_league_intensity':  round(player_intensity, 4),
            'leagues':                  leagues,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── Club Suitability ───────────────────────────────────────────────────────────
@app.route('/api/club_suitability')
def api_club_suitability():
    player_path   = request.args.get('player', '').strip()
    league        = request.args.get('league', '').strip()
    role_file     = request.args.get('role', '').strip()
    source_league = request.args.get('source_league', '').strip()

    if not player_path or not league or not role_file:
        return jsonify({'error': 'player, league, and role are required'}), 400

    l_parts = league.split('/')
    if len(l_parts) != 2:
        return jsonify({'error': 'league must be Country/Competition'}), 400

    _is_import = is_import_token(player_path)
    if not _is_import:
        p_parts = player_path.split('/')
        if len(p_parts) != 4:
            return jsonify({'error': 'invalid player path'}), 400
        player_fname = p_parts[3] if p_parts[3].endswith('.json') else p_parts[3] + '.json'

    tc, tcomp = l_parts

    try:
        from tactical_match_engine.services.json_loader import get_league_info
        from tactical_match_engine.engine.role_encoder import get_role_fitness_vector
        from tactical_match_engine.engine.physical_adaptation import calculate_physical_adaptation

        cand = (build_engine_candidate(store_get(token_id(player_path)), source_league, get_league_info)
                if _is_import else
                _load_player_for_engine_mongo(p_parts[0], p_parts[1], p_parts[2], player_fname))
        if cand is None:
            return jsonify({'error': 'player not found'}), 404

        cand_result = get_role_fitness_vector(
            cand['raw_stats'], cand['position'], role_path=role_file, flat_weights=True
        )
        if cand_result is None:
            return jsonify({'error': 'no role profile available for this position'}), 404

        role_fitness       = cand_result['overall_score']
        player_intensity   = cand['current_league_intensity']
        player_global_rank = cand.get('current_league_global_rank')

        t_info           = get_league_info(tc.replace('_', ' '), tcomp.replace('_', ' '))
        target_intensity = t_info['intensity']
        target_rank      = t_info.get('global_rank')
        league_adaptation = round(
            calculate_physical_adaptation(player_intensity, target_intensity) * 100.0, 2
        )

        if player_global_rank and target_rank:
            rank_diff = player_global_rank - target_rank
            if rank_diff > 5:    move_label = f'+{rank_diff} ranks up'
            elif rank_diff < -5: move_label = f'{abs(rank_diff)} ranks down'
            else:                move_label = 'lateral move'
        else:
            move_label = ''

        cand_positions = [str(p).upper() for p in cand.get('positions', [cand['position']])]
        cand_line      = get_position_line(cand_positions)

        clubs = []
        for club_doc in _loader._db().clubs.find({'_country': tc, '_competition': tcomp}):
            club_slug = club_doc.get('_club', '')
            if not club_slug:
                continue

            squad     = _get_squad_role_analysis_mongo(tc, tcomp, club_slug, role_file, cand_line=cand_line)
            squad_avg = squad['avg_overall']
            if squad['player_count'] == 0:
                squad_impact, squad_impact_norm = 0.0, 50.0
            else:
                squad_impact      = round(role_fitness - squad_avg, 2)
                squad_impact_norm = round(min(100.0, max(0.0, 50.0 + squad_impact)), 2)

            combined = round(0.40 * role_fitness + 0.30 * squad_impact_norm + 0.30 * league_adaptation, 2)
            if combined >= 72:   verdict = 'Starter Upgrade'
            elif combined >= 58: verdict = 'Strong Signing'
            elif combined >= 44: verdict = 'Squad Depth'
            elif combined >= 32: verdict = 'Development Option'
            else:                verdict = 'Below Squad Level'

            club_display = club_doc.get('team_name', club_slug.replace('_', ' '))
            clubs.append({
                'club':              club_display,
                'club_slug':         club_slug,
                'club_path':         f"{tc}/{tcomp}/{club_slug}",
                'overall_score':     combined,
                'role_fitness':      round(role_fitness, 2),
                'squad_impact':      squad_impact,
                'league_adaptation': league_adaptation,
                'player_count':      squad['player_count'],
                'verdict':           verdict,
                'pool_info': {
                    'position_line': cand_line,
                    'pool_size':     squad['player_count'],
                    'small_sample':  squad['player_count'] < 5,
                },
            })

        clubs.sort(key=lambda x: x['overall_score'], reverse=True)
        return jsonify({
            'player_name':        cand['name'],
            'player_global_rank': player_global_rank,
            'league':             tcomp.replace('_', ' '),
            'country':            tc.replace('_', ' '),
            'league_intensity':   round(target_intensity, 4),
            'target_global_rank': target_rank,
            'move_label':         move_label,
            'clubs':              clubs,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── Club Compatibility detail ──────────────────────────────────────────────────
@app.route('/api/club_compatibility')
def api_club_compatibility():
    player_path   = request.args.get('player', '')
    role_file     = request.args.get('role', '')
    club_path     = request.args.get('club', '')
    source_league = request.args.get('source_league', '').strip()

    if not player_path or not role_file or not club_path:
        return jsonify({'error': 'player, role, and club are required'}), 400

    c_parts = club_path.split('/')
    if len(c_parts) != 3:
        return jsonify({'error': 'club path must be country/competition/club'}), 400

    _is_import  = is_import_token(player_path)
    exclude_file = None
    if not _is_import:
        p_parts = player_path.split('/')
        if len(p_parts) != 4:
            return jsonify({'error': 'invalid player path'}), 400
        player_fname = p_parts[3] if p_parts[3].endswith('.json') else p_parts[3] + '.json'
        exclude_file = player_fname if c_parts[2] == p_parts[2] else None

    try:
        from tactical_match_engine.services.json_loader import get_league_info
        from tactical_match_engine.engine.role_encoder import get_role_fitness_vector
        from tactical_match_engine.engine.physical_adaptation import calculate_physical_adaptation

        cand = (build_engine_candidate(store_get(token_id(player_path)), source_league, get_league_info)
                if _is_import else
                _load_player_for_engine_mongo(p_parts[0], p_parts[1], p_parts[2], player_fname))
        if cand is None:
            return jsonify({'error': 'player not found'}), 404

        cand_result = get_role_fitness_vector(
            cand['raw_stats'], cand['position'], role_path=role_file, flat_weights=True
        )
        if cand_result is None:
            return jsonify({'error': 'no role profile available for this position'}), 404

        tc, tcomp, tclub = c_parts
        t_info            = get_league_info(tc.replace('_', ' '), tcomp.replace('_', ' '))
        target_intensity  = t_info['intensity']
        target_rank       = t_info.get('global_rank')
        player_global_rank = cand.get('current_league_global_rank')
        league_adaptation = round(
            calculate_physical_adaptation(cand['current_league_intensity'], target_intensity) * 100.0, 2
        )

        if player_global_rank and target_rank:
            rank_diff = player_global_rank - target_rank
            if rank_diff > 5:    move_label = f'+{rank_diff} ranks up'
            elif rank_diff < -5: move_label = f'{abs(rank_diff)} ranks down'
            else:                move_label = 'lateral move'
        else:
            move_label = ''

        cand_positions = [str(p).upper() for p in cand.get('positions', [cand['position']])]
        cand_line      = get_position_line(cand_positions)
        squad = _get_squad_role_analysis_mongo(
            tc, tcomp, tclub, role_file, cand_line=cand_line, exclude_file=exclude_file
        )
        pool_info = {
            'position_line': cand_line,
            'pool_size':     squad['player_count'],
            'small_sample':  squad['player_count'] < 5,
        }

        role_fitness = cand_result['overall_score']
        squad_avg    = squad['avg_overall']
        if squad['player_count'] == 0:
            squad_impact, squad_impact_norm = 0.0, 50.0
        else:
            squad_impact      = round(role_fitness - squad_avg, 2)
            squad_impact_norm = round(min(100.0, max(0.0, 50.0 + squad_impact)), 2)

        combined = round(0.40 * role_fitness + 0.30 * squad_impact_norm + 0.30 * league_adaptation, 2)
        if combined >= 72:   verdict = 'Starter Upgrade'
        elif combined >= 58: verdict = 'Strong Signing'
        elif combined >= 44: verdict = 'Squad Depth'
        elif combined >= 32: verdict = 'Development Option'
        else:                verdict = 'Below Squad Level'

        sq_metric_avgs = squad['avg_metric_scores']
        sq_metric_raw  = squad['avg_metric_raw']
        metric_deltas  = [
            {
                'name':             m['name'],
                'category':         m['category'],
                'higher_is_better': m['higher_is_better'],
                'candidate_raw':    m['raw_value'],
                'candidate_score':  m['score'],
                'squad_avg_raw':    sq_metric_raw.get(m['name'], 0.0),
                'squad_avg_score':  sq_metric_avgs.get(m['name'], 0.0),
                'raw_delta':        round(m['raw_value'] - sq_metric_raw.get(m['name'], 0.0), 4),
                'score_delta':      round(m['score'] - sq_metric_avgs.get(m['name'], 0.0), 2),
            }
            for m in cand_result['metric_details']
        ]

        from tactical_match_engine.engine.explanation_generator import generate_explanation
        from tactical_match_engine.engine.contender_simulation import simulate_contender_impact

        compat_scores = {
            'tactical_similarity': role_fitness / 100.0,
            'statistical_match':   min(1.0, squad_impact_norm / 100.0),
            'physical_adaptation': league_adaptation / 100.0,
        }
        _mins    = float(cand['raw_stats'].get('minutesPlayed', 90) or 90)
        _per90   = max(_mins / 90.0, 0.001)
        _ft      = float(cand['raw_stats'].get('accurateFinalThirdPasses', 0) or 0)
        _drb     = float(cand['raw_stats'].get('successfulDribbles', 0) or 0)
        _prog    = ((_ft / _per90) + (_drb / _per90)) / 2.0 if (_ft > 0 or _drb > 0) else 0.0
        _sq_prog = (float(sq_metric_raw.get('Final Third Passes', 0) or 0) +
                    float(sq_metric_raw.get('Successful Dribbles', 0) or 0)) / 2.0

        contender_sim = simulate_contender_impact(round(_prog, 4), round(_sq_prog, 4))
        explanations  = generate_explanation(compat_scores, contender_sim)

        return jsonify({
            'candidate': {
                'name':             cand['name'],
                'position':         cand['position'],
                'positions':        cand.get('positions', []),
                'age':              cand['age'],
                'league_intensity': round(cand['current_league_intensity'], 4),
                'global_rank':      player_global_rank,
                'league_name':      cand.get('current_league_name', ''),
                'role_fitness': {
                    'overall_score':   role_fitness,
                    'category_scores': cand_result['category_scores'],
                    'weights':         cand_result['weights'],
                },
            },
            'target': {
                'club':               tclub.replace('_', ' '),
                'competition':        tcomp.replace('_', ' '),
                'country':            tc.replace('_', ' '),
                'league_intensity':   round(target_intensity, 4),
                'global_rank':        target_rank,
                'squad_avg_score':    squad_avg,
                'avg_category_scores': squad['avg_category_scores'],
                'squad_players':      squad['players'],
                'player_count':       squad['player_count'],
            },
            'scores': {
                'role_fitness':      role_fitness,
                'squad_impact':      squad_impact,
                'squad_impact_norm': squad_impact_norm,
                'league_adaptation': league_adaptation,
                'combined_score':    combined,
            },
            'move_label':           move_label,
            'metric_deltas':        metric_deltas,
            'verdict':              verdict,
            'pool_info':            pool_info,
            'contender_simulation': contender_sim,
            'explanations':         explanations,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── Compare ────────────────────────────────────────────────────────────────────
@app.route('/compare')
def compare():
    return render_template('compare.html')

@app.route('/api/player_search')
def api_player_search():
    q = request.args.get('q', '').lower()
    return jsonify(get_all_players_flat(OUTPUT_DIR, query=q, limit=20))

@app.route('/api/player_info')
def api_player_info():
    path = request.args.get('path', '').strip()
    if not path:
        return jsonify({'error': 'path is required'}), 400
    parts = path.split('/')
    if len(parts) != 4:
        return jsonify({'error': 'invalid player path'}), 400
    try:
        doc = _loader._db().players.find_one({
            '_country': parts[0], '_competition': parts[1],
            '_club': parts[2],
            '_file': parts[3] if parts[3].endswith('.json') else parts[3] + '.json',
        })
        if not doc:
            return jsonify({'error': 'player file not found'}), 404
        profile   = doc.get('profile', {}).get('player', {})
        positions = [str(p).upper() for p in
                     (profile.get('positionsDetailed') or doc.get('positions', [profile.get('position', '')]))]
        primary   = positions[0] if positions else ''
        return jsonify({
            'positions':     positions,
            'position':      primary,
            'position_line': get_position_line(positions),
            'is_gk':         primary == 'GK',
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/import_player_positions')
def api_import_player_positions():
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
        return jsonify({
            'positions':     positions,
            'position':      primary,
            'position_line': get_position_line(positions),
            'is_gk':         primary == 'GK',
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/league_position_avg')
def api_league_position_avg():
    player_path = request.args.get('player', '').strip()
    pos_mode    = request.args.get('pos_mode', 'exact')
    if pos_mode not in ('exact', 'grouped'):
        pos_mode = 'exact'
    if not player_path:
        return jsonify({'error': 'player path required'}), 400
    result = _loader.get_league_position_avg(OUTPUT_DIR, player_path, pos_mode)
    if result is None:
        return jsonify({'error': 'player not found or invalid path'}), 404
    if 'error' in result:
        return jsonify(result), 400
    return jsonify(result)

@app.route('/api/player_detail')
def api_player_detail():
    path  = request.args.get('path', '')
    parts = path.split('/')
    if len(parts) == 4:
        data    = get_player(OUTPUT_DIR, parts[0], parts[1], parts[2], parts[3])
        heatmap = get_heatmap_path(OUTPUT_DIR, parts[0], parts[1], parts[2], parts[3])
        return jsonify({'data': data, 'heatmap': heatmap})
    return jsonify({'error': 'invalid path'}), 400

# ── Player percentiles ─────────────────────────────────────────────────────────
@app.route('/api/player_percentiles')
def api_player_percentiles():
    player_path = request.args.get('player', '').strip()
    if not player_path:
        return jsonify({'error': 'player is required'}), 400
    parts = player_path.split('/')
    if len(parts) != 4:
        return jsonify({'error': 'invalid player path'}), 400

    country, competition, club_slug, filename = parts
    target_file = filename if filename.endswith('.json') else filename + '.json'

    try:
        from tactical_match_engine.engine.normalization import percentile_score

        target_doc = _loader._db().players.find_one({
            '_country': country, '_competition': competition,
            '_club': club_slug, '_file': target_file,
        })
        if not target_doc:
            return jsonify({'error': 'player file not found'}), 404

        player_stats  = target_doc.get('statistics', {}).get('statistics', {})
        pos_raw       = (target_doc.get('profile', {}).get('player', {}).get('positionsDetailed')
                         or target_doc.get('positions', []))
        player_line   = get_position_line([str(p).upper() for p in pos_raw])

        pool_stats: dict = {}
        for doc in _loader._db().players.find(
                {'_country': country, '_competition': competition},
                {'profile.player.positionsDetailed': 1, 'positions': 1,
                 'profile.player.position': 1, 'statistics.statistics': 1,
                 '_club': 1, '_file': 1}):
            if doc.get('_club') == club_slug and doc.get('_file') == target_file:
                continue
            pos_raw2 = (doc.get('profile', {}).get('player', {}).get('positionsDetailed')
                        or doc.get('positions', []))
            if get_position_line([str(p).upper() for p in pos_raw2]) != player_line:
                continue
            ps = doc.get('statistics', {}).get('statistics', {})
            for k, v in ps.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool) and v is not None:
                    pool_stats.setdefault(k, []).append(float(v))

        LOWER_IS_BETTER = {'yellowCards', 'redCards', 'fouls', 'ownGoals', 'bigChancesMissed'}
        percentiles = {}
        for stat, val in player_stats.items():
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                continue
            pop = pool_stats.get(stat, [])
            if len(pop) < 2:
                continue
            pct = percentile_score(float(val), pop, higher_is_better=(stat not in LOWER_IS_BETTER))
            percentiles[stat] = round(pct, 1)

        return jsonify({
            'player_path':   player_path,
            'position_line': player_line,
            'pool_size':     len(pool_stats.get('appearances', [])),
            'competition':   competition.replace('_', ' '),
            'percentiles':   percentiles,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/player_radar')
def api_player_radar():
    player_path = request.args.get('player', '').strip()
    if not player_path:
        return jsonify({'error': 'player is required'}), 400
    try:
        from player_engine import build_radar
        result = build_radar(player_path, OUTPUT_DIR)
        if result is None:
            return jsonify({'error': 'player not found'}), 404
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── Export ─────────────────────────────────────────────────────────────────────
@app.route('/export')
def export_page():
    competitions = get_all_competitions(OUTPUT_DIR)
    return render_template('export.html', competitions=competitions)

@app.route('/api/export/csv')
def export_csv():
    filters           = {k: v for k, v in request.args.items()}
    filters['page']     = 1
    filters['per_page'] = 99999
    result  = search_players(OUTPUT_DIR, filters)
    players_list = result.get('players', [])

    si     = io.StringIO()
    writer = csv.DictWriter(si, fieldnames=[
        'name', 'team', 'competition', 'country', 'position', 'nationality',
        'age', 'height', 'preferred_foot', 'shirt_number',
        'appearances', 'goals', 'assists', 'rating',
        'pass_accuracy', 'key_passes', 'accurate_long_balls',
        'dribbles', 'duels_won', 'aerial_won',
        'tackles', 'interceptions', 'clearances',
        'shots_on_target', 'big_chances_missed',
        'yellow_cards', 'red_cards', 'minutes_played',
    ])
    writer.writeheader()
    for p in players_list:
        s = p.get('stats', {})
        writer.writerow({
            'name':                p.get('name', ''),
            'team':                p.get('team', ''),
            'competition':         p.get('competition', ''),
            'country':             p.get('country', ''),
            'position':            p.get('position', ''),
            'nationality':         p.get('nationality', ''),
            'age':                 p.get('age', ''),
            'height':              p.get('height', ''),
            'preferred_foot':      p.get('preferred_foot', ''),
            'shirt_number':        p.get('shirt_number', ''),
            'appearances':         s.get('appearances', ''),
            'goals':               s.get('goals', ''),
            'assists':             s.get('assists', ''),
            'rating':              round(s.get('rating', 0), 2) if s.get('rating') else '',
            'pass_accuracy':       s.get('accuratePassesPercentage', ''),
            'key_passes':          s.get('keyPasses', ''),
            'accurate_long_balls': s.get('accurateLongBalls', ''),
            'dribbles':            s.get('successfulDribbles', ''),
            'duels_won':           s.get('totalDuelsWon', ''),
            'aerial_won':          s.get('aerialDuelsWon', ''),
            'tackles':             s.get('tackles', ''),
            'interceptions':       s.get('interceptions', ''),
            'clearances':          s.get('clearances', ''),
            'shots_on_target':     s.get('shotsOnTarget', ''),
            'big_chances_missed':  s.get('bigChancesMissed', ''),
            'yellow_cards':        s.get('yellowCards', ''),
            'red_cards':           s.get('redCards', ''),
            'minutes_played':      s.get('minutesPlayed', ''),
        })
    output = si.getvalue()
    return send_file(io.BytesIO(output.encode()), mimetype='text/csv',
                     as_attachment=True, download_name='players_export.csv')

@app.route('/api/export_club_fit_csv')
def api_export_club_fit_csv():
    player_path = request.args.get('player', '')
    role_file   = request.args.get('role', '')
    club_path   = request.args.get('club', '')
    if not player_path or not role_file or not club_path:
        return jsonify({'error': 'player, role, and club are required'}), 400

    p_parts = player_path.split('/')
    c_parts = club_path.split('/')
    if len(p_parts) != 4 or len(c_parts) != 3:
        return jsonify({'error': 'invalid paths'}), 400

    player_fname = p_parts[3] if p_parts[3].endswith('.json') else p_parts[3] + '.json'
    try:
        from tactical_match_engine.services.json_loader import get_league_intensity
        from tactical_match_engine.engine.role_encoder import get_role_fitness_vector
        from tactical_match_engine.engine.physical_adaptation import calculate_physical_adaptation
        from tactical_match_engine.engine.explanation_generator import generate_explanation
        from tactical_match_engine.engine.contender_simulation import simulate_contender_impact

        cand = _load_player_for_engine_mongo(p_parts[0], p_parts[1], p_parts[2], player_fname)
        if cand is None:
            return jsonify({'error': 'player not found'}), 404
        cand_result = get_role_fitness_vector(cand['raw_stats'], cand['position'],
                                              role_path=role_file, flat_weights=True)
        if cand_result is None:
            return jsonify({'error': 'no role profile available for this position'}), 404

        tc, tcomp, tclub   = c_parts
        target_intensity   = get_league_intensity(tc.replace('_', ' '), tcomp.replace('_', ' '))
        league_adaptation  = round(
            calculate_physical_adaptation(cand['current_league_intensity'], target_intensity) * 100.0, 2
        )
        cand_positions = [str(p).upper() for p in cand.get('positions', [cand['position']])]
        cand_line      = get_position_line(cand_positions)
        exclude_file   = player_fname if c_parts[2] == p_parts[2] else None
        squad = _get_squad_role_analysis_mongo(tc, tcomp, tclub, role_file,
                                               cand_line=cand_line, exclude_file=exclude_file)

        role_fitness      = cand_result['overall_score']
        squad_avg         = squad['avg_overall']
        squad_impact      = round(role_fitness - squad_avg, 2) if squad['player_count'] else 0.0
        squad_impact_norm = round(min(100.0, max(0.0, 50.0 + squad_impact)), 2) if squad['player_count'] else 50.0
        combined = round(0.40 * role_fitness + 0.30 * squad_impact_norm + 0.30 * league_adaptation, 2)
        if combined >= 72:   verdict = 'Starter Upgrade'
        elif combined >= 58: verdict = 'Strong Signing'
        elif combined >= 44: verdict = 'Squad Depth'
        elif combined >= 32: verdict = 'Development Option'
        else:                verdict = 'Below Squad Level'

        sq_metric_avgs = squad['avg_metric_scores']
        sq_metric_raw  = squad['avg_metric_raw']
        metric_deltas  = [
            {
                'name': m['name'], 'category': m['category'],
                'higher_is_better': m['higher_is_better'],
                'candidate_raw': m['raw_value'], 'candidate_score': m['score'],
                'squad_avg_raw': sq_metric_raw.get(m['name'], 0.0),
                'squad_avg_score': sq_metric_avgs.get(m['name'], 0.0),
                'raw_delta': round(m['raw_value'] - sq_metric_raw.get(m['name'], 0.0), 4),
                'score_delta': round(m['score'] - sq_metric_avgs.get(m['name'], 0.0), 2),
            }
            for m in cand_result['metric_details']
        ]
        compat_scores = {
            'tactical_similarity': role_fitness / 100.0,
            'statistical_match':   min(1.0, squad_impact_norm / 100.0),
            'physical_adaptation': league_adaptation / 100.0,
        }
        _mins   = float(cand['raw_stats'].get('minutesPlayed', 90) or 90)
        _per90  = max(_mins / 90.0, 0.001)
        _ft     = float(cand['raw_stats'].get('accurateFinalThirdPasses', 0) or 0)
        _drb    = float(cand['raw_stats'].get('successfulDribbles', 0) or 0)
        _prog   = ((_ft / _per90) + (_drb / _per90)) / 2.0 if (_ft > 0 or _drb > 0) else 0.0
        _sq_prog = (float(sq_metric_raw.get('Final Third Passes', 0) or 0) +
                    float(sq_metric_raw.get('Successful Dribbles', 0) or 0)) / 2.0
        contender_sim = simulate_contender_impact(round(_prog, 4), round(_sq_prog, 4))
        explanations  = generate_explanation(compat_scores, contender_sim)

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['SCOUTING REPORT — CLUB FIT ANALYSIS'])
        writer.writerow([])
        writer.writerow(['Player', cand['name']])
        writer.writerow(['Role', role_file.split('/')[-1].replace('.json', '').replace('_', ' ')])
        writer.writerow(['Target Club', tclub.replace('_', ' ')])
        writer.writerow(['Target League', tcomp.replace('_', ' ')])
        writer.writerow([])
        writer.writerow(['SCORES'])
        writer.writerow(['Combined Score', combined])
        writer.writerow(['Verdict', verdict])
        writer.writerow(['Role Fitness', role_fitness])
        writer.writerow(['Squad Impact', squad_impact])
        writer.writerow(['League Adaptation', league_adaptation])
        writer.writerow([])
        writer.writerow(['CONTENDER PROJECTION'])
        writer.writerow(['Progression Gain', contender_sim['progression_gain']])
        writer.writerow(['xG Gain per Match', contender_sim['xg_gain_per_match']])
        writer.writerow(['Season xG Gain', contender_sim['season_xg_gain']])
        writer.writerow(['Projected Goal Gain', contender_sim['goal_gain']])
        writer.writerow(['Points Gain', contender_sim['points_gain']])
        writer.writerow(['Title Probability Shift', f"{contender_sim['title_probability_shift']:+.4f}"])
        writer.writerow([])
        writer.writerow(['SCOUTING NARRATIVE'])
        writer.writerow(['Why Club Needs Player', explanations.get('why_club_needs_player', '')])
        writer.writerow(['Why Player Fits Club', explanations.get('why_player_fits_club', '')])
        writer.writerow(['Why Club Becomes Contender', explanations.get('why_club_becomes_contender', '')])
        writer.writerow(['Risk Assessment', explanations.get('risk_assessment', '')])
        writer.writerow([])
        writer.writerow(['METRIC DETAILS'])
        writer.writerow(['Metric', 'Category', 'Higher Is Better', 'Candidate /90', 'Squad Avg /90',
                         'Raw Delta', 'Candidate Score', 'Squad Avg Score', 'Score Delta'])
        for m in metric_deltas:
            writer.writerow([m['name'], m['category'], m['higher_is_better'],
                             m['candidate_raw'], m['squad_avg_raw'], m['raw_delta'],
                             m['candidate_score'], m['squad_avg_score'], m['score_delta']])

        safe_player = cand['name'].replace(' ', '_')
        safe_club   = tclub.replace('_', ' ').replace(' ', '_')
        return send_file(
            io.BytesIO(output.getvalue().encode('utf-8-sig')),
            mimetype='text/csv',
            as_attachment=True,
            download_name=f"ClubFit_{safe_player}_{safe_club}.csv",
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── League Rankings ────────────────────────────────────────────────────────────
@app.route('/league_rankings')
def league_rankings():
    return render_template('league_rankings.html')

@app.route('/api/league_rankings')
def api_league_rankings():
    conf_filter = request.args.get('confederation', '').strip().lower()
    try:
        from tactical_match_engine.services.json_loader import load_iffhs_data
        data    = load_iffhs_data()
        entries = []
        for entry in data.get('rankings', []):
            if conf_filter and entry.get('confederation', '').lower() != conf_filter:
                continue
            entries.append({
                'rank':          entry.get('rank'),
                'country':       entry.get('country'),
                'confederation': entry.get('confederation'),
                'iffhs_points':  entry.get('iffhs_points'),
                'iffhs_rank':    entry.get('iffhs_rank'),
                's_country':     round(entry.get('s_country', 0), 4),
                'retention':     round(entry.get('retention', 0), 4),
                'divisions':     entry.get('divisions', []),
            })
        return jsonify({
            'source':                  data.get('source'),
            'endpoint':                data.get('endpoint'),
            'iffhs_source':            data.get('iffhs_source'),
            'iffhs_matched_countries': data.get('iffhs_matched_countries'),
            'parameters':              data.get('parameters', {}),
            'rankings':                entries,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Vercel entrypoint ──────────────────────────────────────────────────────────
# Vercel calls this module's `app` object directly via WSGI.
# The block below lets you also run locally with: python api/index.py
if __name__ == '__main__':
    app.run(debug=True, port=5000)
