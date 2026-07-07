"""
mongo_import.py
---------------
One-time (and re-runnable) bulk import of all JSON files from Coding/output/
into a local MongoDB instance (mongodb://localhost:27017, db: tactical_scout).

Run from the repo root:
    python Coding/mongo_import.py

Or specify a custom output dir:
    python Coding/mongo_import.py --output path/to/output

Collections created:
    players      – one doc per Players/*.json
    clubs        – one doc per Club_*.json
    standings    – one doc per Standings_*.json
    season_info  – one doc per SeasonInfo_*.json
    cup_trees    – one doc per CupTrees_*.json
    heatmaps     – one doc per Heatmaps/*.png (binary)

All upserts are idempotent; re-running updates existing docs.
"""

import argparse
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

try:
    from pymongo import MongoClient, UpdateOne
    from pymongo.errors import BulkWriteError
    from bson import Binary
except ImportError:
    sys.exit("pymongo not installed. Run: pip install pymongo")

# ── Config ────────────────────────────────────────────────────────────────────
# Prefer the MONGO_URI env var (same one the app uses) so this always targets the
# live cluster. Falls back to the active cluster0 if the env var is unset.
MONGO_URI  = os.environ.get("MONGO_URI") or (
    "mongodb+srv://d16651308_db_user:R0ckyM0untain100300"
    "@cluster0.onvzxpl.mongodb.net/tactical_scout?retryWrites=true&w=majority&appName=Cluster0"
)
DB_NAME    = "tactical_scout"
BATCH_SIZE = 200   # upsert this many docs per bulk_write call

# ── Helpers ───────────────────────────────────────────────────────────────────

def _nrm(s: str) -> str:
    """Normalize string to lowercase, NFD, and remove diacritics."""
    if not s: return ""
    nfc = unicodedata.normalize("NFC", str(s))
    return "".join(
        c for c in unicodedata.normalize("NFD", nfc)
        if unicodedata.category(c) != "Mn"
    ).lower()


def _load(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"  SKIP (bad JSON): {path}  — {e}")
        return {}


def _stem_ids(stem: str):
    """
    Extract (tournament_id, season_id) from stems like:
      Standings_HNL_170_Season_76980
      Club_GNK_Dinamo_Zagreb_2032_HNL_170_Season_76980
    Returns ('', '') on no match.
    """
    m = re.search(r"_(\d+)_Season_(\d+)$", stem)
    if m:
        return m.group(1), m.group(2)
    return "", ""


def _build_ops_players(f: Path, country: str, competition: str, club: str) -> UpdateOne | None:
    data = _load(f)
    if not data:
        return None
    pid       = str(data.get("player_id", ""))
    tid       = str(data.get("tournament_id", ""))
    season_id = str(data.get("season_id", ""))
    if not pid:
        return None
    data["_country"]     = country
    data["_competition"] = competition
    data["_club"]        = club
    data["_file"]        = f.name

    # Normalized name for accent-insensitive search
    if "player_name" in data:
        data["player_name_nrm"] = _nrm(data["player_name"])
    if "team_name" in data:
        data["team_name_nrm"] = _nrm(data["team_name"])

    # Ensure unique_tournament_id is available as INT for aggregator
    if tid:
        try: data["unique_tournament_id"] = int(tid)
        except: pass

    return UpdateOne(
        {"player_id": pid, "tournament_id": tid, "season_id": season_id},
        {"$set": data},
        upsert=True,
    )


def _build_ops_clubs(f: Path, country: str, competition: str, club: str) -> UpdateOne | None:
    data = _load(f)
    if not data:
        return None
    team_id   = str(data.get("team_id", ""))
    tid       = str(data.get("tournament_id", ""))
    season_id = str(data.get("season_id", ""))
    if not team_id:
        return None
    data["_country"]     = country
    data["_competition"] = competition
    data["_club"]        = club
    data["_file"]        = f.name

    # Normalized name for accent-insensitive search
    if "player_name" in data:
        data["player_name_nrm"] = _nrm(data["player_name"])
    if "team_name" in data:
        data["team_name_nrm"] = _nrm(data["team_name"])

    # Ensure unique_tournament_id is available as INT for aggregator
    if tid:
        try: data["unique_tournament_id"] = int(tid)
        except: pass

    return UpdateOne(
        {"team_id": team_id, "tournament_id": tid, "season_id": season_id},
        {"$set": data},
        upsert=True,
    )


