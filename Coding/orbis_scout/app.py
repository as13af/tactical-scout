"""
app.py
------
Application entry point and Flask app factory for the Scout web app.

The 4000-line monolith that used to live here has been split into:

    context.py          shared state / config / loaders / job registries
    services/           business-logic helpers (positions, scatter, ...)
    routes/             one Blueprint per domain (core, transfermarkt, ...)
    auth.py             sign up / sign in / sessions

This module just assembles those pieces: it creates the Flask app, registers
the cross-cutting request handlers, mounts every blueprint, and exposes a few
module-level names (`app`, `_loader`, `_USING_MONGO`) that the test-suite and
the dev runner rely on.
"""

import os
import sys
import time as _timing

# Make this directory importable as top-level modules (`context`, `services`,
# `routes`) regardless of how the app is launched: `python app.py` from webapp/,
# `python -m webapp.app` from the parent dir, or via the test suite.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, request, jsonify, redirect, url_for

import context as ctx
from context import (
    _logger, _WEBAPP_DIR, _loader, _USING_MONGO,
    register_import_routes, mongo_display,
)
from auth import register_auth_routes

# Blueprints (each module owns a `bp`)
# NOTE: This is the non-admin ("main") app — scraper/import blueprints
# (scraping, manual_import, manual_match_import, transfermarkt) are intentionally
# excluded. Scraping lives only in the admin app (Coding/webapp).
from routes import (
    core, compatibility, exports,
    scatter, team_viz, rankings, matches,
)

_BLUEPRINTS = [
    core.bp, compatibility.bp, exports.bp,
    scatter.bp, team_viz.bp, rankings.bp, matches.bp,
]


_AUTH_PUBLIC = frozenset(['/signin', '/signup', '/logout'])


def _register_request_hooks(app: Flask) -> None:
    """Cross-cutting request/response logging + error handling + globals."""

    @app.before_request
    def _enforce_login():
        from auth import current_user
        if request.path in _AUTH_PUBLIC or request.path.startswith('/static/'):
            return
        if current_user() is None:
            return redirect(url_for('signin', next=request.path))

    @app.before_request
    def _log_request():
        request.start_time = _timing.time()
        _logger.info(f"[REQ] {request.method} {request.path} from {request.remote_addr}")
        if request.args:
            _logger.debug(f"  Query params: {dict(request.args)}")
        if request.method in ('POST', 'PUT', 'PATCH'):
            try:
                _logger.debug(f"  Body: {request.get_json(silent=True) or request.form}")
            except Exception:
                pass

    @app.after_request
    def _log_response(response):
        elapsed = _timing.time() - getattr(request, 'start_time', _timing.time())
        _logger.info(f"[RES] {request.method} {request.path} → {response.status_code} ({elapsed:.3f}s)")
        return response

    @app.errorhandler(Exception)
    def _log_error(error):
        # Let real HTTP errors (404, 405, …) keep their status instead of
        # masking them all as 500 — otherwise abort(404) surfaces as a 500.
        from werkzeug.exceptions import HTTPException
        if isinstance(error, HTTPException):
            return error
        _logger.error(f"[ERR] Unhandled exception: {error}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500

    @app.context_processor
    def _inject_globals():
        # `endpoint` is the blueprint-unqualified endpoint name (e.g. 'home'
        # rather than 'core.home') so templates can keep comparing against the
        # original bare route names after the blueprint split.
        ep = (request.endpoint or '').rsplit('.', 1)[-1]
        return {'using_mongo': ctx._USING_MONGO, 'endpoint': ep}


def create_app() -> Flask:
    """Build and configure the Flask application."""
    app = Flask(
        __name__,
        template_folder=os.path.join(_WEBAPP_DIR, 'templates'),
        static_folder=os.path.join(_WEBAPP_DIR, 'static'),
    )

    _register_request_hooks(app)

    # In-memory ad-hoc player import (adds POST /api/import_player)
    register_import_routes(app)
    # Authentication (sign up / sign in / logout) + current_user globals
    register_auth_routes(app)

    # Domain blueprints
    for bp in _BLUEPRINTS:
        app.register_blueprint(bp)

    _logger.info(f"Flask app assembled — {len(_BLUEPRINTS)} blueprints, "
                 f"{len(list(app.url_map.iter_rules()))} routes")
    return app


app = create_app()

if __name__ == '__main__':
    import socket

    # Non-admin app runs on 5001 so it can run alongside the admin app (5000).
    PORT = 5001
    # Local-only by default. Set HOST = '0.0.0.0' to also open the app from
    # other devices on your network (phone, another PC).
    HOST = '127.0.0.1'

    def _lan_ip():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('8.8.8.8', 80))
            return s.getsockname()[0]
        except Exception:
            return None
        finally:
            s.close()

    # The debug reloader re-runs this file; only print in the supervisor pass.
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        print('\n  Scout (main) is running — open in your browser:')
        print(f'      Local:    http://127.0.0.1:{PORT}')
        if HOST == '0.0.0.0':
            ip = _lan_ip()
            if ip:
                print(f'      Network:  http://{ip}:{PORT}')
        print(f'      Database: {mongo_display()}')
        print()

    app.run(debug=True, host=HOST, port=PORT)
