"""
db_tls.py
---------
Shared MongoDB TLS helper.

On hosts without a system CA bundle (e.g. Vercel serverless), pymongo's TLS
handshake to MongoDB Atlas fails with "SSL handshake failed" because it can't
verify Atlas's certificate. Passing certifi's CA bundle via `tlsCAFile` fixes it.

Only applied to TLS/SRV connection strings (Atlas), never to a plain local
`mongodb://localhost` connection.
"""
import os

try:
    import certifi
    _CA_FILE = certifi.where()
except Exception:  # certifi not installed (e.g. minimal local dev) — no-op
    _CA_FILE = None


def tls_kwargs(uri: str) -> dict:
    """Return MongoClient kwargs to enable proper CA verification for Atlas."""
    u = (uri or "").lower()
    if _CA_FILE and ("mongodb+srv" in u or "tls=true" in u or "ssl=true" in u):
        return {"tlsCAFile": _CA_FILE}
    return {}
