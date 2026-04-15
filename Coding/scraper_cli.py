#!/usr/bin/env python3
"""
scraper_cli.py — Headless CLI league scraper, invoked by the webapp's Update button.

Usage:
    python scraper_cli.py --tid 692 --uniq-tid 9 --season 77849 --output /path/to/output [--no-skip]

Output lines starting with "PROGRESS:<n>" carry a 0-100 integer percentage.
The final line starts with "DONE:" on success or "ERROR:" on failure.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Ensure Coding/ (this script's directory) is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from driver  import make_driver
from helpers import fetch_retry, strip_field_translations, safe_name, extract_competition_info
from heatmap import save_heatmap


def log(msg: str) -> None:
    print(msg, flush=True)


def run_scrape(tid: str, uniq_tid: str, season_id: str,
               out_dir: str, skip_existing: bool = True,
               include_career: bool = False,
               log_fn=None) -> None:
    """Run a full league scrape.  Pass *log_fn* to capture output in-process;
    defaults to printing to stdout (used by the CLI entry-point)."""
    if log_fn is None:
        log_fn = log

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    total_players = 0
    skipped = 0
    failed = 0

    driver = make_driver()
    try:
        driver.get("https://www.sofascore.com/football")
    except Exception:
        pass
    time.sleep(8)
    log_fn("Browser ready.")

    try:
        log_fn("Fetching standings...")
        standings_data = fetch_retry(
            driver,
            f"https://api.sofascore.com/api/v1/unique-tournament/{uniq_tid}/season/{season_id}/standings/total"
        )
        strip_field_translations(standings_data)
        for group in standings_data.get("standings", []):
            for row in group.get("rows", []):
                row["team_id"] = row["team"]["id"]

        competition_name, competition_country = extract_competition_info(standings_data)
        competition_dir = out_dir / competition_country / competition_name
        competition_dir.mkdir(parents=True, exist_ok=True)

        standings_path = competition_dir / f"Standings_{competition_name}_{uniq_tid}_Season_{season_id}.json"
        with open(standings_path, "w", encoding="utf-8") as f:
            json.dump(standings_data, f, indent=4, ensure_ascii=False)

        log_fn(f"Competition: {competition_name} | Country: {competition_country}")

        teams = []
        for group in standings_data.get("standings", []):
            for row in group.get("rows", []):
                teams.append({"id": str(row["team_id"]), "name": row["team"]["name"]})

        total_teams = len(teams)
        log_fn(f"Found {total_teams} teams. Starting scrape...")

        for team_idx, team in enumerate(teams):
            team_id   = team["id"]
            team_name = team["name"]
            safe_team = safe_name(team_name)

            log_fn(f"PROGRESS:{int(team_idx / total_teams * 100)}")
            log_fn(f"[{team_idx + 1}/{total_teams}] {team_name}")

            club_dir     = competition_dir / safe_team
            players_dir  = club_dir / "Players"
            heatmaps_dir = club_dir / "Heatmaps"
            club_dir.mkdir(parents=True, exist_ok=True)
            players_dir.mkdir(exist_ok=True)
            heatmaps_dir.mkdir(exist_ok=True)

            # ── Club data ─────────────────────────────────────────────────────
            club_json = club_dir / f"Club_{team_id}_{safe_team}_{uniq_tid}_Season_{season_id}.json"
            if skip_existing and club_json.exists():
                log_fn("  Club data already exists, skipping.")
            else:
                try:
                    log_fn("  Fetching club profile...")
                    club_profile = fetch_retry(driver, f"https://api.sofascore.com/api/v1/team/{team_id}")
                    time.sleep(0.3)
                    log_fn("  Fetching club season stats...")
                    club_stats = fetch_retry(
                        driver,
                        f"https://api.sofascore.com/api/v1/team/{team_id}"
                        f"/unique-tournament/{uniq_tid}/season/{season_id}/statistics/overall"
                    )
                    time.sleep(0.3)
                    strip_field_translations(club_profile)
                    strip_field_translations(club_stats)
                    with open(club_json, "w", encoding="utf-8") as f:
                        json.dump({
                            "team_id": team_id, "team_name": team_name,
                            "tournament_id": uniq_tid, "season_id": season_id,
                            "competition_name": competition_name,
                            "competition_country": competition_country,
                            "profile": club_profile,
                            "season_statistics": club_stats,
                        }, f, indent=4, ensure_ascii=False)
                    log_fn("  Club JSON saved")
                except Exception as e:
                    log_fn(f"  Club data failed: {e}")

            # ── Squad ─────────────────────────────────────────────────────────
            try:
                squad = fetch_retry(
                    driver,
                    f"https://api.sofascore.com/api/v1/team/{team_id}/players"
                ).get("players", [])
                log_fn(f"  Squad: {len(squad)} players")
            except Exception as e:
                log_fn(f"  Squad fetch failed: {e}")
                continue

            # ── Per player ────────────────────────────────────────────────────
            for p_idx, entry in enumerate(squad):
                player      = entry.get("player", entry)
                player_id   = str(player["id"])
                player_name = player.get("name", f"Player_{player_id}")
                safe_player = safe_name(player_name)

                json_path = players_dir / (
                    f"{safe_player}_{player_id}_{safe_team}"
                    f"_{competition_name}_{uniq_tid}_Season_{season_id}.json"
                )
                png_path = heatmaps_dir / (
                    f"{safe_player}_{player_id}_{safe_team}"
                    f"_{competition_name}_{uniq_tid}_Season_{season_id}_heatmap.png"
                )

                if skip_existing and json_path.exists():
                    log_fn(f"    [{p_idx + 1}/{len(squad)}] {player_name} — skipped")
                    skipped += 1
                    continue

                log_fn(f"    [{p_idx + 1}/{len(squad)}] {player_name} (id={player_id})...")

                try:
                    base_url = (
                        f"https://api.sofascore.com/api/v1/player/{player_id}"
                        f"/unique-tournament/{uniq_tid}/season/{season_id}"
                    )

                    profile_data = fetch_retry(
                        driver, f"https://api.sofascore.com/api/v1/player/{player_id}"
                    )
                    player_name = profile_data.get("player", {}).get("name", player_name)
                    time.sleep(0.3)

                    stats_data   = fetch_retry(driver, f"{base_url}/statistics/overall")
                    time.sleep(0.3)
                    heatmap_data = fetch_retry(driver, f"{base_url}/heatmap/overall")
                    heatmap_data.pop("events", None)
                    time.sleep(0.3)
                    char_data = fetch_retry(
                        driver,
                        f"https://api.sofascore.com/api/v1/player/{player_id}/characteristics"
                    )
                    positions = char_data.get("positions", [])
                    time.sleep(0.3)

                    career_data = None
                    if include_career:
                        career_data  = fetch_retry(
                            driver,
                            f"https://api.sofascore.com/api/v1/player/{player_id}/statistics"
                        )
                        seasons_list = career_data.get("seasons", [])
                        time.sleep(0.3)
                        for s in seasons_list:
                            t_id = s["uniqueTournament"]["id"]
                            s_id = s["season"]["id"]
                            try:
                                full = fetch_retry(
                                    driver,
                                    f"https://api.sofascore.com/api/v1/player/{player_id}"
                                    f"/unique-tournament/{t_id}/season/{s_id}/statistics/overall"
                                )
                                s["statistics_full"] = full.get(
                                    "statistics", s.get("statistics", {})
                                )
                                time.sleep(0.2)
                            except Exception:
                                s["statistics_full"] = s.get("statistics", {})

                    for obj in [profile_data, stats_data, heatmap_data]:
                        strip_field_translations(obj)
                    if career_data:
                        strip_field_translations(career_data)

                    combined = {
                        "player_id": player_id, "player_name": player_name,
                        "team": team_name, "team_id": team_id,
                        "tournament_id": uniq_tid, "season_id": season_id,
                        "competition_name": competition_name,
                        "competition_country": competition_country,
                        "positions": positions,
                        "profile": profile_data,
                        "statistics": stats_data,
                        "heatmap": heatmap_data,
                    }
                    if career_data:
                        combined["career_statistics"] = career_data

                    with open(json_path, "w", encoding="utf-8") as f:
                        json.dump(combined, f, indent=4, ensure_ascii=False)

                    points = heatmap_data.get("points", [])
                    apps   = stats_data.get("statistics", {}).get("appearances", "?")
                    if points:
                        save_heatmap(
                            points, stats_data.get("statistics", {}),
                            player_name, positions, team_name, png_path
                        )
                        log_fn(f"    JSON + heatmap saved  ({apps} apps)")
                    else:
                        log_fn(f"    JSON saved (no heatmap)  ({apps} apps)")
                    total_players += 1

                except Exception as e:
                    log_fn(f"    Failed: {e}")
                    failed += 1
                    time.sleep(1)

        log_fn("PROGRESS:100")
        log_fn(f"DONE: scraped={total_players}, skipped={skipped}, failed={failed}")

    except Exception as e:
        log_fn(f"ERROR: {e}")
        try:
            driver.quit()
        except Exception:
            pass
        sys.exit(1)

    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CLI league scraper for SofaScore")
    parser.add_argument("--tid",      required=True, help="Tournament ID")
    parser.add_argument("--uniq-tid", required=True, dest="uniq_tid",
                        help="Unique Tournament ID")
    parser.add_argument("--season",   required=True, help="Season ID")
    parser.add_argument("--output",   required=True, help="Output directory path")
    parser.add_argument("--no-skip",  action="store_false", dest="skip",
                        default=True, help="Re-scrape all players even if already saved")
    parser.add_argument("--career",   action="store_true", default=False,
                        help="Include full career statistics (slower)")
    args = parser.parse_args()

    run_scrape(
        tid=args.tid,
        uniq_tid=args.uniq_tid,
        season_id=args.season,
        out_dir=args.output,
        skip_existing=args.skip,
        include_career=args.career,
    )
