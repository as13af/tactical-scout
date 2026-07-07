"""routes/exports.py -- exports blueprint (auto-extracted from the original app.py)."""

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

bp = Blueprint('exports', __name__)


@bp.route('/export')
def export_page():
    competitions = get_all_competitions(OUTPUT_DIR)
    return render_template('export.html', competitions=competitions)


@bp.route('/api/export/csv')
def export_csv():
    filters = {k: v for k, v in request.args.items()}
    filters['page']     = 1
    filters['per_page'] = 99999
    result   = search_players(OUTPUT_DIR, filters)
    players  = result.get('players', [])

    si     = io.StringIO()
    writer = csv.DictWriter(si, fieldnames=[
        'name','team','competition','country','position','nationality',
        'age','height','preferred_foot','shirt_number',
        'appearances','goals','assists','rating',
        'pass_accuracy','key_passes','accurate_long_balls',
        'dribbles','duels_won','aerial_won',
        'tackles','interceptions','clearances',
        'shots_on_target','big_chances_missed',
        'yellow_cards','red_cards','minutes_played'
    ])
    writer.writeheader()
    for p in players:
        s = p.get('stats', {})
        writer.writerow({
            'name':               p.get('name',''),
            'team':               p.get('team',''),
            'competition':        p.get('competition',''),
            'country':            p.get('country',''),
            'position':           p.get('position',''),
            'nationality':        p.get('nationality',''),
            'age':                p.get('age',''),
            'height':             p.get('height',''),
            'preferred_foot':     p.get('preferred_foot',''),
            'shirt_number':       p.get('shirt_number',''),
            'appearances':        s.get('appearances',''),
            'goals':              s.get('goals',''),
            'assists':            s.get('assists',''),
            'rating':             round(s.get('rating',0),2) if s.get('rating') else '',
            'pass_accuracy':      s.get('accuratePassesPercentage',''),
            'key_passes':         s.get('keyPasses',''),
            'accurate_long_balls':s.get('accurateLongBalls',''),
            'dribbles':           s.get('successfulDribbles',''),
            'duels_won':          s.get('totalDuelsWon',''),
            'aerial_won':         s.get('aerialDuelsWon',''),
            'tackles':            s.get('tackles',''),
            'interceptions':      s.get('interceptions',''),
            'clearances':         s.get('clearances',''),
            'shots_on_target':    s.get('shotsOnTarget',''),
            'big_chances_missed': s.get('bigChancesMissed',''),
            'yellow_cards':       s.get('yellowCards',''),
            'red_cards':          s.get('redCards',''),
            'minutes_played':     s.get('minutesPlayed',''),
        })

    output = si.getvalue()
    return send_file(
        io.BytesIO(output.encode()),
        mimetype='text/csv',
        as_attachment=True,
        download_name='players_export.csv'
    )


