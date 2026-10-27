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
                    'details':             club_doc.get('details') or {},
                    'transfers':           club_doc.get('transfers') or {},
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
                        'details':             cd.get('details') or {},
                        'transfers':           cd.get('transfers') or {},
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

        from services.recruitment import build_nudge
        raw_by_slug = {c['slug']: c for c in clubs_raw}

        teams = []
        for fr in form_results:
            slug = fr['slug']
            style_info = style_by_slug.get(slug, {})
            raw        = raw_by_slug.get(slug, {})
            teams.append({
                **fr,
                'positional_passing': pos_by_slug.get(slug, {}),
                'style_scores':       style_info.get('style_scores', {}),
                'primary_style':      style_info.get('primary_style', ''),
                'radar':              style_info.get('radar', {}),
                'stats_pm':           stats_by_slug.get(slug, {}),
                'nudge':              build_nudge(
                    stats_by_slug.get(slug, {}), stat_defs, league_avg, total_teams,
                    style_info.get('style_scores', {}), style_info.get('primary_style', ''),
                    style_info.get('radar', {}),
                    raw.get('details'), raw.get('transfers')),
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


def _load_playing_style_pool(country, competition):
    """Load the club pool for a competition (Mongo-first, file-fallback).

    Returns (clubs_raw, manager_by_slug, standings_by_tid, tid_by_slug) or None
    when the competition has no data. coach (`manager_by_slug`) and standings
    come from the live DB when Mongo is up, so they reflect the Refresh Coach
    Data button."""
    clubs_raw = []
    manager_by_slug  = {}
    standings_by_tid = {}
    tid_by_slug      = {}

    def _ingest_standings(sdoc):
        for grp in sdoc.get('standings', []):
            for row in grp.get('rows', []):
                tid = str((row.get('team') or {}).get('id', ''))
                if not tid:
                    continue
                gf, ga = row.get('scoresFor'), row.get('scoresAgainst')
                gd = (gf - ga) if isinstance(gf, (int, float)) and isinstance(ga, (int, float)) else None
                standings_by_tid[tid] = {
                    'rank': row.get('position'), 'points': row.get('points'),
                    'w': row.get('wins'), 'd': row.get('draws'), 'l': row.get('losses'),
                    'gf': gf, 'ga': ga, 'gd': gd,
                }

    if ctx._USING_MONGO:
        import mongo_loader as _ml
        db = _ml._db()
        standings_doc = db.standings.find_one(
            {'_country': country, '_competition': competition}) or {}
        _ingest_standings(standings_doc)
        for club_doc in db.clubs.find({'_country': country, '_competition': competition},
                                      sort=[('team_name', 1)]):
            season_stats = club_doc.get('season_statistics', {}).get('statistics', {})
            tc = club_doc.get('profile', {}).get('team', {}).get('teamColors', {})
            cslug = club_doc.get('_club', '')
            tid   = str(club_doc.get('team_id', ''))
            tid_by_slug[cslug] = tid
            manager_by_slug[cslug] = (club_doc.get('details') or {}).get('manager') or {}
            clubs_raw.append({
                'name': club_doc.get('team_name', cslug), 'slug': cslug, 'logo_url': '',
                'season_stats': season_stats, 'matches': int(season_stats.get('matches') or 0),
                'team_colors': {'primary': tc.get('primary', '#6E6E6E'),
                                'secondary': tc.get('secondary', '#D9D9D9')},
                'team_id': tid,
                # squad shape + window activity, for the recruitment nudge
                'details':   club_doc.get('details') or {},
                'transfers': club_doc.get('transfers') or {},
            })
    else:
        comp_dir = os.path.join(OUTPUT_DIR, country, competition)
        if not os.path.isdir(comp_dir):
            return None
        for spath in glob.glob(os.path.join(comp_dir, 'Standings_*_Season_*.json')):
            try:
                with open(spath, encoding='utf-8') as f:
                    _ingest_standings(json.load(f))
            except Exception:
                pass
        for club_name in sorted(os.listdir(comp_dir)):
            cj = glob.glob(os.path.join(comp_dir, club_name, 'Club_*_Season_*.json'))
            if not cj:
                continue
            try:
                with open(cj[0], encoding='utf-8') as f:
                    cd = json.load(f)
            except Exception:
                continue
            season_stats = cd.get('season_statistics', {}).get('statistics', {})
            tid = str(cd.get('team_id', ''))
            tid_by_slug[club_name] = tid
            manager_by_slug[club_name] = (cd.get('details') or {}).get('manager') or {}
            clubs_raw.append({
                'name': cd.get('team_name', club_name.replace('_', ' ')), 'slug': club_name,
                'logo_url': '', 'season_stats': season_stats,
                'matches': int(season_stats.get('matches') or 0),
                'team_colors': {'primary': '#6E6E6E', 'secondary': '#D9D9D9'}, 'team_id': tid,
                'details':   cd.get('details') or {},
                'transfers': cd.get('transfers') or {},
            })

    if not clubs_raw:
        return None
    return clubs_raw, manager_by_slug, standings_by_tid, tid_by_slug


@bp.route('/api/playing_style_export_xlsx')
def api_playing_style_export_xlsx():
    """Styled 3-sheet Excel for ONE team's playing-style profile:
       Sheet 1 Overview     — primary/top-3 styles, coach, formation, standing.
       Sheet 2 Per-Match     — per-match stats vs league avg (diff/rank/pct/tier).
       Sheet 3 Style Scores  — all 10 style confidences + 15 tactical metrics.
    Data is Mongo-first (coach/standings live), file-fallback."""
    import openpyxl
    from openpyxl.utils import get_column_letter
    from datetime import datetime
    from services.xlsx_style import (
        hdr_cell, data_cell, tier_cell, title_row, meta_row, tier_for,
        C_ACCENT, C_TEXT, C_TEXT2,
    )
    from style_engine import (compute_style_scores, compute_stats_table,
                              METRIC_KEYS, METRIC_LABELS)

    country     = request.args.get('country', '').strip()
    competition = request.args.get('competition', '').strip()
    slug        = request.args.get('slug', '').strip()
    if not country or not competition or not slug:
        return jsonify({'error': 'country, competition and slug are required'}), 400

    pool = _load_playing_style_pool(country, competition)
    if pool is None:
        return jsonify({'error': 'no club data for this competition'}), 404
    clubs_raw, manager_by_slug, standings_by_tid, tid_by_slug = pool

    sel = next((c for c in clubs_raw if c['slug'] == slug), None)
    if sel is None:
        return jsonify({'error': f'club {slug} not found in {competition}'}), 404

    # ── Compute styles (pool) + per-match stats table ─────────────────────────
    style_by_slug = {r['slug']: r for r in compute_style_scores(clubs_raw)}
    stats_result  = compute_stats_table(clubs_raw)
    stat_defs   = stats_result['stat_defs']
    league_avg  = stats_result['league_avg']
    total_teams = stats_result['total_teams']
    stats_pm    = stats_result['teams'].get(slug, {})

    style_info = style_by_slug.get(slug, {})
    style_scores = style_info.get('style_scores', {})
    primary = style_info.get('primary_style', '—')
    radar   = style_info.get('radar', {})
    top_styles = sorted(style_scores.items(), key=lambda kv: kv[1], reverse=True)

    mgr = manager_by_slug.get(slug, {})
    st  = standings_by_tid.get(tid_by_slug.get(slug, ''), {})
    w, d, l = st.get('w'), st.get('d'), st.get('l')
    games = (w + d + l) if all(isinstance(x, (int, float)) for x in (w, d, l)) else None
    ppg   = round(st['points'] / games, 2) if isinstance(st.get('points'), (int, float)) and games else '—'

    comp_label = competition.replace('_', ' ')
    gen = datetime.now().strftime('%d %b %Y  %H:%M')
    src = 'live DB' if ctx._USING_MONGO else 'on-disk JSON'

    wb = openpyxl.Workbook()

    # ── SHEET 1 — Overview (key/value) ────────────────────────────────────────
    ws1 = wb.active
    ws1.title = 'Overview'
    title_row(ws1, f"{sel['name']}  ·  Playing Style Profile", 2)
    meta_row(ws1, f'{comp_label}   ·   Generated: {gen}   ·   coach/standings from {src}', 2)
    for ci, h in enumerate(['Attribute', 'Value'], 1):
        hdr_cell(ws1, 3, ci, h)
    _rows = [
        ('Primary Style',       f"{primary}  ({round(style_scores.get(primary, 0) * 100)}%)"),
        ('2nd Style',           f"{top_styles[1][0]}  ({round(top_styles[1][1]*100)}%)" if len(top_styles) > 1 else '—'),
        ('3rd Style',           f"{top_styles[2][0]}  ({round(top_styles[2][1]*100)}%)" if len(top_styles) > 2 else '—'),
        ('Head Coach',          mgr.get('name') or '—'),
        ('Preferred Formation', mgr.get('preferred_formation') or '—'),
        ('League Position',     st.get('rank', '—')),
        ('Points',              st.get('points', '—')),
        ('Record (W-D-L)',      f"{w}-{d}-{l}" if games else '—'),
        ('Points / Game',       ppg),
        ('Goals (F / A / GD)',  f"{st.get('gf','—')} / {st.get('ga','—')} / {st.get('gd','—')}"),
        ('Matches (stats)',     sel['matches']),
    ]
    for i, (k, v) in enumerate(_rows, 1):
        r = 3 + i
        data_cell(ws1, r, 1, k, align='left', color=C_TEXT2, bold=True, row_num=r)
        clr = C_ACCENT if k in ('Primary Style', 'Preferred Formation', 'League Position') else C_TEXT
        data_cell(ws1, r, 2, v, align='left', color=clr, row_num=r)
    ws1.column_dimensions['A'].width = 22
    ws1.column_dimensions['B'].width = 34
    ws1.freeze_panes = 'A4'

    # ── SHEET 2 — Per-Match Stats ─────────────────────────────────────────────
    ws2 = wb.create_sheet('Per-Match Stats')
    S2 = ['Metric', 'This Team', 'Lg Avg', 'Diff', 'Rank', 'Percentile', 'Tier']
    title_row(ws2, f"{sel['name']}  ·  Per-Match Stats vs League", len(S2))
    meta_row(ws2, f'{comp_label}   ·   {total_teams} teams   ·   diff/rank vs league average', len(S2))
    for ci, h in enumerate(S2, 1):
        hdr_cell(ws2, 3, ci, h)
    r = 3
    for d_ in stat_defs:
        entry = stats_pm.get(d_['field']) or {}
        val   = entry.get('value')
        if val is None:
            continue
        r += 1
        avg  = league_avg.get(d_['field'])
        rank = entry.get('rank')
        diff = round(val - avg, 2) if isinstance(avg, (int, float)) else None
        pct  = round((1 - (rank - 1) / (total_teams - 1)) * 100, 1) if rank and total_teams > 1 else None
        # green/red diff respecting lower_is_better
        good = None
        if diff is not None and diff != 0:
            good = (diff > 0) != bool(d_.get('lower_is_better'))
        diff_color = ('34D399' if good else 'F87171') if good is not None else C_TEXT
        data_cell(ws2, r, 1, d_['label'], align='left', bold=True, row_num=r)
        data_cell(ws2, r, 2, round(val, 2), row_num=r)
        data_cell(ws2, r, 3, round(avg, 2) if isinstance(avg, (int, float)) else '—', color=C_TEXT2, row_num=r)
        data_cell(ws2, r, 4, (f'+{diff}' if diff and diff > 0 else diff) if diff is not None else '—',
                  color=diff_color, bold=True, row_num=r)
        data_cell(ws2, r, 5, f'{rank} / {total_teams}' if rank else '—', color=C_ACCENT, row_num=r)
        data_cell(ws2, r, 6, f'{pct}%' if pct is not None else '—', row_num=r)
        tier_cell(ws2, r, 7, tier_for(pct), row_num=r)
    for i, wd in enumerate([22, 11, 10, 9, 11, 11, 14], 1):
        ws2.column_dimensions[get_column_letter(i)].width = wd
    ws2.freeze_panes = 'A4'

    # ── SHEET 3 — Style Scores + tactical metrics ─────────────────────────────
    ws3 = wb.create_sheet('Style Scores')
    title_row(ws3, f"{sel['name']}  ·  Style Fingerprint", 2)
    meta_row(ws3, f'{comp_label}   ·   confidence 0-100   ·   metrics: 50 = league average', 2)
    hdr_cell(ws3, 3, 1, 'Playing Style'); hdr_cell(ws3, 3, 2, 'Confidence')
    r = 3
    for style, score in top_styles:
        r += 1
        hi = (style == primary)
        data_cell(ws3, r, 1, style, align='left', color=C_ACCENT if hi else C_TEXT, bold=hi, row_num=r)
        data_cell(ws3, r, 2, f'{round(score * 100)}%', bold=hi, row_num=r)
    r += 2
    hdr_cell(ws3, r, 1, 'Tactical Metric'); hdr_cell(ws3, r, 2, 'Value (0-100)')
    for k in METRIC_KEYS:
        r += 1
        data_cell(ws3, r, 1, METRIC_LABELS.get(k, k), align='left', row_num=r)
        data_cell(ws3, r, 2, radar.get(k, 50), row_num=r)
    ws3.column_dimensions['A'].width = 24
    ws3.column_dimensions['B'].width = 14

    # ── SHEET 4 — Recruitment Nudge ───────────────────────────────────────────
    from services.recruitment import build_nudge
    nudge = build_nudge(stats_pm, stat_defs, league_avg, total_teams,
                        style_scores, primary, radar,
                        sel.get('details'), sel.get('transfers'))

    ws4 = wb.create_sheet('Recruitment Nudge')
    S4_W = 6
    title_row(ws4, f"{sel['name']}  ·  Recruitment Nudge", S4_W)
    meta_row(ws4, f'{comp_label}   ·   {total_teams} teams   ·   rule-based from season stats, '
                  f'style fingerprint, squad shape and this transfer window', S4_W)

    def _section(row, text):
        hdr_cell(ws4, row, 1, text)
        for ci in range(2, S4_W + 1):
            hdr_cell(ws4, row, ci, '')
        return row + 1

    r = 3
    styles_txt = '   ·   '.join(f"{s['style']} {s['pct']}%" for s in nudge['style_summary']) or '—'
    data_cell(ws4, r, 1, 'Style', align='left', color=C_TEXT2, bold=True, row_num=r)
    ws4.merge_cells(start_row=r, start_column=2, end_row=r, end_column=S4_W)
    data_cell(ws4, r, 2, styles_txt, align='left', color=C_ACCENT, row_num=r)
    r += 2

    # A — priority gaps
    r = _section(r, 'A. PRIORITY GAPS  (worst ranks vs league)')
    for ci, h in enumerate(['Metric', 'This Team', 'Lg Avg', 'Diff', 'Rank', ''], 1):
        hdr_cell(ws4, r, ci, h)
    r += 1
    if nudge['priority_gaps']:
        for g in nudge['priority_gaps']:
            data_cell(ws4, r, 1, g['label'], align='left', bold=True, row_num=r)
            data_cell(ws4, r, 2, g['value'], row_num=r)
            data_cell(ws4, r, 3, g['avg'] if g['avg'] is not None else '—', row_num=r)
            diff = g['diff']
            data_cell(ws4, r, 4, (f'+{diff}' if diff is not None and diff >= 0 else diff)
                      if diff is not None else '—', row_num=r)
            data_cell(ws4, r, 5, f"{g['rank']} / {g['total']}", color=C_ACCENT, bold=True, row_num=r)
            data_cell(ws4, r, 6, 'lower is better' if g['lower_is_better'] else '', row_num=r)
            r += 1
    else:
        data_cell(ws4, r, 1, 'No bottom-third weaknesses — squad is balanced against this league.',
                  align='left', color=C_TEXT2, row_num=r)
        r += 1
    r += 1

    # B — style conflicts
    r = _section(r, 'B. UNDERCUTS YOUR STYLE')
    for ci, h in enumerate(['Metric', 'Direction', 'Style it serves', 'z-score', '', ''], 1):
        hdr_cell(ws4, r, ci, h)
    r += 1
    if nudge['style_conflicts']:
        for x in nudge['style_conflicts']:
            data_cell(ws4, r, 1, x['label'], align='left', bold=True, row_num=r)
            data_cell(ws4, r, 2, x['direction'], color=C_ACCENT, row_num=r)
            data_cell(ws4, r, 3, f"{x['style']}  {x['style_pct']}%", align='left', row_num=r)
            data_cell(ws4, r, 4, x['z'], row_num=r)
            data_cell(ws4, r, 5, '', row_num=r)
            data_cell(ws4, r, 6, '', row_num=r)
            r += 1
    else:
        data_cell(ws4, r, 1, 'Nothing in the profile actively works against the style played.',
                  align='left', color=C_TEXT2, row_num=r)
        r += 1
    r += 1

    # Suggested profiles
    r = _section(r, 'SUGGESTED PLAYER PROFILES')
    for ci, h in enumerate(['Priority', 'Role', 'Key attributes', '', 'Why', ''], 1):
        hdr_cell(ws4, r, ci, h)
    r += 1
    if nudge['profiles']:
        for p in nudge['profiles']:
            data_cell(ws4, r, 1, p['priority'],
                      color=C_ACCENT if p['priority'] == 'High' else C_TEXT2, bold=True, row_num=r)
            data_cell(ws4, r, 2, p['role'], align='left', bold=True, row_num=r)
            ws4.merge_cells(start_row=r, start_column=3, end_row=r, end_column=4)
            data_cell(ws4, r, 3, ', '.join(p['attributes']), align='left', row_num=r)
            ws4.merge_cells(start_row=r, start_column=5, end_row=r, end_column=6)
            why = p['why'] + (f"  ({p['caveat']})" if p.get('caveat') else '')
            data_cell(ws4, r, 5, why, align='left', color=C_TEXT2, row_num=r)
            r += 1
    else:
        data_cell(ws4, r, 1, 'No archetype triggered — no clear statistical case for a signing.',
                  align='left', color=C_TEXT2, row_num=r)
        r += 1
    r += 1

    # Squad context
    r = _section(r, 'SQUAD CONTEXT')
    for note in (nudge['squad_notes'] or ['No squad details on file for this club.']):
        ws4.merge_cells(start_row=r, start_column=1, end_row=r, end_column=S4_W)
        data_cell(ws4, r, 1, note, align='left', color=C_TEXT2, row_num=r)
        r += 1

    for i, wd in enumerate([18, 30, 22, 22, 46, 18], 1):
        ws4.column_dimensions[get_column_letter(i)].width = wd

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    safe = re.sub(r'[^A-Za-z0-9 _-]', '', sel['name']).strip().replace(' ', '_') or 'Team'
    return send_file(
        buf,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'TeamStyle_{safe}.xlsx',
    )


@bp.route('/api/playing_style_export_all_xlsx')
def api_playing_style_export_all_xlsx():
    """League-wide styled workbook for ALL teams, ordered by league position:
       Sheet 1 Team Profiles — style + coach + formation + standing per team.
       Sheet 2 Style Matrix  — every team × 10 style confidences (primary hi-lit).
       Sheet 3 Per-Match      — every team × per-match stats (+ league avg row).
    Mongo-first (coach/standings live), file-fallback."""
    import openpyxl
    from openpyxl.utils import get_column_letter
    from datetime import datetime
    from services.xlsx_style import (
        hdr_cell, data_cell, title_row, meta_row, C_ACCENT, C_TEXT, C_TEXT2,
    )
    from style_engine import compute_style_scores, compute_stats_table, STYLE_KEYS

    country     = request.args.get('country', '').strip()
    competition = request.args.get('competition', '').strip()
    if not country or not competition:
        return jsonify({'error': 'country and competition are required'}), 400

    pool = _load_playing_style_pool(country, competition)
    if pool is None:
        return jsonify({'error': 'no club data for this competition'}), 404
    clubs_raw, manager_by_slug, standings_by_tid, tid_by_slug = pool

    style_by_slug = {r['slug']: r for r in compute_style_scores(clubs_raw)}
    stats_result  = compute_stats_table(clubs_raw)
    stat_defs   = stats_result['stat_defs']
    league_avg  = stats_result['league_avg']
    stats_teams = stats_result['teams']

    # Order all teams by league position (unranked fall to the end, by name).
    def _pos_key(c):
        st  = standings_by_tid.get(tid_by_slug.get(c['slug'], ''), {})
        pos = st.get('rank')
        return (0, pos) if isinstance(pos, int) else (1, c['name'].lower())
    ordered = sorted(clubs_raw, key=_pos_key)

    comp_label = competition.replace('_', ' ')
    gen = datetime.now().strftime('%d %b %Y  %H:%M')
    src = 'live DB' if ctx._USING_MONGO else 'on-disk JSON'
    meta_base = f'{comp_label}   ·   {len(ordered)} teams   ·   Generated: {gen}'

    wb = openpyxl.Workbook()

    # ── SHEET 1 — Team Profiles ───────────────────────────────────────────────
    ws1 = wb.active
    ws1.title = 'Team Profiles'
    S1 = ['Team', 'Rank', 'Pts', 'W', 'D', 'L', 'GD', 'PPG',
          'Primary Style', 'Coach', 'Preferred Formation']
    title_row(ws1, f'{comp_label}  ·  Team Profiles', len(S1))
    meta_row(ws1, meta_base + f'   ·   coach/standings from {src}', len(S1))
    for ci, h in enumerate(S1, 1):
        hdr_cell(ws1, 3, ci, h)
    r = 3
    for c in ordered:
        r += 1
        st  = standings_by_tid.get(tid_by_slug.get(c['slug'], ''), {})
        mgr = manager_by_slug.get(c['slug'], {})
        w, d, l = st.get('w'), st.get('d'), st.get('l')
        games = (w + d + l) if all(isinstance(x, (int, float)) for x in (w, d, l)) else None
        ppg = round(st['points'] / games, 2) if isinstance(st.get('points'), (int, float)) and games else '—'
        primary = style_by_slug.get(c['slug'], {}).get('primary_style', '—')
        vals = [c['name'], st.get('rank', '—'), st.get('points', '—'),
                w if w is not None else '—', d if d is not None else '—', l if l is not None else '—',
                st.get('gd', '—'), ppg, primary, mgr.get('name') or '—',
                mgr.get('preferred_formation') or '—']
        for ci, v in enumerate(vals, 1):
            if ci == 1:
                data_cell(ws1, r, ci, v, align='left', bold=True, row_num=r)
            elif ci == 2:
                data_cell(ws1, r, ci, v, color=C_ACCENT, bold=True, row_num=r)
            elif ci == 9:
                data_cell(ws1, r, ci, v, align='left', color=C_ACCENT, row_num=r)
            elif ci in (10, 11):
                data_cell(ws1, r, ci, v, align='left', row_num=r)
            else:
                data_cell(ws1, r, ci, v, row_num=r)
    for i, wd in enumerate([22, 6, 6, 5, 5, 5, 7, 7, 22, 20, 18], 1):
        ws1.column_dimensions[get_column_letter(i)].width = wd
    ws1.freeze_panes = 'B4'

    # ── SHEET 2 — Style Matrix (teams × 10 styles) ────────────────────────────
    ws2 = wb.create_sheet('Style Matrix')
    S2 = ['Team'] + STYLE_KEYS
    title_row(ws2, f'{comp_label}  ·  Style Matrix (confidence 0-100)', len(S2))
    meta_row(ws2, meta_base + '   ·   each row = a team’s confidence per style; primary highlighted', len(S2))
    for ci, h in enumerate(S2, 1):
        hdr_cell(ws2, 3, ci, h)
    r = 3
    for c in ordered:
        r += 1
        ss = style_by_slug.get(c['slug'], {}).get('style_scores', {})
        primary = style_by_slug.get(c['slug'], {}).get('primary_style', '')
        data_cell(ws2, r, 1, c['name'], align='left', bold=True, row_num=r)
        for ci, style in enumerate(STYLE_KEYS, 2):
            val = round(ss.get(style, 0) * 100)
            hi  = (style == primary)
            data_cell(ws2, r, ci, val, color=C_ACCENT if hi else C_TEXT, bold=hi, row_num=r)
    ws2.column_dimensions['A'].width = 22
    for i in range(2, len(S2) + 1):
        ws2.column_dimensions[get_column_letter(i)].width = 13
    ws2.freeze_panes = 'B4'

    # ── SHEET 3 — Per-Match Stats (teams × display stats) ─────────────────────
    ws3 = wb.create_sheet('Per-Match Stats')
    S3 = ['Team'] + [d_['label'] for d_ in stat_defs]
    title_row(ws3, f'{comp_label}  ·  Per-Match Stats (per game)', len(S3))
    meta_row(ws3, meta_base + '   ·   values are per match; last row = league average', len(S3))
    for ci, h in enumerate(S3, 1):
        hdr_cell(ws3, 3, ci, h)
    r = 3
    for c in ordered:
        r += 1
        tstats = stats_teams.get(c['slug'], {})
        data_cell(ws3, r, 1, c['name'], align='left', bold=True, row_num=r)
        for ci, d_ in enumerate(stat_defs, 2):
            v = (tstats.get(d_['field']) or {}).get('value')
            data_cell(ws3, r, ci, v if v is not None else '—', row_num=r)
    # League average row
    r += 1
    data_cell(ws3, r, 1, 'League Average', align='left', color=C_ACCENT, bold=True, row_num=r)
    for ci, d_ in enumerate(stat_defs, 2):
        av = league_avg.get(d_['field'])
        data_cell(ws3, r, ci, av if av is not None else '—', color=C_TEXT2, row_num=r)
    ws3.column_dimensions['A'].width = 22
    for i in range(2, len(S3) + 1):
        ws3.column_dimensions[get_column_letter(i)].width = 12
    ws3.freeze_panes = 'B4'

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    safe = re.sub(r'[^A-Za-z0-9 _-]', '', comp_label).strip().replace(' ', '_') or 'Competition'
    return send_file(
        buf,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'PlayingStyles_{safe}.xlsx',
    )


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
