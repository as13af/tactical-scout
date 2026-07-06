"""
mongo_loader.py
---------------
Drop-in replacement for data_loader.py backed by MongoDB instead of the
file tree.  All public function signatures are identical so app.py only
needs a one-line import change:

    import mongo_loader as data_loader

MongoDB connection is configured via the MONGO_URI environment variable
(defaults to mongodb://localhost:27017).  Database name: tactical_scout.

The output_dir argument is accepted by every function (for signature
compatibility) but is only used by get_heatmap_path() as a disk fallback
when the PNG has not yet been imported into MongoDB.
"""

import os
import json
import re
import unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    from pymongo import MongoClient
except ImportError:
    raise ImportError("pymongo is required. Run: pip install pymongo")

# ── Connection ────────────────────────────────────────────────────────────────
_MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
_DB_NAME   = "tactical_scout"

_client: MongoClient | None = None

_client: MongoClient | None = None

def _db():
    global _client
    if _client is None:
        _client = MongoClient(
            _MONGO_URI,
            serverSelectionTimeoutMS=int(os.environ.get("MONGO_TIMEOUT_MS", "3000")),
        )
    return _client[_DB_NAME]

# ── Shared helpers (kept identical to data_loader) ────────────────────────────

def _nrm(s: str) -> str:
    nfc = unicodedata.normalize("NFC", str(s))
    return "".join(
        c for c in unicodedata.normalize("NFD", nfc)
        if unicodedata.category(c) != "Mn"
    ).lower()

def _load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _calc_age(dob_str: str) -> int:
    """Return age in years from an ISO-8601 dateOfBirth string, or 25 on failure."""
    if not dob_str:
        return 25
    try:
        from datetime import date
        dob_str = dob_str.replace("Z", "+00:00")
        birth = datetime.fromisoformat(dob_str).date()
        today = date.today()
        age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
        return max(15, min(45, age))
    except Exception:
        return 25


def _contract_info(profile: dict):
    """Return (contract_until_ts, contract_until_str, contract_badge)."""
    _cts = int(profile.get("contractUntilTimestamp") or 0)
    contract_until = ""
    contract_badge = ""
    if _cts:
        _dt = datetime.fromtimestamp(_cts, tz=timezone.utc)
        contract_until = _dt.strftime("%b %Y")
        _now_ts = datetime.now(timezone.utc).timestamp()
        _remaining = (_cts - _now_ts) / 86400
        if _cts < _now_ts:
            contract_badge = "expired"
        elif _remaining <= 182:
            contract_badge = "red"
        elif _remaining <= 365:
            contract_badge = "yellow"
        else:
            contract_badge = "green"
    return _cts, contract_until, contract_badge

def _parse_player_doc(doc: dict) -> dict:
    """Convert a MongoDB player document → flat summary dict (same shape as data_loader)."""
    profile  = doc.get("profile", {}).get("player", {})
    stats    = doc.get("statistics", {}).get("statistics", {})
    pos_list = profile.get("positionsDetailed") or doc.get("positions", [])

    age = profile.get("age", "")
    dob = profile.get("dateOfBirth", "")
    if not age and dob:
        from datetime import date
        try:
            today      = date.today()
            birth_date = datetime.fromisoformat(dob).date()
            age = today.year - birth_date.year - (
                (today.month, today.day) < (birth_date.month, birth_date.day)
            )
        except Exception:
            age = ""

    _cts, contract_until, contract_badge = _contract_info(profile)
    country     = doc.get("_country", doc.get("competition_country", ""))
    competition = doc.get("_competition", doc.get("competition_name", ""))
    club        = doc.get("_club", doc.get("team", ""))

    return {
        "name":              doc.get("player_name", profile.get("name", "")),
        "player_id":         doc.get("player_id", ""),
        "team":              doc.get("team", club),
        "team_id":           doc.get("team_id", ""),
        "competition":       competition,
        "country":           country,
        "club_slug":         club,
        "position":          profile.get("position", pos_list[0] if pos_list else ""),
        "positions":         pos_list,
        "nationality":       (profile.get("country") or {}).get("name", "") or profile.get("nationality", ""),
        "age":               age,
        "height":            profile.get("height", ""),
        "preferred_foot":    profile.get("preferredFoot", ""),
        "shirt_number":      profile.get("shirtNumber", ""),
        "stats":             stats,
        "file":              doc.get("_file", ""),
        "path":              f"{country}/{competition}/{club}/{doc.get('_file', '')}",
        "contract_until_ts": _cts,
        "contract_until":    contract_until,
        "contract_badge":    contract_badge,
    }

