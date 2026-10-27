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
        from db_tls import tls_kwargs
        _client = MongoClient(
            _MONGO_URI,
            serverSelectionTimeoutMS=int(os.environ.get("MONGO_TIMEOUT_MS", "3000")),
            **tls_kwargs(_MONGO_URI),
        )
    return _client[_DB_NAME]

# World Cup docs land in Mongo with _country == "World" the same way they land
# in output/World/World_Championship/ on disk (scraper_cli.py writes both
# when Mongo is reachable). Exclude them here too — see _EXCLUDED_COUNTRIES
# in data_loader.py and Coding/output_worldcup/README.md for the full story.
_EXCLUDED_COUNTRIES = {"World"}
_NOT_WORLDCUP = {"_country": {"$nin": list(_EXCLUDED_COUNTRIES)}}

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
        "country_alpha2":    (profile.get("country") or {}).get("alpha2", ""),
        "in_current_squad":  doc.get("in_current_squad", True),
        "departed":          doc.get("departed", False),
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

    total_players      = db.players.count_documents(_NOT_WORLDCUP)
    total_clubs        = db.clubs.count_documents(_NOT_WORLDCUP)
    total_competitions = db.standings.count_documents(_NOT_WORLDCUP)

    pipeline = [
        {"$match": _NOT_WORLDCUP},
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
    seen   = set()
    # Use standings docs — one per league per season, so deduplicate
    for doc in db.standings.find(_NOT_WORLDCUP, {"_country": 1, "_competition": 1}).sort(
            [("_country", 1), ("_competition", 1)]):
        country     = doc.get("_country", "")
        competition = doc.get("_competition", "")
        if (country, competition) in seen:
            continue
        seen.add((country, competition))
        # Count the latest season only — a league holding several seasons
        # otherwise reports every season's clubs added together.
        season_id  = _resolve_season(output_dir, country, competition, None)
        club_count = db.clubs.count_documents({
            "_country": country, "_competition": competition,
            **({"season_id": season_id} if season_id else {})})
        result.append({
            "country":        country,
            "competition":    competition,
            "club_count":     club_count,
            "standings_path": None,   # not needed; kept for template compat
        })
    return result

# ── Seasons ───────────────────────────────────────────────────────────────────

def _season_year(db, country, competition, season_id):
    """Season label (e.g. "25/26") from the season_info doc — lets docs that
    predate the season_name backfill show a real name without going online."""
    doc = db.season_info.find_one(
        {"_country": country, "_competition": competition, "season_id": season_id},
        {"info.season.year": 1}) or {}
    return (((doc.get("info") or {}).get("season") or {}).get("year")) or None


def _matches_played(doc) -> int:
    return sum(int(r.get("matches") or 0)
               for g in (doc.get("standings") or []) for r in (g.get("rows") or []))


def _pick_standings_doc(docs):
    """Pick one standings doc when a season holds several (older scrapes named
    files by tournament.id instead of uniqueTournament.id, leaving a stale
    partial table next to the real one). Prefer the doc whose tournament_id is
    the uniqueTournament id, then the most complete table, then the newest."""
    if not docs:
        return {}

    def _uniq(doc):
        for g in doc.get("standings") or []:
            ut = (g.get("tournament") or {}).get("uniqueTournament") or {}
            if ut.get("id"):
                return str(ut["id"])
        return ""

    def _tid(doc):
        tid = str(doc.get("tournament_id") or "")
        if not tid:
            m = re.search(r"_(\d+)_Season_\d+$", Path(doc.get("_file", "")).stem)
            tid = m.group(1) if m else ""
        return tid

    def _key(doc):
        uniq = _uniq(doc)
        return (bool(uniq) and _tid(doc) == uniq, _matches_played(doc), str(doc.get("_id", "")))

    return max(docs, key=_key)


def get_competition_seasons(output_dir, country, competition):
    """Return [{season_id, season_name, season_order}] for a league, newest-first.

    Ordering: `season_order` ascending when present (0 = current season). For docs
    predating the season-name backfill (no season_order), fall back to numeric
    season_id descending so the highest id (latest) sorts first.
    """
    db = _db()
    # Only seasons with clubs — a scrape writes standings first, so a new season
    # that is half-scraped (or abandoned) would otherwise become the default
    # and render an empty competition page.
    with_clubs = {str(s) for s in db.clubs.distinct(
        "season_id", {"_country": country, "_competition": competition})}
    seasons: dict[str, dict] = {}
    for doc in db.standings.find(
            {"_country": country, "_competition": competition},
            {"season_id": 1, "season_name": 1, "season_order": 1}):
        sid = str(doc.get("season_id") or "")
        if not sid or sid in seasons or sid not in with_clubs:
            continue
        seasons[sid] = {
            "season_id":    sid,
            "season_name":  doc.get("season_name") or _season_year(db, country, competition, sid) or sid,
            "season_order": doc.get("season_order"),
        }
    result = list(seasons.values())

    def _key(s):
        o   = s.get("season_order")
        sid = int(s["season_id"]) if s["season_id"].isdigit() else 0
        return (o if o is not None else 9999, -sid)

    result.sort(key=_key)
    return result


def get_competition_club_stats(output_dir, country, competition, season_id=None):
    """Return [{club_slug, club, matches, stats}] for every club in a competition
    season — the comparison pool behind the club percentile panel.

    One query with a projection rather than get_club() per club, since the pool
    is rebuilt on every club page load.
    """
    db = _db()
    season_id = _resolve_season(output_dir, country, competition, season_id)
    _sf = {"season_id": season_id} if season_id else {}

    rows = []
    for doc in db.clubs.find(
            {"_country": country, "_competition": competition, **_sf},
            {"_club": 1, "team_name": 1, "season_statistics.statistics": 1}):
        raw = ((doc.get("season_statistics") or {}).get("statistics") or {})
        if not raw:
            continue
        stats = {
            k: v for k, v in raw.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v is not None
        }
        slug = doc.get("_club") or ""
        rows.append({
            "club_slug": slug,
            "club":      doc.get("team_name") or slug.replace("_", " "),
            "matches":   stats.get("matches") or 0,
            "stats":     stats,
        })
    return rows


def get_player_competitions(player_id):
    """Return [{country, competition, club, season_id, season_name, path}] for
    every competition/season this player_id appears in, newest-first.

    The same SofaScore player_id shows up under different uniqueTournament ids
    (e.g. Thom Haye in Indonesia Super League 1015 and the AFF Championship 602),
    stored as separate docs by the (player_id, tournament_id, season_id) key.
    This links those records so the player page can switch between them.
    """
    db = _db()
    rows = []
    for doc in db.players.find(
            {"player_id": str(player_id), **_NOT_WORLDCUP},
            {"_country": 1, "_competition": 1, "_club": 1, "_file": 1,
             "tournament_id": 1, "season_id": 1, "season_name": 1,
             "season_order": 1, "competition_name": 1, "team": 1,
             "player_name": 1}):
        country     = doc.get("_country") or ""
        competition = doc.get("_competition") or ""
        club        = doc.get("_club") or ""
        fname       = doc.get("_file") or ""
        if not (country and competition and club and fname):
            continue
        sid = str(doc.get("season_id") or "")
        rows.append({
            "country":       country,
            "competition":   competition,
            "club":          club,
            "team":          doc.get("team") or club.replace("_", " "),
            "player_name":   doc.get("player_name") or "",
            "tournament_id": str(doc.get("tournament_id") or ""),
            "season_id":     sid,
            "season_name":   doc.get("season_name") or sid,
            "season_order":  doc.get("season_order"),
            "path":          f"{country}/{competition}/{club}/{fname[:-5] if fname.endswith('.json') else fname}",
        })

    # Same ordering as get_competition_seasons: season_order asc (0 = current),
    # falling back to numeric season_id desc for docs predating the backfill.
    def _key(r):
        o   = r.get("season_order")
        sid = int(r["season_id"]) if r["season_id"].isdigit() else 0
        return (o if o is not None else 9999, -sid, r["competition"])

    rows.sort(key=_key)
    return rows


def _resolve_season(output_dir, country, competition, season_id):
    """Return an explicit season_id — the given one, or the latest if None."""
    if season_id:
        return str(season_id)
    seasons = get_competition_seasons(output_dir, country, competition)
    return seasons[0]["season_id"] if seasons else None


# ── Competition ───────────────────────────────────────────────────────────────

def get_competition(output_dir, country, competition, season_id=None):
    db = _db()

    season_id = _resolve_season(output_dir, country, competition, season_id)
    seasons   = get_competition_seasons(output_dir, country, competition)
    _season_filter = {"season_id": season_id} if season_id else {}

    # Standings
    standings_doc = _pick_standings_doc(list(db.standings.find(
        {"_country": country, "_competition": competition, **_season_filter})))
    tid       = str(standings_doc.get("tournament_id") or "")
    season_id = str(standings_doc.get("season_id") or season_id or "")
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
            {"_country": country, "_competition": competition, **_season_filter},
            sort=[("team_name", 1)]):
        profile      = club_doc.get("profile", {}).get("team", {})
        season_stats = club_doc.get("season_statistics", {}).get("statistics", {})
        _team_filter = {
            "_country": country, "_competition": competition,
            "team_id": str(club_doc.get("team_id", "")), **_season_filter,
        }
        # Current squad only — a club card should say who is there now, not
        # everyone who turned out this season. The foreign count below already
        # excluded departed players, so without this the two disagreed.
        player_count        = db.players.count_documents(
            {**_team_filter, "departed": {"$ne": True}})
        season_player_count = db.players.count_documents(_team_filter)

        # Foreign count for the club card: prefer the scraper's precomputed
        # details.foreign_players; else count current-squad players whose country
        # differs from the club's (older docs lack the details block).
        foreign_players = (club_doc.get("details") or {}).get("foreign_players")
        if foreign_players is None:
            club_a2 = ((profile.get("country") or {}).get("alpha2") or "").upper()
            if club_a2:
                foreign_players = db.players.count_documents({
                    **_team_filter,
                    "departed": {"$ne": True},
                    "profile.player.country.alpha2": {"$ne": club_a2, "$exists": True},
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
            "slug":            club_doc.get("_club", ""),
            "name":            club_doc.get("team_name", ""),
            "team_id":         str(club_doc.get("team_id", "")),
            "player_count":        player_count,
            "season_player_count": season_player_count,
            "foreign_players":     foreign_players,
            "stats":           season_stats,
            "profile":         profile,
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
    ct_doc   = db.cup_trees.find_one(
        {"_country": country, "_competition": competition, **_season_filter}) or {}
    cup_trees = ct_doc.get("cupTrees", [])

    # Season info
    si_doc     = db.season_info.find_one(
        {"_country": country, "_competition": competition, **_season_filter}) or {}
    season_info = si_doc.get("info", {})

    # Derive competition type from stored field or infer from shape
    comp_type = standings_doc.get("competition_type") or (
        "cup_knockout" if (not standings_doc.get("standings") and cup_trees) else
        "cup_groups"   if (len(standings_doc.get("standings", [])) > 1 or
                           (len(standings_doc.get("standings", [])) == 1 and cup_trees)) else
        "league"
    )

    season_name = standings_doc.get("season_name") or (
        next((s["season_name"] for s in seasons if s["season_id"] == season_id), season_id))

    available_groups, group_filter = _group_scope(uniq_tid, season_id,
                                                  standings_doc, standings_phases)

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
        "season_name":        season_name,
        "seasons":            seasons,
        "competition_colors": competition_colors or {"primary": "#6E6E6E", "secondary": "#D9D9D9"},
        "competition_type":   comp_type,
        "available_groups":   available_groups,
        "group_filter":       group_filter,
    }


def _group_scope(uniq_tid, season_id, standings_doc, standings_phases):
    """(available_groups, group_filter) for the competition page's scope picker.

    available_groups is the FULL group list a scoped competition was trimmed
    from — cached on the saved scope, then on the standings doc, and finally
    falling back to whatever groups are actually present. group_filter is the
    active selection, or None when the competition is unscoped.
    """
    saved = {}
    if uniq_tid and season_id:
        try:
            import scrape_filter
            saved = scrape_filter.load_filter(uniq_tid, season_id) or {}
        except Exception:
            saved = {}

    groups = (saved.get("available_groups")
              or standings_doc.get("available_groups")
              or [{"phase_name":    p["phase_name"],
                   "tournament_id": p.get("tournament_id"),
                   "team_count":    len(p.get("rows") or [])}
                  for p in standings_phases])

    group_filter = saved.get("group_names") or standings_doc.get("group_filter")
    return groups, (group_filter or None)

# ── Club ──────────────────────────────────────────────────────────────────────

def get_club(output_dir, country, competition, club, season_id=None):
    season_id = _resolve_season(output_dir, country, competition, season_id)
    _sf = {"season_id": season_id} if season_id else {}
    doc = _db().clubs.find_one(
        {"_country": country, "_competition": competition, "_club": club, **_sf})
    if doc:
        doc.pop("_id", None)
        return doc
    return {}

def get_club_players(output_dir, country, competition, club, season_id=None):
    db = _db()
    season_id = _resolve_season(output_dir, country, competition, season_id)
    _sf = {"season_id": season_id} if season_id else {}

    # The club's own country (alpha2) — the yardstick for who counts as foreign.
    club_doc = db.clubs.find_one(
        {"_country": country, "_competition": competition, "_club": club, **_sf},
        {"profile.team.country.alpha2": 1})
    club_a2 = ((((club_doc or {}).get("profile") or {}).get("team") or {})
               .get("country") or {}).get("alpha2", "")
    club_a2 = (club_a2 or "").upper()

    players = []
    for doc in db.players.find(
            {"_country": country, "_competition": competition, "_club": club, **_sf},
            sort=[("player_name", 1)]):
        p = _parse_player_doc(doc)
        p_a2 = (p.get("country_alpha2") or "").upper()
        p["is_foreign"] = bool(club_a2 and p_a2 and p_a2 != club_a2)
        players.append(p)
    return players

# ── Player ────────────────────────────────────────────────────────────────────

def get_player(output_dir, country, competition, club, player_file, season_id=None):
    """Return the full player document from MongoDB."""
    fname = player_file if player_file.endswith(".json") else player_file + ".json"
    # Player files are season-specific (…_Season_<sid>.json), so without an
    # explicit season the filename's own season wins — resolving to the latest
    # season would miss every older-season file.
    if not season_id:
        m = re.search(r"_Season_(\d+)\.json$", fname)
        season_id = m.group(1) if m else _resolve_season(output_dir, country, competition, None)
    _sf = {"season_id": str(season_id)} if season_id else {}
    doc = _db().players.find_one({"_country": country, "_competition": competition,
                                   "_club": club, "_file": fname, **_sf})
    if doc:
        doc.pop("_id", None)
        return doc
    return {}

def get_heatmap_path(output_dir, country, competition, club, player_file):
    """Return the MongoDB-served URL for the player's heatmap, or None.

    orbis_scout is MongoDB-only: heatmaps come from the `heatmaps` collection
    and are served by the /heatmap_db route. There is NO disk (output/) fallback
    — `output_dir` is accepted only for signature compatibility with callers.
    """
    stem = Path(player_file).stem
    heatmap_stem = f"{stem}_heatmap"
    doc = _db().heatmaps.find_one(
        {"_country": country, "_competition": competition,
         "_club": club, "_stem": heatmap_stem},
        {"_id": 1}
    )
    if doc:
        return f"/heatmap_db/{country}/{competition}/{club}/{heatmap_stem}"
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
    mongo_filter: dict = dict(_NOT_WORLDCUP)

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

# ── Free agents ───────────────────────────────────────────────────────────────

# SofaScore models "no club" as a real team rather than a null destination:
#   {"id": 241802, "name": "No team", "slug": "no-team", "disabled": true}
# A departure whose transferTo is that team is a free agent.  Retirements arrive
# as {"toTeamName": "Retired"} with NO transferTo object at all, so keying on
# transferTo.id drops them without needing a special case.
_NO_TEAM_ID = 241802


def _fa_sort_paginate(rows, filters):
    """Filter / sort / paginate free-agent rows. Kept identical to data_loader."""
    def _f(val, key, cast=str):
        v = filters.get(key, "")
        if not v: return True
        try: return cast(val or 0) >= cast(v)
        except Exception: return True

    q_nrm = _nrm(filters.get("q", "")) if filters.get("q") else ""

    filtered = []
    for p in rows:
        s = p.get("stats", {}) or {}
        if q_nrm and q_nrm not in _nrm(p.get("name", "")):
            continue
        if filters.get("country") and filters["country"].lower() != str(p.get("country", "")).lower():
            continue
        if filters.get("competition") and _nrm(filters["competition"]) not in _nrm(p.get("competition", "")):
            continue
        if filters.get("club") and _nrm(filters["club"]) not in _nrm(p.get("team", "")):
            continue
        if filters.get("nationality") and _nrm(filters["nationality"]) not in _nrm(p.get("nationality", "")):
            continue
        # Broad position category (G/D/M/F)
        cat_filter = filters.get("position", "").upper()
        if cat_filter:
            broad = profile_category(p.get("positions", []), p.get("position", ""))
            if not broad.upper().startswith(cat_filter):
                continue
        # Specific position codes (e.g. "DC,AM")
        sp_list = [x.strip() for x in filters.get("specific_pos", "").upper().split(",") if x.strip()]
        if sp_list:
            player_pos = [str(x).upper() for x in p.get("positions", [])]
            if not any(sp in player_pos for sp in sp_list):
                continue
        if not _f(p.get("age", ""),         "age_min",       int):   continue
        if not _f(s.get("appearances", 0),  "apps_min",      int):   continue
        if not _f(s.get("rating", 0),       "rating_min",    float): continue
        if not _f(s.get("goals", 0),        "goals_min",     int):   continue
        if not _f(s.get("assists", 0),      "assists_min",   int):   continue
        age_max = filters.get("age_max", "")
        if age_max:
            try:
                if int(p.get("age") or 999) > int(age_max): continue
            except Exception:
                pass
        filtered.append(p)

    sort_by  = filters.get("sort_by", "departure_date")
    sort_dir = filters.get("sort_dir", "desc")
    sort_map = {
        "departure_date": lambda p: int(p.get("departure_ts") or 0),
        "rating":         lambda p: float((p.get("stats") or {}).get("rating", 0) or 0),
        "goals":          lambda p: int((p.get("stats") or {}).get("goals", 0) or 0),
        "assists":        lambda p: int((p.get("stats") or {}).get("assists", 0) or 0),
        "appearances":    lambda p: int((p.get("stats") or {}).get("appearances", 0) or 0),
        "age":            lambda p: int(p.get("age") or 0),
        "name":           lambda p: p.get("name", "").lower(),
    }
    key_fn = sort_map.get(sort_by, sort_map["departure_date"])
    filtered.sort(key=key_fn, reverse=(sort_dir == "desc"))

    page     = int(filters.get("page", 1))
    per_page = int(filters.get("per_page", 50))
    total    = len(filtered)
    start    = (page - 1) * per_page
    return {
        "players":     filtered[start : start + per_page],
        "total":       total,
        "page":        page,
        "per_page":    per_page,
        "total_pages": (total + per_page - 1) // per_page,
    }


def search_free_agents(output_dir, filters):
    """Players who left a club and have no new club ("No team" on SofaScore).

    Two sources, unioned and deduped by player_id:
      * players.transfer_out.team_id == 241802  → fully enriched rows
      * clubs.transfers.transfersOut[].transferTo.id == 241802 → bare rows for
        players we hold no player document for (never appeared in a scraped
        season for that club).

    A player can show up under several club/season documents; the record with
    the newest departure timestamp wins — the same rule scraper_cli.py's
    _index_transfers() uses.
    """
    db = _db()
    by_id: dict = {}

    # ── Enriched: player documents carry the flattened transfer_out summary ────
    for doc in db.players.find({**_NOT_WORLDCUP, "transfer_out.team_id": _NO_TEAM_ID}):
        t   = doc.get("transfer_out") or {}
        row = _parse_player_doc(doc)
        row["departure_ts"]   = int(t.get("timestamp") or 0)
        row["departure_date"] = t.get("date") or ""
        row["from_club"]      = row.get("team", "")
        row["fee"]            = t.get("fee") or ""
        row["enriched"]       = True
        prev = by_id.get(row["player_id"])
        if prev is None or row["departure_ts"] > prev["departure_ts"]:
            by_id[row["player_id"]] = row

    # ── Bare: raw club transfer records for players with no player document ───
    for club in db.clubs.find(
        {**_NOT_WORLDCUP, "transfers.transfersOut.transferTo.id": _NO_TEAM_ID},
        {"transfers.transfersOut": 1, "_country": 1, "_competition": 1,
         "_club": 1, "team_name": 1, "team_id": 1},
    ):
        for t in (club.get("transfers") or {}).get("transfersOut") or []:
            if (t.get("transferTo") or {}).get("id") != _NO_TEAM_ID:
                continue
            player = t.get("player") or {}
            pid    = player.get("id")
            if pid is None:
                continue
            pid = str(pid)
            existing = by_id.get(pid)
            if existing is not None and existing.get("enriched"):
                continue          # the player document is strictly better
            ts = int(t.get("transferDateTimestamp") or 0)
            if existing is not None and ts <= existing["departure_ts"]:
                continue
            club_name = club.get("_club") or club.get("team_name", "")
            by_id[pid] = {
                "name":              player.get("name", ""),
                "player_id":         pid,
                "team":              club.get("team_name") or club_name,
                "team_id":           club.get("team_id", ""),
                "competition":       club.get("_competition", ""),
                "country":           club.get("_country", ""),
                "club_slug":         club_name,
                "position":          player.get("position", ""),
                "positions":         [],
                "nationality":       "",
                "country_alpha2":    "",
                "age":               "",
                "height":            "",
                "preferred_foot":    "",
                "shirt_number":      "",
                "stats":             {},
                "file":              "",
                "path":              "",
                "contract_until_ts": 0,
                "contract_until":    "",
                "contract_badge":    "",
                "departure_ts":      ts,
                "departure_date":    _fa_fmt_date(ts),
                "from_club":         club.get("team_name") or club_name,
                "fee":               t.get("transferFeeDescription", "") or "",
                "enriched":          False,
            }

    return _fa_sort_paginate(list(by_id.values()), filters)


def _fa_fmt_date(ts) -> str:
    """Epoch seconds → 'YYYY-MM-DD', matching scraper_cli._fmt_epoch_date()."""
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return ""


# ── All-players flat (compare autocomplete) ───────────────────────────────────

_PLAYER_CACHE = None

def get_all_players_flat(output_dir, query="", limit=20):
    global _PLAYER_CACHE
    if _PLAYER_CACHE is None:
        db = _db()
        _PLAYER_CACHE = []
        # Fetch all players once to cache names and metadata
        for doc in db.players.find(_NOT_WORLDCUP, {
                "player_name": 1, "team": 1,
                "_competition": 1, "_country": 1, "_club": 1, "_file": 1,
                "season_id": 1, "season_name": 1,
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
                # One player_id can appear in several competitions/seasons; the
                # season label is what tells two otherwise-identical rows apart.
                "season_name": doc.get("season_name") or str(doc.get("season_id") or ""),
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
    mfilter: dict = dict(_NOT_WORLDCUP)
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

def _team_scope_clause(team_ids):
    """Mongo clause matching only matches involving `team_ids`, or {} for none.

    Lets a group-scoped competition (e.g. the Big East inside the NCAA) purge or
    optimise its own matches without touching another conference's.
    """
    if not team_ids:
        return {}
    ids = []
    for t in team_ids:
        try:
            ids.append(int(t))
        except (TypeError, ValueError):
            continue
    if not ids:
        return {}
    return {"$or": [{"home_team_id": {"$in": ids}},
                    {"away_team_id": {"$in": ids}}]}


def purge_league_matches(uniq_tid: int | str, team_ids=None):
    """Delete all match documents for a specific unique_tournament_id.

    team_ids restricts the purge to a group scope, so purging the Big East
    leaves any other NCAA conference already scraped intact.
    """
    db = _db()
    try:
        tid   = int(uniq_tid)
        scope = _team_scope_clause(team_ids)
        res = db.matches.delete_many({"unique_tournament_id": tid, **scope})
        # Also clear average heatmaps for this league
        hm_filter = {"unique_tournament_id": tid}
        if scope:
            hm_filter["team_id"] = {"$in": [int(t) for t in team_ids
                                            if str(t).lstrip('-').isdigit()]}
        db.team_last5match_average_heatmap.delete_many(hm_filter)
        return res.deleted_count
    except Exception:
        return 0

def optimize_league_storage(uniq_tid: int | str, n: int = 5, team_ids=None):
    """Keep only the N most recent matches for every team in a league.

    A match is kept if it's in the 'top N' for EITHER the home or away team.
    Matches not belonging to any team's 'top N' in this league are deleted.

    team_ids restricts both the teams considered and the matches eligible for
    deletion, so optimising a scoped competition can't discard another group's
    history.
    """
    db = _db()
    try:
        tid   = int(uniq_tid)
        scope = _team_scope_clause(team_ids)
        # 1. Find all teams that have matches in this league
        league_teams = set()
        for side in ["home_team_id", "away_team_id"]:
            league_teams.update(
                db.matches.distinct(side, {"unique_tournament_id": tid, **scope}))
        if team_ids:
            allowed = {int(t) for t in team_ids if str(t).lstrip('-').isdigit()}
            league_teams &= allowed

        # 2. For each team, get their top N match event_ids
        keep_ids = set()
        for t_id in league_teams:
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
            "event_id": {"$nin": list(keep_ids)},
            **scope,
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
