import re
import time
import json
import threading
from pathlib import Path
import customtkinter as ctk

from driver  import make_driver
from helpers import fetch_retry, strip_field_translations, safe_name

class PlayerTab:
    def __init__(self, app, tab):
        self.app      = app
        self._running = False
        self._build(tab)

    def _build(self, tab):
        cfg = ctk.CTkFrame(tab)
        cfg.pack(fill="x", pady=(0, 8))

        def cfg_field(label, default, width=120):
            ctk.CTkLabel(cfg, text=label, width=160, anchor="w").pack(side="left", padx=(8, 0))
            var = ctk.StringVar(value=default)
            ctk.CTkEntry(cfg, textvariable=var, width=width).pack(side="left", padx=(0, 12))
            return var

        self.uniq_tid = cfg_field("Unique Tournament ID:", "9")
        self.season   = cfg_field("Season ID:", "77849")

        self.outdir = ctk.StringVar(value=str(Path("output").resolve()))
        self.app.dir_row(tab, self.outdir)

        ctk.CTkLabel(tab, text="Player URLs  (one per line):", anchor="w").pack(fill="x")
        self.url_box = ctk.CTkTextbox(tab, height=130, font=("Courier", 11))
        self.url_box.pack(fill="x", pady=(2, 8))
        self.url_box.insert("end", "https://www.sofascore.com/football/player/toshio-lake/929630\n")

        tog = ctk.CTkFrame(tab, fg_color="transparent")
        tog.pack(fill="x", pady=(0, 8))
        self.career = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(tog, text="Include full career statistics",
                        variable=self.career).pack(side="left", padx=(0, 20))
        self.skip = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(tog, text="Skip players already saved",
                        variable=self.skip).pack(side="left")

        btn_row = ctk.CTkFrame(tab, fg_color="transparent")
        btn_row.pack(fill="x")
        self.start_btn = ctk.CTkButton(btn_row, text="▶  Start", width=120, command=self.start)
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
        self._running = False
        self.lg("⏹  Stop requested — will halt after current player.")

    def start(self):
        urls = [u.strip() for u in self.url_box.get("1.0", "end").strip().splitlines() if u.strip()]
        if not urls:
            self.lg("⚠  No URLs entered.")
            return
        self._running = True
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.progress.set(0)
        threading.Thread(target=self._run, args=(urls,), daemon=True).start()

    def _run(self, urls):
        from heatmap import save_heatmap
        uniq_tid  = self.uniq_tid.get().strip()
        season_id = self.season.get().strip()
        out_dir   = Path(self.outdir.get().strip())
        out_dir.mkdir(parents=True, exist_ok=True)
        do_career = self.career.get()
        do_skip   = self.skip.get()

        players = []
        for url in urls:
            m = re.search(r'/player/([^/]+)/(\d+)', url)
            if m:
                players.append({"slug": m.group(1), "id": m.group(2)})
            else:
                self.lg(f"⚠  Could not parse: {url}")

        if not players:
            self.lg("⚠  No valid player URLs found.")
            self.app.after(0, self._done)
            return

        driver = make_driver()
        try: driver.get("https://www.sofascore.com/football")
        except: pass
        time.sleep(8)

        total = len(players)
        for idx, p in enumerate(players):
            if not self._running:
                break

            player_id   = p["id"]
            player_name = p["slug"].replace("-", " ").title()
            self.app.after(0, lambda v=(idx / total): self.progress.set(v))

            existing = list(out_dir.glob(f"*_{player_id}_*_Season_{season_id}.json"))
            if do_skip and existing:
                self.lg(f"[{idx+1}/{total}] {player_name} → skipped")
                continue

            self.lg(f"\n[{idx+1}/{total}] {player_name} (id={player_id})")

            try:
                base = f"https://api.sofascore.com/api/v1/player/{player_id}/unique-tournament/{uniq_tid}/season/{season_id}"

                self.lg("  fetching profile...")
                profile_data = fetch_retry(driver, f"https://api.sofascore.com/api/v1/player/{player_id}")
                player_name  = profile_data.get("player", {}).get("name", player_name)
                time.sleep(0.3)

                self.lg("  fetching stats...")
                stats_data = fetch_retry(driver, f"{base}/statistics/overall")
                time.sleep(0.3)

                self.lg("  fetching heatmap...")
                heatmap_data = fetch_retry(driver, f"{base}/heatmap/overall")
                heatmap_data.pop("events", None)
                time.sleep(0.3)

                self.lg("  fetching characteristics...")
                char_data = fetch_retry(driver, f"https://api.sofascore.com/api/v1/player/{player_id}/characteristics")
                positions = char_data.get("positions", [])
                time.sleep(0.3)

                competition_name    = "Unknown_Competition"
                competition_country = "Unknown_Country"
                career_data         = None

                if do_career:
                    self.lg("  fetching career...")
                    career_data  = fetch_retry(driver, f"https://api.sofascore.com/api/v1/player/{player_id}/statistics")
                    seasons_list = career_data.get("seasons", [])
                    for s in seasons_list:
                        t_id = s["uniqueTournament"]["id"]
                        s_id = s["season"]["id"]
                        try:
                            full = fetch_retry(driver, f"https://api.sofascore.com/api/v1/player/{player_id}/unique-tournament/{t_id}/season/{s_id}/statistics/overall")
                            s["statistics_full"] = full.get("statistics", s.get("statistics", {}))
                            time.sleep(0.2)
                        except:
                            s["statistics_full"] = s.get("statistics", {})
                    for s in career_data.get("seasons", []):
                        if str(s["uniqueTournament"]["id"]) == str(uniq_tid):
                            competition_name    = safe_name(s["uniqueTournament"]["name"])
                            competition_country = safe_name(s["uniqueTournament"].get("category", {}).get("name", "Unknown_Country"))
                            break

                strip_field_translations(profile_data)
                strip_field_translations(stats_data)
                strip_field_translations(heatmap_data)
                if career_data:
                    strip_field_translations(career_data)

                team_name   = stats_data.get("team", {}).get("name", "Unknown")
                s_stats     = stats_data.get("statistics", {})
                safe_player = safe_name(player_name)

                combined = {
                    "player_id": player_id, "player_name": player_name,
                    "team": team_name, "tournament_id": uniq_tid, "season_id": season_id,
                    "competition_name": competition_name, "competition_country": competition_country,
                    "positions": positions, "profile": profile_data,
                    "statistics": stats_data, "heatmap": heatmap_data,
                }
                if career_data:
                    combined["career_statistics"] = career_data

                json_path = out_dir / f"{safe_player}_{player_id}_{competition_name}_{uniq_tid}_Season_{season_id}.json"
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(combined, f, indent=4, ensure_ascii=False)

                points = heatmap_data.get("points", [])
                if points:
                    png_path = out_dir / f"{safe_player}_{player_id}_{competition_name}_{uniq_tid}_Season_{season_id}_heatmap.png"
                    save_heatmap(points, s_stats, player_name, positions, team_name, png_path)
                    self.lg(f"  ✓ JSON + heatmap  ({s_stats.get('appearances','?')} apps, {s_stats.get('goals','?')}G {s_stats.get('assists','?')}A)")
                else:
                    self.lg(f"  ✓ JSON saved (no heatmap)")

            except Exception as e:
                self.lg(f"  ✗ failed: {e}")
                time.sleep(1)

        driver.quit()
        self.app.after(0, lambda: self.progress.set(1))
        self.lg("\n✅  All done!")
        self.app.after(0, self._done)

    def _done(self):
        self._running = False
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")