@bp.route('/api/export_club_fit_csv')
def api_export_club_fit_csv():
    """
    Run /api/club_compatibility and return the result as a downloadable CSV
    containing the full metric deltas table + summary scores + explanations.
    """
    player_path = request.args.get('player', '')
    role_file   = request.args.get('role', '')
    club_path   = request.args.get('club', '')

    if not player_path or not role_file or not club_path:
        return jsonify({'error': 'player, role, and club are required'}), 400

    # Re-use the existing logic by doing an internal fetch via the service layer
    p_parts = player_path.split('/')
    c_parts = club_path.split('/')
    if len(p_parts) != 4 or len(c_parts) != 3:
        return jsonify({'error': 'invalid paths'}), 400

    player_fname = p_parts[3] if p_parts[3].endswith('.json') else p_parts[3] + '.json'
    player_full  = os.path.join(OUTPUT_DIR, p_parts[0], p_parts[1], p_parts[2], 'Players', player_fname)

    try:
        from tactical_match_engine.services.json_loader import get_league_intensity
        from tactical_match_engine.engine.role_encoder import get_role_fitness_vector
        from tactical_match_engine.engine.physical_adaptation import calculate_physical_adaptation
        from tactical_match_engine.engine.explanation_generator import generate_explanation
        from tactical_match_engine.engine.contender_simulation import simulate_contender_impact

        cand = _load_player_for_engine(p_parts[0], p_parts[1], p_parts[2], player_fname)
        if cand is None:
            return jsonify({'error': 'player not found'}), 404
        cand_result = get_role_fitness_vector(cand['raw_stats'], cand['position'], role_path=role_file, flat_weights=True)
        if cand_result is None:
            return jsonify({'error': 'no role profile available for this position'}), 404

        tc, tcomp, tclub = c_parts
        target_intensity  = get_league_intensity(tc.replace('_', ' '), tcomp.replace('_', ' '))
        league_adaptation = round(calculate_physical_adaptation(cand['current_league_intensity'], target_intensity) * 100.0, 2)

        cand_positions = [str(p).upper() for p in cand.get('positions', [cand['position']])]
        cand_line      = get_position_line(cand_positions)
        squad    = _get_squad_role_analysis(tc, tcomp, tclub, role_file, cand_line=cand_line, exclude_path=player_full)

        role_fitness     = cand_result['overall_score']
        squad_avg        = squad['avg_overall']
        squad_impact     = round(role_fitness - squad_avg, 2) if squad['player_count'] else 0.0
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
                'name':            m['name'],
                'category':        m['category'],
                'higher_is_better': m['higher_is_better'],
                'candidate_raw':   m['raw_value'],
                'candidate_score': m['score'],
                'squad_avg_raw':   sq_metric_raw.get(m['name'], 0.0),
                'squad_avg_score': sq_metric_avgs.get(m['name'], 0.0),
                'raw_delta':       round(m['raw_value'] - sq_metric_raw.get(m['name'], 0.0), 4),
                'score_delta':     round(m['score']     - sq_metric_avgs.get(m['name'], 0.0), 2),
            }
            for m in cand_result['metric_details']
        ]

        compat_scores = {
            'tactical_similarity': role_fitness / 100.0,
            'statistical_match':   min(1.0, squad_impact_norm / 100.0),
            'physical_adaptation': league_adaptation / 100.0,
        }
        _mins  = float(cand['raw_stats'].get('minutesPlayed', 90) or 90)
        _per90 = max(_mins / 90.0, 0.001)
        _ft    = float(cand['raw_stats'].get('accurateFinalThirdPasses', 0) or 0)
        _drb   = float(cand['raw_stats'].get('successfulDribbles', 0) or 0)
        _prog  = ((_ft / _per90) + (_drb / _per90)) / 2.0 if (_ft > 0 or _drb > 0) else 0.0
        _sq_prog = (float(squad['avg_metric_raw'].get('Final Third Passes', 0) or 0) +
                    float(squad['avg_metric_raw'].get('Successful Dribbles', 0) or 0)) / 2.0
        contender_sim = simulate_contender_impact(round(_prog, 4), round(_sq_prog, 4))
        explanations  = generate_explanation(compat_scores, contender_sim)

        output = io.StringIO()
        writer = csv.writer(output)

        # Section 1: Summary
        writer.writerow(['SCOUTING REPORT — CLUB FIT ANALYSIS'])
        writer.writerow([])
        writer.writerow(['Player', cand['name']])
        writer.writerow(['Position', '/'.join(cand.get('positions', [cand['position']]))])
        writer.writerow(['Age', cand['age']])
        writer.writerow(['Current League Intensity', f"{cand['current_league_intensity'] * 100:.1f}%"])
        writer.writerow(['Role', role_file.split('/')[-1].replace('.json', '').replace('_', ' ')])
        writer.writerow(['Target Club', tclub.replace('_', ' ')])
        writer.writerow(['Target League', tcomp.replace('_', ' ')])
        writer.writerow(['Target Country', tc.replace('_', ' ')])
        writer.writerow(['Target League Intensity', f"{target_intensity * 100:.1f}%"])
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
            writer.writerow([
                m['name'], m['category'], m['higher_is_better'],
                m['candidate_raw'], m['squad_avg_raw'], m['raw_delta'],
                m['candidate_score'], m['squad_avg_score'], m['score_delta'],
            ])

        output.seek(0)
        safe_player = cand['name'].replace(' ', '_')
        safe_club   = tclub.replace('_', ' ').replace(' ', '_')
        filename_csv = f"ClubFit_{safe_player}_{safe_club}.csv"
        return send_file(
            io.BytesIO(output.getvalue().encode('utf-8-sig')),
            mimetype='text/csv',
            as_attachment=True,
            download_name=filename_csv,
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/export_club_fit_pdf', methods=['POST'])
def api_export_club_fit_pdf():
    """
    Run the Club Fit engine for the given player/role/club combination
    and return the result as a downloadable PDF scouting report.
    """
    body       = request.get_json(silent=True) or {}
    player_path = body.get('player', '')
    role_file   = body.get('role', '')
    club_path   = body.get('club', '')

    if not player_path or not role_file or not club_path:
        return jsonify({'error': 'player, role, and club are required'}), 400

    p_parts = player_path.split('/')
    c_parts = club_path.split('/')
    if len(p_parts) != 4 or len(c_parts) != 3:
        return jsonify({'error': 'invalid paths'}), 400

    player_fname = p_parts[3] if p_parts[3].endswith('.json') else p_parts[3] + '.json'
    player_full  = os.path.join(OUTPUT_DIR, p_parts[0], p_parts[1], p_parts[2], 'Players', player_fname)

    try:
        from tactical_match_engine.services.json_loader import get_league_info
        from tactical_match_engine.engine.role_encoder import get_role_fitness_vector
        from tactical_match_engine.engine.physical_adaptation import calculate_physical_adaptation
        from tactical_match_engine.engine.explanation_generator import generate_explanation
        from tactical_match_engine.engine.contender_simulation import simulate_contender_impact
        from tactical_match_engine.services.pdf_generator import generate_pdf

        cand = _load_player_for_engine(p_parts[0], p_parts[1], p_parts[2], player_fname)
        if cand is None:
            return jsonify({'error': 'player not found'}), 404
        cand_result = get_role_fitness_vector(
            cand['raw_stats'], cand['position'], role_path=role_file, flat_weights=True
        )
        if cand_result is None:
            return jsonify({'error': 'no role profile available for this position'}), 404

        tc, tcomp, tclub = c_parts
        t_info           = get_league_info(tc.replace('_', ' '), tcomp.replace('_', ' '))
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
        squad    = _get_squad_role_analysis(tc, tcomp, tclub, role_file,
                                            cand_line=cand_line, exclude_path=player_full)

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
                'name':             m['name'],
                'category':         m['category'],
                'higher_is_better': m['higher_is_better'],
                'candidate_raw':    m['raw_value'],
                'candidate_score':  m['score'],
                'squad_avg_raw':    sq_metric_raw.get(m['name'], 0.0),
                'squad_avg_score':  sq_metric_avgs.get(m['name'], 0.0),
                'raw_delta':        round(m['raw_value'] - sq_metric_raw.get(m['name'], 0.0), 4),
                'score_delta':      round(m['score']     - sq_metric_avgs.get(m['name'], 0.0), 2),
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
        _sq_prog = (float(squad['avg_metric_raw'].get('Final Third Passes', 0) or 0) +
                    float(squad['avg_metric_raw'].get('Successful Dribbles', 0) or 0)) / 2.0
        contender_sim = simulate_contender_impact(round(_prog, 4), round(_sq_prog, 4))
        explanations  = generate_explanation(compat_scores, contender_sim)

        result_dict = {
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
                },
            },
            'target': {
                'club':        tclub.replace('_', ' '),
                'competition': tcomp.replace('_', ' '),
                'country':     tc.replace('_', ' '),
                'squad_avg_score': squad_avg,
            },
            'scores': {
                'role_fitness':      role_fitness,
                'squad_impact':      squad_impact,
                'squad_impact_norm': squad_impact_norm,
                'league_adaptation': league_adaptation,
                'combined_score':    combined,
            },
            'verdict':              verdict,
            'move_label':           move_label,
            'metric_deltas':        metric_deltas,
            'contender_simulation': contender_sim,
            'explanations':         explanations,
        }

        pdf_bytes = generate_pdf(result_dict)
        safe_player = cand['name'].replace(' ', '_')
        safe_club   = tclub.replace('_', ' ').replace(' ', '_')
        filename_pdf = f"ClubFit_{safe_player}_{safe_club}.pdf"
        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename_pdf,
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/batch_compare')
def batch_compare():
    competitions = get_all_competitions(OUTPUT_DIR)
    return render_template('batch_compare.html', competitions=competitions)


