"""
match_loader.py — Data layer for scraped match JSON files.

Reads files from match_output/ and exposes:
  - get_all_matches         — list of match summaries
  - get_match               — single match by event_id
  - get_player_match_history — all scraped appearances for a player_id
  - compute_per90           — aggregate raw stats → per-90 values + derived %s

Stats catalogued in MATCH_STATS_META include ~21 metrics that only exist in
per-match data (xA, tackles_total/won, ball_recoveries, fouls, etc.) and are
absent from the overall season JSON; these are surfaced as "match-only" insights
on the player profile page.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


# ── Stat metadata ─────────────────────────────────────────────────────────────

# (key, label, per90_eligible, category, match_only)
# match_only=True means this stat is NOT in the seasonal overall JSON
MATCH_STATS_META: list[dict[str, Any]] = [
    # Core
    {"key": "minutes_played",      "label": "Minutes Played",       "per90": False, "category": "core",       "match_only": False},
    {"key": "rating",              "label": "Rating",               "per90": False, "category": "core",       "match_only": False},
    {"key": "goals",               "label": "Goals",                "per90": True,  "category": "core",       "match_only": False},
    {"key": "assists",             "label": "Assists",              "per90": True,  "category": "core",       "match_only": False},
    # Shooting
    {"key": "xG",                  "label": "xG",                   "per90": True,  "category": "shooting",   "match_only": False},
    {"key": "xA",                  "label": "xA",                   "per90": True,  "category": "shooting",   "match_only": True},
    {"key": "shots_total",         "label": "Shots Total",          "per90": True,  "category": "shooting",   "match_only": False},
    {"key": "shots_on_target",     "label": "Shots on Target",      "per90": True,  "category": "shooting",   "match_only": False},
    {"key": "shots_off_target",    "label": "Shots Off Target",     "per90": True,  "category": "shooting",   "match_only": True},
    {"key": "shots_blocked",       "label": "Shots Blocked",        "per90": True,  "category": "shooting",   "match_only": True},
    # Passing
    {"key": "key_passes",          "label": "Key Passes",           "per90": True,  "category": "passing",    "match_only": False},
    {"key": "passes_total",        "label": "Passes Total",         "per90": True,  "category": "passing",    "match_only": False},
    {"key": "passes_accurate",     "label": "Passes Accurate",      "per90": True,  "category": "passing",    "match_only": False},
    {"key": "long_balls_total",    "label": "Long Balls Total",     "per90": True,  "category": "passing",    "match_only": True},
    {"key": "long_balls_accurate", "label": "Long Balls Accurate",  "per90": True,  "category": "passing",    "match_only": True},
    {"key": "crosses_total",       "label": "Crosses Total",        "per90": True,  "category": "passing",    "match_only": True},
    {"key": "crosses_accurate",    "label": "Crosses Accurate",     "per90": True,  "category": "passing",    "match_only": True},
    # Touches & Possession
    {"key": "touches",             "label": "Touches",              "per90": True,  "category": "possession", "match_only": True},
    {"key": "dribbles_attempted",  "label": "Dribbles Att.",        "per90": True,  "category": "possession", "match_only": True},
    {"key": "dribbles_won",        "label": "Dribbles Won",         "per90": True,  "category": "possession", "match_only": False},
    {"key": "dispossessed",        "label": "Dispossessed",         "per90": True,  "category": "possession", "match_only": True},
    {"key": "possession_lost",     "label": "Possession Lost",      "per90": True,  "category": "possession", "match_only": True},
    # Duels
    {"key": "duels_won",           "label": "Duels Won",            "per90": True,  "category": "duels",      "match_only": False},
    {"key": "duels_lost",          "label": "Duels Lost",           "per90": True,  "category": "duels",      "match_only": True},
    {"key": "aerial_won",          "label": "Aerial Won",           "per90": True,  "category": "duels",      "match_only": False},
    {"key": "aerial_lost",         "label": "Aerial Lost",          "per90": True,  "category": "duels",      "match_only": True},
    # Defence
    {"key": "tackles_total",       "label": "Tackles Total",        "per90": True,  "category": "defence",    "match_only": True},
    {"key": "tackles_won",         "label": "Tackles Won",          "per90": True,  "category": "defence",    "match_only": True},
    {"key": "interceptions",       "label": "Interceptions",        "per90": True,  "category": "defence",    "match_only": False},
    {"key": "clearances",          "label": "Clearances",           "per90": True,  "category": "defence",    "match_only": False},
    {"key": "blocks",              "label": "Blocks",               "per90": True,  "category": "defence",    "match_only": True},
    {"key": "ball_recoveries",     "label": "Ball Recoveries",      "per90": True,  "category": "defence",    "match_only": True},
    # Discipline / misc
    {"key": "fouls_committed",     "label": "Fouls Committed",      "per90": True,  "category": "discipline", "match_only": True},
    {"key": "fouls_suffered",      "label": "Fouls Suffered",       "per90": True,  "category": "discipline", "match_only": True},
    {"key": "offsides",            "label": "Offsides",             "per90": True,  "category": "discipline", "match_only": True},
    {"key": "errors_led_to_shot",  "label": "Errors Led to Shot",   "per90": True,  "category": "discipline", "match_only": True},
    {"key": "own_goals",           "label": "Own Goals",            "per90": True,  "category": "discipline", "match_only": True},
    # GK
    {"key": "saves",               "label": "Saves",                "per90": True,  "category": "goalkeeper", "match_only": True},
]

_META_BY_KEY: dict[str, dict] = {m["key"]: m for m in MATCH_STATS_META}

# Numeric stat keys (per90_eligible ones — used for summing)
_NUMERIC_KEYS = [m["key"] for m in MATCH_STATS_META if m["per90"] and m["key"] != "rating"]
_RATING_KEY = "rating"

# Derived percentage stats computed after summing
_DERIVED_STATS = [
    {"key": "pass_accuracy_pct",      "label": "Pass Acc. %",       "num": "passes_accurate",     "den": "passes_total",        "match_only": False},
    {"key": "long_ball_accuracy_pct", "label": "Long Ball Acc. %",  "num": "long_balls_accurate", "den": "long_balls_total",    "match_only": True},
    {"key": "cross_accuracy_pct",     "label": "Cross Acc. %",      "num": "crosses_accurate",    "den": "crosses_total",       "match_only": True},
    {"key": "dribble_success_pct",    "label": "Dribble Succ. %",   "num": "dribbles_won",        "den": "dribbles_attempted",  "match_only": False},
    {"key": "aerial_win_pct",         "label": "Aerial Win %",      "num": "aerial_won",          "den_sum": ["aerial_won", "aerial_lost"], "match_only": False},
    {"key": "duel_win_pct",           "label": "Duel Win %",        "num": "duels_won",           "den_sum": ["duels_won", "duels_lost"],   "match_only": True},
    {"key": "tackle_success_pct",     "label": "Tackle Succ. %",    "num": "tackles_won",         "den": "tackles_total",       "match_only": True},
]


# ── Internal helpers ──────────────────────────────────────────────────────────

def _load_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _match_output_dir(base_dir: str | None = None) -> str:
    """Return absolute path to match_output/ next to Coding/."""
    if base_dir:
        return base_dir
    return os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "match_output")
    )


def _is_match_file(path: Path) -> bool:
    """Return True if the JSON file looks like a bulk/single match output."""
    name = path.name.lower()
    # Formation files have 'formations' in name — exclude them
    return path.suffix == ".json" and "formation" not in name


# ── Public API ────────────────────────────────────────────────────────────────

def get_all_matches(match_output_dir: str | None = None) -> list[dict]:
    """
    Scan match_output/ and return a list of match summaries, newest first.

    Each summary dict:
        event_id, date, competition, season, home_team, away_team,
        home_score, away_score, winner, home_formation, away_formation,
        scraped_at, filepath, is_bulk, match_count
    """
    base = Path(_match_output_dir(match_output_dir))
    if not base.exists():
        return []

    summaries: list[dict] = []
    for f in sorted(base.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        if not _is_match_file(f):
            continue
        data = _load_json(str(f))
        if not data:
            continue

        # Bulk file: top-level has "matches" list
        if "matches" in data and isinstance(data["matches"], list):
            for m in data["matches"]:
                match_meta = m.get("match", {})
                summaries.append({
                    "event_id":       m.get("event_id"),
                    "date":           match_meta.get("date", ""),
                    "competition":    match_meta.get("competition", ""),
                    "season":         match_meta.get("season", ""),
                    "home_team":      match_meta.get("home_team", ""),
                    "away_team":      match_meta.get("away_team", ""),
                    "home_score":     match_meta.get("home_score", ""),
                    "away_score":     match_meta.get("away_score", ""),
                    "winner":         match_meta.get("winner", ""),
                    "home_formation": match_meta.get("home_formation", ""),
                    "away_formation": match_meta.get("away_formation", ""),
                    "scraped_at":     m.get("scraped_at", data.get("scraped_at", "")),
                    "filepath":       str(f),
                    "source_file":    f.name,
                    "is_bulk":        True,
                })
        else:
            # Single match file
            match_meta = data.get("match", {})
            summaries.append({
                "event_id":       data.get("event_id"),
                "date":           match_meta.get("date", ""),
                "competition":    match_meta.get("competition", ""),
                "season":         match_meta.get("season", ""),
                "home_team":      match_meta.get("home_team", ""),
                "away_team":      match_meta.get("away_team", ""),
                "home_score":     match_meta.get("home_score", ""),
                "away_score":     match_meta.get("away_score", ""),
                "winner":         match_meta.get("winner", ""),
                "home_formation": match_meta.get("home_formation", ""),
                "away_formation": match_meta.get("away_formation", ""),
                "scraped_at":     data.get("scraped_at", ""),
                "filepath":       str(f),
                "source_file":    f.name,
                "is_bulk":        False,
            })

    # De-duplicate by event_id, keeping first occurrence (newest file)
    seen: set = set()
    unique: list[dict] = []
    for s in summaries:
        eid = s.get("event_id")
        if eid and eid not in seen:
            seen.add(eid)
            unique.append(s)
        elif not eid:
            unique.append(s)

    return unique


def get_match(event_id: int | str, match_output_dir: str | None = None) -> dict | None:
    """
    Find and return the full match dict for *event_id*.
    Searches both single-match files and bulk file containers.
    Returns None if not found.
    """
    event_id = int(event_id)
    base = Path(_match_output_dir(match_output_dir))
    if not base.exists():
        return None

    for f in sorted(base.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        if not _is_match_file(f):
            continue
        data = _load_json(str(f))
        if not data:
            continue

        if "matches" in data and isinstance(data["matches"], list):
            for m in data["matches"]:
                if m.get("event_id") == event_id:
                    return m
        else:
            if data.get("event_id") == event_id:
                return data

    return None


def get_player_match_history(
    player_id: int | str,
    match_output_dir: str | None = None,
) -> list[dict]:
    """
    Return a list of per-match records for *player_id*, oldest first.

    Each record:
        match_meta  — {date, competition, home_team, away_team, home_score,
                       away_score, winner, home_formation, away_formation}
        side        — "home" | "away"
        player_info — {name, position, jersey_number, substitute, captain}
        stats       — raw stats dict from player_statistics
    """
    player_id = int(player_id)
    base = Path(_match_output_dir(match_output_dir))
    if not base.exists():
        return []

    appearances: list[dict] = []

    def _scan_match(m: dict) -> None:
        for side in ("home", "away"):
            for p in m.get("player_statistics", {}).get(side, []):
                if p.get("player_id") == player_id:
                    appearances.append({
                        "event_id":   m.get("event_id"),
                        "match_meta": m.get("match", {}),
                        "side":       side,
                        "player_info": {
                            "name":           p.get("name", ""),
                            "short_name":     p.get("short_name", ""),
                            "position":       p.get("position", ""),
                            "jersey_number":  p.get("jersey_number", ""),
                            "substitute":     p.get("substitute", False),
                            "captain":        p.get("captain", False),
                        },
                        "stats": p.get("stats", {}),
                    })

    for f in sorted(base.glob("*.json"), key=lambda p: p.stat().st_mtime):
        if not _is_match_file(f):
            continue
        data = _load_json(str(f))
        if not data:
            continue
        if "matches" in data and isinstance(data["matches"], list):
            for m in data["matches"]:
                _scan_match(m)
        else:
            _scan_match(data)

    # De-duplicate by event_id (same match can appear in multiple bulk files)
    seen: set = set()
    unique: list[dict] = []
    for a in appearances:
        eid = a.get("event_id")
        if eid and eid not in seen:
            seen.add(eid)
            unique.append(a)
        elif not eid:
            unique.append(a)

    # Sort by match date
    unique.sort(key=lambda a: a["match_meta"].get("date", ""))
    return unique


def compute_per90(appearances: list[dict]) -> dict:
    """
    Aggregate raw per-match stats into totals, per-90 values, and derived %s.

    Returns:
        total_minutes    — int
        appearances      — int
        avg_rating       — float | None
        totals           — {key: float}   raw summed values
        per90            — {key: float}   per-90 normalised
        derived          — {key: float}   accuracy % stats
        match_only_keys  — set of stat keys that are match-only
    """
    if not appearances:
        return {
            "total_minutes": 0,
            "appearances": 0,
            "avg_rating": None,
            "totals": {},
            "per90": {},
            "derived": {},
            "match_only_keys": set(),
        }

    totals: dict[str, float] = {k: 0.0 for k in _NUMERIC_KEYS}
    rating_sum = 0.0
    rating_count = 0
    total_minutes = 0
    app_count = 0

    for a in appearances:
        s = a.get("stats", {})
        mins = float(s.get("minutes_played", 0) or 0)
        total_minutes += int(mins)
        app_count += 1

        r = s.get(_RATING_KEY)
        if r is not None:
            try:
                rating_sum += float(r)
                rating_count += 1
            except (TypeError, ValueError):
                pass

        for key in _NUMERIC_KEYS:
            val = s.get(key)
            if val is not None:
                try:
                    totals[key] += float(val)
                except (TypeError, ValueError):
                    pass

    # Per-90 normalisation
    per90: dict[str, float] = {}
    if total_minutes > 0:
        factor = 90.0 / total_minutes
        for key in _NUMERIC_KEYS:
            per90[key] = round(totals[key] * factor, 2)

    # Derived accuracy percentages (use totals, not per-90)
    derived: dict[str, float] = {}
    for d in _DERIVED_STATS:
        num_val = totals.get(d["num"], 0.0)
        if "den" in d:
            den_val = totals.get(d["den"], 0.0)
        else:
            den_val = sum(totals.get(k, 0.0) for k in d["den_sum"])
        if den_val > 0:
            derived[d["key"]] = round(num_val / den_val * 100, 1)

    match_only_keys = {m["key"] for m in MATCH_STATS_META if m["match_only"]}
    match_only_keys.update({d["key"] for d in _DERIVED_STATS if d.get("match_only")})

    return {
        "total_minutes":   total_minutes,
        "appearances":     app_count,
        "avg_rating":      round(rating_sum / rating_count, 2) if rating_count else None,
        "totals":          {k: round(v, 2) for k, v in totals.items()},
        "per90":           per90,
        "derived":         derived,
        "match_only_keys": match_only_keys,
    }