_POS_CATEGORY = {
    "GK": "G",
    "DC": "D", "DL": "D", "DR": "D",
    "DM": "M", "MC": "M", "AM": "M", "ML": "M", "MR": "M",
    "LW": "F", "RW": "F", "ST": "F",
}

def profile_category(positions, fallback_position):
    for pos in positions:
        cat = _POS_CATEGORY.get(str(pos).upper())
        if cat:
            return cat
    return str(fallback_position or "").upper()

# ── Home stats ────────────────────────────────────────────────────────────────

def get_home_stats(output_dir):
    db = _db()

    total_players      = db.players.count_documents({})
    total_clubs        = db.clubs.count_documents({})
    total_competitions = db.standings.count_documents({})

    pipeline = [
        {"$project": {
            "player_name": 1,
            "team": 1,
            "_competition": 1,
            "_country": 1,
            "_file": 1,
            "_club": 1,
            "rating": {"$toDouble": {"$ifNull": ["$statistics.statistics.rating", 0]}},
            "appearances": {"$toInt": {"$ifNull": ["$statistics.statistics.appearances", 0]}},
            "goals": {"$toInt": {"$ifNull": ["$statistics.statistics.goals", 0]}},
            "assists": {"$toInt": {"$ifNull": ["$statistics.statistics.assists", 0]}},
            "positions": {"$ifNull": ["$profile.player.positionsDetailed", "$positions"]},
            "profile_position": "$profile.player.position",
        }},
        {"$match": {"rating": {"$gte": 6.5}, "appearances": {"$gte": 5}}},
        {"$sort": {"rating": -1}},
        {"$limit": 10},
    ]

    top_players = []
    for doc in db.players.aggregate(pipeline):
        pos_list = doc.get("positions") or []
        top_players.append({
            "name":        doc.get("player_name", ""),
            "team":        doc.get("team", ""),
            "competition": (doc.get("_competition") or "").replace("_", " "),
            "country":     (doc.get("_country") or "").replace("_", " "),
            "position":    pos_list[0] if pos_list else doc.get("profile_position", ""),
            "rating":      round(doc.get("rating", 0), 2),
            "goals":       doc.get("goals", 0),
            "assists":     doc.get("assists", 0),
            "appearances": doc.get("appearances", 0),
            "path":        f"{doc.get('_country','')}/{doc.get('_competition','')}/{doc.get('_club','')}/{doc.get('_file','')}",
        })

    return {
        "competitions": total_competitions,
        "clubs":        total_clubs,
        "players":      total_players,
        "top_players":  top_players,
    }

# ── Competitions ──────────────────────────────────────────────────────────────

def get_all_competitions(output_dir):
    db     = _db()
    result = []
    # Use standings docs — one per league
    for doc in db.standings.find({}, {"_country": 1, "_competition": 1, "_file": 1}).sort(
            [("_country", 1), ("_competition", 1)]):
        country     = doc.get("_country", "")
        competition = doc.get("_competition", "")
        club_count  = db.clubs.count_documents({"_country": country, "_competition": competition})
        result.append({
            "country":        country,
            "competition":    competition,
            "club_count":     club_count,
            "standings_path": None,   # not needed; kept for template compat
        })
    # deduplicate (multiple standings docs per league are possible)
    seen = set()
    deduped = []
    for r in result:
        key = (r["country"], r["competition"])
        if key not in seen:
            seen.add(key)
            deduped.append(r)
    return deduped

