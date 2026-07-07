"""form_engine.py — Rolling form fingerprint scorer.

Computes style scores from each team's last N league matches,
providing form-based style fingerprints alongside season-level scores.

All queries target the 'matches' MongoDB collection populated by
match_stats_scraper.py. Requires 'team_id' and 'tournament_id' keys in
the clubs_raw dicts (available after updating api_playing_style_data).

Recommended indexes on the 'matches' collection (created by mongo_import.py):
    db.matches.create_index([("event_id", 1)], unique=True)
    db.matches.create_index([("home_team_id", 1), ("match_date", -1), ("tournament_id", 1)])
    db.matches.create_index([("away_team_id", 1), ("match_date", -1), ("tournament_id", 1)])
"""

from __future__ import annotations

import math
from statistics import mean, stdev
from typing import Any

from style_engine import METRIC_EXTRACTORS, STYLE_WEIGHTS, METRIC_KEYS

# Minimum non-None observations across the pool required to apply Z-score
# normalisation per metric. Below this floor we still Z-score but accept
# noisy estimates (avoids completely raw dot-product values).
_ZSCORE_MIN_OBS = 10

# Position code → passing zone group label
_POSITION_GROUPS: dict[str, str] = {
    "G": "backline_passes",
    "D": "backline_passes",
    "M": "mid_passes",
    "F": "forward_passes",
}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _flatten_statistics(statistics: dict) -> dict[str, dict[str, Any]]:
    """Flatten ALL-period statistics items into {key: {home: val, away: val}}.

    Args:
        statistics: statistics dict from a match document
                    (keyed by period, e.g. "ALL", "1ST", "2ND").

    Returns:
        Flat dict keyed by SofaScore stat item key.
    """
    flat: dict[str, dict[str, Any]] = {}
    for group in statistics.get("ALL", []):
        for item in group.get("items", []):
            key = item.get("key")
            if key:
                flat[key] = {
                    "home": item.get("homeValue"),
                    "away": item.get("awayValue"),
                }
    return flat


def _build_season_stats_from_match(match_doc: dict, team_side: str) -> dict:
    """Map a match document's statistics to season_statistics-compatible field names.

    Builds the same dict shape that METRIC_EXTRACTORS in style_engine.py
    expects, so the same extractors can be applied with matches=1.

    Args:
        match_doc: a document from the 'matches' MongoDB collection.
        team_side: 'home' or 'away'.

    Returns:
        Dict with season_statistics field names as keys.
    """
    opponent = "away" if team_side == "home" else "home"
    flat = _flatten_statistics(match_doc.get("statistics", {}))

    def _v(key: str, side: str = team_side) -> float | None:
        entry = flat.get(key)
        if entry is None:
            return None
        v = entry.get(side)
        return float(v) if isinstance(v, (int, float)) else None

    # Pass accuracy percentage — computed from raw totals
    total_passes    = _v("totalPass")
    accurate_passes = _v("accuratePass")
    pass_acc_pct = (
        (accurate_passes / total_passes * 100)
        if total_passes and accurate_passes and total_passes > 0
        else None
    )

    # Aerial win percentage — computed from raw totals
    aerial_duels = _v("aerialDuel")
    aerial_won   = _v("aerialDuelWon")
    aerial_win_pct = (
        (aerial_won / aerial_duels * 100)
        if aerial_duels and aerial_won and aerial_duels > 0
        else None
    )

    # Clean sheet — 1 if no goals conceded this match, else 0
    m = match_doc.get("match", {})
    goals_conceded = (
        m.get("away_score") if team_side == "home" else m.get("home_score")
    )
    clean_sheets = 1 if (isinstance(goals_conceded, (int, float)) and goals_conceded == 0) else 0

    return {
        "averageBallPossession":    _v("ballPossession"),
        "accuratePassesPercentage": pass_acc_pct,
        "totalPasses":              total_passes,
        "totalLongBalls":           _v("totalLongBalls"),
        "totalCrosses":             _v("totalCross"),
        "totalAerialDuels":         aerial_duels,
        "aerialDuelsWonPercentage": aerial_win_pct,
        "interceptions":            _v("interceptionWon"),
        "fouls":                    _v("fouls"),
        "ballRecovery":             _v("ballRecovery"),
        "offsides":                 _v("offsideWon"),
        "shots":                    _v("totalShots"),
        "shotsAgainst":             _v("totalShots", opponent),
        "cleanSheets":              clean_sheets,
        "successfulDribbles":       _v("wonContest"),
    }


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_last_n_league_matches(
    team_id: int,
    tournament_id: int | str | None,
    n: int,
    db,
    unique_tournament_id: int | str | None = None,
) -> list[dict]:
    """Fetch the last n finished matches for a team from MongoDB across ALL competitions.

    Tournament filters are intentionally omitted so that form fingerprints reflect
    a team's actual recent form — club competitions, domestic cups, continental cups,
    national team qualifiers and friendlies all count.  The caller may pass
    tournament_id / unique_tournament_id for backward-compat; they are no longer used.

    Returns an empty list gracefully if the matches collection is empty.
    """
    query: dict[str, Any] = {
        "$or": [
            {"home_team_id": int(team_id)},
            {"away_team_id": int(team_id)},
        ],
    }
    try:
        return list(
            db.matches.find(query)
            .sort("match_date", -1)
            .limit(n)
        )
    except Exception:
        return []


