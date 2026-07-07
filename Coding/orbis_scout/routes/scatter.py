"""routes/scatter.py -- scatter blueprint (auto-extracted from the original app.py)."""

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
    compute_player_composites,
)
from composite_club_stats import compute_club_composites, CLUB_COMPOSITE_KEYS

bp = Blueprint('scatter', __name__)


# ── League data access: MongoDB-first, filesystem fallback ────────────────────
# These yield the raw club/player JSON docs for a league. On MongoDB (Vercel and
# local when Mongo is up) they read the `clubs`/`players` collections; otherwise
# they walk output/. Either way each doc has the same shape, and `_club` holds
# the club slug (dir name on disk, `_club` field in Mongo).
def _league_exists(country, competition):
    if ctx._USING_MONGO:
        return ctx._loader._db().clubs.count_documents(
            {'_country': country, '_competition': competition}, limit=1) > 0
    return os.path.isdir(os.path.join(OUTPUT_DIR, country, competition))


def _league_club_docs(country, competition):
    if ctx._USING_MONGO:
        yield from ctx._loader._db().clubs.find(
            {'_country': country, '_competition': competition})
        return
    league_dir = os.path.join(OUTPUT_DIR, country, competition)
    if not os.path.isdir(league_dir):
        return
    for club_entry in sorted(os.scandir(league_dir), key=lambda e: e.name):
        if not club_entry.is_dir():
            continue
        matches = glob.glob(os.path.join(club_entry.path, 'Club_*_Season_*.json'))
        if not matches:
            continue
        try:
            with open(matches[0], encoding='utf-8') as f:
                doc = json.load(f)
            doc.setdefault('_club', club_entry.name)
            yield doc
        except Exception:
            continue


def _league_player_docs(country, competition):
    if ctx._USING_MONGO:
        yield from ctx._loader._db().players.find(
            {'_country': country, '_competition': competition})
        return
    league_dir = os.path.join(OUTPUT_DIR, country, competition)
    if not os.path.isdir(league_dir):
        return
    for club_entry in sorted(os.scandir(league_dir), key=lambda e: e.name):
        if not club_entry.is_dir():
            continue
        players_dir = os.path.join(club_entry.path, 'Players')
        if not os.path.isdir(players_dir):
            continue
        for pf in sorted(os.scandir(players_dir), key=lambda e: e.name):
            if not pf.name.endswith('.json'):
                continue
            try:
                with open(pf.path, encoding='utf-8') as f:
                    doc = json.load(f)
                doc.setdefault('_club', club_entry.name)
                yield doc
            except Exception:
                continue


@bp.route('/scatter')
def scatter():
    competitions = get_all_competitions(OUTPUT_DIR)
    return render_template('scatter.html', competitions=competitions)


@bp.route('/api/scatter_data')
def api_scatter_data():
    import glob as _glob
    import json as _json

    competition = request.args.get('competition', '').strip()
    country     = request.args.get('country',     '').strip()
    if not competition or not country:
        return jsonify({'error': 'competition and country are required'}), 400

    if not _league_exists(country, competition):
        return jsonify({'error': 'competition directory not found'}), 404

    clubs     = []
    keys_seen = []   # preserves CLUB_STAT_FIELDS order, only adds keys once

    for raw in _league_club_docs(country, competition):
        try:
            stat_raw = raw.get('season_statistics', {}).get('statistics', {})
            stats     = {}
            stats_p90 = {}
            num_matches = stat_raw.get('matches')
            for field in CLUB_STAT_FIELDS:
                val = stat_raw.get(field)
                if val is not None and isinstance(val, (int, float)) and not isinstance(val, bool):
                    stats[field] = val
                    if field not in keys_seen:
                        keys_seen.append(field)
                    # Compute p90 for countable stats
                    if field not in _CLUB_P90_EXCLUDE and isinstance(num_matches, (int, float)) and num_matches > 0:
                        stats_p90[field] = round(val / num_matches, 2)
            # Inject club composite scores (attackingScore, passingScore, defendingScore)
            compute_club_composites(stats, stats_p90, num_matches)
            # Derive display name from JSON if available, else slug
            club_name = raw.get('team_name', str(raw.get('_club', '')).replace('_', ' '))
            clubs.append({
                'club':      club_name,
                'club_slug': raw.get('_club', ''),
                'matches':   int(num_matches) if isinstance(num_matches, (int, float)) else 0,
                'stats':     stats,
                'stats_p90': stats_p90,
            })
        except Exception:
            continue

    # stat_keys = union in CLUB_STAT_FIELDS order, then composite keys
    stat_keys = [k for k in CLUB_STAT_FIELDS if k in keys_seen] + CLUB_COMPOSITE_KEYS
    return jsonify({'clubs': clubs, 'stat_keys': stat_keys})