def get_competition(output_dir, country, competition):
    db = _db()

    # Standings
    standings_doc = db.standings.find_one({"_country": country, "_competition": competition}) or {}
    tid       = str(standings_doc.get("tournament_id", ""))
    season_id = str(standings_doc.get("season_id", ""))
    # derive uniq_tid from the nested standings data
    uniq_tid = ""
    for group in standings_doc.get("standings", []):
        tourney = group.get("tournament", {}).get("uniqueTournament", {})
        if tourney.get("id"):
            uniq_tid = str(tourney["id"])
            break

    # If tournament_id/season_id are missing in top-level, try stem
    if not tid:
        fname = standings_doc.get("_file", "")
        m = re.search(r"_(\d+)_Season_(\d+)$", Path(fname).stem)
        if m:
            tid, season_id = m.group(1), m.group(2)

    # Clubs
    clubs = []
    competition_colors = None
    for club_doc in db.clubs.find(
            {"_country": country, "_competition": competition},
            sort=[("team_name", 1)]):
        profile      = club_doc.get("profile", {}).get("team", {})
        season_stats = club_doc.get("season_statistics", {}).get("statistics", {})
        player_count = db.players.count_documents({
            "_country": country, "_competition": competition,
            "team_id": str(club_doc.get("team_id", "")),
        })

        if competition_colors is None:
            ut = profile.get("tournament", {}).get("uniqueTournament", {})
            if ut.get("primaryColorHex"):
                competition_colors = {
                    "primary":   ut["primaryColorHex"],
                    "secondary": ut.get("secondaryColorHex", "#D9D9D9"),
                }

        tc = profile.get("teamColors", {})
        clubs.append({
            "slug":         club_doc.get("_club", ""),
            "name":         club_doc.get("team_name", ""),
            "team_id":      str(club_doc.get("team_id", "")),
            "player_count": player_count,
            "stats":        season_stats,
            "profile":      profile,
            "team_colors":  {
                "primary":   tc.get("primary",   "#6E6E6E"),
                "secondary": tc.get("secondary", "#D9D9D9"),
            },
        })

    # Build standings rows + phases
    rows = []
    standings_phases = []
    for group in standings_doc.get("standings", []):
        phase_name = group.get("phase_name") or group.get("tournament", {}).get("name", "League")
        t_id       = group.get("tournament", {}).get("id")
        phase_rows = group.get("rows", [])
        rows.extend(phase_rows)
        standings_phases.append({
            "phase_name":    phase_name,
            "tournament_id": t_id,
            "rows":          phase_rows,
        })

    # Cup trees
    ct_doc   = db.cup_trees.find_one({"_country": country, "_competition": competition}) or {}
    cup_trees = ct_doc.get("cupTrees", [])

    # Season info
    si_doc     = db.season_info.find_one({"_country": country, "_competition": competition}) or {}
    season_info = si_doc.get("info", {})

    # Derive competition type from stored field or infer from shape
    comp_type = standings_doc.get("competition_type") or (
        "cup_knockout" if (not standings_doc.get("standings") and cup_trees) else
        "cup_groups"   if (len(standings_doc.get("standings", [])) > 1 or
                           (len(standings_doc.get("standings", [])) == 1 and cup_trees)) else
        "league"
    )

    return {
        "competition":        competition,
        "country":            country,
        "clubs":              clubs,
        "standings":          rows,
        "standings_phases":   standings_phases,
        "cup_trees":          cup_trees,
        "season_info":        season_info,
        "tid":                tid,
        "uniq_tid":           uniq_tid,
        "season_id":          season_id,
        "competition_colors": competition_colors or {"primary": "#6E6E6E", "secondary": "#D9D9D9"},
        "competition_type":   comp_type,
    }

# ── Club ──────────────────────────────────────────────────────────────────────

def get_club(output_dir, country, competition, club):
    doc = _db().clubs.find_one({"_country": country, "_competition": competition, "_club": club})
    if doc:
        doc.pop("_id", None)
        return doc
    return {}