def extract_match_metrics(match_doc: dict, team_side: str) -> dict[str, float | None]:
    """Extract style metric values from a single match document.

    Reuses METRIC_EXTRACTORS from style_engine.py with matches=1 so
    per-match normalisations are applied correctly.

    Args:
        match_doc: a document from the 'matches' MongoDB collection.
        team_side: 'home' or 'away'.

    Returns:
        Dict mapping METRIC_KEYS to float values (None if unavailable).
    """
    stats = _build_season_stats_from_match(match_doc, team_side)
    metrics: dict[str, float | None] = {}
    for key, extractor in METRIC_EXTRACTORS.items():
        try:
            val = extractor(stats, 1)  # matches=1 for per-match
            if val is not None and math.isfinite(float(val)):
                metrics[key] = float(val)
            else:
                metrics[key] = None
        except Exception:
            metrics[key] = None
    return metrics


def _zscore_pool(
    obs_list: list[dict[str, float | None]],
    *,
    min_obs: int = _ZSCORE_MIN_OBS,
) -> list[dict[str, float]]:
    """Z-score normalise a flat list of metric observation dicts.

    If the total non-None observations for a metric is below min_obs,
    Z-scoring still occurs but on whatever data is available. If there is
    only one unique value (σ ≈ 0), all z-scores are set to 0.0.

    Args:
        obs_list: list of {metric_key: float_or_None} dicts.
        min_obs: log-level threshold (currently informational only).

    Returns:
        Parallel list of {metric_key: float} dicts (0.0 where None).
    """
    n_obs = len(obs_list)
    z_list: list[dict[str, float]] = [{} for _ in range(n_obs)]

    for metric in METRIC_KEYS:
        pairs = [(i, obs_list[i].get(metric)) for i in range(n_obs)]
        valid = [(i, v) for i, v in pairs if v is not None]

        if len(valid) < 2:
            # Not enough data — zero fill
            for i in range(n_obs):
                z_list[i][metric] = 0.0
            continue

        raw_vals = [v for _, v in valid]
        mu  = mean(raw_vals)
        sig = stdev(raw_vals) if len(raw_vals) > 1 else 0.0
        sig = max(sig, 1e-9)

        for i, v in pairs:
            z_list[i][metric] = (v - mu) / sig if v is not None else 0.0

    return z_list


