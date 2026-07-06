"""
Vercel entry point — thin wrapper that loads the actual app.
Vercel requires a serverless function to live under the root api/ directory,
so this file satisfies that constraint while keeping the real logic in
Coding/vercel_app/api/index.py.
"""
import sys, os, importlib.util

_src = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    '..', 'Coding', 'vercel_app', 'api', 'index.py'
))

_spec = importlib.util.spec_from_file_location('_vercel_app', _src)
_mod  = importlib.util.module_from_spec(_spec)
sys.modules['_vercel_app'] = _mod
_spec.loader.exec_module(_mod)

app = _mod.app
