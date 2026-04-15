"""
launcher.py — Entry point for the Tactical Scout standalone executable.

When double-clicked:
  1. Determines the bundle's base directory (sys._MEIPASS when frozen,
     the workspace root when run as a plain Python script).
  2. Sets environment variables so the webapp and engine modules can find
     their data files regardless of __file__ behaviour inside the frozen exe.
  3. Starts the Flask server on a free port in a daemon thread.
  4. Opens the default browser automatically.
"""

import os
import sys
import socket
import threading
import time
import webbrowser


# ── Resolve base directory ────────────────────────────────────────────────────
def _get_base() -> str:
    """Return the root of bundled data (sys._MEIPASS) or the repo root."""
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS  # type: ignore[attr-defined]
    # Running as a plain script: go up from launcher.py location
    return os.path.dirname(os.path.abspath(__file__))


BASE = _get_base()


# ── Environment variables used by engine + webapp modules ────────────────────
os.environ.setdefault('TACTICAL_BASE_DIR',           BASE)
os.environ.setdefault('TACTICAL_OUTPUT_DIR',         os.path.join(BASE, 'Coding', 'output'))
os.environ.setdefault('TACTICAL_MATCH_OUTPUT_DIR',   os.path.join(BASE, 'Coding', 'match_output'))
os.environ.setdefault('TACTICAL_WEBAPP_DIR',         os.path.join(BASE, 'Coding', 'webapp'))


# ── sys.path setup ────────────────────────────────────────────────────────────
# Order matters: webapp dir first so 'import app' finds Coding/webapp/app.py,
# then Coding/ for scraper_cli / driver / helpers, then workspace root for
# tactical_match_engine.
for _p in (
    os.path.join(BASE, 'Coding', 'webapp'),
    os.path.join(BASE, 'Coding'),
    BASE,
):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ── Pick a free port ──────────────────────────────────────────────────────────
def _free_port(preferred: int = 5000) -> int:
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('127.0.0.1', port)) != 0:
                return port
    return preferred   # fall back; Flask will error if truly occupied


PORT = _free_port()


# ── Flask server thread ───────────────────────────────────────────────────────
def _run_flask() -> None:
    # Import is deferred so that env vars are already set when app.py runs its
    # module-level code (OUTPUT_DIR = ..., MATCH_OUTPUT_DIR = ...).
    import app as flask_app   # Coding/webapp/app.py
    flask_app.app.run(
        host='127.0.0.1',
        port=PORT,
        debug=False,
        use_reloader=False,
        threaded=True,
    )


_server = threading.Thread(target=_run_flask, name='flask-server', daemon=True)
_server.start()


# ── Open browser after server is ready ───────────────────────────────────────
def _wait_and_open() -> None:
    url = f'http://127.0.0.1:{PORT}'
    for _ in range(30):          # wait up to 3 seconds
        time.sleep(0.1)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('127.0.0.1', PORT)) == 0:
                break
    webbrowser.open(url)


threading.Thread(target=_wait_and_open, daemon=True).start()


# ── Keep the process alive ────────────────────────────────────────────────────
# The Flask thread is a daemon; we need the main thread to stay up.
try:
    while True:
        time.sleep(60)
except KeyboardInterrupt:
    pass
