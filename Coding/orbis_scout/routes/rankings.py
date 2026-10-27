"""routes/rankings.py -- rankings blueprint (auto-extracted from the original app.py)."""

import os, io, csv, json, glob, re, uuid, threading
import time as _time
from flask import Blueprint, render_template, request, jsonify, send_file, abort

import context as ctx
from context import (
    _logger,
    OUTPUT_DIR, MATCH_OUTPUT_DIR, _WEBAPP_DIR,
    get_all_competitions, get_competition, get_club, get_club_players,
    get_player, search_players, get_all_players_flat, get_all_clubs_flat,
    get_heatmap_path, get_home_stats, get_competition_club_stats,
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

bp = Blueprint('rankings', __name__)


@bp.route('/league_rankings')
def league_rankings():
    return render_template('league_rankings.html')


@bp.route('/api/league_rankings')
def api_league_rankings():
    """Return the full Opta+IFFHS rankings list, optionally filtered by confederation."""
    conf_filter = request.args.get('confederation', '').strip().lower()
    try:
        from tactical_match_engine.services.json_loader import load_iffhs_data
        data = load_iffhs_data()
        entries = []
        for entry in data.get('rankings', []):
            conf = entry.get('confederation', '').lower()
            if conf_filter and conf != conf_filter:
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


@bp.route('/api/player_percentiles')
def api_player_percentiles():
    """
    Compute percentile rank for every numeric stat of a player
    compared to all players of the same position line in their competition.
    """
    player_path = request.args.get('player', '').strip()
    if not player_path:
        return jsonify({'error': 'player is required'}), 400
    parts = player_path.split('/')
    if len(parts) != 4:
        return jsonify({'error': 'invalid player path'}), 400

    country, competition, club_slug, filename = parts
    from player_engine import load_player_raw, season_pool_docs
    loaded = load_player_raw(OUTPUT_DIR, player_path)
    if loaded is None:
        return jsonify({'error': 'player file not found'}), 404

    try:
        from tactical_match_engine.engine.normalization import percentile_score

        _, target_file, raw = loaded
        player_stats = raw.get('statistics', {}).get('statistics', {})
        player_pos_raw = raw.get('positions') or [raw.get('profile', {}).get('player', {}).get('position', '')]
        player_line = get_position_line([str(p).upper() for p in player_pos_raw])

        # Collect pool: all players in same competition + season with same position line
        pool_stats: dict[str, list] = {}
        for pd in season_pool_docs(OUTPUT_DIR, country, competition, target_file, raw):
            try:
                pos_raw = pd.get('positions') or [pd.get('profile', {}).get('player', {}).get('position', '')]
                if get_position_line([str(p).upper() for p in pos_raw]) != player_line:
                    continue
                ps = pd.get('statistics', {}).get('statistics', {})
                for k, v in ps.items():
                    if isinstance(v, (int, float)) and not isinstance(v, bool) and v is not None:
                        pool_stats.setdefault(k, []).append(float(v))
            except Exception:
                continue

        # Stats where lower is better
        LOWER_IS_BETTER = {'yellowCards', 'redCards', 'fouls', 'ownGoals', 'bigChancesMissed'}

        percentiles = {}
        for stat, val in player_stats.items():
            if not isinstance(val, (int, float)) or isinstance(val, bool) or val is None:
                continue
            pop = pool_stats.get(stat, [])
            if len(pop) < 2:
                continue
            hib = stat not in LOWER_IS_BETTER
            pct = percentile_score(float(val), pop, higher_is_better=hib)
            percentiles[stat] = round(pct, 1)

        return jsonify({
            'player_path':  player_path,
            'position_line': player_line,
            'pool_size':    len(pool_stats.get('appearances', [])),
            'competition':  competition.replace('_', ' '),
            'percentiles':  percentiles,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


"""Club stats where a LOWER value is the better outcome."""
_CLUB_LOWER_IS_BETTER = frozenset({
    'goalsConceded', 'shotsAgainst', 'errorsLeadingToShot',
    'fouls', 'yellowCards', 'yellowRedCards', 'redCards', 'offsides',
})

# Bookkeeping fields — not performance, never worth a percentile.
_CLUB_PCT_EXCLUDE = frozenset({'id', 'matches', 'statisticsType', 'awardedMatches'})


@bp.route('/api/club_percentiles')
def api_club_percentiles():
    """League rank for each of a club's season statistics, against every other
    club in the same competition and season.

    Rank rather than percentile: with an 18-club league the percentile can only
    take ~18 distinct values, so it *is* the rank, just with false precision
    (seven of Persib's stats all read 97.2 — every one of them meant "1st").

    `basis` is "per_match" when clubs in the pool have played markedly different
    numbers of matches (a group-stage cup), otherwise "total". Without that, a
    team that played one match ranks last on every counting stat by construction.
    Rate stats (percentages, average possession) are never divided.
    """
    club_path = request.args.get('club', '').strip()
    if not club_path:
        return jsonify({'error': 'club is required'}), 400
    parts = club_path.split('/')
    if len(parts) != 3:
        return jsonify({'error': 'invalid club path'}), 400

    country, competition, club_slug = parts
    season = request.args.get('season') or None
    basis_arg = (request.args.get('basis') or 'auto').lower()

    try:
        pool_rows = get_competition_club_stats(OUTPUT_DIR, country, competition,
                                               season_id=season)
        target = next((r for r in pool_rows if r['club_slug'] == club_slug), None)
        if target is None:
            return jsonify({'error': 'club not found or has no season statistics'}), 404

        played = [r.get('matches') or 0 for r in pool_rows]
        matches_vary = bool(played) and (max(played) - min(played)) > 2
        if basis_arg in ('total', 'per_match'):
            basis = basis_arg
        else:
            basis = 'per_match' if matches_vary else 'total'

        def _value(row, stat):
            """The comparable figure for one club/stat under the active basis."""
            raw = row['stats'].get(stat)
            if raw is None:
                return None
            if basis == 'total' or stat in _CLUB_P90_EXCLUDE:
                return float(raw)
            n = row.get('matches') or 0
            return float(raw) / n if n else None

        # value lists per stat, for ranking
        pool_vals: dict[str, list] = {}
        for row in pool_rows:
            for stat in row['stats']:
                if stat in _CLUB_PCT_EXCLUDE or stat not in CLUB_STAT_FIELDS:
                    continue
                v = _value(row, stat)
                if v is not None:
                    pool_vals.setdefault(stat, []).append(v)

        ranks = {}
        for stat, pop in pool_vals.items():
            mine = _value(target, stat)
            if mine is None or len(pop) < 2:
                continue
            # Standard competition ranking: count strictly better, +1. Ties share
            # a rank (1,1,3) rather than being ordered arbitrarily.
            if stat in _CLUB_LOWER_IS_BETTER:
                better = sum(1 for v in pop if v < mine)
            else:
                better = sum(1 for v in pop if v > mine)
            ranks[stat] = {
                'rank':  better + 1,
                'total': len(pop),
                'value': round(mine, 2) if isinstance(mine, float) else mine,
            }

        return jsonify({
            'club_path':       club_path,
            'club':            target['club'],
            'competition':     competition.replace('_', ' '),
            'pool_size':       len(pool_rows),
            'matches':         target.get('matches') or 0,
            'matches_min':     min(played) if played else 0,
            'matches_max':     max(played) if played else 0,
            'matches_vary':    matches_vary,
            'basis':           basis,
            'lower_is_better': sorted(_CLUB_LOWER_IS_BETTER),
            'rate_stats':      sorted(_CLUB_P90_EXCLUDE),
            'ranks':           ranks,
        })
    except Exception as e:
        _logger.exception(f'club_percentiles error: {e}')
        return jsonify({'error': str(e)}), 500


@bp.route('/api/player_radar')
def api_player_radar():
    """Position-grouped z-score radar for a single player (see player_engine.py)."""
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
        _logger.exception(f'player_radar error: {e}')
        return jsonify({'error': str(e)}), 500


@bp.route('/api/compare_radar')
def api_compare_radar():
    """Percentile radar for two players + league average (see player_engine.py)."""
    path_a   = request.args.get('a', '').strip()
    path_b   = request.args.get('b', '').strip()
    pos_mode = request.args.get('pos_mode', 'exact')
    if pos_mode not in ('exact', 'grouped'):
        pos_mode = 'exact'
    if not path_a or not path_b:
        return jsonify({'error': 'a and b are required'}), 400
    try:
        from player_engine import build_compare_radar
        result = build_compare_radar(path_a, path_b, OUTPUT_DIR, pos_mode)
        if result is None:
            return jsonify({'error': 'player not found'}), 404
        return jsonify(result)
    except Exception as e:
        _logger.exception(f'compare_radar error: {e}')
        return jsonify({'error': str(e)}), 500


@bp.route('/api/player_role')
def api_player_role():
    """Return FM24 role classification + role fitness + spatial heatmap analysis."""
    player_path = request.args.get('player', '').strip()
    if not player_path:
        return jsonify({'error': 'player is required'}), 400
    parts = player_path.split('/')
    if len(parts) != 4:
        return jsonify({'error': 'invalid player path'}), 400

    try:
        cand = _load_player_for_engine(parts[0], parts[1], parts[2], parts[3])
        if cand is None:
            return jsonify({'error': 'player not found'}), 404

        from tactical_match_engine.engine.fm24_role_classifier import classify
        from tactical_match_engine.engine.role_encoder import get_role_fitness_vector
        from tactical_match_engine.engine.heatmap_role_analyzer import analyze as _heatmap_analyze
        import json as _json

        raw_stats = cand['raw_stats']
        position  = cand['position']
        positions = cand.get('positions', [position])
        minutes   = int(raw_stats.get('minutesPlayed') or 0)

        fm24_roles = classify(raw_stats, positions)

        # Default role fitness for the primary position (no role_path override)
        role_result = get_role_fitness_vector(raw_stats, position)

        role_profile_out = None
        if role_result:
            role_profile_out = {
                'overall_score':   role_result['overall_score'],
                'role_name':       role_result['role_profile']['position'],
                'category_scores': role_result['category_scores'],
                'weights':         role_result['weights'],
            }

        # ── Spatial heatmap analysis ─────────────────────────────────────────
        spatial_out = None
        try:
            from player_engine import load_player_raw
            _raw = load_player_raw(OUTPUT_DIR, player_path)[2]
            heatmap_points = _raw.get('heatmap', {}).get('points', [])
            player_name    = _raw.get('player_name', cand.get('name', 'Player'))
            spatial_result = _heatmap_analyze(heatmap_points, raw_stats, player_name)
            spatial_out = {
                'zone':        spatial_result['zone'],
                'features':    spatial_result['features'],
                'explanation': spatial_result['explanation'],
            }
        except Exception:
            pass  # heatmap data unavailable — degrade gracefully

        return jsonify({
            'player_position':    position,
            'player_minutes':     minutes,
            'fm24_roles':         fm24_roles,
            'role_profile':       role_profile_out,
            'spatial_analysis':   spatial_out,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