def _build_op_heatmap(f: Path, country: str, competition: str, club: str) -> UpdateOne | None:
    try:
        data = Binary(f.read_bytes())
    except Exception:
        return None
    stem = f.stem  # e.g. "PlayerName_123_..._heatmap"
    return UpdateOne(
        {"_country": country, "_competition": competition, "_club": club, "_stem": stem},
        {"$set": {"_country": country, "_competition": competition,
                  "_club": club, "_stem": stem, "data": data}},
        upsert=True,
    )


def _build_op_league_file(f: Path, country: str, competition: str,
                           collection_name: str) -> UpdateOne | None:
    data = _load(f)
    if not data:
        return None
    tid, season_id = _stem_ids(f.stem)
    data["_country"]     = country
    data["_competition"] = competition
    data["_file"]        = f.name
    data["tournament_id"] = data.get("tournament_id") or tid
    data["season_id"]     = data.get("season_id") or season_id
    return UpdateOne(
        {"_country": country, "_competition": competition, "_file": f.name},
        {"$set": data},
        upsert=True,
    )


def _flush(col, ops: list, counters: dict, label: str):
    if not ops:
        return
    try:
        r = col.bulk_write(ops, ordered=False)
        counters["upserted"] += r.upserted_count + r.modified_count
    except BulkWriteError as e:
        counters["errors"] += len(e.details.get("writeErrors", []))
    ops.clear()


# ── Index creation ────────────────────────────────────────────────────────────

def create_indexes(db):
    print("Creating indexes ...")

    # players
    db.players.create_index(
        [("player_id", 1), ("tournament_id", 1), ("season_id", 1)],
        unique=True, name="player_uniq"
    )
    db.players.create_index([("_country", 1), ("_competition", 1)], name="player_league")
    db.players.create_index([("positions", 1)],                     name="player_positions")
    db.players.create_index([("team_id", 1)],                       name="player_team")
    db.players.create_index([("player_name", "text")],              name="player_name_text")

    # clubs
    db.clubs.create_index(
        [("team_id", 1), ("tournament_id", 1), ("season_id", 1)],
        unique=True, name="club_uniq"
    )
    db.clubs.create_index([("_country", 1), ("_competition", 1)], name="club_league")

    # standings / season_info / cup_trees
    for col_name in ("standings", "season_info", "cup_trees"):
        db[col_name].create_index(
            [("_country", 1), ("_competition", 1), ("_file", 1)],
            unique=True, name=f"{col_name}_uniq"
        )

    # heatmaps
    db.heatmaps.create_index(
        [("_country", 1), ("_competition", 1), ("_club", 1), ("_stem", 1)],
        unique=True, name="heatmap_uniq"
    )

    # matches — populated by match_stats_scraper.py; queried by form_engine.py
    db.matches.create_index(
        [("event_id", 1)],
        unique=True, name="match_event_uniq"
    )
    # Supports fetch_last_n_league_matches queries per home/away team
    db.matches.create_index(
        [("home_team_id", 1), ("match_date", -1), ("unique_tournament_id", 1)],
        name="match_home_uniq_tid"
    )
    db.matches.create_index(
        [("away_team_id", 1), ("match_date", -1), ("unique_tournament_id", 1)],
        name="match_away_uniq_tid"
    )
    # Legacy indexes on tournament_id (kept for backward-compat)
    db.matches.create_index(
        [("home_team_id", 1), ("match_date", -1), ("tournament_id", 1)],
        name="match_home_date"
    )
    db.matches.create_index(
        [("away_team_id", 1), ("match_date", -1), ("tournament_id", 1)],
        name="match_away_date"
    )

    print("  Indexes ready.")


