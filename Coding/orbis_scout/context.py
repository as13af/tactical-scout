"""
context.py
----------
Shared application state and infrastructure for the Scout web app.

This module holds everything the route blueprints and service layer need but
that is NOT itself a route: logging setup, path resolution, the MongoDB/JSON
data-loader auto-switch, the cached loaders, background-job registries, and the
match-loader bindings.  It deliberately imports nothing from `routes` or
`services` so there are no circular imports.

The Flask `app` object is created by create_app() in app.py, not here.
"""


# == Preamble: logging, paths, data-loader auto-switch, loaders ==============
import sys
import os
import threading
import uuid
import logging

# ── Enhanced logging with both console and file output ──────────────────────────
_LOG_FORMAT = '%(asctime)s [%(levelname)-8s] %(name)s:%(funcName)s:%(lineno)d | %(message)s'
_LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')

# Root logger configuration
_root_logger = logging.getLogger()
_root_logger.setLevel(logging.DEBUG)

# Console handler (INFO level)
_console_handler = logging.StreamHandler(sys.stdout)
_console_handler.setLevel(logging.INFO)
_console_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
_root_logger.addHandler(_console_handler)

# File handler (DEBUG level - captures everything).
# Best-effort: on read-only hosts (e.g. Vercel) writing a log file fails, so
# fall back to stdout-only logging instead of crashing at import.
try:
    os.makedirs(_LOG_DIR, exist_ok=True)
    _file_handler = logging.FileHandler(os.path.join(_LOG_DIR, 'app.log'), encoding='utf-8')
    _file_handler.setLevel(logging.DEBUG)
    _file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    _root_logger.addHandler(_file_handler)
except OSError:
    _root_logger.warning("File logging disabled (read-only filesystem) — stdout only")

# Suppress werkzeug's verbose logging (keep it at WARNING)
logging.getLogger('werkzeug').setLevel(logging.WARNING)
logging.getLogger('pymongo').setLevel(logging.WARNING)

_logger = logging.getLogger(__name__)
_logger.info('Logging initialized (console: INFO, file: DEBUG)')

# This is the non-admin ("main") app: it is fully self-contained. Its own
# directory holds context/services/routes/loaders/auth, the imported_player
# module, and a LOCAL copy of the tactical_match_engine package. Putting this
# dir first on sys.path means `import tactical_match_engine` resolves to the
# local copy — not the shared one at the workspace root. No scraper paths are
# added (scraping lives only in the admin app).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, render_template, request, jsonify, send_file, abort

# ── Data-layer auto-switch: MongoDB first, file-system fallback ───────────────
import re as _re

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")

def _mask_mongo_uri(uri: str) -> str:
    """Return the connection string with any user:password credentials masked,
    keeping the host/IP visible. Safe to print or log — never leaks secrets."""
    uri = uri.split("?", 1)[0]                          # drop query string
    return _re.sub(r"://[^/@]+@", "://***:***@", uri)   # mask credentials

def _build_data_loader():
    try:
        from pymongo import MongoClient
        _logger.debug(f"Attempting MongoDB connection to {_mask_mongo_uri(MONGO_URI)}")
        from db_tls import tls_kwargs
        MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000, **tls_kwargs(MONGO_URI)).server_info()
        import mongo_loader as _dl
        _logger.info(f"Data layer: MongoDB -> {_mask_mongo_uri(MONGO_URI)} (connection successful)")
        return _dl, True
    except Exception as e:
        _logger.warning(f"MongoDB connection failed: {e} — falling back to file-system JSON")
        import data_loader as _dl
        _logger.info(f"Data layer: file-system (JSON) — MongoDB at {_mask_mongo_uri(MONGO_URI)} unreachable")
        return _dl, False

_loader, _USING_MONGO = _build_data_loader()

def mongo_display() -> str:
    """One-line description of the active data source (safe to print — no secrets)."""
    if _USING_MONGO:
        return f"MongoDB @ {_mask_mongo_uri(MONGO_URI)}"
    return f"file-system JSON (MongoDB at {_mask_mongo_uri(MONGO_URI)} unreachable)"
get_all_competitions  = _loader.get_all_competitions
get_competition       = _loader.get_competition
get_club              = _loader.get_club
get_club_players      = _loader.get_club_players
get_player            = _loader.get_player
search_players        = _loader.search_players
get_all_players_flat  = _loader.get_all_players_flat
get_all_clubs_flat    = _loader.get_all_clubs_flat
get_heatmap_path      = _loader.get_heatmap_path
get_home_stats        = _loader.get_home_stats
import csv
import io
import os

# ── Path resolution (works both in dev and in the frozen .exe) ────────────────
def _resolve_dir(env_key: str, *rel_parts) -> str:
    """Return the env-var value if set, otherwise resolve relative to __file__."""
    from_env = os.environ.get(env_key)
    if from_env:
        return from_env
    return os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), *rel_parts))


_WEBAPP_DIR = _resolve_dir('TACTICAL_WEBAPP_DIR')
OUTPUT_DIR  = _resolve_dir('TACTICAL_OUTPUT_DIR', '..', 'output')

# == Ad-hoc imported-player store (in-memory) ===============================
from imported_player import (
    register_import_routes, store_get, is_import_token, token_id,
    build_engine_candidate,
)

# == Home-page + player-id caches ===========================================
import time as _time
_home_stats_cache = {'data': None, 'ts': 0}
_HOME_STATS_TTL   = 300  # 5 minutes

_player_id_cache: dict = {}  # player_id -> profile_path

# == Match-stats layer + background-job registry ============================
import threading as _threading
import uuid as _uuid
from match_loader import (
    get_all_matches, get_match,
    get_player_match_history, compute_per90,
    MATCH_STATS_META, _DERIVED_STATS,
)

MATCH_OUTPUT_DIR = _resolve_dir('TACTICAL_MATCH_OUTPUT_DIR', '..', 'match_output')

# In-memory job registry for background scrape tasks
_scrape_jobs: dict = {}
_scrape_jobs_lock = _threading.Lock()