def get_club_players(output_dir, country, competition, club):
    players = []
    for doc in _db().players.find(
            {"_country": country, "_competition": competition, "_club": club},
            sort=[("player_name", 1)]):
        players.append(_parse_player_doc(doc))
    return players

# ── Player ────────────────────────────────────────────────────────────────────

def get_player(output_dir, country, competition, club, player_file):
    """Return the full player document from MongoDB."""
    fname = player_file if player_file.endswith(".json") else player_file + ".json"
    doc = _db().players.find_one({"_country": country, "_competition": competition,
                                   "_club": club, "_file": fname})
    if doc:
        doc.pop("_id", None)
        return doc
    return {}

def get_heatmap_path(output_dir, country, competition, club, player_file):
    """Return a URL for the heatmap image.  Checks MongoDB first, then disk."""
    stem = Path(player_file).stem
    heatmap_stem = f"{stem}_heatmap"
    # Check MongoDB
    doc = _db().heatmaps.find_one(
        {"_country": country, "_competition": competition,
         "_club": club, "_stem": heatmap_stem},
        {"_id": 1}
    )
    if doc:
        return f"/heatmap_db/{country}/{competition}/{club}/{heatmap_stem}"
    # Fallback to disk
    base = Path(output_dir) / country / competition / club / "Heatmaps"
    png  = base / f"{heatmap_stem}.png"
    if png.exists():
        rel = png.relative_to(Path(output_dir))
        return str(rel).replace("\\", "/")
    return None

def get_heatmap_bytes(country, competition, club, stem):
    """Return raw PNG bytes from MongoDB, or None if not found."""
    doc = _db().heatmaps.find_one(
        {"_country": country, "_competition": competition,
         "_club": club, "_stem": stem},
        {"data": 1}
    )
    return bytes(doc["data"]) if doc and "data" in doc else None

# ── Player search ─────────────────────────────────────────────────────────────