# ── Storage report ─────────────────────────────────────────────────────────────

FREE_TIER_MB = 512  # MongoDB Atlas M0 shared-tier limit

def print_db_size(db, label: str):
    """Print storage + index size against the 512 MB free-tier budget."""
    try:
        s = db.command("dbstats")
    except Exception as e:
        print(f"  [{label}] could not read dbstats: {e}")
        return
    mb = lambda b: b / 1024 / 1024
    used = mb(s.get("storageSize", 0)) + mb(s.get("indexSize", 0))
    print(f"  [{label}] storage {mb(s.get('storageSize',0)):.1f} MB + "
          f"index {mb(s.get('indexSize',0)):.1f} MB = {used:.1f} MB "
          f"/ {FREE_TIER_MB} MB  ({used/FREE_TIER_MB*100:.0f}% used, "
          f"{FREE_TIER_MB-used:.0f} MB free)")


def _league_allowed(country: str, competition: str | None, only: set | None) -> bool:
    """True when this country/competition passes the --only filter (or no filter)."""
    if not only:
        return True
    c = country.lower()
    for token in only:
        parts = token.lower().split("/", 1)
        if parts[0] != c:
            continue
        if len(parts) == 1 or competition is None:
            return True
        if competition.lower() == parts[1]:
            return True
    return False


# ── Main walk ─────────────────────────────────────────────────────────────────

