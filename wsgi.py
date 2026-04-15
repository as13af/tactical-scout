"""
wsgi.py — Production entry point for Gunicorn / Render deployment.

Sets up sys.path and TACTICAL_BASE_DIR before importing the Flask app,
so the engine and data files resolve correctly on the server.
"""
import os
import sys

# Repo root = the directory this file lives in
BASE = os.path.dirname(os.path.abspath(__file__))

# Make sure the engine and webapp modules are importable
for _p in (
    os.path.join(BASE, 'Coding', 'webapp'),
    os.path.join(BASE, 'Coding'),
    BASE,
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Tell all path-resolution code where the repo root is
os.environ.setdefault('TACTICAL_BASE_DIR',           BASE)
os.environ.setdefault('TACTICAL_OUTPUT_DIR',         os.path.join(BASE, 'Coding', 'output'))
os.environ.setdefault('TACTICAL_MATCH_OUTPUT_DIR',   os.path.join(BASE, 'Coding', 'match_output'))
os.environ.setdefault('TACTICAL_WEBAPP_DIR',         os.path.join(BASE, 'Coding', 'webapp'))

# Import must come AFTER env vars are set
from app import app  # noqa: E402  (Coding/webapp/app.py)

if __name__ == '__main__':
    app.run()