@bp.route('/api/scatter_export_xlsx')
def api_scatter_export_xlsx():
    """Export club scatter data to Excel — two sheets:
       Sheet 1: Club Scatter  — Club, Matches + x/y axes + rank/pct/tier/avg/median (top N)
       Sheet 2: All Attributes — Club, Matches + every raw stat + every p90 stat
       Sheet 3: League Summary — mean/median/min/max for every p90 stat over top N
    """
    import openpyxl, statistics
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from datetime import datetime
    import json as _json
    import glob as _glob

    competition = request.args.get('competition', '').strip()
    country     = request.args.get('country',     '').strip()
    x_key       = request.args.get('x_key',       '').strip()
    y_key       = request.args.get('y_key',       '').strip()
    stat_mode   = request.args.get('stat_mode',   'totals').strip()
    top_n       = int(request.args.get('top_n', '30'))

    if not competition or not country or not x_key or not y_key:
        return jsonify({'error': 'competition, country, x_key, y_key are required'}), 400

    if not _league_exists(country, competition):
        return jsonify({'error': 'competition directory not found'}), 404

    # ── Load clubs ────────────────────────────────────────────────────────────
    clubs = []
    for raw in _league_club_docs(country, competition):
        try:
            stat_raw    = raw.get('season_statistics', {}).get('statistics', {})
            num_matches = stat_raw.get('matches')
            stats     = {}
            stats_p90 = {}
            for field in CLUB_STAT_FIELDS:
                val = stat_raw.get(field)
                if val is not None and isinstance(val, (int, float)) and not isinstance(val, bool):
                    stats[field] = val
                    if field not in _CLUB_P90_EXCLUDE and isinstance(num_matches, (int, float)) and num_matches > 0:
                        stats_p90[field] = round(val / num_matches, 2)
            compute_club_composites(stats, stats_p90, num_matches)
            clubs.append({
                'club':      raw.get('team_name', str(raw.get('_club', '')).replace('_', ' ')),
                'club_slug': raw.get('_club', ''),
                'matches':   int(num_matches) if isinstance(num_matches, (int, float)) else 0,
                'stats':     stats,
                'stats_p90': stats_p90,
            })
        except Exception:
            continue

    # ── Rank by combined normalised x+y score ─────────────────────────────────
    def _sv(c, key):
        src = c['stats_p90'] if stat_mode == 'p90' else c['stats']
        return src.get(key)

    valid = [c for c in clubs if _sv(c, x_key) is not None and _sv(c, y_key) is not None]
    if valid:
        xv = [_sv(c, x_key) for c in valid]
        yv = [_sv(c, y_key) for c in valid]
        xr = max(xv) - min(xv) or 1
        yr = max(yv) - min(yv) or 1
        scored = sorted(
            [(c, (_sv(c, x_key) - min(xv)) / xr + (_sv(c, y_key) - min(yv)) / yr) for c in valid],
            key=lambda t: t[1], reverse=True,
        )
    else:
        scored = []

    export_clubs  = [c for c, _ in scored[:top_n]]
    total_ranked  = len(scored)

    # ── Per-axis analytics (rank, percentile, tier, avg, median) ─────────────
    def axis_analytics(key):
        vals = [(c['club_slug'], _sv(c, key)) for c in export_clubs if _sv(c, key) is not None]
        n = len(vals)
        srt = sorted(vals, key=lambda t: t[1], reverse=True)
        rank_map = {slug: i + 1 for i, (slug, _) in enumerate(srt)}
        all_v = [v for _, v in vals]
        avg_v = round(statistics.mean(all_v),   2) if all_v else None
        med_v = round(statistics.median(all_v), 2) if all_v else None

        def _pct(slug):
            if n < 2 or slug not in rank_map: return None
            return round((1 - (rank_map[slug] - 1) / (n - 1)) * 100, 1)

        def _tier(p):
            if p is None:  return '—'
            if p >= 90:    return 'Elite'
            if p >= 75:    return 'Advanced'
            if p >= 50:    return 'Good'
            if p >= 25:    return 'Average'
            return 'Below Average'

        result = {}
        for slug, _ in vals:
            pv = _pct(slug)
            result[slug] = {
                'rank': rank_map.get(slug, '—'),
                'percentile': f'{pv}%' if pv is not None else '—',
                'tier': _tier(pv),
                'avg':  avg_v if avg_v is not None else '—',
                'median': med_v if med_v is not None else '—',
            }
        return result

    x_analytics = axis_analytics(x_key)
    y_analytics = axis_analytics(y_key)

    # ── Column ordering ───────────────────────────────────────────────────────
    all_raw_keys = list(CLUB_STAT_FIELDS) + CLUB_COMPOSITE_KEYS
    ordered_raw  = [f for f in all_raw_keys if any(f in c['stats'] for c in export_clubs)]
    ordered_p90  = [f for f in ordered_raw  if f not in _CLUB_P90_EXCLUDE and
                    any(f in c['stats_p90'] for c in export_clubs)]

    CLUB_RAW_LABEL = {
        'goalsScored': 'Goals Scored', 'goalsConceded': 'Goals Conceded',
        'assists': 'Assists', 'shots': 'Shots',
        'penaltyGoals': 'Penalty Goals', 'penaltiesTaken': 'Penalties Taken',
        'successfulDribbles': 'Successful Dribbles', 'dribbleAttempts': 'Dribble Attempts',
        'corners': 'Corners', 'averageBallPossession': 'Avg Possession %',
        'totalPasses': 'Total Passes', 'accuratePasses': 'Accurate Passes',
        'accuratePassesPercentage': 'Pass Accuracy %', 'totalLongBalls': 'Total Long Balls',
        'accurateLongBalls': 'Accurate Long Balls',
        'accurateLongBallsPercentage': 'Long Ball Accuracy %',
        'totalCrosses': 'Total Crosses', 'accurateCrosses': 'Accurate Crosses',
        'accurateCrossesPercentage': 'Cross Accuracy %', 'cleanSheets': 'Clean Sheets',
        'interceptions': 'Interceptions', 'saves': 'Saves',
        'errorsLeadingToShot': 'Errors Leading to Shot',
        'totalDuels': 'Total Duels', 'duelsWon': 'Duels Won',
        'duelsWonPercentage': 'Duel Win %', 'totalAerialDuels': 'Total Aerial Duels',
        'aerialDuelsWon': 'Aerial Duels Won',
        'aerialDuelsWonPercentage': 'Aerial Duel Win %',
        'offsides': 'Offsides', 'fouls': 'Fouls',
        'yellowCards': 'Yellow Cards', 'yellowRedCards': 'Yellow-Red Cards',
        'redCards': 'Red Cards', 'shotsAgainst': 'Shots Against',
        'goalKicks': 'Goal Kicks', 'ballRecovery': 'Ball Recoveries',
        'freeKicks': 'Free Kicks', 'matches': 'Matches Played',
        'attackingScore': 'Attacking Score', 'passingScore': 'Passing Score',
        'defendingScore': 'Defending Score',
    }

    def rl(key):  return CLUB_RAW_LABEL.get(key, key)
    def pl(key):  return f'{rl(key)} p90'

    # ── Shared style helpers ──────────────────────────────────────────────────
    C_BG0   = '0d1117'; C_BG1 = '0f172a'; C_HDR  = '1e293b'
    C_TITLE = '0d1b2a'; C_TEXT = 'E2E8F0'; C_TEXT2 = '94A3B8'
    C_ACCENT = '38BDF8'
    C_TIER  = {'Elite': 'F59E0B', 'Advanced': '34D399', 'Good': '60A5FA',
               'Average': 'CBD5E1', 'Below Average': 'F87171'}
    _side = Side(style='thin', color='1e293b')
    _thin = Border(left=_side, right=_side, top=_side, bottom=_side)

    def _bg(r): return C_BG0 if r % 2 == 0 else C_BG1

    def hdr_cell(ws, row, col, value, fg=None):
        c = ws.cell(row=row, column=col, value=value)
        c.font      = Font(bold=True, color=C_TEXT, size=9, name='Consolas')
        c.fill      = PatternFill('solid', fgColor=fg or C_HDR)
        c.alignment = Alignment(horizontal='center', vertical='center')
        c.border    = _thin
        return c

    def data_cell(ws, row, col, value, align='center', color=C_TEXT, bold=False, row_num=None):
        c = ws.cell(row=row, column=col, value=value)
        c.font      = Font(size=9, color=color, name='Consolas', bold=bold)
        c.fill      = PatternFill('solid', fgColor=_bg(row_num or row))
        c.alignment = Alignment(horizontal=align, vertical='center')
        c.border    = _thin
        return c

    def tier_cell(ws, row, col, tier_str, row_num=None):
        c = ws.cell(row=row, column=col, value=tier_str)
        clr = C_TIER.get(tier_str, C_TEXT2)
        c.font      = Font(size=9, color=clr, name='Consolas', bold=True)
        c.fill      = PatternFill('solid', fgColor=_bg(row_num or row))
        c.alignment = Alignment(horizontal='center', vertical='center')
        c.border    = _thin
        return c

    def title_row(ws, text, col_span, row=1):
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=col_span)
        c = ws.cell(row=row, column=1, value=text)
        c.font      = Font(bold=True, size=13, color=C_TEXT, name='Consolas')
        c.fill      = PatternFill('solid', fgColor=C_TITLE)
        c.alignment = Alignment(horizontal='center', vertical='center')
        ws.row_dimensions[row].height = 24

    def meta_row(ws, text, col_span, row=2):
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=col_span)
        c = ws.cell(row=row, column=1, value=text)
        c.font      = Font(size=8, color=C_TEXT2, name='Consolas')
        c.fill      = PatternFill('solid', fgColor=C_HDR)
        c.alignment = Alignment(horizontal='left', vertical='center')
        ws.row_dimensions[row].height = 14

    # ── Workbook ──────────────────────────────────────────────────────────────
    wb           = openpyxl.Workbook()
    comp_label   = competition.replace('_', ' ')
    mode_label   = 'Per Match' if stat_mode == 'p90' else 'Totals'
    generated_str = datetime.now().strftime('%d %b %Y  %H:%M')
    meta_base    = f'Generated: {generated_str}   ·   Top N: {top_n}   ·   Pool: {total_ranked}'

    # ═══════════════════════════════════════════════════════════════════════════
    # SHEET 1 — Club Scatter
    # ═══════════════════════════════════════════════════════════════════════════
    ws1 = wb.active
    ws1.title = 'Club Scatter'

    S1_ID = ['Club', 'Matches']
    S1_AXIS = []
    for ax in (x_key, y_key):
        S1_AXIS += [rl(ax), f'Rank ({rl(ax)})', f'Pct ({rl(ax)})',
                    f'Tier ({rl(ax)})', f'Avg ({rl(ax)})', f'Median ({rl(ax)})']

    S1_HEADERS = S1_ID + S1_AXIS
    S1_N = len(S1_HEADERS)

    title_row(ws1, f'{comp_label}  ·  Club Scatter  ·  {mode_label}', S1_N)
    meta_row(ws1, meta_base, S1_N)
    for ci, h in enumerate(S1_HEADERS, 1):
        hdr_cell(ws1, 3, ci, h)
    ws1.row_dimensions[3].height = 16

    # Column indices: tier at positions 7 and 13 (1-indexed), rank+pct at 5,6 and 11,12
    TIER_COLS   = {7, 13}
    RANKPCT_COLS = {5, 6, 11, 12}

    for rank, c in enumerate(export_clubs, 1):
        r    = 3 + rank
        slug = c['club_slug']
        xa   = x_analytics.get(slug, {})
        ya   = y_analytics.get(slug, {})
        xv   = _sv(c, x_key)
        yv   = _sv(c, y_key)
        row_vals = [
            c['club'], c['matches'],
            round(xv, 2) if xv is not None else '—',
            xa.get('rank', '—'), xa.get('percentile', '—'), xa.get('tier', '—'),
            xa.get('avg', '—'), xa.get('median', '—'),
            round(yv, 2) if yv is not None else '—',
            ya.get('rank', '—'), ya.get('percentile', '—'), ya.get('tier', '—'),
            ya.get('avg', '—'), ya.get('median', '—'),
        ]
        for ci, val in enumerate(row_vals, 1):
            if ci in TIER_COLS:
                tier_cell(ws1, r, ci, val, row_num=r)
            elif ci in RANKPCT_COLS:
                data_cell(ws1, r, ci, val, color=C_ACCENT, bold=True, row_num=r)
            elif ci == 1:
                data_cell(ws1, r, ci, val, align='left', bold=True, row_num=r)
            else:
                data_cell(ws1, r, ci, val, row_num=r)
        ws1.row_dimensions[r].height = 15

    s1_widths = [22, 9, 12, 10, 14, 14, 14, 12, 12, 12, 10, 14, 14, 12, 12]
    for i, w in enumerate(s1_widths[:S1_N], 1):
        ws1.column_dimensions[get_column_letter(i)].width = w
    ws1.freeze_panes = 'A4'

    # ═══════════════════════════════════════════════════════════════════════════
    # SHEET 2 — All Attributes
    # ═══════════════════════════════════════════════════════════════════════════
    ws2 = wb.create_sheet('All Attributes')

    S2_ID       = ['Club', 'Matches']
    raw_headers = [rl(f) for f in ordered_raw]
    p90_headers = [pl(f) for f in ordered_p90]
    S2_HEADERS  = S2_ID + raw_headers + p90_headers
    S2_N        = len(S2_HEADERS)
    n_id        = len(S2_ID)
    n_raw       = len(ordered_raw)

    title_row(ws2, f'{comp_label}  ·  All Attributes  ·  Top {top_n}', S2_N)
    meta_row(ws2, meta_base + '   ·   Cols: identity | raw totals | per match', S2_N)
    for ci, h in enumerate(S2_HEADERS, 1):
        hdr_cell(ws2, 3, ci, h, fg=('0a2540' if ci > n_id + n_raw else None))
    ws2.row_dimensions[3].height = 16

    for rank, c in enumerate(export_clubs, 1):
        r = 3 + rank
        id_vals  = [c['club'], c['matches']]
        raw_vals = [(lambda v: round(v, 2) if isinstance(v, float) else (v if v is not None else '—'))
                    (c['stats'].get(f)) for f in ordered_raw]
        p90_vals = [(lambda v: round(v, 2) if v is not None else '—')
                    (c['stats_p90'].get(f)) for f in ordered_p90]
        all_vals = id_vals + raw_vals + p90_vals
        for ci, val in enumerate(all_vals, 1):
            if ci == 1:
                data_cell(ws2, r, ci, val, align='left', bold=True, row_num=r)
            elif ci > n_id + n_raw:
                data_cell(ws2, r, ci, val,
                          color=C_ACCENT if val != '—' else C_TEXT2, row_num=r)
            else:
                data_cell(ws2, r, ci, val, row_num=r)
        ws2.row_dimensions[r].height = 14

    ws2.column_dimensions[get_column_letter(1)].width = 22
    ws2.column_dimensions[get_column_letter(2)].width = 9
    for i in range(3, S2_N + 1):
        ws2.column_dimensions[get_column_letter(i)].width = 11
    ws2.freeze_panes = ws2.cell(row=4, column=n_id + 1)

    # ═══════════════════════════════════════════════════════════════════════════
    # SHEET 3 — League Summary (mean / median / min / max over top N)
    # ═══════════════════════════════════════════════════════════════════════════
    ws3 = wb.create_sheet('League Summary')

    s3_cols = [pl(f) for f in ordered_p90]
    S3_N    = 1 + len(s3_cols)
    title_row(ws3, f'{comp_label}  ·  League Summary (Top {top_n})  ·  per-match stats', S3_N)
    meta_row(ws3, meta_base, S3_N)
    hdr_cell(ws3, 3, 1, 'Metric')
    for ci, h in enumerate(s3_cols, 2):
        hdr_cell(ws3, 3, ci, h, fg='0a2540')
    ws3.row_dimensions[3].height = 16

    def _summ(key):
        vals = [c['stats_p90'].get(key) for c in export_clubs]
        vals = [v for v in vals if v is not None]
        if not vals:
            return {'Mean': '—', 'Median': '—', 'Min': '—', 'Max': '—'}
        return {'Mean': round(statistics.mean(vals), 2), 'Median': round(statistics.median(vals), 2),
                'Min': round(min(vals), 2), 'Max': round(max(vals), 2)}

    summaries = {f: _summ(f) for f in ordered_p90}
    for ri, metric in enumerate(['Mean', 'Median', 'Min', 'Max'], 1):
        r = 3 + ri
        data_cell(ws3, r, 1, metric, align='left', color=C_ACCENT, bold=True, row_num=r)
        for ci, f in enumerate(ordered_p90, 2):
            val = summaries[f].get(metric, '—')
            data_cell(ws3, r, ci, val, color=C_TEXT if val != '—' else C_TEXT2, row_num=r)
        ws3.row_dimensions[r].height = 15

    ws3.column_dimensions['A'].width = 10
    for i in range(2, S3_N + 1):
        ws3.column_dimensions[get_column_letter(i)].width = 11
    ws3.freeze_panes = 'B4'

    # ── Stream response ───────────────────────────────────────────────────────
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    safe_comp = competition.replace(' ', '_')
    return send_file(
        buf,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'ClubScatter_{safe_comp}.xlsx',
    )