def run_import(output_dir: str, mongo_uri: str = MONGO_URI, only: set | None = None):
    base = Path(output_dir)
    if not base.exists():
        sys.exit(f"output dir not found: {base.resolve()}")

    # On hosts without a system CA bundle, pymongo's TLS handshake to Atlas can
    # fail cert verification. Pass certifi's CA bundle for TLS/SRV URIs (same
    # rationale as webapp/db_tls.py). Never applied to plain localhost.
    client_kwargs = {"serverSelectionTimeoutMS": 8000}
    _u = (mongo_uri or "").lower()
    if "mongodb+srv" in _u or "tls=true" in _u or "ssl=true" in _u:
        try:
            import certifi
            client_kwargs["tlsCAFile"] = certifi.where()
        except Exception:
            pass

    client = MongoClient(mongo_uri, **client_kwargs)
    try:
        client.admin.command("ping")
    except Exception as e:
        sys.exit(f"Cannot connect to MongoDB at {mongo_uri}: {e}")

    db = client[DB_NAME]
    if only:
        print(f"Filtering import to: {', '.join(sorted(only))}")
    print("Before import:")
    print_db_size(db, "size")
    create_indexes(db)

    counters = {
        "players":    {"upserted": 0, "errors": 0},
        "clubs":      {"upserted": 0, "errors": 0},
        "standings":  {"upserted": 0, "errors": 0},
        "season_info":{"upserted": 0, "errors": 0},
        "cup_trees":  {"upserted": 0, "errors": 0},
        "heatmaps":   {"upserted": 0, "errors": 0},
    }

    player_ops:    list = []
    club_ops:      list = []
    standings_ops: list = []
    sinfo_ops:     list = []
    cup_ops:       list = []
    heatmap_ops:   list = []

    country_dirs = sorted(d for d in base.iterdir() if d.is_dir())
    if only:
        country_dirs = [d for d in country_dirs if _league_allowed(d.name, None, only)]
        if not country_dirs:
            sys.exit(f"--only {sorted(only)} matched no country folders under {base}")
    total_countries = len(country_dirs)

    for ci, country_dir in enumerate(country_dirs, 1):
        country = country_dir.name
        comp_dirs = sorted(d for d in country_dir.iterdir() if d.is_dir())
        if only:
            comp_dirs = [d for d in comp_dirs if _league_allowed(country, d.name, only)]
        print(f"[{ci}/{total_countries}] {country} — {len(comp_dirs)} league(s)")

        for comp_dir in comp_dirs:
            competition = comp_dir.name

            # ── League-level files ────────────────────────────────────────────
            for f in comp_dir.glob("Standings_*.json"):
                op = _build_op_league_file(f, country, competition, "standings")
                if op:
                    standings_ops.append(op)
                    if len(standings_ops) >= BATCH_SIZE:
                        _flush(db.standings, standings_ops, counters["standings"], "standings")

            for f in comp_dir.glob("SeasonInfo_*.json"):
                op = _build_op_league_file(f, country, competition, "season_info")
                if op:
                    sinfo_ops.append(op)
                    if len(sinfo_ops) >= BATCH_SIZE:
                        _flush(db.season_info, sinfo_ops, counters["season_info"], "season_info")

            for f in comp_dir.glob("CupTrees_*.json"):
                op = _build_op_league_file(f, country, competition, "cup_trees")
                if op:
                    cup_ops.append(op)
                    if len(cup_ops) >= BATCH_SIZE:
                        _flush(db.cup_trees, cup_ops, counters["cup_trees"], "cup_trees")

            # ── Club folders ──────────────────────────────────────────────────
            for club_dir in sorted(d for d in comp_dir.iterdir() if d.is_dir()):
                club = club_dir.name

                # Club JSON
                for f in club_dir.glob("Club_*.json"):
                    op = _build_ops_clubs(f, country, competition, club)
                    if op:
                        club_ops.append(op)
                        if len(club_ops) >= BATCH_SIZE:
                            _flush(db.clubs, club_ops, counters["clubs"], "clubs")

                # Player JSONs
                players_dir = club_dir / "Players"
                if players_dir.exists():
                    for f in players_dir.glob("*.json"):
                        op = _build_ops_players(f, country, competition, club)
                        if op:
                            player_ops.append(op)
                            if len(player_ops) >= BATCH_SIZE:
                                _flush(db.players, player_ops, counters["players"], "players")

                # Heatmap PNGs
                heatmaps_dir = club_dir / "Heatmaps"
                if heatmaps_dir.exists():
                    for f in heatmaps_dir.glob("*.png"):
                        op = _build_op_heatmap(f, country, competition, club)
                        if op:
                            heatmap_ops.append(op)
                            if len(heatmap_ops) >= BATCH_SIZE:
                                _flush(db.heatmaps, heatmap_ops, counters["heatmaps"], "heatmaps")

    # Flush remainders
    _flush(db.players,    player_ops,    counters["players"],    "players")
    _flush(db.clubs,      club_ops,      counters["clubs"],      "clubs")
    _flush(db.standings,  standings_ops, counters["standings"],  "standings")
    _flush(db.season_info,sinfo_ops,     counters["season_info"],"season_info")
    _flush(db.cup_trees,  cup_ops,       counters["cup_trees"],  "cup_trees")
    _flush(db.heatmaps,   heatmap_ops,   counters["heatmaps"],   "heatmaps")

    print("\n-- Import complete --------------------------------")
    for col, c in counters.items():
        print(f"  {col:<12} upserted/updated: {c['upserted']:>6}   errors: {c['errors']}")
    print(f"\n  Total docs in DB:")
    for col in ("players", "clubs", "standings", "season_info", "cup_trees", "heatmaps", "matches"):
        print(f"    db.{col:<12} -> {db[col].count_documents({}):>6} docs")
    print("\nAfter import:")
    print_db_size(db, "size")
    client.close()


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import output/ JSON files into MongoDB.")
    parser.add_argument(
        "--output",
        default=str(Path(__file__).parent / "output"),
        help="Path to the output/ directory (default: Coding/output)",
    )
    parser.add_argument(
        "--mongo-uri",
        default=MONGO_URI,
        help=f"MongoDB connection string (default: {MONGO_URI})",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=None,
        metavar="Country[/Competition]",
        help="Import only these leagues (repeatable). "
             "E.g. --only Malaysia --only Vietnam, or --only Vietnam/V_League_1. "
             "Matches folder names under output/. Omit to import everything.",
    )
    args = parser.parse_args()
    only = set(args.only) if args.only else None
    run_import(args.output, args.mongo_uri, only=only)