def search_players(output_dir, filters):
    db = _db()

    # Build MongoDB query
    mongo_filter: dict = {}

    if filters.get("competition"):
        mongo_filter["_competition"] = {
            "$regex": re.escape(filters["competition"]), "$options": "i"
        }
    if filters.get("club"):
        mongo_filter["_club"] = {
            "$regex": re.escape(filters["club"]), "$options": "i"
        }

    # Name normalization filter (for Search page / exports)
    q_name = filters.get("name")
    q_name_nrm = _nrm(q_name) if q_name else ""

    if filters.get("nationality"):
        nrm = _nrm(filters["nationality"])
        mongo_filter["$or"] = [
            {"profile.player.country.name": {"$regex": re.escape(filters["nationality"]), "$options": "i"}},
            {"profile.player.nationality": {"$regex": re.escape(filters["nationality"]), "$options": "i"}},
        ]

    # Numeric min filters mapped to nested stat fields
    _num_filters = {
        "apps_min":      ("statistics.statistics.appearances",           int),
        "rating_min":    ("statistics.statistics.rating",                float),
        "goals_min":     ("statistics.statistics.goals",                 int),
        "assists_min":   ("statistics.statistics.assists",               int),
        "pass_acc_min":  ("statistics.statistics.accuratePassesPercentage", float),
        "duels_min":     ("statistics.statistics.totalDuelsWon",         int),
        "aerial_min":    ("statistics.statistics.aerialDuelsWon",        int),
        "dribbles_min":  ("statistics.statistics.successfulDribbles",    int),
        "tackles_min":   ("statistics.statistics.tackles",               int),
        "key_passes_min":("statistics.statistics.keyPasses",             int),
    }
    for fkey, (field, cast) in _num_filters.items():
        val = filters.get(fkey, "")
        if val:
            try:
                mongo_filter[field] = {"$gte": cast(val)}
            except ValueError:
                pass

    # Age min/max — stored as profile.player.age OR calculated; filter post-query for safety
    # (age is not always a top-level field in the raw JSON)

    # Contract expiry cutoff
    _contract_expiry = filters.get("contract_expiry", "")
    contract_cutoff_ts = None
    if _contract_expiry:
        _now = datetime.now(timezone.utc)
        if _contract_expiry in ("6", "12", "18"):
            contract_cutoff_ts = (_now + timedelta(days=int(_contract_expiry) * 30.44)).timestamp()
        else:
            try:
                contract_cutoff_ts = datetime.fromisoformat(_contract_expiry).replace(
                    tzinfo=timezone.utc).timestamp()
            except Exception:
                pass

    # Fetch matching docs (broad filter; position/age/contract refined in Python)
    cursor = db.players.find(mongo_filter)
    rows   = [_parse_player_doc(doc) for doc in cursor]

    # ── Python-side refinement (position, age, contract) ─────────────────────
    def _f(val, key, cast=str):
        v = filters.get(key, "")
        if not v: return True
        try: return cast(val or 0) >= cast(v)
        except Exception: return True

    filtered = []
    for p in rows:
        # Broad position category (G/D/M/F)
        cat_filter = filters.get("position", "").upper()
        if cat_filter:
            broad = profile_category(p.get("positions", []), p.get("position", ""))
            if not broad.upper().startswith(cat_filter):
                continue
        # Specific position codes (e.g. "DC,AM")
        sp_raw  = filters.get("specific_pos", "").upper()
        sp_list = [x.strip() for x in sp_raw.split(",") if x.strip()]
        if sp_list:
            player_pos = [str(x).upper() for x in p.get("positions", [])]
            if not any(sp in player_pos for sp in sp_list):
                continue
        # Age
        if not _f(p.get("age", ""), "age_min", int): continue
        age_max = filters.get("age_max", "")
        if age_max:
            try:
                if int(p.get("age") or 999) > int(age_max): continue
            except Exception:
                pass
        # Contract expiry
        if contract_cutoff_ts is not None:
            p_ts = p.get("contract_until_ts", 0)
            if not (0 < p_ts <= contract_cutoff_ts):
                continue
        filtered.append(p)

    # Sort
    sort_by  = filters.get("sort_by", "rating")
    sort_dir = filters.get("sort_dir", "desc")
    sort_map = {
        "rating":      lambda p: float(p["stats"].get("rating", 0) or 0),
        "goals":       lambda p: int(p["stats"].get("goals", 0) or 0),
        "assists":     lambda p: int(p["stats"].get("assists", 0) or 0),
        "appearances": lambda p: int(p["stats"].get("appearances", 0) or 0),
        "age":         lambda p: int(p.get("age") or 0),
        "name":        lambda p: p.get("name", "").lower(),
    }
    key_fn = sort_map.get(sort_by, sort_map["rating"])
    filtered.sort(key=key_fn, reverse=(sort_dir == "desc"))

    # Paginate
    page      = int(filters.get("page", 1))
    per_page  = int(filters.get("per_page", 50))
    total     = len(filtered)
    start     = (page - 1) * per_page
    paginated = filtered[start : start + per_page]

    return {
        "players":     paginated,
        "total":       total,
        "page":        page,
        "per_page":    per_page,
        "total_pages": (total + per_page - 1) // per_page,
    }

# ── All-players flat (compare autocomplete) ───────────────────────────────────

_PLAYER_CACHE = None

