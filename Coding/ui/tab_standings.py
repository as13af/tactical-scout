import time
import json
import threading
from pathlib import Path
import customtkinter as ctk

from driver  import make_driver
from helpers import fetch_retry, strip_field_translations, extract_competition_info

class StandingsTab:
    def __init__(self, app, tab):
        self.app = app
        self._build(tab)

    def _build(self, tab):
        cfg = ctk.CTkFrame(tab)
        cfg.pack(fill="x", pady=(0, 8))

        def cfg_field(label, default, width=160):
            ctk.CTkLabel(cfg, text=label, width=160, anchor="w").pack(side="left", padx=(8, 0))
            var = ctk.StringVar(value=default)
            ctk.CTkEntry(cfg, textvariable=var, width=width).pack(side="left", padx=(0, 12))
            return var

        self.tid    = cfg_field("Tournament ID:", "692")
        self.season = cfg_field("Season ID:", "77849")

        self.outdir = ctk.StringVar(value=str(Path("output").resolve()))
        self.app.dir_row(tab, self.outdir)

        btn_row = ctk.CTkFrame(tab, fg_color="transparent")
        btn_row.pack(fill="x", pady=8)
        self.start_btn = ctk.CTkButton(btn_row, text="▶  Fetch Standings", width=160,
                                        command=self.start)
        self.start_btn.pack(side="left")
        self.progress = ctk.CTkProgressBar(btn_row, mode="indeterminate")
        self.progress.pack(side="left", fill="x", expand=True, padx=8)

        self.log_box = self.app.log_box(tab)

    def lg(self, msg): self.app.after(0, lambda m=msg: self.app.log(self.log_box, m))

    def start(self):
        self.start_btn.configure(state="disabled")
        self.progress.start()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        tid       = self.tid.get().strip()
        season_id = self.season.get().strip()
        out_dir   = Path(self.outdir.get().strip())
        out_dir.mkdir(parents=True, exist_ok=True)

        driver = make_driver()
        try: driver.get("https://www.sofascore.com/football")
        except: pass
        time.sleep(8)

        try:
            self.lg("Fetching standings...")
            standings_data = fetch_retry(
                driver,
                f"https://api.sofascore.com/api/v1/tournament/{tid}/season/{season_id}/standings/total"
            )
            driver.quit()

            strip_field_translations(standings_data)
            for group in standings_data.get("standings", []):
                for row in group.get("rows", []):
                    row["team_id"] = row["team"]["id"]

            competition_name, competition_country = extract_competition_info(standings_data)
            competition_dir = out_dir / competition_country / competition_name
            competition_dir.mkdir(parents=True, exist_ok=True)

            self.lg(f"\nCompetition: {competition_name}  |  Country: {competition_country}")
            col_hdr = f"{'Pos':<4} {'Team':<35} {'ID':<10} {'P':<4} {'W':<4} {'D':<4} {'L':<4} {'GF':<5} {'GA':<5} {'GD':<6} Pts"
            self.lg(col_hdr) ; self.lg("-" * 95)

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

            out_path = competition_dir / f"Standings_{competition_name}_{tid}_Season_{season_id}.json"
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(standings_data, f, indent=4, ensure_ascii=False)
            self.lg(f"\n✅  Saved → {out_path.name}")

        except Exception as e:
            self.lg(f"✗ Failed: {e}")
            try: driver.quit()
            except: pass

        self.app.after(0, self._done)

    def _done(self):
        self.start_btn.configure(state="normal")
        self.progress.stop()
        self.progress.set(0)