@bp.route('/player_scatter')
def player_scatter():
    competitions = get_all_competitions(OUTPUT_DIR)
    return render_template('player_scatter.html', competitions=competitions)


@bp.route('/api/player_scatter_data')
def api_player_scatter_data():
    import glob as _glob
    import json as _json
    from data_loader import profile_category

    competition = request.args.get('competition', '').strip()
    country     = request.args.get('country',     '').strip()
    imported_id = request.args.get('imported_id', '').strip()
    if not competition or not country:
        return jsonify({'error': 'competition and country are required'}), 400

    if not _league_exists(country, competition):
        return jsonify({'error': 'competition directory not found'}), 404

    from datetime import datetime, timezone, date

    # ── Pass 1: load every player who actually appeared (minutes > 0) ──────────
    loaded = []   # list of (minutes, record)

    for _pdocs in (_league_player_docs(country, competition),):
        for raw in _pdocs:
            try:
                profile  = raw.get('profile', {}).get('player', {})
                stat_raw = raw.get('statistics', {}).get('statistics', {})

                minutes = stat_raw.get('minutesPlayed')
                if not isinstance(minutes, (int, float)) or minutes <= 0:
                    continue

                pos_list = profile.get('positionsDetailed') or raw.get('positions', [])
                fallback = profile.get('position', pos_list[0] if pos_list else '')
                cat      = profile_category(pos_list, fallback)
                pos_grp  = _CAT_TO_GROUP.get(cat, 'MID')

                stats     = {}
                stats_p90 = {}
                for field in PLAYER_STAT_FIELDS:
                    val = stat_raw.get(field)
                    if val is not None and isinstance(val, (int, float)) and not isinstance(val, bool):
                        stats[field] = val
                        if field not in _P90_EXCLUDE and minutes > 0:
                            stats_p90[field] = round((val / minutes) * 90, 2)
                compute_player_composites(stats, stats_p90, minutes)

                # ── Bio / contract fields (consumed by the ranks table) ───────
                age = profile.get('age', '')
                dob = profile.get('dateOfBirth', '')
                if not age and dob:
                    try:
                        today      = date.today()
                        birth_date = datetime.fromisoformat(dob).date()
                        age = today.year - birth_date.year - (
                            (today.month, today.day) < (birth_date.month, birth_date.day)
                        )
                    except Exception:
                        age = None
                else:
                    age = int(age) if age else None

                contract_until_ts = int(profile.get('contractUntilTimestamp') or 0)
                contract_until    = ''
                if contract_until_ts:
                    contract_until = datetime.fromtimestamp(
                        contract_until_ts, tz=timezone.utc).strftime('%b %Y')

                appearances = stat_raw.get('appearances')
                record = {
                    'player':            raw.get('player_name', profile.get('name', '')),
                    'player_slug':       raw.get('_file', ''),
                    'club':              raw.get('team', str(raw.get('_club', '')).replace('_', ' ')),
                    'club_slug':         raw.get('_club', ''),
                    'position':          pos_grp,
                    'positionsDetailed': pos_list,
                    'minutesPlayed':     int(minutes),
                    'appearances':       appearances if isinstance(appearances, (int, float)) else 0,
                    'stats':             stats,
                    'stats_p90':         stats_p90,
                    'nationality':       (profile.get('country') or {}).get('name', '') or profile.get('nationality', ''),
                    'age':               age,
                    'height':            profile.get('height', ''),
                    'market_value':      profile.get('proposedMarketValue') or (profile.get('proposedMarketValueRaw') or {}).get('value') or profile.get('marketValue') or 0,
                    'sofascore_slug':    profile.get('slug', ''),
                    'player_id':         raw.get('player_id', ''),
                    'contract_until_ts': contract_until_ts,
                    'contract_until':    contract_until,
                }
                loaded.append((float(minutes), record))
            except Exception:
                continue

    # ── Qualifying threshold = 33rd percentile of minutes among players who appeared ──
    all_minutes       = sorted(m for m, _ in loaded)
    minutes_threshold = _percentile(all_minutes, 0.33)

    players   = []
    keys_seen = []
    for minutes, rec in loaded:
        if minutes < minutes_threshold:
            continue
        players.append(rec)
        for k in rec['stats']:
            if k not in keys_seen:
                keys_seen.append(k)

    stat_keys = [k for k in PLAYER_STAT_FIELDS if k in keys_seen]

    # ── Inject imported player (always shown, bypasses minutes threshold) ──────
    if imported_id:
        rec = store_get(imported_id)
        if rec:
            stat_raw = rec['statistics']['statistics']
            iprof    = rec['profile']['player']
            iminutes = stat_raw.get('minutesPlayed') or 0
            ipos     = iprof.get('positionsDetailed') or rec.get('positions', [])
            icat     = profile_category(ipos, iprof.get('position', ipos[0] if ipos else ''))
            ipos_grp = _CAT_TO_GROUP.get(icat, 'MID')

            istats, istats_p90 = {}, {}
            for field in PLAYER_STAT_FIELDS:
                val = stat_raw.get(field)
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    istats[field] = val
                    if field not in _P90_EXCLUDE and iminutes > 0:
                        istats_p90[field] = round((val / iminutes) * 90, 2)

            players.append({
                'player':            rec['player_name'],
                'player_slug':       'imported:' + imported_id,
                'club':              rec.get('team', 'Imported'),
                'club_slug':         'imported',
                'position':          ipos_grp,
                'positionsDetailed': ipos,
                'minutesPlayed':     int(iminutes),
                'appearances':       stat_raw.get('appearances') or 0,
                'stats':             istats,
                'stats_p90':         istats_p90,
                'nationality':       iprof.get('nationality', ''),
                'age':               iprof.get('age'),
                'height':            iprof.get('height', ''),
                'market_value':      iprof.get('proposedMarketValue') or 0,
                'sofascore_slug':    iprof.get('slug', ''),
                'player_id':         rec.get('player_id', ''),
                'contract_until_ts': iprof.get('contractUntilTimestamp') or 0,
                'contract_until':    '',
                'imported':          True,
            })
            for k in istats:
                if k not in keys_seen:
                    keys_seen.append(k)
            stat_keys = [k for k in PLAYER_STAT_FIELDS if k in keys_seen]

    return jsonify({
        'players':           players,
        'stat_keys':         stat_keys,
        'minutes_threshold': round(minutes_threshold, 1),
    })