def get_all_players_flat(output_dir, query="", limit=20):
    global _PLAYER_CACHE
    if _PLAYER_CACHE is None:
        db = _db()
        _PLAYER_CACHE = []
        # Fetch all players once to cache names and metadata
        for doc in db.players.find({}, {
                "player_name": 1, "team": 1,
                "_competition": 1, "_country": 1, "_club": 1, "_file": 1,
                "profile.player.position": 1,
                "profile.player.positionsDetailed": 1, "positions": 1}):
            name  = doc.get("player_name", "")
            _prof = doc.get("profile", {}).get("player", {})
            _pos  = _prof.get("positionsDetailed") or doc.get("positions", [])
            _PLAYER_CACHE.append({
                "name":        name,
                "team":        doc.get("team", ""),
                "position":    _prof.get("position") or (_pos[0] if _pos else ""),
                "competition": (doc.get("_competition") or "").replace("_", " "),
                "path":        f"{doc.get('_country','')}/{doc.get('_competition','')}/{doc.get('_club','')}/{doc.get('_file','')}",
                "nrm":         _nrm(name)
            })

    q_nrm = _nrm(query) if query else ""
    result = []
    
    for p in _PLAYER_CACHE:
        if q_nrm and q_nrm not in p["nrm"]:
            continue
        result.append(p)
        # If no query, we can stop early once we hit the limit
        if not q_nrm and len(result) >= limit:
            break
            
    result.sort(key=lambda x: x["name"].lower())
    return result[:limit]

# ── All-clubs flat (compatibility autocomplete) ───────────────────────────────

def get_all_clubs_flat(output_dir, query="", limit=20, league_filter=""):
    db = _db()
    mfilter: dict = {}
    if league_filter:
        parts = league_filter.split("/")
        if len(parts) >= 1 and parts[0]:
            mfilter["_country"] = parts[0]
        if len(parts) >= 2 and parts[1]:
            mfilter["_competition"] = parts[1]

    result = []
    for doc in db.clubs.find(mfilter, {
            "team_name": 1, "_competition": 1, "_country": 1, "_club": 1}
    ).sort([("team_name", 1)]):
        display = (doc.get("_club") or "").replace("_", " ")
        if query and _nrm(query) not in _nrm(display) \
                  and _nrm(query) not in _nrm((doc.get("_competition") or "").replace("_", " ")) \
                  and _nrm(query) not in _nrm((doc.get("_country") or "")):
            continue
        result.append({
            "name":        display,
            "competition": (doc.get("_competition") or "").replace("_", " "),
            "country":     (doc.get("_country") or "").replace("_", " "),
            "path":        f"{doc.get('_country','')}/{doc.get('_competition','')}/{doc.get('_club','')}",
        })
    return result[:limit]

# ── League positional average ─────────────────────────────────────────────────

_COMPARE_STAT_META = [
    ("appearances",              False, False),
    ("goals",                    True,  False),
    ("assists",                  True,  False),
    ("minutesPlayed",            False, False),
    ("rating",                   False, True ),
    ("totalShots",               True,  False),
    ("shotsOnTarget",            True,  False),
    ("keyPasses",                True,  False),
    ("accuratePassesPercentage", False, True ),
    ("accuratePasses",           True,  False),
    ("successfulDribbles",       True,  False),
    ("totalDuelsWon",            True,  False),
    ("totalDuelsWonPercentage",  False, True ),
    ("aerialDuelsWon",           True,  False),
    ("aerialDuelsWonPercentage", False, True ),
    ("clearances",               True,  False),
    ("ballRecovery",             True,  False),
    ("yellowCards",              False, False),
    ("redCards",                 False, False),
]

_BROAD_GROUP = {
    "GK": "GK",
    "DC": "DEF", "DL": "DEF", "DR": "DEF", "WBL": "DEF", "WBR": "DEF",
    "DM": "MID", "MC": "MID", "AM": "MID", "ML": "MID", "MR": "MID", "CM": "MID",
    "LW": "FWD", "RW": "FWD", "ST": "FWD", "SS": "FWD", "CF": "FWD",
}

