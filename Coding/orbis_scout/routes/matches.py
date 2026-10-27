"""routes/matches.py -- matches blueprint (auto-extracted from the original app.py)."""

import os, io, csv, json, glob, re, uuid, threading
import time as _time
from flask import Blueprint, render_template, request, jsonify, send_file, abort

import context as ctx
from context import (
    _logger,
    OUTPUT_DIR, MATCH_OUTPUT_DIR, _WEBAPP_DIR,
    get_all_competitions, get_competition, get_club, get_club_players,
    get_player, search_players, get_all_players_flat, get_all_clubs_flat,
    get_heatmap_path, get_home_stats, get_player_competitions,
    store_get, is_import_token, token_id, build_engine_candidate,
    get_all_matches, get_match, get_player_match_history, compute_per90, LAST_N_MATCHES,
    MATCH_STATS_META, _DERIVED_STATS,
    _home_stats_cache, _HOME_STATS_TTL, _player_id_cache,
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

bp = Blueprint('matches', __name__)


@bp.route('/matches')
def matches():
    all_matches = get_all_matches(MATCH_OUTPUT_DIR)
    return render_template('matches.html', matches=all_matches)


@bp.route('/match/<int:event_id>')
def match_detail(event_id: int):
    data = get_match(event_id, MATCH_OUTPUT_DIR)
    if data is None:
        return render_template('matches.html',
                               matches=get_all_matches(MATCH_OUTPUT_DIR),
                               error=f'Match {event_id} not found.'), 404
    return render_template('match_detail.html', match=data,
                           stat_meta=MATCH_STATS_META)


def _find_player_profile(player_id, tournament_id=None):
    """Return (profile_path, player_name) for a player_id, or (None, '').

    Prefers the Mongo index, which stores one doc per (player_id, tournament_id,
    season_id); when *tournament_id* is given the matching competition wins,
    otherwise the newest record does. Falls back to walking output/ for the
    file-system loader.
    """
    rows = get_player_competitions(player_id)               # newest-first
    if rows:
        match = next((r for r in rows
                      if not tournament_id
                      or str(r.get('tournament_id')) == str(tournament_id)), None)
        # No record in that competition — fall back to the player's newest one.
        row = match or rows[0]
        return row['path'], row.get('player_name', '')

    import json as _json2
    for root, _dirs, files in os.walk(OUTPUT_DIR):
        for fname in files:
            if not fname.endswith('.json'):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, encoding='utf-8') as _f:
                    _d = _json2.load(_f)
                if str(_d.get('player_id', '')) != str(player_id):
                    continue
                if tournament_id and str(_d.get('tournament_id', '')) != str(tournament_id):
                    continue
                # output/<country>/<competition>/<club>/Players/<file>.json — the
                # /player/ route takes country/competition/club/<file>, no "Players".
                rel   = os.path.relpath(fpath, OUTPUT_DIR).replace('\\', '/')
                parts = rel.split('/')
                if len(parts) == 5 and parts[3] == 'Players':
                    return ('/'.join(parts[:3]) + '/' + parts[4][:-5],
                            _d.get('player_name', ''))
            except Exception as e:
                _logger.debug(f"player_id lookup error reading {fpath}: {e}")
    return None, ''


@bp.route('/player_matches/<int:player_id>')
def player_matches(player_id: int):
    # Optional competition scope. Without it a player who appears in more than
    # one competition gets all of them pooled into a single per-90.
    tournament_id = request.args.get('tournament') or None
    season_id     = request.args.get('season') or None
    appearances = get_player_match_history(player_id, MATCH_OUTPUT_DIR,
                                           tournament_id=tournament_id,
                                           season_id=season_id, limit=LAST_N_MATCHES)
    per90_data  = compute_per90(appearances)

    # Try to find the player's name + overall profile link
    player_name = ''
    profile_path = None
    if appearances:
        player_name = appearances[0]['player_info'].get('name', '')

    # Cross-lookup by player_id. Keyed with the tournament because one player_id
    # can own a profile per competition — caching on the id alone lets whichever
    # competition is found first shadow the rest for the life of the process.
    cache_key = (str(player_id), str(tournament_id or ''))
    if cache_key in _player_id_cache:
        profile_path = _player_id_cache[cache_key]
        _logger.debug(f"player_id_cache HIT for {cache_key} → {profile_path}")
    else:
        _logger.debug(f"player_id_cache MISS for {cache_key}")
        profile_path, _name = _find_player_profile(player_id, tournament_id)
        if profile_path:
            _player_id_cache[cache_key] = profile_path
            _logger.debug(f"player_id_cache STORE for {cache_key} → {profile_path}")
        if _name and not player_name:
            player_name = _name

    return render_template(
        'player_match_history.html',
        player_id=player_id,
        player_name=player_name,
        appearances=appearances,
        per90=per90_data,
        stat_meta=MATCH_STATS_META,
        derived_meta=_DERIVED_STATS,
        profile_path=profile_path,
    )