def _dot_style_score(z: dict[str, float]) -> dict[str, float]:
    """Compute style scores from a Z-score dict.

    Uses the same tanh normalisation formula as style_engine.compute_style_scores.

    Args:
        z: dict mapping metric keys to Z-score floats.

    Returns:
        Dict mapping style names to confidence floats in [0, 1].
    """
    scores: dict[str, float] = {}
    for style_name, weights in STYLE_WEIGHTS.items():
        raw_dot     = sum(z.get(m, 0.0) * w for m, w in weights.items())
        weight_norm = math.sqrt(sum(w * w for w in weights.values()))
        normalised  = raw_dot / weight_norm if weight_norm > 0 else 0.0
        scores[style_name] = round((math.tanh(normalised * 0.8) + 1.0) / 2.0, 3)
    return scores


def compute_form_style_scores(
    clubs_raw: list[dict],
    db,
    n: int = 5,
) -> list[dict]:
    """Compute rolling form style scores for each team.

    Fetches each team's last n league matches from MongoDB, extracts
    per-match metrics, Z-score normalises across all teams × matches
    pooled together, then averages per-match style scores with equal weight.

    Args:
        clubs_raw: same list used by style_engine.compute_style_scores,
            extended with 'team_id' (int) and 'tournament_id' (int|str).
        db: pymongo Database instance.
        n: maximum recent league matches per team (default 5).

    Returns:
        List of dicts (one per team) with:
            name, slug, logo_url,
            form_style_scores  {style_name: float 0–1},
            form_primary_style str,
            form_match_count   int (≤ n).
    """
    # ── Step 1: collect per-match metric observations for all teams ───────────
    obs_list: list[dict[str, float | None]] = []
    # club_obs_indices[i] = list of indices into obs_list for club i
    club_obs_indices: list[list[int]] = []

    for club in clubs_raw:
        team_id  = club.get("team_id")
        tid      = club.get("tournament_id")
        uniq_tid = club.get("unique_tournament_id")
        if not team_id:
            club_obs_indices.append([])
            continue

        matches  = fetch_last_n_league_matches(int(team_id), tid, n, db, unique_tournament_id=uniq_tid)
        indices: list[int] = []
        for match_doc in matches:
            home_id  = match_doc.get("home_team_id")
            side     = "home" if (home_id is not None and int(home_id) == int(team_id)) else "away"
            metrics  = extract_match_metrics(match_doc, side)
            idx      = len(obs_list)
            obs_list.append(metrics)
            indices.append(idx)
        club_obs_indices.append(indices)

    # ── Step 2: Z-score normalise across the full pool ────────────────────────
    z_list = _zscore_pool(obs_list) if obs_list else []

    # ── Step 3: average per-team style scores ─────────────────────────────────
    results: list[dict] = []
    for club_idx, club in enumerate(clubs_raw):
        indices    = club_obs_indices[club_idx] if club_idx < len(club_obs_indices) else []
        match_count = len(indices)

        if match_count == 0:
            form_style_scores  = {style: 0.5 for style in STYLE_WEIGHTS}
            form_primary_style = ""
        else:
            per_match: list[dict[str, float]] = [_dot_style_score(z_list[i]) for i in indices]
            form_style_scores  = {
                style: round(mean(s[style] for s in per_match), 3)
                for style in STYLE_WEIGHTS
            }
            form_primary_style = max(form_style_scores, key=form_style_scores.get)

        results.append({
            "name":               club["name"],
            "slug":               club["slug"],
            "logo_url":           club.get("logo_url", ""),
            "form_style_scores":  form_style_scores,
            "form_primary_style": form_primary_style,
            "form_match_count":   match_count,
        })

    return results


