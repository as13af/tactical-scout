"""routes/team_viz.py -- team_viz blueprint (auto-extracted from the original app.py)."""

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

bp = Blueprint('team_viz', __name__)


@bp.route('/api/playing_style_data')
def api_playing_style_data():
    """Return form fingerprint + positional data for all clubs in a competition."""
    country     = request.args.get('country', '').strip()
    competition = request.args.get('competition', '').strip()
    if not country or not competition:
        return jsonify({'error': 'country and competition are required'}), 400

    try:
        from style_engine import compute_style_scores
        from form_engine  import compute_form_style_scores, apply_positional_context

        # Build clubs_raw from MongoDB or file
        clubs_raw = []
        if ctx._USING_MONGO:
            import mongo_loader as _ml
            db = _ml._db()
            # Get unique_tournament_id from standings for form queries
            standings_doc = db.standings.find_one(
                {'_country': country, '_competition': competition}) or {}
            uniq_tid = None
            for group in standings_doc.get('standings', []):
                tourney = group.get('tournament', {}).get('uniqueTournament', {})
                if tourney.get('id'):
                    uniq_tid = int(tourney['id'])
                    break
            for club_doc in db.clubs.find(
                    {'_country': country, '_competition': competition},
                    sort=[('team_name', 1)]):
                profile     = club_doc.get('profile', {}).get('team', {})
                season_stats = club_doc.get('season_statistics', {}).get('statistics', {})
                tc           = profile.get('teamColors', {})
                tid          = club_doc.get('tournament_id') or standings_doc.get('tournament_id')
                clubs_raw.append({
                    'name':                club_doc.get('team_name', club_doc.get('_club', '')),
                    'slug':                club_doc.get('_club', ''),
                    'logo_url':            '',
                    'season_stats':        season_stats,
                    'matches':             int(season_stats.get('matches') or 0),
                    'team_colors':         {'primary': tc.get('primary', '#6E6E6E'),
                                           'secondary': tc.get('secondary', '#D9D9D9')},
                    'team_id':             club_doc.get('team_id'),
                    'tournament_id':       tid,
                    'unique_tournament_id': uniq_tid,
                })
            form_results = compute_form_style_scores(clubs_raw, db)
            pos_results  = apply_positional_context(clubs_raw, db)
        else:
            import glob as _glob, json as _json
            comp_dir = os.path.join(OUTPUT_DIR, country, competition)
            if not os.path.isdir(comp_dir):
                return jsonify({'error': 'competition not found'}), 404
            for club_name in sorted(os.listdir(comp_dir)):
                club_jsons = _glob.glob(os.path.join(comp_dir, club_name, 'Club_*_Season_*.json'))
                if not club_jsons:
                    continue
                try:
                    with open(club_jsons[0], encoding='utf-8') as _f:
                        cd = _json.load(_f)
                    season_stats = cd.get('season_statistics', {}).get('statistics', {})
                    clubs_raw.append({
                        'name':                cd.get('team_name', club_name.replace('_', ' ')),
                        'slug':                club_name,
                        'logo_url':            '',
                        'season_stats':        season_stats,
                        'matches':             int(season_stats.get('matches') or 0),
                        'team_colors':         {'primary': '#6E6E6E', 'secondary': '#D9D9D9'},
                        'team_id':             None,
                        'tournament_id':       None,
                        'unique_tournament_id': None,
                    })
                except Exception:
                    continue
            # No match data available on file-system — form scores will show empty
            form_results = compute_form_style_scores(clubs_raw, None)
            pos_results  = [{'slug': c['slug'], 'positional_passing':
                             {'backline_passes': None, 'mid_passes': None, 'forward_passes': None}}
                            for c in clubs_raw]

        # Merge form + positional results by slug
        pos_by_slug  = {r['slug']: r.get('positional_passing', {}) for r in pos_results}
        # Compute style scores once across entire competition pool for proper Z-score normalization
        all_styles    = compute_style_scores(clubs_raw) if clubs_raw else []
        style_by_slug = {r['slug']: r for r in all_styles}

        # Compute stats table for per-match stats, league averages, and stat definitions
        from style_engine import compute_stats_table
        stats_result = compute_stats_table(clubs_raw)
        stat_defs = stats_result['stat_defs']
        league_avg = stats_result['league_avg']
        total_teams = stats_result['total_teams']
        stats_by_slug = stats_result['teams']

        teams = []
        for fr in form_results:
            slug = fr['slug']
            style_info = style_by_slug.get(slug, {})
            teams.append({
                **fr,
                'positional_passing': pos_by_slug.get(slug, {}),
                'style_scores':       style_info.get('style_scores', {}),
                'primary_style':      style_info.get('primary_style', ''),
                'radar':              style_info.get('radar', {}),
                'stats_pm':           stats_by_slug.get(slug, {}),
            })

        # Extract competition colors from the first club or use defaults
        competition_colors = {'primary': '#6E6E6E', 'secondary': '#D9D9D9'}
        for club in clubs_raw:
            tc = club.get('team_colors', {})
            if tc.get('primary'):
                competition_colors = tc
                break

        return jsonify({
            'teams': teams,
            'stat_defs': stat_defs,
            'league_avg': league_avg,
            'total_teams': total_teams,
            'competition_colors': competition_colors,
            'competition': competition,
            'country': country
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/passmap/<country>/<competition>/<club>')
def api_passmap(country, competition, club):
    """Return a positional passmap PNG for a single club."""
    club = club.replace('-', '_')
    n = int(request.args.get('n', 5))
    try:
        from form_engine   import apply_positional_context, fetch_last_n_league_matches
        from passmap_engine import generate_passmap_bytes

        if ctx._USING_MONGO:
            import mongo_loader as _ml
            db = _ml._db()
            standings_doc = db.standings.find_one(
                {'_country': country, '_competition': competition}) or {}
            uniq_tid = None
            for group in standings_doc.get('standings', []):
                tourney = group.get('tournament', {}).get('uniqueTournament', {})
                if tourney.get('id'):
                    uniq_tid = int(tourney['id'])
                    break
            club_doc = db.clubs.find_one(
                {'_country': country, '_competition': competition, '_club': club}) or {}
            club_raw = [{
                'name':                club_doc.get('team_name', club.replace('_', ' ')),
                'slug':                club,
                'logo_url':            '',
                'team_id':             club_doc.get('team_id'),
                'tournament_id':       club_doc.get('tournament_id') or standings_doc.get('tournament_id'),
                'unique_tournament_id': uniq_tid,
            }]
            pos_results = apply_positional_context(club_raw, db, n=n)
        else:
            return '', 204  # no match data on file-system

        pos_data = pos_results[0].get('positional_passing') if pos_results else None
        if not pos_data or not any(v for v in pos_data.values()):
            return '', 204

        club_display = club_doc.get('team_name', club.replace('_', ' ')) if ctx._USING_MONGO else club.replace('_', ' ')
        png_bytes = generate_passmap_bytes(pos_data, label=club_display, n=n)
        return png_bytes, 200, {'Content-Type': 'image/png'}
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/club_avg_heatmap/<country>/<competition>/<club>/<season>')
def api_club_avg_heatmap_meta(country, competition, club, season):
    """Return metadata for the club's average heatmap."""
    club = club.replace('-', '_')
    if not ctx._USING_MONGO:
        return jsonify({'error': 'not available without MongoDB'}), 404
    try:
        import mongo_loader as _ml
        db = _ml._db()
        club_doc = db.clubs.find_one(
            {'_country': country, '_competition': competition, '_club': club},
            {'team_id': 1, 'unique_tournament_id': 1}) or {}
        team_id  = club_doc.get('team_id')
        uniq_tid = club_doc.get('unique_tournament_id')
        if not team_id:
            return jsonify({'error': 'club not found'}), 404
        doc = db.team_last5match_average_heatmap.find_one(
            {'team_id': int(team_id)},
            {'data': 0, 'zones_data': 0})
        if not doc:
            return jsonify({'error': 'no heatmap data'}), 404
        return jsonify({
            'match_count':  doc.get('match_count', 0),
            'generated_at': str(doc.get('generated_at', '')),
            'has_png':      True,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/club_avg_heatmap_png/<country>/<competition>/<club>/<season>')
def api_club_avg_heatmap_png(country, competition, club, season):
    """Serve the club average heatmap PNG from MongoDB."""
    club = club.replace('-', '_')
    if not ctx._USING_MONGO:
        return '', 404
    try:
        import mongo_loader as _ml
        db = _ml._db()
        club_doc = db.clubs.find_one(
            {'_country': country, '_competition': competition, '_club': club},
            {'team_id': 1}) or {}
        team_id = club_doc.get('team_id')
        if not team_id:
            return '', 404
        doc = db.team_last5match_average_heatmap.find_one({'team_id': int(team_id)})
        if not doc or 'heatmap_png' not in doc:
            return '', 404
        return bytes(doc['heatmap_png']), 200, {'Content-Type': 'image/png'}
    except Exception:
        return '', 404


@bp.route('/api/club_avg_heatmap_zones_png/<country>/<competition>/<club>/<season>')
def api_club_avg_heatmap_zones_png(country, competition, club, season):
    """Serve the 18-zone club average heatmap PNG from MongoDB."""
    club = club.replace('-', '_')
    if not ctx._USING_MONGO:
        return '', 404
    try:
        import mongo_loader as _ml
        db = _ml._db()
        club_doc = db.clubs.find_one(
            {'_country': country, '_competition': competition, '_club': club},
            {'team_id': 1}) or {}
        team_id = club_doc.get('team_id')
        if not team_id:
            return '', 404
        doc = db.team_last5match_average_heatmap.find_one({'team_id': int(team_id)})
        if not doc or 'heatmap_18zones_png' not in doc or not doc['heatmap_18zones_png']:
            return '', 404
        return bytes(doc['heatmap_18zones_png']), 200, {'Content-Type': 'image/png'}
    except Exception:
        return '', 404


@bp.route('/heatmap_db/<country>/<competition>/<club>/<stem>')
def heatmap_db(country, competition, club, stem):
    """Serve a player heatmap PNG stored as bytes in MongoDB."""
    club = club.replace('-', '_')
    if not ctx._USING_MONGO:
        return '', 404
    import mongo_loader as _ml
    data = _ml.get_heatmap_bytes(country, competition, club, stem)
    if data:
        return data, 200, {'Content-Type': 'image/png'}
    return '', 404