@bp.route('/api/batch_compare', methods=['POST'])
def api_batch_compare():
    """
    Score a list of players against a single target club + role.
    Body JSON::
        {
            "players": ["Country/Competition/Club/file.json", ...],  # max 30
            "role":    "2. Mid-Line/I_Central_Midfielder.json",
            "club":    "Country/Competition/Club"
        }
    Returns a ranked list of per-player results.
    """
    import concurrent.futures

    body        = request.get_json(silent=True) or {}
    player_list = body.get('players', [])
    role_file   = str(body.get('role', '')).strip()
    club_path   = str(body.get('club', '')).strip()

    if not player_list or not role_file or not club_path:
        return jsonify({'error': 'players, role, and club are required'}), 400
    if len(player_list) > 30:
        return jsonify({'error': 'maximum 30 players per batch'}), 400

    c_parts = club_path.split('/')
    if len(c_parts) != 3:
        return jsonify({'error': 'club path must be Country/Competition/Club'}), 400

    try:
        from tactical_match_engine.services.json_loader import (
            load_sofascore_player, get_league_info,
        )
        from tactical_match_engine.engine.role_encoder import get_role_fitness_vector
        from tactical_match_engine.engine.physical_adaptation import calculate_physical_adaptation

        tc, tcomp, tclub = c_parts
        t_info           = get_league_info(tc.replace('_', ' '), tcomp.replace('_', ' '))
        target_intensity = t_info['intensity']

        # Compute squad stats once (shared across all players)
        squad = _get_squad_role_analysis(tc, tcomp, tclub, role_file, cand_line=None)
        squad_avg = squad['avg_overall']

        def _score_player(player_path: str) -> dict:
            p_parts  = player_path.strip().split('/')
            if len(p_parts) != 4:
                return {'error': 'invalid path', 'player_path': player_path}
            player_fname = p_parts[3] if p_parts[3].endswith('.json') else p_parts[3] + '.json'
            try:
                cand = _load_player_for_engine(p_parts[0], p_parts[1], p_parts[2], player_fname)
                if cand is None:
                    return {'error': 'player not found', 'player_path': player_path}
                cand_result = get_role_fitness_vector(
                    cand['raw_stats'], cand['position'],
                    role_path=role_file, flat_weights=True,
                )
                if cand_result is None:
                    return {'error': 'no role profile', 'player_path': player_path, 'name': cand.get('name', '')}

                role_fitness      = cand_result['overall_score']
                league_adaptation = round(
                    calculate_physical_adaptation(cand['current_league_intensity'], target_intensity) * 100.0, 2
                )
                squad_impact      = round(role_fitness - squad_avg, 2) if squad['player_count'] else 0.0
                squad_impact_norm = round(min(100.0, max(0.0, 50.0 + squad_impact)), 2) if squad['player_count'] else 50.0
                combined = round(0.40 * role_fitness + 0.30 * squad_impact_norm + 0.30 * league_adaptation, 2)

                if combined >= 72:   verdict = 'Starter Upgrade'
                elif combined >= 58: verdict = 'Strong Signing'
                elif combined >= 44: verdict = 'Squad Depth'
                elif combined >= 32: verdict = 'Development Option'
                else:                verdict = 'Below Squad Level'

                return {
                    'player_path':       player_path,
                    'name':              cand['name'],
                    'position':          cand['position'],
                    'age':               cand.get('age', ''),
                    'team':              cand.get('stats', {}).get('team', p_parts[2].replace('_', ' ')),
                    'league':            cand.get('current_league_name', ''),
                    'league_intensity':  round(cand['current_league_intensity'], 4),
                    'combined_score':    combined,
                    'role_fitness':      round(role_fitness, 2),
                    'squad_impact_norm': squad_impact_norm,
                    'league_adaptation': league_adaptation,
                    'verdict':           verdict,
                }
            except Exception as exc:
                return {'error': str(exc), 'player_path': player_path}

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            raw_results = list(pool.map(_score_player, player_list))

        ok      = [r for r in raw_results if 'error' not in r]
        errors  = [r for r in raw_results if 'error' in r]
        ok.sort(key=lambda x: x['combined_score'], reverse=True)
        for i, r in enumerate(ok, 1):
            r['rank'] = i

        return jsonify({
            'club':         tclub.replace('_', ' '),
            'competition':  tcomp.replace('_', ' '),
            'country':      tc.replace('_', ' '),
            'role':         role_file.split('/')[-1].replace('.json', '').replace('_', ' '),
            'squad_avg':    round(squad_avg, 2),
            'results':      ok,
            'errors':       errors,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/export_shortlist_xlsx', methods=['POST'])
def api_export_shortlist_xlsx():
    """
    Convert a batch comparison result set to an XLSX file for download.
    Body JSON::
        {
            "results":   [...],    # list of scored player dicts from /api/batch_compare
            "club_name": "FC ...",
            "role_name": "Deep-Lying Playmaker"
        }
    """
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    body      = request.get_json(silent=True) or {}
    results   = body.get('results', [])
    club_name = str(body.get('club_name', 'Club')).strip()
    role_name = str(body.get('role_name', 'Role')).strip()

    if not results:
        return jsonify({'error': 'results are required'}), 400

    # ── Colour helpers ────────────────────────────────────────────────────────
    def _verdict_fill(verdict: str) -> PatternFill:
        mapping = {
            'Starter Upgrade':   '22c55e',
            'Strong Signing':    '0ea5e9',
            'Squad Depth':       'f59e0b',
            'Development Option':'a855f7',
            'Below Squad Level': 'ef4444',
        }
        return PatternFill('solid', fgColor=mapping.get(verdict, '64748b'))

    def _score_fill(score: float) -> PatternFill:
        if score >= 72:  return PatternFill('solid', fgColor='22c55e')
        if score >= 44:  return PatternFill('solid', fgColor='f59e0b')
        return PatternFill('solid', fgColor='ef4444')

    thin = Border(
        left=Side(style='thin', color='1e293b'),
        right=Side(style='thin', color='1e293b'),
        top=Side(style='thin', color='1e293b'),
        bottom=Side(style='thin', color='1e293b'),
    )

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Shortlist'

    # ── Title row ─────────────────────────────────────────────────────────────
    ws.merge_cells('A1:J1')
    ws['A1'] = f'Shortlist — {club_name} | Role: {role_name}'
    ws['A1'].font      = Font(bold=True, size=13, color='E2E8F0')
    ws['A1'].fill      = PatternFill('solid', fgColor='0d1b2a')
    ws['A1'].alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[1].height = 22

    # ── Column headers ────────────────────────────────────────────────────────
    headers = [
        'Rank', 'Name', 'Position', 'Age', 'Team', 'League',
        'Combined', 'Role Fit', 'Squad Impact', 'League Adapt', 'Verdict',
    ]
    ws.append(headers)
    for col_idx, _ in enumerate(headers, 1):
        cell = ws.cell(row=2, column=col_idx)
        cell.font      = Font(bold=True, color='E2E8F0', size=9)
        cell.fill      = PatternFill('solid', fgColor='1e293b')
        cell.alignment = Alignment(horizontal='center', vertical='center')
        cell.border    = thin
    ws.row_dimensions[2].height = 16

    # ── Data rows ─────────────────────────────────────────────────────────────
    for r in results:
        row_num = ws.max_row + 1
        combined = float(r.get('combined_score', 0))
        verdict  = r.get('verdict', '')
        row_data = [
            r.get('rank', ''),
            r.get('name', ''),
            r.get('position', ''),
            r.get('age', ''),
            r.get('team', ''),
            r.get('league', ''),
            combined,
            float(r.get('role_fitness', 0)),
            float(r.get('squad_impact_norm', 0)),
            float(r.get('league_adaptation', 0)),
            verdict,
        ]
        ws.append(row_data)
        for col_idx, value in enumerate(row_data, 1):
            cell = ws.cell(row=row_num, column=col_idx)
            cell.alignment = Alignment(horizontal='center', vertical='center')
            cell.border    = thin
            cell.font      = Font(size=9, color='E2E8F0')
            # Dark alternating row bg
            bg = '0f172a' if row_num % 2 == 0 else '0d1117'
            cell.fill = PatternFill('solid', fgColor=bg)
        # Score colours for Combined column (col 7)
        ws.cell(row=row_num, column=7).fill  = _score_fill(combined)
        ws.cell(row=row_num, column=7).font  = Font(bold=True, size=9, color='0d1117')
        ws.cell(row=row_num, column=11).fill = _verdict_fill(verdict)
        ws.cell(row=row_num, column=11).font = Font(bold=True, size=9, color='0d1117')
        ws.row_dimensions[row_num].height = 15

    # ── Column widths ─────────────────────────────────────────────────────────
    col_widths = [6, 24, 10, 6, 22, 22, 10, 10, 13, 13, 18]
    for i, w in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    # ── Freeze panes ──────────────────────────────────────────────────────────
    ws.freeze_panes = 'A3'

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    safe_club = club_name.replace(' ', '_')
    safe_role = role_name.replace(' ', '_')
    return send_file(
        buf,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'Shortlist_{safe_club}_{safe_role}.xlsx',
    )