def apply_positional_context(
    clubs_raw: list[dict],
    db,
    n: int = 5,
) -> list[dict]:
    """Compute positional passing context from each team's last n matches.

    Groups player statistics by position code into three zones:
      G/D → backline_passes
      M   → mid_passes
      F   → forward_passes

    Computes P90 metrics per zone using total minutes across all players
    in that zone across all n matches.

    Args:
        clubs_raw: same format as compute_form_style_scores.
        db: pymongo Database instance.
        n: maximum recent league matches per team.

    Returns:
        List of dicts (one per team):
            slug              str
            positional_passing {
                'backline_passes': {
                    touches_p90, accurate_passes_p90, total_passes_p90,
                    pass_accuracy, player_count, heatmap_centroid (optional)
                },
                'mid_passes':     { ... },
                'forward_passes': { ... },
            }
    """
    results: list[dict] = []

    for club in clubs_raw:
        team_id  = club.get("team_id")
        tid      = club.get("tournament_id")
        uniq_tid = club.get("unique_tournament_id")

        empty = {"backline_passes": None, "mid_passes": None, "forward_passes": None}

        if not team_id:
            results.append({"slug": club["slug"], "positional_passing": empty})
            continue

        matches = fetch_last_n_league_matches(int(team_id), tid, n, db, unique_tournament_id=uniq_tid)
        if not matches:
            results.append({"slug": club["slug"], "positional_passing": empty})
            continue

        # Per-group accumulators
        acc: dict[str, dict[str, Any]] = {
            grp: {
                "touches": 0.0, "accurate_passes": 0.0,
                "total_passes": 0.0, "minutes": 0.0,
                "player_ids": set(),
                "heatmap_xs": [], "heatmap_ys": [],
            }
            for grp in ("backline_passes", "mid_passes", "forward_passes")
        }

        for match_doc in matches:
            home_id  = match_doc.get("home_team_id")
            side     = "home" if (home_id is not None and int(home_id) == int(team_id)) else "away"
            players  = match_doc.get("player_statistics", {}).get(side, [])

            for player in players:
                pos_code  = (player.get("position") or "").upper().strip()
                group_key = _POSITION_GROUPS.get(pos_code)
                if not group_key:
                    continue

                stats    = player.get("stats", {})
                minutes  = float(stats.get("minutes_played") or 0)
                touches  = float(stats.get("touches") or 0)
                tot_pass = float(stats.get("passes_total") or 0)
                acc_pass = float(stats.get("passes_accurate") or 0)

                g = acc[group_key]
                g["minutes"]         += minutes
                g["touches"]         += touches
                g["total_passes"]    += tot_pass
                g["accurate_passes"] += acc_pass
                if player.get("player_id"):
                    g["player_ids"].add(player["player_id"])

                # Optional: heatmap centroid (if player heatmap stored in doc)
                hm = player.get("heatmap")
                if isinstance(hm, dict):
                    for pt in hm.get("points", []):
                        if isinstance(pt.get("x"), (int, float)):
                            g["heatmap_xs"].append(float(pt["x"]))
                        if isinstance(pt.get("y"), (int, float)):
                            g["heatmap_ys"].append(float(pt["y"]))

        # Compute P90 values for each group
        positional_passing: dict[str, Any] = {}
        for grp_key, g in acc.items():
            mins = g["minutes"]
            if mins <= 0:
                positional_passing[grp_key] = None
                continue

            entry: dict[str, Any] = {
                "touches_p90":         round((g["touches"]         / mins) * 90, 2),
                "total_passes_p90":    round((g["total_passes"]    / mins) * 90, 2),
                "accurate_passes_p90": round((g["accurate_passes"] / mins) * 90, 2),
                "pass_accuracy":       round(
                    g["accurate_passes"] / g["total_passes"]
                    if g["total_passes"] > 0 else 0.0,
                    4,
                ),
                "player_count": len(g["player_ids"]),
            }
            if g["heatmap_xs"]:
                entry["heatmap_centroid"] = {
                    "x": round(sum(g["heatmap_xs"]) / len(g["heatmap_xs"]), 2),
                    "y": round(sum(g["heatmap_ys"]) / len(g["heatmap_ys"]), 2),
                }
            positional_passing[grp_key] = entry

        results.append({"slug": club["slug"], "positional_passing": positional_passing})

    return results