def get_league_position_avg(output_dir, player_path, pos_mode="exact"):
    db    = _db()
    parts = player_path.split("/")
    if len(parts) != 4:
        return None

    country, competition, club_slug, filename = parts
    target_file = filename if filename.endswith(".json") else filename + ".json"

    target_doc = db.players.find_one({
        "_country": country, "_competition": competition,
        "_club": club_slug, "_file": target_file,
    })
    if not target_doc:
        return None

    t_profile = target_doc.get("profile", {}).get("player", {})
    pos_list  = [
        str(p).upper()
        for p in (t_profile.get("positionsDetailed") or
                  target_doc.get("positions", [t_profile.get("position", "")]))
        if p
    ]
    if not pos_list:
        return {"error": "could not determine player position"}

    target_broad = {_BROAD_GROUP.get(p) for p in pos_list} - {None}

    # Fetch all players in same competition
    pool_docs = db.players.find(
        {"_country": country, "_competition": competition},
        {"profile.player.positionsDetailed": 1, "positions": 1,
         "profile.player.position": 1, "statistics.statistics": 1, "_club": 1, "_file": 1}
    )

    raw_buckets = {key: [] for key, _, _ in _COMPARE_STAT_META}
    p90_buckets = {
        key: []
        for key, can_p90, is_pct in _COMPARE_STAT_META
        if can_p90 and not is_pct
    }
    pool_size = 0

    for doc in pool_docs:
        # Exclude self
        if doc.get("_club") == club_slug and doc.get("_file") == target_file:
            continue
        p_prof = doc.get("profile", {}).get("player", {})
        p_pos  = [
            str(p).upper()
            for p in (p_prof.get("positionsDetailed") or
                      doc.get("positions", [p_prof.get("position", "")]))
            if p
        ]
        if not p_pos:
            continue

        if pos_mode == "grouped":
            p_broad = {_BROAD_GROUP.get(p) for p in p_pos} - {None}
            if not target_broad.intersection(p_broad):
                continue
        else:
            if not any(p in pos_list for p in p_pos):
                continue

        s    = doc.get("statistics", {}).get("statistics", {})
        mins = float(s.get("minutesPlayed", 0) or 0)
        m90  = mins / 90.0 if mins > 0 else None

        for key, can_p90, is_pct in _COMPARE_STAT_META:
            val = float(s.get(key, 0) or 0)
            raw_buckets[key].append(val)
            if can_p90 and not is_pct and m90:
                p90_buckets[key].append(val / m90)

        pool_size += 1

    averages     = {k: round(sum(v) / len(v), 4) if v else 0.0 for k, v in raw_buckets.items()}
    p90_averages = {k: round(sum(v) / len(v), 4) if v else 0.0 for k, v in p90_buckets.items()}

    return {
        "averages":       averages,
        "p90_averages":   p90_averages,
        "pool_size":      pool_size,
        "positions_used": pos_list,
        "pos_mode":       pos_mode,
        "competition":    competition,
        "country":        country,
        "small_pool":     pool_size < 5,
    }

# ── Resolve player paths (batch compatibility lookup) ────────────────────────

def resolve_player_paths(output_dir: str, player_paths: list) -> list:
    db      = _db()
    results = []
    seen    = set()
    for raw_path in player_paths:
        raw_path = str(raw_path).strip()
        if raw_path in seen:
            continue
        seen.add(raw_path)
        parts = raw_path.split("/")
        if len(parts) != 4:
            continue
        country, competition, club, filename = parts
        fname = filename if filename.endswith(".json") else filename + ".json"
        doc   = db.players.find_one(
            {"_country": country, "_competition": competition,
             "_club": club, "_file": fname},
            {"profile.player.name": 1, "profile.player.position": 1,
             "player_name": 1, "positions": 1}
        )
        if not doc:
            continue
        profile  = doc.get("profile", {}).get("player", {})
        name     = profile.get("name", "") or doc.get("player_name", filename.replace("_", " "))
        position = profile.get("position", "") or (doc.get("positions") or [""])[0]
        results.append({
            "path":        raw_path,
            "name":        name,
            "position":    position,
            "team":        club.replace("_", " "),
            "competition": competition.replace("_", " "),
            "country":     country.replace("_", " "),
        })
    return results

# ── Engine bridge ────────────────────────────────────────────────────────────

