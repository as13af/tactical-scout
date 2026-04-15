"""
SofaScore Match Statistics Scraper — core logic.

Fetches match metadata, formations, and per-period statistics for one or many
events, writing structured JSON to ``Coding/match_output/``.

Single usage (CLI)::
    python match_stats_scraper.py 14214854
    python match_stats_scraper.py "https://www.sofascore.com/football/match/…#id:14214854"

Bulk usage (CLI) — space-separated IDs or URLs::
    python match_stats_scraper.py 14214854 14214855 14214856
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Callable, Iterable

from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager

BASE_URL = "https://www.sofascore.com/api/v1"

# Matches: "…#id:14214854,…" or "&id:14214854"
_HASH_ID_RE = re.compile(r"[#&]id:(\d+)")


# ── ID parsing ────────────────────────────────────────────────────────────────

def extract_event_id(text: str) -> int | None:
    """Extract a SofaScore event ID from a plain integer string or match URL.

    Handles:
    - Plain integer: ``"14214854"``
    - URL fragment:  ``"…#id:14214854,tab:lineups"``
    - API URL path:  ``"…/event/14214854/statistics"``

    Args:
        text: Raw input from the user (URL or bare ID).

    Returns:
        The parsed event ID, or ``None`` if not found.
    """
    text = text.strip()
    if text.isdigit():
        return int(text)

    m = _HASH_ID_RE.search(text)
    if m:
        return int(m.group(1))

    m = re.search(r"/event/(\d+)", text)
    if m:
        return int(m.group(1))

    return None


# ── Selenium helpers ──────────────────────────────────────────────────────────

def create_driver() -> webdriver.Chrome:
    """Instantiate a headless Chrome driver with anti-detection options.

    Returns:
        A configured ``webdriver.Chrome`` instance.
    """
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    driver = webdriver.Chrome(
        service=ChromeService(ChromeDriverManager().install()),
        options=options,
    )
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return driver


def fetch_json(
    driver: webdriver.Chrome,
    url: str,
    retries: int = 3,
) -> dict | None:
    """Navigate to a SofaScore API URL and parse the JSON response from ``<pre>``.

    Args:
        driver:  Active Selenium Chrome driver.
        url:     The API endpoint to fetch.
        retries: Number of retry attempts on failure.

    Returns:
        Parsed JSON dict, or ``None`` if all retries are exhausted.
    """
    for attempt in range(retries):
        try:
            driver.get(url)
            time.sleep(0.8)
            raw = driver.find_element("tag name", "pre").text
            return json.loads(raw)
        except Exception as exc:
            print(f"    Retry {attempt + 1} on {url} — {exc}")
            time.sleep(2 ** attempt)
    return None


# ── Data parsers ──────────────────────────────────────────────────────────────

def _ts_to_iso(ts: int | None) -> str:
    """Convert a Unix timestamp to an ISO-8601 date string (UTC)."""
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _winner_label(code: int | None) -> str:
    """Map SofaScore winner codes to readable labels."""
    return {1: "Home", 2: "Away", 3: "Draw"}.get(code or 0, "Unknown")


def _parse_statistics(raw: dict | None) -> dict[str, list[dict]]:
    """Transform the raw statistics API response into a structured dict keyed by period.

    Output structure::

        {
            "ALL": [
                {
                    "group": "Match overview",
                    "items": [
                        {
                            "name": "Ball possession",
                            "key": "ballPossession",
                            "home": "54%",
                            "away": "46%",
                            "homeValue": 54,
                            "awayValue": 46
                        }
                    ]
                }
            ],
            "1ST": [...],
            "2ND": [...]
        }

    Args:
        raw: Raw JSON from ``/event/{id}/statistics``.

    Returns:
        Structured statistics dict, or an empty dict on failure.
    """
    result: dict[str, list[dict]] = {}
    if not raw:
        return result

    for period_block in raw.get("statistics", []):
        period = period_block.get("period", "ALL")
        groups: list[dict] = []
        for group in period_block.get("groups", []):
            items = [
                {
                    "name":      item.get("name", ""),
                    "key":       item.get("key", ""),
                    "home":      item.get("home", ""),
                    "away":      item.get("away", ""),
                    "homeValue": item.get("homeValue"),
                    "awayValue": item.get("awayValue"),
                }
                for item in group.get("statisticsItems", [])
            ]
            groups.append({
                "group": group.get("groupName", ""),
                "items": items,
            })
        result[period] = groups

    return result


# ── Player stats parser ─────────────────────────────────────────────────────────

# Mapping from SofaScore camelCase stat key → clean output key
_PLAYER_STAT_MAP: dict[str, str] = {
    "minutesPlayed":               "minutes_played",
    "rating":                      "rating",
    "goals":                       "goals",
    "goalAssist":                  "assists",
    "expectedGoals":               "xG",
    "expectedAssists":             "xA",
    "keyPass":                     "key_passes",
    "totalShots":                  "shots_total",
    "onTargetScoringAttempt":      "shots_on_target",
    "shotOffTarget":               "shots_off_target",
    "blockedScoringAttempt":       "shots_blocked",
    "touches":                     "touches",
    "totalPass":                   "passes_total",
    "accuratePass":                "passes_accurate",
    "totalLongBalls":              "long_balls_total",
    "accurateLongBalls":           "long_balls_accurate",
    "totalCross":                  "crosses_total",
    "accurateCross":               "crosses_accurate",
    "duelWon":                     "duels_won",
    "duelLost":                    "duels_lost",
    "aerialWon":                   "aerial_won",
    "aerialLost":                  "aerial_lost",
    "totalContest":                "dribbles_attempted",
    "wonContest":                  "dribbles_won",
    "dispossessed":                "dispossessed",
    "possessionLostCtrl":          "possession_lost",
    "totalTackle":                 "tackles_total",
    "wonTackle":                   "tackles_won",
    "interceptionWon":             "interceptions",
    "totalClearance":              "clearances",
    "outfielderBlock":             "blocks",
    "ballRecovery":                "ball_recoveries",
    "fouls":                       "fouls_committed",
    "wasFouled":                   "fouls_suffered",
    "totalOffside":                "offsides",
    "errorLeadToAShot":            "errors_led_to_shot",
    "ownGoals":                    "own_goals",
    # Goalkeeper
    "saves":                       "saves",
    "savedShotsFromInsideTheBox":  "saves_inside_box",
    "goodHighClaim":               "high_claims",
    "crossNotClaimed":             "crosses_not_claimed",
    "totalKeeperSweeper":          "sweeper_actions",
    "accurateKeeperSweeper":       "sweeper_actions_accurate",
}


def _parse_player_statistics(lineup_data: dict | None) -> dict[str, list[dict]]:
    """Extract per-player statistics from the lineups API response.

    Player stats are embedded inside ``/event/{id}/lineups`` — no extra
    API call is required.  Players who did not enter the pitch (unused subs)
    may have no ``statistics`` key; they are still included with an empty
    ``stats`` dict so the full squad roster is preserved.

    Args:
        lineup_data: Raw JSON from ``/event/{id}/lineups``.

    Returns:
        Dict with ``"home"`` and ``"away"`` keys, each a list of player
        entries::

            {
                "home": [
                    {
                        "player_id":     929630,
                        "name":          "Toshio Lake",
                        "short_name":    "T. Lake",
                        "position":      "M",
                        "jersey_number": "8",
                        "substitute":    False,
                        "captain":       False,
                        "stats": {
                            "minutes_played": 90,
                            "rating": 7.8,
                            "goals": 1,
                            "xG": 0.42,
                            ...
                        }
                    }
                ],
                "away": [...]
            }
    """
    result: dict[str, list[dict]] = {"home": [], "away": []}
    if not lineup_data:
        return result

    for side in ("home", "away"):
        for entry in lineup_data.get(side, {}).get("players", []):
            player = entry.get("player", {})
            raw_stats: dict = entry.get("statistics") or {}

            # Map only the keys we care about; skip absent fields so the
            # output stays lean and doesn't contain a wall of None values.
            stats = {
                out_key: raw_stats[api_key]
                for api_key, out_key in _PLAYER_STAT_MAP.items()
                if api_key in raw_stats
            }

            result[side].append({
                "player_id":     player.get("id"),
                "name":          player.get("name", ""),
                "short_name":    player.get("shortName", ""),
                "position":      entry.get("position") or player.get("position", ""),
                "jersey_number": entry.get("jerseyNumber", ""),
                "substitute":    entry.get("substitute", False),
                "captain":       entry.get("captain", False),
                "stats":         stats,
            })

    return result


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_match_data(
    event_id: int,
    log_fn: Callable[[str], None] = print,
) -> dict:
    """Fetch all data for a single event: basic info, formations, and statistics.

    Opens a single Selenium session for all three API calls, then closes
    it before returning, so the driver is never leaked.

    Args:
        event_id: The SofaScore event ID.
        log_fn:   Callable used for progress messages.

    Returns:
        Structured dict ready for JSON serialisation::

            {
                "event_id":   14214854,
                "scraped_at": "2026-04-14T10:30:00+00:00",
                "match": {
                    "date": "2025-09-28",
                    "competition": "BRI Liga 1",
                    "season": "24/25",
                    "home_team": "Persija Jakarta",
                    "away_team": "Persib Bandung",
                    "home_score": 1,
                    "away_score": 2,
                    "ht_home": 0,
                    "ht_away": 1,
                    "winner": "Away",
                    "home_formation": "4-2-3-1",
                    "away_formation": "4-3-3"
                },
                "statistics": { "ALL": [...], "1ST": [...], "2ND": [...] },
                "player_statistics": {
                    "home": [...],
                    "away": [...]
                }
            }
    """
    log_fn("=" * 55)
    log_fn("  SofaScore Match Statistics Scraper")
    log_fn("=" * 55)

    driver = create_driver()
    try:
        # 1. Event metadata
        log_fn(f"\n[1/3] Fetching event info for ID {event_id}…")
        event_data = fetch_json(driver, f"{BASE_URL}/event/{event_id}")
        if not event_data:
            raise RuntimeError(f"Failed to fetch event data for ID {event_id}.")

        event = event_data.get("event", {})
        home_team = event.get("homeTeam", {}).get("name", "Unknown")
        away_team = event.get("awayTeam", {}).get("name", "Unknown")
        log_fn(f"      {home_team} vs {away_team}")

        match_info: dict = {
            "date":           _ts_to_iso(event.get("startTimestamp")),
            "competition":    event.get("tournament", {}).get("name", ""),
            "season":         event.get("season", {}).get("name", ""),
            "home_team":      home_team,
            "away_team":      away_team,
            "home_score":     event.get("homeScore", {}).get("current"),
            "away_score":     event.get("awayScore", {}).get("current"),
            "ht_home":        event.get("homeScore", {}).get("period1"),
            "ht_away":        event.get("awayScore", {}).get("period1"),
            "winner":         _winner_label(event.get("winnerCode")),
            "home_formation": "",
            "away_formation": "",
        }

        # 2. Lineups — formations + player stats
        log_fn("\n[2/3] Fetching lineups & player stats…")
        time.sleep(0.5)
        lineup_data = fetch_json(driver, f"{BASE_URL}/event/{event_id}/lineups")
        player_statistics: dict = {"home": [], "away": []}
        if lineup_data:
            match_info["home_formation"] = lineup_data.get("home", {}).get("formation", "")
            match_info["away_formation"] = lineup_data.get("away", {}).get("formation", "")
            player_statistics = _parse_player_statistics(lineup_data)
            home_count = len(player_statistics["home"])
            away_count = len(player_statistics["away"])
            log_fn(
                f"      {match_info['home_formation']} vs "
                f"{match_info['away_formation']}  "
                f"({home_count} + {away_count} players)"
            )
        else:
            log_fn("      No lineup data available for this match.")

        # 3. Statistics
        log_fn("\n[3/3] Fetching match statistics…")
        time.sleep(0.8)
        stats_data = fetch_json(driver, f"{BASE_URL}/event/{event_id}/statistics")
        if not stats_data:
            raise RuntimeError(
                f"No statistics returned for event ID {event_id}. "
                "The match may not have started yet or stats are unavailable."
            )
        statistics = _parse_statistics(stats_data)
        total_items = sum(
            len(g["items"]) for groups in statistics.values() for g in groups
        )
        log_fn(f"      {len(statistics)} period(s) · {total_items} stat items collected")

    finally:
        driver.quit()

    return {
        "event_id":          event_id,
        "scraped_at":        datetime.now(tz=timezone.utc).isoformat(),
        "match":             match_info,
        "statistics":        statistics,
        "player_statistics": player_statistics,
    }


def run_scraper(
    event_id_or_url: str,
    output_dir: str = "match_output",
    log_fn: Callable[[str], None] = print,
) -> str:
    """Full pipeline: parse ID → fetch → save JSON.

    Args:
        event_id_or_url: Raw event ID integer string or a SofaScore match URL.
        output_dir:      Directory to write the JSON file into.
        log_fn:          Callable used for progress messages.

    Returns:
        Absolute path to the saved JSON file.

    Raises:
        ValueError: If the event ID cannot be parsed from the input.
        RuntimeError: If the API returns no usable data.
    """
    event_id = extract_event_id(event_id_or_url)
    if event_id is None:
        raise ValueError(
            f"Could not extract a valid event ID from: {event_id_or_url!r}\n"
            "Provide a plain integer ID or a SofaScore match URL containing "
            "'#id:XXXXXXX'."
        )

    data = fetch_match_data(event_id, log_fn=log_fn)

    # Build a filesystem-safe filename
    home = re.sub(r"[^\w\s-]", "", data["match"]["home_team"]).strip().replace(" ", "_")
    away = re.sub(r"[^\w\s-]", "", data["match"]["away_team"]).strip().replace(" ", "_")
    filename = f"{home}_vs_{away}_{event_id}.json"

    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, filename)

    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)

    periods = list(data["statistics"].keys())
    log_fn(f"\n  Saved → {output_path}")
    log_fn(f"  Periods  : {', '.join(periods)}")
    log_fn("\nDone!")

    return os.path.abspath(output_path)


# ── Bulk scraper ──────────────────────────────────────────────────────────────

def run_bulk_scraper(
    inputs: Iterable[str],
    output_dir: str = "match_output",
    output_filename: str | None = None,
    log_fn: Callable[[str], None] = print,
) -> tuple[str, dict]:
    """Scrape multiple matches in one Selenium session and merge into a single JSON.

    Reuses a single Chrome driver across all events to minimise startup
    overhead. Each event is attempted independently — a failure on one match is
    logged and skipped rather than aborting the whole batch.

    Args:
        inputs:          Iterable of raw event IDs (str) or SofaScore match URLs.
        output_dir:      Directory to write the combined JSON file into.
        output_filename: Override the auto-generated filename. Must end in ``.json``.
        log_fn:          Callable used for progress messages.

    Returns:
        Absolute path to the saved bulk JSON file.

    Raises:
        ValueError: If none of the inputs yield a parseable event ID.
    """
    raw_list = list(inputs)

    # Resolve & validate all IDs up front
    id_pairs: list[tuple[int, str]] = []
    for raw in raw_list:
        eid = extract_event_id(raw)
        if eid is None:
            log_fn(f"  [SKIP] Cannot parse event ID from: {raw!r}")
        else:
            id_pairs.append((eid, raw))

    if not id_pairs:
        raise ValueError(
            "No valid event IDs found in the input list. "
            "Provide plain integers or SofaScore match URLs."
        )

    log_fn("=" * 60)
    log_fn(f"  SofaScore Bulk Match Scraper — {len(id_pairs)} match(es)")
    log_fn("=" * 60)

    driver = create_driver()
    results: list[dict] = []
    errors:  list[dict] = []

    try:
        for idx, (event_id, original_input) in enumerate(id_pairs, 1):
            log_fn(f"\n[{idx}/{len(id_pairs)}] Event ID {event_id}")
            try:
                # ── Event metadata ────────────────────────────────────────
                event_data = fetch_json(driver, f"{BASE_URL}/event/{event_id}")
                if not event_data:
                    raise RuntimeError("Empty response from event endpoint.")

                event = event_data.get("event", {})
                home_team = event.get("homeTeam", {}).get("name", "Unknown")
                away_team = event.get("awayTeam", {}).get("name", "Unknown")
                log_fn(f"      {home_team} vs {away_team}")

                match_info: dict = {
                    "date":           _ts_to_iso(event.get("startTimestamp")),
                    "competition":    event.get("tournament", {}).get("name", ""),
                    "season":         event.get("season", {}).get("name", ""),
                    "home_team":      home_team,
                    "away_team":      away_team,
                    "home_score":     event.get("homeScore", {}).get("current"),
                    "away_score":     event.get("awayScore", {}).get("current"),
                    "ht_home":        event.get("homeScore", {}).get("period1"),
                    "ht_away":        event.get("awayScore", {}).get("period1"),
                    "winner":         _winner_label(event.get("winnerCode")),
                    "home_formation": "",
                    "away_formation": "",
                }
                time.sleep(0.5)

                # ── Lineups — formations + player stats ───────────────
                lineup_data = fetch_json(driver, f"{BASE_URL}/event/{event_id}/lineups")
                player_statistics: dict = {"home": [], "away": []}
                if lineup_data:
                    match_info["home_formation"] = lineup_data.get("home", {}).get("formation", "")
                    match_info["away_formation"] = lineup_data.get("away", {}).get("formation", "")
                    player_statistics = _parse_player_statistics(lineup_data)
                time.sleep(0.8)

                # ── Statistics ────────────────────────────────────────────
                stats_data = fetch_json(driver, f"{BASE_URL}/event/{event_id}/statistics")
                statistics = _parse_statistics(stats_data)
                if not statistics:
                    log_fn("      ⚠ No statistics returned — match may be live or stats unavailable.")

                home_count = len(player_statistics["home"])
                away_count = len(player_statistics["away"])
                results.append({
                    "event_id":          event_id,
                    "scraped_at":        datetime.now(tz=timezone.utc).isoformat(),
                    "match":             match_info,
                    "statistics":        statistics,
                    "player_statistics": player_statistics,
                })
                log_fn(
                    f"      ✓  {len(statistics)} period(s) · "
                    f"{sum(len(g['items']) for gs in statistics.values() for g in gs)} team-stat items · "
                    f"{home_count + away_count} players"
                )
                time.sleep(1.0)  # gentle pacing between matches

            except Exception as exc:
                log_fn(f"      ✗  Failed: {exc}")
                errors.append({"event_id": event_id, "input": original_input, "error": str(exc)})

    finally:
        driver.quit()

    if not results:
        raise RuntimeError("All events failed to scrape. See log for details.")

    # ── Build combined payload ─────────────────────────────────────────────────
    bulk_payload: dict = {
        "bulk_scraped_at": datetime.now(tz=timezone.utc).isoformat(),
        "total":           len(id_pairs),
        "succeeded":       len(results),
        "failed":          len(errors),
        "errors":          errors,
        "matches":         results,
    }

    # ── Auto-generate filename ─────────────────────────────────────────────────
    if output_filename is None:
        ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_filename = f"bulk_{len(results)}_matches_{ts}.json"

    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, output_filename)

    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(bulk_payload, fh, indent=2, ensure_ascii=False)

    log_fn(f"\n  Saved     → {output_path}")
    log_fn(f"  Succeeded : {len(results)} / {len(id_pairs)}")
    if errors:
        log_fn(f"  Failed    : {len(errors)} — see 'errors' key in JSON")
    log_fn("\nDone!")

    return os.path.abspath(output_path), bulk_payload


# ── CLI entry point ───────────────────────────────────────────────────────────

def main() -> None:
    """CLI runner — pass one or more event IDs / URLs as arguments.

    Examples::
        python match_stats_scraper.py 14214854
        python match_stats_scraper.py 14214854 14214855 14214856
    """
    args = sys.argv[1:] if len(sys.argv) > 1 else ["14214854"]
    if len(args) == 1:
        run_scraper(args[0])
    else:
        run_bulk_scraper(args)


if __name__ == "__main__":
    main()
