import time
import json
import threading
from pathlib import Path
import customtkinter as ctk

from driver  import make_driver
from helpers import fetch_retry, strip_field_translations, safe_name, extract_competition_info
from heatmap import save_heatmap

class LeagueTab:
    def __init__(self, app, tab):
        self.app             = app
        self._league_running = False
        self._build(tab)

    def _build(self, tab):
        cfg = ctk.CTkFrame(tab)
        cfg.pack(fill="x", pady=(0, 8))

        def cfg_field(label, default, width=120):
            ctk.CTkLabel(cfg, text=label, width=190, anchor="w").pack(side="left", padx=(8, 0))
            var = ctk.StringVar(value=default)
            ctk.CTkEntry(cfg, textvariable=var, width=width).pack(side="left", padx=(0, 12))
            return var

        self.tid      = cfg_field("Tournament ID:", "692")
        self.uniq_tid = cfg_field("Unique Tournament ID:", "9")
        self.season   = cfg_field("Season ID:", "77849")

        self.outdir = ctk.StringVar(value=str(Path("output").resolve()))
        self.app.dir_row(tab, self.outdir)

        tog = ctk.CTkFrame(tab, fg_color="transparent")
        tog.pack(fill="x", pady=(0, 8))
        self.skip = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(tog, text="Skip players/clubs already saved",
                        variable=self.skip).pack(side="left")
        self.career = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(tog, text="Include full career statistics (slower)",
                        variable=self.career).pack(side="left", padx=(20, 0))

        btn_row = ctk.CTkFrame(tab, fg_color="transparent")
        btn_row.pack(fill="x")
        self.start_btn = ctk.CTkButton(btn_row, text="▶  Start League Scrape", width=180,
                                        command=self.start)
        self.start_btn.pack(side="left")
        self.stop_btn = ctk.CTkButton(btn_row, text="■  Stop", width=100,
                                       fg_color="#c0392b", hover_color="#922b21",
                                       command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=8)
        self.progress = ctk.CTkProgressBar(btn_row)
        self.progress.pack(side="left", fill="x", expand=True, padx=8)
        self.progress.set(0)

        self.log_box = self.app.log_box(tab)

    def lg(self, msg): self.app.after(0, lambda m=msg: self.app.log(self.log_box, m))

    def stop(self):
        self._league_running = False
        self.lg("⏹  Stop requested — will halt after current player.")

    def start(self):
        self._league_running = True
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.progress.set(0)
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        tid       = self.tid.get().strip()
        uniq_tid  = self.uniq_tid.get().strip()
        season_id = self.season.get().strip()
        out_dir   = Path(self.outdir.get().strip())
        do_skip   = self.skip.get()
        do_career = self.career.get()

        total_players = 0 ; skipped = 0 ; failed = 0

        driver = make_driver()
        try: driver.get("https://www.sofascore.com/football")
        except: pass
        time.sleep(8)
        self.lg("Browser ready.")

        try:
            self.lg("Fetching standings...")
            standings_data = fetch_retry(
                driver,
                f"https://api.sofascore.com/api/v1/tournament/{tid}/season/{season_id}/standings/total"
            )
            strip_field_translations(standings_data)
            for group in standings_data.get("standings", []):
                for row in group.get("rows", []):
                    row["team_id"] = row["team"]["id"]

            competition_name, competition_country = extract_competition_info(standings_data)
            competition_dir = out_dir / competition_country / competition_name
            competition_dir.mkdir(parents=True, exist_ok=True)

            with open(competition_dir / f"Standings_{competition_name}_{tid}_Season_{season_id}.json", "w", encoding="utf-8") as f:
                json.dump(standings_data, f, indent=4, ensure_ascii=False)

            self.lg(f"\nCompetition: {competition_name}  |  Country: {competition_country}")
            col_hdr = f"{'Pos':<4} {'Team':<35} {'ID':<10} {'P':<4} {'W':<4} {'D':<4} {'L':<4} {'GF':<5} {'GA':<5} {'GD':<6} Pts"
            self.lg(col_hdr) ; self.lg("-" * 95)

            teams = []
            for group in standings_data.get("standings", []):
                for row in group.get("rows", []):
                    promotion  = row.get("promotion", {}).get("text", "")
                    marker     = " ↑" if promotion == "Promotion" else " ↗" if "Playoffs" in promotion else " ↓" if "Relegation" in promotion else ""
                    deductions = " *" if row.get("descriptions") else ""
                    self.lg(
                        f"{row['position']:<4} {row['team']['name'][:34]:<35} {row['team_id']:<10} "
                        f"{row['matches']:<4} {row['wins']:<4} {row['draws']:<4} {row['losses']:<4} "
                        f"{row['scoresFor']:<5} {row['scoresAgainst']:<5} {row['scoreDiffFormatted']:<6} "
                        f"{row['points']}{marker}{deductions}"
                    )
                    teams.append({"id": str(row["team_id"]), "name": row["team"]["name"]})

            total_teams = len(teams)
            self.lg(f"\nFound {total_teams} teams. Starting scrape...\n")

            for team_idx, team in enumerate(teams):
                if not self._league_running: break

                team_id   = team["id"]
                team_name = team["name"]
                safe_team = safe_name(team_name)
                self.app.after(0, lambda v=(team_idx / total_teams): self.progress.set(v))

                club_dir     = competition_dir / safe_team
                players_dir  = club_dir / "Players"
                heatmaps_dir = club_dir / "Heatmaps"
                club_dir.mkdir(parents=True, exist_ok=True)
                players_dir.mkdir(exist_ok=True)
                heatmaps_dir.mkdir(exist_ok=True)

                self.lg(f"\n{'='*50}")
                self.lg(f"[{team_idx+1}/{total_teams}] {team_name}")

                # Club data
                club_json = club_dir / f"Club_{team_id}_{safe_team}_{uniq_tid}_Season_{season_id}.json"
                if do_skip and club_json.exists():
                    self.lg("  Club data already exists, skipping.")
                else:
                    try:
                        self.lg("  Fetching club profile...")
                        club_profile = fetch_retry(driver, f"https://api.sofascore.com/api/v1/team/{team_id}")
                        time.sleep(0.3)
                        self.lg("  Fetching club season stats...")
                        club_stats = fetch_retry(driver, f"https://api.sofascore.com/api/v1/team/{team_id}/unique-tournament/{uniq_tid}/season/{season_id}/statistics/overall")
                        time.sleep(0.3)
                        strip_field_translations(club_profile)
                        strip_field_translations(club_stats)
                        with open(club_json, "w", encoding="utf-8") as f:
                            json.dump({
                                "team_id": team_id, "team_name": team_name,
                                "tournament_id": uniq_tid, "season_id": season_id,
                                "competition_name": competition_name, "competition_country": competition_country,
                                "profile": club_profile, "season_statistics": club_stats
                            }, f, indent=4, ensure_ascii=False)
                        self.lg("  ✓ Club JSON saved")
                    except Exception as e:
                        self.lg(f"  ✗ Club data failed: {e}")

                # Squad
                try:
                    squad = fetch_retry(driver, f"https://api.sofascore.com/api/v1/team/{team_id}/players").get("players", [])
                    self.lg(f"  Squad: {len(squad)} players")
                except Exception as e:
                    self.lg(f"  ✗ Squad fetch failed: {e}") ; continue

                # Per player
                for p_idx, entry in enumerate(squad):
                    if not self._league_running: break

                    player      = entry.get("player", entry)
                    player_id   = str(player["id"])
                    player_name = player.get("name", f"Player_{player_id}")
                    safe_player = safe_name(player_name)

                    json_path = players_dir  / f"{safe_player}_{player_id}_{safe_team}_{competition_name}_{uniq_tid}_Season_{season_id}.json"
                    png_path  = heatmaps_dir / f"{safe_player}_{player_id}_{safe_team}_{competition_name}_{uniq_tid}_Season_{season_id}_heatmap.png"

                    if do_skip and json_path.exists():
                        self.lg(f"  [{p_idx+1}/{len(squad)}] {player_name} → skipped")
                        skipped += 1 ; continue

                    self.lg(f"  [{p_idx+1}/{len(squad)}] {player_name} (id={player_id})...")

                    try:
                        base = f"https://api.sofascore.com/api/v1/player/{player_id}/unique-tournament/{uniq_tid}/season/{season_id}"

                        profile_data = fetch_retry(driver, f"https://api.sofascore.com/api/v1/player/{player_id}")
                        player_name  = profile_data.get("player", {}).get("name", player_name)
                        time.sleep(0.3)
                        stats_data   = fetch_retry(driver, f"{base}/statistics/overall") ; time.sleep(0.3)
                        heatmap_data = fetch_retry(driver, f"{base}/heatmap/overall")
                        heatmap_data.pop("events", None) ; time.sleep(0.3)
                        char_data    = fetch_retry(driver, f"https://api.sofascore.com/api/v1/player/{player_id}/characteristics")
                        positions    = char_data.get("positions", []) ; time.sleep(0.3)

                        career_data = None
                        if do_career:
                            career_data  = fetch_retry(driver, f"https://api.sofascore.com/api/v1/player/{player_id}/statistics")
                            seasons_list = career_data.get("seasons", []) ; time.sleep(0.3)
                            for s in seasons_list:
                                t_id = s["uniqueTournament"]["id"] ; s_id = s["season"]["id"]
                                try:
                                    full = fetch_retry(driver, f"https://api.sofascore.com/api/v1/player/{player_id}/unique-tournament/{t_id}/season/{s_id}/statistics/overall")
                                    s["statistics_full"] = full.get("statistics", s.get("statistics", {}))
                                    time.sleep(0.2)
                                except:
                                    s["statistics_full"] = s.get("statistics", {})

                        for obj in [profile_data, stats_data, heatmap_data]:
                            strip_field_translations(obj)
                        if career_data:
                            strip_field_translations(career_data)

                        combined = {
                            "player_id": player_id, "player_name": player_name,
                            "team": team_name, "team_id": team_id,
                            "tournament_id": uniq_tid, "season_id": season_id,
                            "competition_name": competition_name, "competition_country": competition_country,
                            "positions": positions, "profile": profile_data,
                            "statistics": stats_data, "heatmap": heatmap_data,
                        }
                        if career_data:
                            combined["career_statistics"] = career_data

                        with open(json_path, "w", encoding="utf-8") as f:
                            json.dump(combined, f, indent=4, ensure_ascii=False)

                        points = heatmap_data.get("points", [])
                        apps   = stats_data.get("statistics", {}).get("appearances", "?")
                        if points:
                            save_heatmap(points, stats_data.get("statistics", {}), player_name, positions, team_name, png_path)
                            self.lg(f"  ✓ JSON + heatmap  ({apps} apps)")
                        else:
                            self.lg(f"  ✓ JSON saved (no heatmap)  ({apps} apps)")
                        total_players += 1

                    except Exception as e:
                        self.lg(f"  ✗ failed: {e}") ; failed += 1 ; time.sleep(1)

        except Exception as e:
            self.lg(f"✗ Failed: {e}")

        try: driver.quit()
        except: pass

        self.app.after(0, lambda: self.progress.set(1))
        self.lg(f"\n✅  Done — {total_players} saved, {skipped} skipped, {failed} failed.")
        self.app.after(0, self._done)

    def _done(self):
        self._league_running = False
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")