def load_sofascore_player_for_engine(doc: dict) -> dict:
    """Convert a MongoDB player document → engine-compatible dict.

    Returns the same shape as tactical_match_engine.services.json_loader
    .load_sofascore_player(path), so the engine can work without file paths.
    """
    from tactical_match_engine.services.json_loader import get_league_info

    profile   = doc.get("profile", {}).get("player", {})
    raw_stats = doc.get("statistics", {}).get("statistics", {})
    country   = doc.get("_country", "").replace("_", " ")
    comp      = doc.get("_competition", "").replace("_", " ")
    league    = get_league_info(country, comp)

    pos_list = (profile.get("positionsDetailed")
                or doc.get("positions")
                or [profile.get("position", "")])
    pos_list = [p for p in pos_list if p]
    position = pos_list[0] if pos_list else "MC"

    age = profile.get("age") or _calc_age(profile.get("dateOfBirth", ""))
    stats_filtered = {
        k: float(v) for k, v in raw_stats.items()
        if isinstance(v, (int, float)) and not isinstance(v, bool) and float(v) >= 0
    }

    return {
        "id":                                  doc.get("player_id"),
        "name":                                doc.get("player_name", profile.get("name", "")),
        "age":                                 age,
        "position":                            position,
        "positions":                           pos_list,
        "preferred_foot":                      profile.get("preferredFoot", ""),
        "height_cm":                           float(profile.get("height") or 0),
        "weight_kg":                           float(profile.get("weight") or 0),
        "raw_stats":                           raw_stats,
        "stats":                               stats_filtered,
        "current_league_intensity":            league["intensity"],
        "current_league_global_rank":          league.get("global_rank"),
        "current_league_name":                 league.get("name", comp),
        "current_league_intensity_estimated":  league.get("is_estimated", False),
    }


# ── Storage Management ────────────────────────────────────────────────────────

def purge_league_matches(uniq_tid: int | str):
    """Delete all match documents for a specific unique_tournament_id."""
    db = _db()
    try:
        tid = int(uniq_tid)
        res = db.matches.delete_many({"unique_tournament_id": tid})
        # Also clear average heatmaps for this league
        db.team_last5match_average_heatmap.delete_many({"unique_tournament_id": tid})
        return res.deleted_count
    except Exception:
        return 0

def optimize_league_storage(uniq_tid: int | str, n: int = 5):
    """Keep only the N most recent matches for every team in a league.
    
    A match is kept if it's in the 'top N' for EITHER the home or away team.
    Matches not belonging to any team's 'top N' in this league are deleted.
    """
    db = _db()
    try:
        tid = int(uniq_tid)
        # 1. Find all teams that have matches in this league
        team_ids = set()
        for side in ["home_team_id", "away_team_id"]:
            team_ids.update(db.matches.distinct(side, {"unique_tournament_id": tid}))
        
        # 2. For each team, get their top N match event_ids
        keep_ids = set()
        for t_id in team_ids:
            if t_id is None: continue
            recent = list(db.matches.find(
                {
                    "unique_tournament_id": tid,
                    "$or": [{"home_team_id": int(t_id)}, {"away_team_id": int(t_id)}]
                },
                {"event_id": 1}
            ).sort("match_date", -1).limit(n))
            for m in recent:
                keep_ids.add(m["event_id"])
        
        # 3. Delete matches in this league that are NOT in keep_ids
        res = db.matches.delete_many({
            "unique_tournament_id": tid,
            "event_id": {"$nin": list(keep_ids)}
        })
        return res.deleted_count
    except Exception:
        return 0

def purge_team_matches(team_id: int | str, uniq_tid: int | str):
    """Delete all match history for a specific team in a league."""
    db = _db()
    try:
        tid = int(uniq_tid)
        t_id = int(team_id)
        res = db.matches.delete_many({
            "unique_tournament_id": tid,
            "$or": [{"home_team_id": t_id}, {"away_team_id": t_id}]
        })
        # Clear average heatmap for this team
        db.team_last5match_average_heatmap.delete_many({
            "team_id": t_id,
            "unique_tournament_id": tid
        })
        return res.deleted_count
    except Exception:
        return 0