@bp.route('/api/player_scatter_export_xlsx')
def api_player_scatter_export_xlsx():
    """Export filtered player scatter data to Excel — three sheets:
       Sheet 1: Player Scatter  — identity + x/y axes + rank/percentile/tier/avg/median (top N only)
       Sheet 2: All Attributes  — identity + every raw stat + every p90 stat (preferred order)
       Sheet 3: League Summary  — mean/median/min/max for every p90 col over top N
    """
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from datetime import datetime, timezone, date
    import json as _json
    import statistics
    from data_loader import profile_category

    # ── Query params ──────────────────────────────────────────────────────────
    competition       = request.args.get('competition', '').strip()
    country           = request.args.get('country',     '').strip()
    x_key             = request.args.get('x_key',       '').strip()
    y_key             = request.args.get('y_key',       '').strip()
    stat_mode         = request.args.get('stat_mode',   'totals').strip()
    top_n             = int(request.args.get('top_n', '30'))
    position          = request.args.get('position',    '').strip()
    club              = request.args.get('club',        '').strip()
    age_min_str       = request.args.get('age_min',     '').strip()
    age_max_str       = request.args.get('age_max',     '').strip()
    contract_year_str = request.args.get('contract_year', '').strip()

    if not competition or not country or not x_key or not y_key:
        return jsonify({'error': 'competition, country, x_key, y_key are required'}), 400

    age_min       = int(age_min_str)       if age_min_str       else None
    age_max       = int(age_max_str)       if age_max_str       else None
    contract_year = int(contract_year_str) if contract_year_str else None

    if not _league_exists(country, competition):
        return jsonify({'error': 'competition directory not found'}), 404

    # ── Load players ──────────────────────────────────────────────────────────
    players = []
    for _pdocs in (_league_player_docs(country, competition),):
        for raw in _pdocs:
            try:
                profile  = raw.get('profile', {}).get('player', {})
                stat_raw = raw.get('statistics', {}).get('statistics', {})

                minutes = stat_raw.get('minutesPlayed')
                if not isinstance(minutes, (int, float)) or minutes <= 0:
                    continue

                pos_list = profile.get('positionsDetailed') or raw.get('positions', [])
                fallback = profile.get('position', pos_list[0] if pos_list else '')
                cat      = profile_category(pos_list, fallback)
                pos_grp  = _CAT_TO_GROUP.get(cat, 'MID')

                stats     = {}
                stats_p90 = {}
                for field in PLAYER_STAT_FIELDS:
                    val = stat_raw.get(field)
                    if val is not None and isinstance(val, (int, float)) and not isinstance(val, bool):
                        stats[field] = val
                        if field not in _P90_EXCLUDE and minutes > 0:
                            stats_p90[field] = round((val / minutes) * 90, 2)
                compute_player_composites(stats, stats_p90, minutes)

                player_name = raw.get('player_name', profile.get('name', ''))
                club_name   = raw.get('team', str(raw.get('_club', '')).replace('_', ' '))

                age = profile.get('age', '')
                dob = profile.get('dateOfBirth', '')
                if not age and dob:
                    try:
                        today      = date.today()
                        birth_date = datetime.fromisoformat(dob).date()
                        age        = today.year - birth_date.year - (
                            (today.month, today.day) < (birth_date.month, birth_date.day)
                        )
                    except Exception:
                        age = None
                else:
                    age = int(age) if age else None

                contract_until_ts = int(profile.get('contractUntilTimestamp') or 0)
                contract_until = ''
                if contract_until_ts:
                    _dt = datetime.fromtimestamp(contract_until_ts, tz=timezone.utc)
                    contract_until = _dt.strftime('%b %Y')

                sofascore_slug = profile.get('slug', '')
                player_id      = raw.get('player_id', '')
                profile_link   = (
                    f'https://www.sofascore.com/player/{sofascore_slug}/{player_id}'
                    if sofascore_slug and player_id else ''
                )

                players.append({
                    'player':            player_name,
                    'player_slug':       raw.get('_file', ''),
                    'club':              club_name,
                    'club_slug':         raw.get('_club', ''),
                    'position':          pos_grp,
                    'positionsDetailed': pos_list,
                    'minutesPlayed':     int(minutes),
                    'appearances':       stat_raw.get('appearances') or 0,
                    'stats':             stats,
                    'stats_p90':         stats_p90,
                    'nationality':       (profile.get('country') or {}).get('name', '') or profile.get('nationality', ''),
                    'age':               age,
                    'height':            profile.get('height', ''),
                    'market_value':      profile.get('proposedMarketValue') or (profile.get('proposedMarketValueRaw') or {}).get('value') or profile.get('marketValue') or 0,
                    'sofascore_slug':    sofascore_slug,
                    'player_id':         player_id,
                    'profile_link':      profile_link,
                    'contract_until_ts': contract_until_ts,
                    'contract_until':    contract_until,
                })
            except Exception:
                continue

    # ── Qualifying threshold = 33rd percentile of minutes ────────────────────
    _mins_sorted      = sorted(p['minutesPlayed'] for p in players)
    minutes_threshold = _percentile(_mins_sorted, 0.33)
    players = [p for p in players if p['minutesPlayed'] >= minutes_threshold]

    # ── Apply filters ─────────────────────────────────────────────────────────
    filtered = players
    if position and position != 'All':
        filtered = [p for p in filtered if p['position'] == position]
    if club and club != 'All':
        filtered = [p for p in filtered if p['club'] == club]
    if age_min is not None:
        filtered = [p for p in filtered if p['age'] is None or p['age'] >= age_min]
    if age_max is not None:
        filtered = [p for p in filtered if p['age'] is None or p['age'] <= age_max]
    if contract_year is not None:
        filtered = [p for p in filtered if not p['contract_until_ts'] or
                    datetime.fromtimestamp(p['contract_until_ts'], tz=timezone.utc).year == contract_year]

    # ── Rank by combined x+y normalised score ────────────────────────────────
    def get_stat_val(player, key):
        src = player['stats_p90'] if stat_mode == 'p90' else player['stats']
        return src.get(key)

    valid = [p for p in filtered if (
        get_stat_val(p, x_key) is not None and
        get_stat_val(p, y_key) is not None
    )]

    scored = []
    if valid:
        x_vals = [get_stat_val(p, x_key) for p in valid]
        y_vals = [get_stat_val(p, y_key) for p in valid]
        x_min, x_max = min(x_vals), max(x_vals)
        y_min, y_max = min(y_vals), max(y_vals)
        x_range = x_max - x_min if x_max > x_min else 1
        y_range = y_max - y_min if y_max > y_min else 1

        for p in valid:
            s = (get_stat_val(p, x_key) - x_min) / x_range + (get_stat_val(p, y_key) - y_min) / y_range
            scored.append((p, s))

        scored.sort(key=lambda t: t[1], reverse=True)

    export_players = [p for p, _ in scored[:top_n]]
    total_ranked   = len(scored)

    # ── Helpers: per-axis analytics over top N ────────────────────────────────
    def axis_analytics(key):
        """Returns dict with rank, percentile, tier, avg, median for each export player."""
        vals = []
        for p in export_players:
            v = get_stat_val(p, key)
            if v is not None:
                vals.append((p['player_slug'], v))

        n = len(vals)
        sorted_vals = sorted(vals, key=lambda t: t[1], reverse=True)
        rank_map  = {slug: i + 1 for i, (slug, _) in enumerate(sorted_vals)}
        all_vals  = [v for _, v in vals]
        avg_val   = round(statistics.mean(all_vals), 2)     if all_vals else None
        med_val   = round(statistics.median(all_vals), 2)   if all_vals else None

        def pct(slug):
            if n < 2 or slug not in rank_map:
                return None
            return round((1 - (rank_map[slug] - 1) / (n - 1)) * 100, 1)

        def tier(p_val):
            if p_val is None:
                return '—'
            if p_val >= 90:   return 'Elite'
            if p_val >= 75:   return 'Advanced'
            if p_val >= 50:   return 'Good'
            if p_val >= 25:   return 'Average'
            return 'Below Average'

        result = {}
        for slug, _ in vals:
            p_val = pct(slug)
            result[slug] = {
                'rank':       rank_map.get(slug, '—'),
                'percentile': f'{p_val}%' if p_val is not None else '—',
                'tier':       tier(p_val),
                'avg':        avg_val if avg_val is not None else '—',
                'median':     med_val if med_val is not None else '—',
            }
        return result

    x_analytics = axis_analytics(x_key)
    y_analytics = axis_analytics(y_key)

    def mv_str(mv):
        if not mv:
            return '—'
        if mv >= 1_000_000:
            return f'€{mv / 1_000_000:.1f}M'
        if mv >= 1_000:
            return f'€{mv / 1_000:.0f}K'
        return f'€{mv}'

    # ── Preferred column order for Sheet 2 raw stats ──────────────────────────
    # Human-readable label → internal PLAYER_STAT_FIELDS key mapping.
    # Labels that are identical to keys are listed without renaming.
    S2_RAW_ORDER = [
        'goals', 'goalAssist', 'totalShots', 'onTargetScoringAttempt',
        'keyPass', 'successfulDribbles', 'tackles', 'wonTackle',
        'interceptionWon', 'totalWon', 'aerialWon', 'accuratePasses',
        'passAccuracyPercentage', 'accurateCross', 'crossAccuracyPercentage',
        'accurateLongBalls', 'longBallAccuracyPercentage', 'totalLongBalls',
        'totalPasses', 'totalCross', 'yellowCards', 'redCards',
        'minutesPlayed', 'appearances', 'rating',
        'bigChanceCreated', 'bigChanceMissed', 'clearances', 'blockedShotsAll',
        'ballRecovery', 'savedShotsFromInsideTheBox', 'fouls', 'wasFouled',
        'offsideGiven', 'touches', 'possessionLost', 'possessionWonAttThird',
        'dispossessed', 'penaltyGoals', 'penaltyTaken', 'goalConversionPercentage',
        'penaltyConversionPercentage', 'dribbleSuccessPercentage', 'duelSuccessPercentage',
        'aerialDuelSuccessPercentage', 'tackleSuccessPercentage', 'groundDuelsWon',
        'groundDuelSuccessPercentage', 'errorLeadToGoal', 'errorLeadToShot',
        'headedGoals', 'freeKickGoal', 'goalsFromInsideTheBox', 'goalsFromOutsideTheBox',
        'matchesStarted', 'totalRating', 'countRating', 'goalsAssistsSum',
        'inaccuratePasses', 'accurateOwnHalfPasses', 'accurateOppositionHalfPasses',
        'accurateFinalThirdPasses', 'directRedCards', 'shotsOffTarget',
        'penaltyWon', 'penaltyConceded', 'shotFromSetPiece',
        'shotsFromInsideTheBox', 'shotsFromOutsideTheBox', 'leftFootGoals',
        'rightFootGoals', 'totalChippedPasses', 'accurateChippedPasses',
        'hitWoodwork', 'ownGoals', 'dribbledPast', 'passToAssist', 'cleanSheet',
        'aerialLost', 'inaccuratePasses', 'accurateFinalThirdPasses',
        'penaltyFaced', 'penaltySave',
        'savedShotsFromInsideTheBox', 'savedShotsFromOutsideTheBox',
        'goalsConcededInsideTheBox', 'goalsConcededOutsideTheBox',
        'punches', 'runsOut', 'successfulRunsOut', 'highClaims',
        'crossesNotClaimed', 'setPieceConversion', 'totalAttemptAssist',
        'totalContest', 'duelLost', 'aerialLost', 'attemptPenaltyMiss',
        'attemptPenaltyPost', 'attemptPenaltyTarget', 'goalsConceded',
        'scoringFrequency', 'yellowRedCards', 'savesCaught', 'savesParried',
        'totalOwnHalfPasses', 'totalOppositionHalfPasses', 'totwAppearances',
        'goalKicks', 'outfielderBlock', 'id',
        'attackingScore', 'possessionScore', 'defensiveScore',
    ]

    # Build ordered list: preferred order first, then any remaining stat fields
    preferred_set = set(S2_RAW_ORDER)
    extra_fields  = [f for f in PLAYER_STAT_FIELDS if f not in preferred_set]
    ordered_raw   = [f for f in S2_RAW_ORDER if f in set(PLAYER_STAT_FIELDS)] + extra_fields
    # Only keep fields that at least one export player actually has
    ordered_raw   = [f for f in ordered_raw if any(f in p['stats'] for p in export_players)]
    ordered_p90   = [f for f in ordered_raw if f not in _P90_EXCLUDE and
                     any(f in p['stats_p90'] for p in export_players)]

    # ── Shared style helpers ──────────────────────────────────────────────────
    C_BG0    = '0d1117'
    C_BG1    = '0f172a'
    C_HDR    = '1e293b'
    C_TITLE  = '0d1b2a'
    C_TEXT   = 'E2E8F0'
    C_TEXT2  = '94A3B8'
    C_ACCENT = '38BDF8'
    C_TIER   = {
        'Elite':         'F59E0B',
        'Advanced':      '34D399',
        'Good':          '60A5FA',
        'Average':       'CBD5E1',
        'Below Average': 'F87171',
    }

    _side  = Side(style='thin', color='1e293b')
    _thin  = Border(left=_side, right=_side, top=_side, bottom=_side)

    def _bg(row_num):
        return C_BG0 if row_num % 2 == 0 else C_BG1

    def hdr_cell(ws, row, col, value, fg=None):
        c = ws.cell(row=row, column=col, value=value)
        c.font      = Font(bold=True, color=C_TEXT, size=9, name='Consolas')
        c.fill      = PatternFill('solid', fgColor=fg or C_HDR)
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=False)
        c.border    = _thin
        return c

    def data_cell(ws, row, col, value, align='center', color=C_TEXT, bold=False, row_num=None):
        c = ws.cell(row=row, column=col, value=value)
        c.font      = Font(size=9, color=color, name='Consolas', bold=bold)
        c.fill      = PatternFill('solid', fgColor=_bg(row_num or row))
        c.alignment = Alignment(horizontal=align, vertical='center')
        c.border    = _thin
        return c

    def tier_cell(ws, row, col, tier_str, row_num=None):
        c = ws.cell(row=row, column=col, value=tier_str)
        clr = C_TIER.get(tier_str, C_TEXT2)
        c.font      = Font(size=9, color=clr, name='Consolas', bold=True)
        c.fill      = PatternFill('solid', fgColor=_bg(row_num or row))
        c.alignment = Alignment(horizontal='center', vertical='center')
        c.border    = _thin
        return c

    def title_row(ws, text, col_span, row=1):
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=col_span)
        c = ws.cell(row=row, column=1, value=text)
        c.font      = Font(bold=True, size=13, color=C_TEXT, name='Consolas')
        c.fill      = PatternFill('solid', fgColor=C_TITLE)
        c.alignment = Alignment(horizontal='center', vertical='center')
        ws.row_dimensions[row].height = 24

    def meta_row(ws, text, col_span, row=2):
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=col_span)
        c = ws.cell(row=row, column=1, value=text)
        c.font      = Font(size=8, color=C_TEXT2, name='Consolas')
        c.fill      = PatternFill('solid', fgColor=C_HDR)
        c.alignment = Alignment(horizontal='left', vertical='center')
        ws.row_dimensions[row].height = 14

    # ── Workbook ──────────────────────────────────────────────────────────────
    wb = openpyxl.Workbook()

    comp_label    = competition.replace('_', ' ')
    mode_label    = 'Per 90' if stat_mode == 'p90' else 'Totals'
    generated_str = datetime.now().strftime('%d %b %Y  %H:%M')

    meta_base = (
        f'Generated: {generated_str}   ·   Top N: {top_n}'
        f'   ·   Pool: {total_ranked}'
    )
    if age_min or age_max:
        meta_base += f'   ·   Age: {age_min or "—"}–{age_max or "—"}'
    if contract_year:
        meta_base += f'   ·   Contract year: {contract_year}'

    # ═══════════════════════════════════════════════════════════════════════════
    # SHEET 1 — Player Scatter
    # ═══════════════════════════════════════════════════════════════════════════
    ws1 = wb.active
    ws1.title = 'Player Scatter'

    # Identity + x/y stat value + per-axis analytics (rank, pct, tier, avg, median)
    S1_IDENTITY = [
        'Player', 'Club', 'Position', 'Positions', 'Nationality', 'Age',
        'Contract Until', 'Market Value', 'Height (cm)', 'Minutes', 'Profile Link',
    ]
    # Per axis: value, rank, percentile, tier, avg, median — 6 cols × 2 axes = 12
    S1_AXIS_COLS = []
    for ax in (x_key, y_key):
        S1_AXIS_COLS += [
            ax,
            f'Rank ({ax})',
            f'Percentile ({ax})',
            f'Tier ({ax})',
            f'Avg ({ax})',
            f'Median ({ax})',
        ]

    S1_HEADERS  = S1_IDENTITY + S1_AXIS_COLS
    S1_COLS_N   = len(S1_HEADERS)

    title_row(ws1, f'{comp_label}  ·  Player Scatter  ·  {mode_label}', S1_COLS_N)
    meta_row(ws1, meta_base, S1_COLS_N)

    for ci, h in enumerate(S1_HEADERS, 1):
        hdr_cell(ws1, 3, ci, h)
    ws1.row_dimensions[3].height = 16

    for rank, p in enumerate(export_players, 1):
        r    = 3 + rank
        slug = p['player_slug']
        xa   = x_analytics.get(slug, {})
        ya   = y_analytics.get(slug, {})
        xv   = get_stat_val(p, x_key)
        yv   = get_stat_val(p, y_key)

        row_vals = [
            p['player'],
            p['club'],
            p['position'],
            ' / '.join(p.get('positionsDetailed') or []) or p['position'],
            p['nationality'] or '—',
            p['age'] if p['age'] is not None else '—',
            p['contract_until'] or '—',
            mv_str(p.get('market_value')),
            p['height'] or '—',
            p['minutesPlayed'],
            p['profile_link'] or '—',
            round(xv, 2) if xv is not None else '—',
            xa.get('rank', '—'),
            xa.get('percentile', '—'),
            xa.get('tier', '—'),
            xa.get('avg', '—'),
            xa.get('median', '—'),
            round(yv, 2) if yv is not None else '—',
            ya.get('rank', '—'),
            ya.get('percentile', '—'),
            ya.get('tier', '—'),
            ya.get('avg', '—'),
            ya.get('median', '—'),
        ]

        TIER_COLS = {15, 21}   # 1-indexed positions of the two Tier columns
        for ci, val in enumerate(row_vals, 1):
            if ci in TIER_COLS:
                tier_cell(ws1, r, ci, val, row_num=r)
            elif ci in (13, 14, 19, 20):   # rank + percentile cols
                data_cell(ws1, r, ci, val, color=C_ACCENT, bold=True, row_num=r)
            elif ci == 11:                  # profile link — clickable hyperlink
                has_link = val and val != '—'
                c = ws1.cell(row=r, column=ci, value='Visit Profile' if has_link else '—')
                c.font      = Font(size=9, color='60A5FA', name='Consolas',
                                   underline='single' if has_link else None)
                c.fill      = PatternFill('solid', fgColor=_bg(r))
                c.alignment = Alignment(horizontal='left', vertical='center')
                c.border    = _thin
                if has_link:
                    c.hyperlink = val
            else:
                data_cell(ws1, r, ci, val, row_num=r)

        ws1.row_dimensions[r].height = 15

    s1_widths = [22, 18, 8, 14, 14, 6, 12, 13, 10, 9, 16,
                 12, 10, 14, 14, 12, 12,
                 12, 10, 14, 14, 12, 12]
    for i, w in enumerate(s1_widths[:S1_COLS_N], 1):
        ws1.column_dimensions[get_column_letter(i)].width = w

    ws1.freeze_panes = 'A4'

    # ═══════════════════════════════════════════════════════════════════════════
    # SHEET 2 — All Attributes
    # ═══════════════════════════════════════════════════════════════════════════
    ws2 = wb.create_sheet('All Attributes')

    S2_IDENTITY = [
        'Player', 'Club', 'Position', 'Positions', 'Nationality', 'Age',
        'Contract Until', 'Market Value', 'Height (cm)', 'Minutes', 'Profile Link',
    ]
    # Raw stat display labels — try to map keys to friendly names, fall back to key
    RAW_LABEL = {
        'goals': 'Goals', 'goalAssist': 'Assists', 'totalShots': 'Total Shots',
        'onTargetScoringAttempt': 'Shots On Target', 'keyPass': 'Key Passes',
        'successfulDribbles': 'Successful Dribbles', 'tackles': 'Tackles',
        'wonTackle': 'Tackles Won', 'interceptionWon': 'Interceptions',
        'totalWon': 'Total Duels Won', 'aerialWon': 'Aerial Duels Won',
        'accuratePasses': 'Accurate Passes', 'passAccuracyPercentage': 'Pass Accuracy %',
        'accurateCross': 'Accurate Crosses', 'crossAccuracyPercentage': 'Cross Accuracy %',
        'accurateLongBalls': 'Accurate Long Balls', 'longBallAccuracyPercentage': 'Long Ball Accuracy %',
        'totalLongBalls': 'Total Long Balls', 'totalPasses': 'Total Passes',
        'totalCross': 'Total Crosses', 'yellowCards': 'Yellow Cards',
        'redCards': 'Red Cards', 'minutesPlayed': 'Minutes Played',
        'appearances': 'Appearances', 'rating': 'Rating',
        'bigChanceCreated': 'Big Chances Created', 'bigChanceMissed': 'Big Chances Missed',
        'clearances': 'Clearances', 'blockedShotsAll': 'Blocked Shots',
        'ballRecovery': 'Ball Recoveries', 'fouls': 'Fouls',
        'wasFouled': 'Was Fouled', 'offsideGiven': 'Offsides', 'touches': 'Touches',
        'possessionLost': 'Possession Lost', 'possessionWonAttThird': 'Possession Won Att 3rd',
        'dispossessed': 'Dispossessed', 'penaltyGoals': 'Penalty Goals',
        'penaltyTaken': 'Penalties Taken', 'goalConversionPercentage': 'Goal Conversion %',
        'penaltyConversionPercentage': 'Penalty Conversion %',
        'dribbleSuccessPercentage': 'Dribble Success %', 'duelSuccessPercentage': 'Duel Win %',
        'aerialDuelSuccessPercentage': 'Aerial Duel Win %', 'tackleSuccessPercentage': 'Tackle Win %',
        'groundDuelsWon': 'Ground Duels Won', 'groundDuelSuccessPercentage': 'Ground Duel Win %',
        'errorLeadToGoal': 'Errors Leading to Goal', 'errorLeadToShot': 'Errors Leading to Shot',
        'headedGoals': 'Headed Goals', 'freeKickGoal': 'Free Kick Goals',
        'goalsFromInsideTheBox': 'Goals Inside Box', 'goalsFromOutsideTheBox': 'Goals Outside Box',
        'matchesStarted': 'Matches Started', 'totalRating': 'Totalrating',
        'countRating': 'Countrating', 'goalsAssistsSum': 'Goalsassistssum',
        'inaccuratePasses': 'Inaccuratepasses', 'accurateOwnHalfPasses': 'Accurateownhalfpasses',
        'accurateOppositionHalfPasses': 'Accurateoppositionhalfpasses',
        'accurateFinalThirdPasses': 'Accuratefinalthirdpasses', 'directRedCards': 'Directredcards',
        'shotsOffTarget': 'Shotsofftarget', 'penaltyWon': 'Penaltywon',
        'penaltyConceded': 'Penaltyconceded', 'shotFromSetPiece': 'Shotfromsetpiece',
        'shotsFromInsideTheBox': 'Shotsfrominsidethebox', 'shotsFromOutsideTheBox': 'Shotsfromoutsidethebox',
        'leftFootGoals': 'Leftfootgoals', 'rightFootGoals': 'Rightfootgoals',
        'totalChippedPasses': 'Totalchippedpasses', 'accurateChippedPasses': 'Accuratechippedpasses',
        'hitWoodwork': 'Hit Woodwork', 'ownGoals': 'Own Goals', 'dribbledPast': 'Times Dribbled Past',
        'passToAssist': 'Pass to Assist', 'cleanSheet': 'Clean Sheets',
        'aerialLost': 'Aerial Duels Lost', 'inaccuratePasses': 'Inaccurate Passes',
        'accurateFinalThirdPasses': 'Accurate Final Third Passes',
        'penaltyFaced': 'Penaltyfaced', 'penaltySave': 'Penaltysave',
        'savedShotsFromInsideTheBox': 'Savedshotsfrominsidethebox',
        'savedShotsFromOutsideTheBox': 'Savedshotsfromoutsidethebox',
        'goalsConcededInsideTheBox': 'Goalsconcededinsidethebox',
        'goalsConcededOutsideTheBox': 'Goalsconcededoutsidethebox',
        'punches': 'Punches', 'runsOut': 'Runsout', 'successfulRunsOut': 'Successfulrunsout',
        'highClaims': 'Highclaims', 'crossesNotClaimed': 'Crossesnotclaimed',
        'setPieceConversion': 'Setpiececonversion', 'totalAttemptAssist': 'Totalattemptassist',
        'totalContest': 'Total Contest', 'duelLost': 'Duels Lost',
        'attemptPenaltyMiss': 'Attemptpenaltymiss', 'attemptPenaltyPost': 'Attemptpenaltypost',
        'attemptPenaltyTarget': 'Attemptpenaltytarget', 'goalsConceded': 'Goals Conceded',
        'scoringFrequency': 'Scoringfrequency', 'yellowRedCards': 'Yellow-Red Cards',
        'savesCaught': 'Savescaught', 'savesParried': 'Savesparried',
        'totalOwnHalfPasses': 'Totalownhalfpasses', 'totalOppositionHalfPasses': 'Totaloppositionhalfpasses',
        'totwAppearances': 'Totwappearances', 'goalKicks': 'Goal Kicks',
        'outfielderBlock': 'Outfielderblocks', 'id': 'Id',
        'attackingScore':  'Attacking Score',
        'possessionScore': 'Possession Score',
        'defensiveScore':  'Defensive Score',
    }

    def raw_label(key):
        return RAW_LABEL.get(key, key)

    def p90_label(key):
        base = RAW_LABEL.get(key, key)
        return f'{base} p90'

    raw_headers = [raw_label(f) for f in ordered_raw]
    p90_headers = [p90_label(f) for f in ordered_p90]
    S2_HEADERS  = S2_IDENTITY + raw_headers + p90_headers
    S2_COLS_N   = len(S2_HEADERS)
    n_id        = len(S2_IDENTITY)
    n_raw       = len(ordered_raw)

    title_row(ws2, f'{comp_label}  ·  All Attributes  ·  Top {top_n}', S2_COLS_N)
    meta_row(ws2, meta_base + '   ·   Cols: identity | raw totals | p90s', S2_COLS_N)

    for ci, h in enumerate(S2_HEADERS, 1):
        if ci <= n_id:
            hdr_cell(ws2, 3, ci, h)
        elif ci <= n_id + n_raw:
            hdr_cell(ws2, 3, ci, h)
        else:
            hdr_cell(ws2, 3, ci, h, fg='0a2540')   # different shade for p90 block
    ws2.row_dimensions[3].height = 16

    for rank, p in enumerate(export_players, 1):
        r = 3 + rank
        id_vals = [
            p['player'], p['club'], p['position'],
            ' / '.join(p.get('positionsDetailed') or []) or p['position'],
            p['nationality'] or '—',
            p['age'] if p['age'] is not None else '—',
            p['contract_until'] or '—', mv_str(p.get('market_value')),
            p['height'] or '—', p['minutesPlayed'], p['profile_link'] or '—',
        ]
        raw_vals = [
            (lambda v: round(v, 2) if isinstance(v, float) else (v if v is not None else '—'))
            (p['stats'].get(f))
            for f in ordered_raw
        ]
        p90_vals = [
            (lambda v: round(v, 2) if v is not None else '—')
            (p['stats_p90'].get(f))
            for f in ordered_p90
        ]

        all_vals = id_vals + raw_vals + p90_vals
        for ci, val in enumerate(all_vals, 1):
            if ci == 11:   # profile link — clickable hyperlink
                has_link = val and val != '—'
                c = ws2.cell(row=r, column=ci, value='Visit Profile' if has_link else '—')
                c.font      = Font(size=9, color='60A5FA', name='Consolas',
                                   underline='single' if has_link else None)
                c.fill      = PatternFill('solid', fgColor=_bg(r))
                c.alignment = Alignment(horizontal='left', vertical='center')
                c.border    = _thin
                if has_link:
                    c.hyperlink = val
            elif ci in (2, 3, 4, 5):
                data_cell(ws2, r, ci, val, align='left', row_num=r)
            elif ci > n_id + n_raw:   # p90 block
                data_cell(ws2, r, ci, val, color=C_ACCENT if val != '—' else C_TEXT2, row_num=r)
            else:
                data_cell(ws2, r, ci, val, row_num=r)

        ws2.row_dimensions[r].height = 14

    id_widths = [22, 18, 8, 14, 14, 6, 12, 13, 10, 9, 16]
    for i, w in enumerate(id_widths, 1):
        ws2.column_dimensions[get_column_letter(i)].width = w
    for i in range(n_id + 1, S2_COLS_N + 1):
        ws2.column_dimensions[get_column_letter(i)].width = 11

    ws2.freeze_panes = ws2.cell(row=4, column=n_id + 1)   # freeze identity cols + header rows

    # ═══════════════════════════════════════════════════════════════════════════
    # SHEET 3 — League Summary  (mean / median / min / max over top N)
    # ═══════════════════════════════════════════════════════════════════════════
    ws3 = wb.create_sheet('League Summary')

    # p90 fields for summary — same ordered_p90 list, labelled with p90_label
    s3_stat_cols  = [p90_label(f) for f in ordered_p90]
    S3_COLS_N     = 1 + len(s3_stat_cols)   # 'Metric' + one col per stat

    title_row(ws3, f'{comp_label}  ·  League Summary (Top {top_n})  ·  p90 stats', S3_COLS_N)
    meta_row(ws3, meta_base, S3_COLS_N)

    hdr_cell(ws3, 3, 1, 'Metric')
    for ci, h in enumerate(s3_stat_cols, 2):
        hdr_cell(ws3, 3, ci, h, fg='0a2540')
    ws3.row_dimensions[3].height = 16

    METRIC_ROWS = ['Mean', 'Median', 'Min', 'Max']

    def _summary_vals(key):
        vals = [p['stats_p90'].get(key) for p in export_players]
        vals = [v for v in vals if v is not None]
        if not vals:
            return {'Mean': '—', 'Median': '—', 'Min': '—', 'Max': '—'}
        return {
            'Mean':   round(statistics.mean(vals), 2),
            'Median': round(statistics.median(vals), 2),
            'Min':    round(min(vals), 2),
            'Max':    round(max(vals), 2),
        }

    # Pre-compute summaries
    summaries = {f: _summary_vals(f) for f in ordered_p90}

    for ri, metric in enumerate(METRIC_ROWS):
        r = 3 + ri + 1
        data_cell(ws3, r, 1, metric, align='left', color=C_ACCENT, bold=True, row_num=r)
        for ci, f in enumerate(ordered_p90, 2):
            val = summaries[f].get(metric, '—')
            data_cell(ws3, r, ci, val, color=C_TEXT if val != '—' else C_TEXT2, row_num=r)
        ws3.row_dimensions[r].height = 15

    ws3.column_dimensions['A'].width = 12
    for i in range(2, S3_COLS_N + 1):
        ws3.column_dimensions[get_column_letter(i)].width = 11

    ws3.freeze_panes = 'B4'

    # ── Stream response ───────────────────────────────────────────────────────
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    safe_comp = competition.replace(' ', '_')
    return send_file(
        buf,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'PlayerScatter_{safe_comp}.xlsx',
    )
