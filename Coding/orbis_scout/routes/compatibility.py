"""routes/compatibility.py -- compatibility blueprint (auto-extracted from the original app.py)."""

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

bp = Blueprint('compatibility', __name__)


@bp.route('/compatibility')
def compatibility():
    return render_template('compatibility.html')


@bp.route('/api/compatibility')
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

        player_data = _load_player_for_engine(parts[0], parts[1], parts[2], parts[3])
        if player_data is None:
            return jsonify({'error': 'player not found'}), 404
        result = get_role_fitness_vector(
            player_data['raw_stats'],
            player_data['position'],
            role_path=role_file
        )
        if result is None:
            return jsonify({'error': 'no role profile for this position'}), 404

        return jsonify({
            'player_name':       player_data['name'],
            'player_position':   player_data['position'],
            'player_positions':  player_data.get('positions', []),
            'player_age':        player_data['age'],
            'player_team':       player_data.get('stats', {}).get('team', parts[2].replace('_', ' ')),
            'league_intensity':  round(player_data['current_league_intensity'], 4),
            'role_name':         result['role_profile']['position'],
            'overall_score':     result['overall_score'],
            'category_scores':   result['category_scores'],
            'weights':           result['weights'],
            'metric_details':    result['metric_details'],
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/compatibility_avg')
def api_compatibility_avg():
    """
    Score every position-matched player in the selected player's league against
    the chosen role profile and return the averaged result.

    Query params:
      player – "Country/Competition/Club/filename.json"  (derives league + self-exclude)
      role   – role profile path (e.g. "2. Mid-Line/I_Central_Midfielder.json")
    """
    player_path = request.args.get('player', '').strip()
    role_file   = request.args.get('role', '').strip()

    if not player_path or not role_file:
        return jsonify({'error': 'player and role are required'}), 400

    parts = player_path.split('/')
    if len(parts) != 4:
        return jsonify({'error': 'invalid player path'}), 400

    country, competition, club_slug, filename = parts
    target_file = filename if filename.endswith('.json') else filename + '.json'

    # Infer which position line this role belongs to from the path prefix
    role_line  = role_file.split('/')[0]           # e.g. "1. Back-Line"
    line_codes = _LINE_BROAD_POS.get(role_line, set())

    try:
        from tactical_match_engine.engine.role_encoder import get_role_fitness_vector

        pool_results = []

        def _scan_player(pd):
            pos = [str(p).upper() for p in pd.get('positions', [pd.get('position', '')])]
            if line_codes and not any(p in line_codes for p in pos):
                return
            res = get_role_fitness_vector(pd['raw_stats'], pd['position'], role_path=role_file)
            if res is not None:
                pool_results.append(res)

        if ctx._USING_MONGO:
            if ctx._loader._db().clubs.count_documents(
                    {'_country': country, '_competition': competition}, limit=1) == 0:
                return jsonify({'error': 'competition directory not found'}), 404
            for doc in ctx._loader._db().players.find(
                    {'_country': country, '_competition': competition}):
                if doc.get('_club') == club_slug and doc.get('_file') == target_file:
                    continue
                try:
                    _scan_player(ctx._loader.load_sofascore_player_for_engine(doc))
                except Exception:
                    continue
        else:
            import glob as _glob
            from tactical_match_engine.services.json_loader import load_sofascore_player
            comp_dir = os.path.join(OUTPUT_DIR, country, competition)
            if not os.path.isdir(comp_dir):
                return jsonify({'error': 'competition directory not found'}), 404
            for club_name in sorted(os.listdir(comp_dir)):
                players_dir = os.path.join(comp_dir, club_name, 'Players')
                if not os.path.isdir(players_dir):
                    continue
                for fpath in sorted(_glob.glob(os.path.join(players_dir, '*.json'))):
                    if club_name == club_slug and os.path.basename(fpath) == target_file:
                        continue
                    try:
                        _scan_player(load_sofascore_player(fpath))
                    except Exception:
                        continue

        pool_size = len(pool_results)

        if pool_size == 0:
            return jsonify({
                'avg_pool_size':   0,
                'overall_score':   0,
                'category_scores': {},
                'weights':         {},
                'metric_details':  [],
            })

        avg_overall = round(sum(r['overall_score'] for r in pool_results) / pool_size, 2)

        cat_buckets = {}
        for r in pool_results:
            for cat, sc in r['category_scores'].items():
                cat_buckets.setdefault(cat, []).append(sc)
        avg_cats = {cat: round(sum(v) / len(v), 2) for cat, v in cat_buckets.items()}

        metric_score_buckets = {}
        metric_raw_buckets   = {}
        for r in pool_results:
            for m in r['metric_details']:
                metric_score_buckets.setdefault(m['name'], []).append(m['score'])
                metric_raw_buckets.setdefault(m['name'],   []).append(m['raw_value'])

        avg_metrics = [
            {
                'name':             m['name'],
                'category':         m['category'],
                'higher_is_better': m['higher_is_better'],
                'raw_value':        round(sum(metric_raw_buckets[m['name']])   / len(metric_raw_buckets[m['name']]),   4),
                'score':            round(sum(metric_score_buckets[m['name']]) / len(metric_score_buckets[m['name']]), 2),
            }
            for m in pool_results[0]['metric_details']
        ]

        return jsonify({
            'avg_pool_size':   pool_size,
            'overall_score':   avg_overall,
            'category_scores': avg_cats,
            'weights':         pool_results[0]['weights'],
            'metric_details':  avg_metrics,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/club_compatibility')
def club_compatibility():
    return render_template('club_compatibility.html',
                           competitions=get_all_competitions(OUTPUT_DIR))


@bp.route('/api/club_search')
def api_club_search():
    q      = request.args.get('q', '').lower()
    league = request.args.get('league', '')   # optional "Country/Competition" filter
    result = get_all_clubs_flat(OUTPUT_DIR, query=q, limit=20, league_filter=league)
    return jsonify(result)


@bp.route('/api/league_suitability')
def api_league_suitability():
    """
    Stage 1 of the two-step Wizard.
    Score every scraped league for a player+role combination.
    suitability_score = 0.6 * role_fitness + 0.4 * league_adaptation
    league_adaptation uses the sigmoid-based formula from physical_adaptation.py
    so upward leaps are penalised correctly (asymmetric, nonlinear).
    """
    player_path = request.args.get('player', '').strip()
    role_file   = request.args.get('role',   '').strip()
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
        import glob as _glob
        import json as _json
        from tactical_match_engine.services.json_loader import get_league_info
        from tactical_match_engine.engine.role_encoder import get_role_fitness_vector
        from tactical_match_engine.engine.physical_adaptation import calculate_physical_adaptation

        if _is_import:
            rec = store_get(token_id(player_path))
            if rec is None:
                return jsonify({'error': 'imported player expired \u2014 re-upload'}), 404
            cand = build_engine_candidate(rec, source_league, get_league_info)
        else:
            cand = _load_player_for_engine(p_parts[0], p_parts[1], p_parts[2], player_fname)
            if cand is None:
                return jsonify({'error': 'player not found'}), 404
        cand_result = get_role_fitness_vector(
            cand['raw_stats'], cand['position'], role_path=role_file, flat_weights=True
        )
        if cand_result is None:
            return jsonify({'error': 'no role profile available for this position'}), 404

        role_fitness          = cand_result['overall_score']
        player_intensity      = cand['current_league_intensity']
        player_global_rank    = cand.get('current_league_global_rank')
        player_league_name    = cand.get('current_league_name', '')
        role_name             = cand_result.get('role_profile', {}).get('position', role_file)
        cand_positions        = [str(p).upper() for p in cand.get('positions', [cand['position']])]
        cand_line             = get_position_line(cand_positions)

        # Build league list — from MongoDB (Vercel) or disk. get_all_competitions
        # is already data-source aware.
        leagues = []
        comp_list_dedup = sorted(
            (c['country'], c['competition']) for c in get_all_competitions(OUTPUT_DIR)
        )

        for c_name, comp_name in comp_list_dedup:
            t_info           = get_league_info(
                c_name.replace('_', ' '),
                comp_name.replace('_', ' ')
            )
            target_intensity = t_info['intensity']
            target_rank      = t_info.get('global_rank')

            player_count = 0
            if ctx._USING_MONGO:
                for _pd in ctx._loader._db().players.find(
                        {'_country': c_name, '_competition': comp_name},
                        {'positions': 1, 'position': 1}):
                    _pos = [str(p).upper() for p in (_pd.get('positions') or [_pd.get('position', '')])]
                    if get_position_line(_pos) == cand_line:
                        player_count += 1
            else:
                comp_path = os.path.join(OUTPUT_DIR, c_name, comp_name)
                for _pf in _glob.glob(os.path.join(comp_path, '*', 'Players', '*.json')):
                    try:
                        with open(_pf, encoding='utf-8') as _f:
                            _pd = _json.load(_f)
                        _pos = [str(p).upper() for p in (_pd.get('positions') or [_pd.get('position', '')])]
                        if get_position_line(_pos) == cand_line:
                            player_count += 1
                    except Exception:
                        pass

            league_adaptation = round(
                calculate_physical_adaptation(player_intensity, target_intensity) * 100.0, 2
            )
            suitability = round(0.6 * role_fitness + 0.4 * league_adaptation, 2)

            # Tier gap label
            if player_global_rank and target_rank:
                rank_diff = player_global_rank - target_rank   # positive = target is stronger
                if rank_diff > 5:
                    move_label = f'+{rank_diff} ranks up'
                elif rank_diff < -5:
                    move_label = f'{abs(rank_diff)} ranks down'
                else:
                    move_label = 'lateral move'
            else:
                move_label = ''

            leagues.append({
                'league':            comp_name.replace('_', ' '),
                'league_key':        f"{c_name}/{comp_name}",
                'country':           c_name.replace('_', ' '),
                'suitability_score': suitability,
                'league_intensity':  round(target_intensity, 4),
                'global_rank':       target_rank,
                'role_fitness':      round(role_fitness, 2),
                'league_adaptation': league_adaptation,
                'move_label':        move_label,
                'player_count':      player_count,
            })

        leagues.sort(key=lambda x: x['suitability_score'], reverse=True)
        return jsonify({
            'player_name':            cand['name'],
            'role_name':              role_name,
            'player_league_name':     player_league_name,
            'player_global_rank':     player_global_rank,
            'player_league_intensity': round(player_intensity, 4),
            'leagues':                leagues,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/club_suitability')
def api_club_suitability():
    """
    Stage 2 of the two-step Wizard.
    Score every club in a selected league for a player+role combination.
    Reuses the existing club-fit scoring formula from /api/club_compatibility.
    """
    player_path = request.args.get('player', '').strip()
    league      = request.args.get('league', '').strip()   # "Country/Competition"
    role_file   = request.args.get('role',   '').strip()
    source_league = request.args.get('source_league', '').strip()

    if not player_path or not league or not role_file:
        return jsonify({'error': 'player, league, and role are required'}), 400

    p_parts = player_path.split('/')
    l_parts = league.split('/')
    if len(l_parts) != 2:
        return jsonify({'error': 'league must be Country/Competition'}), 400

    _is_import = is_import_token(player_path)
    if not _is_import:
        if len(p_parts) != 4:
            return jsonify({'error': 'invalid player path'}), 400
        player_fname = p_parts[3] if p_parts[3].endswith('.json') else p_parts[3] + '.json'

    tc, tcomp    = l_parts
    league_dir   = os.path.join(OUTPUT_DIR, tc, tcomp)
    if not ctx._USING_MONGO and not os.path.isdir(league_dir):
        return jsonify({'error': 'league directory not found'}), 404

    try:
        import json as _json
        from tactical_match_engine.services.json_loader import get_league_info
        from tactical_match_engine.engine.role_encoder import get_role_fitness_vector
        from tactical_match_engine.engine.physical_adaptation import calculate_physical_adaptation

        cand = (build_engine_candidate(store_get(token_id(player_path)), source_league, get_league_info)
                if _is_import else
                _load_player_for_engine(p_parts[0], p_parts[1], p_parts[2], player_fname))
        if cand is None:
            return jsonify({'error': 'imported player expired \u2014 re-upload' if _is_import else 'player not found'}), 404
        cand_result = get_role_fitness_vector(
            cand['raw_stats'], cand['position'], role_path=role_file, flat_weights=True
        )
        if cand_result is None:
            return jsonify({'error': 'no role profile available for this position'}), 404

        role_fitness       = cand_result['overall_score']
        player_intensity   = cand['current_league_intensity']
        player_global_rank = cand.get('current_league_global_rank')
        age                = cand.get('age', 26)

        t_info           = get_league_info(tc.replace('_', ' '), tcomp.replace('_', ' '))
        target_intensity = t_info['intensity']
        target_rank      = t_info.get('global_rank')
        league_adaptation = round(
            calculate_physical_adaptation(player_intensity, target_intensity) * 100.0, 2
        )

        # Tier gap label
        if player_global_rank and target_rank:
            rank_diff = player_global_rank - target_rank
            if rank_diff > 5:
                move_label = f'+{rank_diff} ranks up'
            elif rank_diff < -5:
                move_label = f'{abs(rank_diff)} ranks down'
            else:
                move_label = 'lateral move'
        else:
            move_label = ''

        cand_positions = [str(p).upper() for p in cand.get('positions', [cand['position']])]
        cand_line      = get_position_line(cand_positions)

        clubs = []
        # Build [(club_slug, club_display_name), ...] from Mongo (Vercel) or disk.
        if ctx._USING_MONGO:
            club_entries = [
                (d.get('_club', ''), d.get('team_name') or str(d.get('_club', '')).replace('_', ' '))
                for d in ctx._loader._db().clubs.find(
                    {'_country': tc, '_competition': tcomp}, {'_club': 1, 'team_name': 1})
            ]
        else:
            club_entries = []
            for _e in sorted(os.scandir(league_dir), key=lambda e: e.name):
                if not _e.is_dir():
                    continue
                _disp = _e.name.replace('_', ' ')
                _cj = next((x for x in os.scandir(_e.path)
                            if x.name.startswith('Club_') and x.name.endswith('.json')), None)
                if _cj:
                    try:
                        with open(_cj.path, encoding='utf-8') as f:
                            _disp = _json.load(f).get('team_name', _disp)
                    except Exception:
                        pass
                club_entries.append((_e.name, _disp))

        for club_slug, club_display in sorted(club_entries):
            squad     = _get_squad_role_analysis(tc, tcomp, club_slug, role_file, cand_line=cand_line)
            squad_avg = squad['avg_overall']
            if squad['player_count'] == 0:
                squad_impact      = 0.0
                squad_impact_norm = 50.0
            else:
                squad_impact      = round(role_fitness - squad_avg, 2)
                squad_impact_norm = round(min(100.0, max(0.0, 50.0 + squad_impact)), 2)

            combined = round(
                0.40 * role_fitness + 0.30 * squad_impact_norm + 0.30 * league_adaptation, 2
            )
            if combined >= 72:   verdict = 'Starter Upgrade'
            elif combined >= 58: verdict = 'Strong Signing'
            elif combined >= 44: verdict = 'Squad Depth'
            elif combined >= 32: verdict = 'Development Option'
            else:                verdict = 'Below Squad Level'

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
            'player_name':         cand['name'],
            'player_global_rank':  player_global_rank,
            'league':              tcomp.replace('_', ' '),
            'country':             tc.replace('_', ' '),
            'league_intensity':    round(target_intensity, 4),
            'target_global_rank':  target_rank,
            'move_label':          move_label,
            'clubs':               clubs,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/club_compatibility')
def api_club_compatibility():
    player_path = request.args.get('player', '')
    role_file   = request.args.get('role', '')
    club_path   = request.args.get('club', '')
    source_league = request.args.get('source_league', '').strip()

    if not player_path or not role_file or not club_path:
        return jsonify({'error': 'player, role, and club are required'}), 400

    c_parts = club_path.split('/')
    if len(c_parts) != 3:
        return jsonify({'error': 'club path must be country/competition/club'}), 400

    _is_import  = is_import_token(player_path)
    player_full = None                      # nothing to exclude for an import
    if not _is_import:
        p_parts = player_path.split('/')
        if len(p_parts) != 4:
            return jsonify({'error': 'invalid player path'}), 400
        player_fname = p_parts[3] if p_parts[3].endswith('.json') else p_parts[3] + '.json'
        player_full  = os.path.join(OUTPUT_DIR, p_parts[0], p_parts[1], p_parts[2], 'Players', player_fname)

    try:
        from tactical_match_engine.services.json_loader import get_league_info
        from tactical_match_engine.engine.role_encoder import get_role_fitness_vector
        from tactical_match_engine.engine.physical_adaptation import calculate_physical_adaptation

        # ── Candidate ────────────────────────────────────────────────────────
        cand = (build_engine_candidate(store_get(token_id(player_path)), source_league, get_league_info)
                if _is_import else
                _load_player_for_engine(p_parts[0], p_parts[1], p_parts[2], player_fname))
        if cand is None:
            return jsonify({'error': 'imported player expired \u2014 re-upload' if _is_import else 'player not found'}), 404
        cand_result = get_role_fitness_vector(cand['raw_stats'], cand['position'], role_path=role_file, flat_weights=True)
        if cand_result is None:
            return jsonify({'error': 'no role profile available for this position'}), 404

        # ── Target club ───────────────────────────────────────────────────────
        tc, tcomp, tclub = c_parts
        t_info            = get_league_info(tc.replace('_', ' '), tcomp.replace('_', ' '))
        target_intensity  = t_info['intensity']
        target_rank       = t_info.get('global_rank')
        player_global_rank = cand.get('current_league_global_rank')
        age               = cand.get('age', 26)
        league_adaptation = round(
            calculate_physical_adaptation(cand['current_league_intensity'], target_intensity) * 100.0, 2
        )

        # Tier gap label
        if player_global_rank and target_rank:
            rank_diff = player_global_rank - target_rank
            if rank_diff > 5:
                move_label = f'+{rank_diff} ranks up'
            elif rank_diff < -5:
                move_label = f'{abs(rank_diff)} ranks down'
            else:
                move_label = 'lateral move'
        else:
            move_label = ''

        # ── Squad analysis ───────────────────────────────────────────────────
        cand_positions = [str(p).upper() for p in cand.get('positions', [cand['position']])]
        cand_line      = get_position_line(cand_positions)
        squad    = _get_squad_role_analysis(tc, tcomp, tclub, role_file,
                                            cand_line=cand_line, exclude_path=player_full)
        pool_info = {
            'position_line': cand_line,
            'pool_size':     squad['player_count'],
            'small_sample':  squad['player_count'] < 5,
        }

        # ── Scores ────────────────────────────────────────────────────────────
        role_fitness = cand_result['overall_score']
        squad_avg    = squad['avg_overall']
        if squad['player_count'] == 0:
            squad_impact      = 0.0
            squad_impact_norm = 50.0
        else:
            squad_impact      = round(role_fitness - squad_avg, 2)
            squad_impact_norm = round(min(100.0, max(0.0, 50.0 + squad_impact)), 2)

        combined = round(
            0.40 * role_fitness + 0.30 * squad_impact_norm + 0.30 * league_adaptation, 2
        )

        if combined >= 72:   verdict = 'Starter Upgrade'
        elif combined >= 58: verdict = 'Strong Signing'
        elif combined >= 44: verdict = 'Squad Depth'
        elif combined >= 32: verdict = 'Development Option'
        else:                verdict = 'Below Squad Level'

        # ── Metric deltas ─────────────────────────────────────────────────────
        sq_metric_avgs = squad['avg_metric_scores']
        sq_metric_raw  = squad['avg_metric_raw']
        metric_deltas  = [
            {
                'name':              m['name'],
                'category':          m['category'],
                'higher_is_better':  m['higher_is_better'],
                # candidate
                'candidate_raw':     m['raw_value'],
                'candidate_score':   m['score'],
                # squad avg (per-90 raw values averaged across position-matched players)
                'squad_avg_raw':     sq_metric_raw.get(m['name'], 0.0),
                'squad_avg_score':   sq_metric_avgs.get(m['name'], 0.0),
                # deltas
                'raw_delta':         round(m['raw_value']  - sq_metric_raw.get(m['name'], 0.0),  4),
                'score_delta':       round(m['score']      - sq_metric_avgs.get(m['name'], 0.0), 2),
            }
            for m in cand_result['metric_details']
        ]

        # ── Scouting narrative explanations ──────────────────────────────────
        from tactical_match_engine.engine.explanation_generator import generate_explanation
        from tactical_match_engine.engine.contender_simulation import simulate_contender_impact

        compat_scores_for_explain = {
            'tactical_similarity':  role_fitness / 100.0,
            'statistical_match':    min(1.0, squad_impact_norm / 100.0),
            'physical_adaptation':  league_adaptation / 100.0,
        }
        # Progression index for contender simulation
        _mins   = float(cand['raw_stats'].get('minutesPlayed', 90) or 90)
        _per90  = max(_mins / 90.0, 0.001)
        _ft     = float(cand['raw_stats'].get('accurateFinalThirdPasses', 0) or 0)
        _drb    = float(cand['raw_stats'].get('successfulDribbles', 0) or 0)
        _prog   = ((_ft / _per90) + (_drb / _per90)) / 2.0 if (_ft > 0 or _drb > 0) else 0.0
        _sq_ft  = float(squad.get('avg_metric_raw', {}).get('Final Third Passes', 0) or 0)
        _sq_drb = float(squad.get('avg_metric_raw', {}).get('Successful Dribbles', 0) or 0)
        _sq_prog = (_sq_ft + _sq_drb) / 2.0

        contender_sim = simulate_contender_impact(
            player_progression_index=round(_prog, 4),
            squad_average_progression=round(_sq_prog, 4),
        )
        explanations = generate_explanation(compat_scores_for_explain, contender_sim)

        return jsonify({
            'candidate': {
                'name':             cand['name'],
                'position':         cand['position'],
                'positions':        cand.get('positions', []),
                'age':              cand['age'],
                'league_intensity': round(cand['current_league_intensity'], 4),
                'global_rank':      cand.get('current_league_global_rank'),
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
            'move_label':       move_label,
            'metric_deltas':    metric_deltas,
            'verdict':          verdict,
            'pool_info':        pool_info,
            'contender_simulation': contender_sim,
            'explanations':     explanations,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
