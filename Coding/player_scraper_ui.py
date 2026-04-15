import json
import time
import re
import threading
import queue
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from pathlib import Path

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
import numpy as np
from scipy.ndimage import gaussian_filter

from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager


class PlayerScraperApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Player Heatmap Scraper")
        self.root.geometry("960x680")
        self.root.resizable(True, True)

        self.log_queue: queue.Queue = queue.Queue()
        self.fetch_data: dict = {}
        self.season_vars: list = []   # list of (BooleanVar, season_dict)

        self._build_ui()
        self._poll_queue()

    # ── UI Construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        # ── Input frame ──────────────────────────────────────────────────────
        input_frame = ttk.LabelFrame(self.root, text="Player Input", padding=10)
        input_frame.pack(fill="x", padx=12, pady=(10, 4))

        # URL row
        ttk.Label(input_frame, text="SofaScore URL:").grid(row=0, column=0, sticky="w", pady=2)
        self.url_var = tk.StringVar(value="https://www.sofascore.com/football/player/toshio-lake/929630#tab:season")
        url_entry = ttk.Entry(input_frame, textvariable=self.url_var, width=72)
        url_entry.grid(row=0, column=1, columnspan=5, padx=(6, 0), sticky="ew", pady=2)
        url_entry.bind("<FocusOut>", self._on_url_changed)
        url_entry.bind("<Return>",   self._on_url_changed)

        # Manual ID row
        ttk.Label(input_frame, text="Player ID:").grid(row=1, column=0, sticky="w", pady=(6, 2))
        self.pid_var = tk.StringVar()
        ttk.Entry(input_frame, textvariable=self.pid_var, width=12).grid(row=1, column=1, padx=(6, 12), sticky="w")

        ttk.Label(input_frame, text="Tournament ID:").grid(row=1, column=2, sticky="w")
        self.tid_var = tk.StringVar()
        ttk.Entry(input_frame, textvariable=self.tid_var, width=10).grid(row=1, column=3, padx=(6, 12), sticky="w")

        ttk.Label(input_frame, text="Season ID:").grid(row=1, column=4, sticky="w")
        self.sid_var = tk.StringVar()
        ttk.Entry(input_frame, textvariable=self.sid_var, width=10).grid(row=1, column=5, padx=(6, 0), sticky="w")

        ttk.Label(
            input_frame,
            text="IDs are auto-detected from the URL. Fill them manually only if you want to skip browser navigation.",
            foreground="gray"
        ).grid(row=2, column=0, columnspan=6, sticky="w", pady=(2, 0))

        input_frame.columnconfigure(1, weight=1)

        # ── Main area: log + seasons panel ───────────────────────────────────
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill="both", expand=True, padx=12, pady=4)

        # Progress log
        log_frame = ttk.LabelFrame(main_frame, text="Progress Log", padding=6)
        log_frame.pack(side="left", fill="both", expand=True)

        self.log_box = scrolledtext.ScrolledText(
            log_frame, state="disabled", wrap="word",
            height=20, font=("Consolas", 9), bg="#f7f7f7"
        )
        self.log_box.pack(fill="both", expand=True)

        # Seasons panel
        seasons_outer = ttk.LabelFrame(main_frame, text="Career Seasons  (uncheck to remove)", padding=6)
        seasons_outer.pack(side="right", fill="y", padx=(8, 0))

        self.seasons_canvas = tk.Canvas(seasons_outer, width=280, highlightthickness=0, bg="#ffffff")
        s_scroll = ttk.Scrollbar(seasons_outer, orient="vertical", command=self.seasons_canvas.yview)
        self.seasons_canvas.configure(yscrollcommand=s_scroll.set)
        s_scroll.pack(side="right", fill="y")
        self.seasons_canvas.pack(side="left", fill="both", expand=True)

        self.seasons_inner = ttk.Frame(self.seasons_canvas)
        self.seasons_canvas.create_window((0, 0), window=self.seasons_inner, anchor="nw")
        self.seasons_inner.bind(
            "<Configure>",
            lambda e: self.seasons_canvas.configure(scrollregion=self.seasons_canvas.bbox("all"))
        )

        # ── Bottom bar ───────────────────────────────────────────────────────
        bottom = ttk.Frame(self.root)
        bottom.pack(fill="x", padx=12, pady=(4, 10))

        self.fetch_btn = ttk.Button(bottom, text="Fetch Data", command=self.on_fetch, width=14)
        self.fetch_btn.pack(side="left", padx=(0, 8))

        self.generate_btn = ttk.Button(
            bottom, text="Generate JSON & Heatmap",
            command=self.on_generate, state="disabled", width=24
        )
        self.generate_btn.pack(side="left")

        self.status_var = tk.StringVar(value="Paste a SofaScore player URL above and click Fetch Data.")
        ttk.Label(bottom, textvariable=self.status_var, foreground="gray").pack(side="right")

        # Pre-fill Player ID from default URL
        self._on_url_changed()

    # ── URL → Player ID auto-parse ────────────────────────────────────────────

    def _on_url_changed(self, _event=None):
        url = self.url_var.get().strip()
        m = re.search(r'/player/[^/]+/(\d+)', url)
        if m and not self.pid_var.get():
            self.pid_var.set(m.group(1))

    # ── Logging (thread-safe) ─────────────────────────────────────────────────

    def log(self, msg: str):
        self.log_queue.put(msg)

    def _append_log(self, msg: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _poll_queue(self):
        while not self.log_queue.empty():
            self._append_log(self.log_queue.get_nowait())
        self.root.after(100, self._poll_queue)

    # ── Fetch ─────────────────────────────────────────────────────────────────

    def on_fetch(self):
        for w in self.seasons_inner.winfo_children():
            w.destroy()
        self.season_vars.clear()
        self.generate_btn.configure(state="disabled")
        self.fetch_btn.configure(state="disabled")
        self.status_var.set("Fetching…")
        threading.Thread(target=self._fetch_worker, daemon=True).start()

    def _fetch_worker(self):
        try:
            self._do_fetch()
        except Exception as e:
            self.log(f"ERROR: {e}")
            self.root.after(0, lambda: self.fetch_btn.configure(state="normal"))
            self.root.after(0, lambda: self.status_var.set("Fetch failed — see log."))

    def _do_fetch(self):
        url_input    = self.url_var.get().strip()
        manual_pid   = self.pid_var.get().strip()
        manual_tid   = self.tid_var.get().strip()
        manual_sid   = self.sid_var.get().strip()

        # ── If all three IDs are provided manually, skip browser navigation ──
        skip_browser = bool(manual_pid and manual_tid and manual_sid)

        player_id    = manual_pid  or None
        tournament_id = manual_tid or None
        season_id    = manual_sid  or None
        player_name  = "Player"

        driver = None

        if not skip_browser:
            # Build navigation URL
            if url_input.startswith("http"):
                nav_url = url_input.split("#")[0] + "#tab:season"
            elif manual_pid:
                nav_url = f"https://www.sofascore.com/football/player/player/{manual_pid}#tab:season"
            else:
                nav_url = "https://www.sofascore.com/football/player/toshio-lake/929630#tab:season"

            self.log("Starting browser…")
            options = webdriver.ChromeOptions()
            options.set_capability("goog:loggingPrefs", {"performance": "ALL", "browser": "ALL"})
            driver = webdriver.Chrome(
                service=ChromeService(ChromeDriverManager().install()), options=options
            )
            driver.set_page_load_timeout(15)

            try:
                driver.get(nav_url)
            except Exception:
                pass

            time.sleep(3)
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)

            # Try resource entries first
            resource_logs = driver.execute_script("return window.performance.getEntriesByType('resource');")
            for entry in resource_logs:
                url = entry.get("name", "")
                if "/api/v1/player/" in url and "/unique-tournament/" in url and "/season/" in url:
                    parts = url.split("/")
                    try:
                        player_id     = parts[parts.index("player") + 1]
                        tournament_id = parts[parts.index("unique-tournament") + 1]
                        season_id     = parts[parts.index("season") + 1]
                        self.log(f"Found (resource): player={player_id}, tournament={tournament_id}, season={season_id}")
                        break
                    except (ValueError, IndexError):
                        continue

            # Fallback: CDP logs
            if not all([player_id, tournament_id, season_id]):
                self.log("Checking CDP logs…")
                for log_entry in driver.get_log("performance"):
                    try:
                        msg = json.loads(log_entry["message"])["message"]
                        if msg.get("method") == "Network.requestWillBeSent":
                            url = msg["params"]["request"]["url"]
                            if "/api/v1/player/" in url and "/unique-tournament/" in url and "/season/" in url:
                                parts = url.split("/")
                                player_id     = parts[parts.index("player") + 1]
                                tournament_id = parts[parts.index("unique-tournament") + 1]
                                season_id     = parts[parts.index("season") + 1]
                                self.log(f"Found (CDP): player={player_id}, tournament={tournament_id}, season={season_id}")
                                break
                    except (ValueError, IndexError, KeyError):
                        continue

            # Extract player name from current URL
            m = re.search(r'/player/([^/]+)/\d+', driver.current_url)
            if m:
                player_name = m.group(1).replace("-", " ").title()

            # Last resort: extract player_id from the URL string
            if not player_id:
                m2 = re.search(r'/player/[^/]+/(\d+)', url_input)
                if m2:
                    player_id = m2.group(1)

        # Final fallback defaults
        if not all([player_id, tournament_id, season_id]):
            self.log("Could not extract all IDs — using fallback defaults.")
            player_id     = player_id     or "929630"
            tournament_id = tournament_id or "9"
            season_id     = season_id     or "77849"

        # Update ID fields in UI
        self.root.after(0, lambda: self.pid_var.set(player_id))
        self.root.after(0, lambda: self.tid_var.set(tournament_id))
        self.root.after(0, lambda: self.sid_var.set(season_id))

        # ── API fetching ──────────────────────────────────────────────────────
        def browser_fetch(url: str):
            resp = driver.execute_async_script(f"""
                const callback = arguments[arguments.length - 1];
                (async () => {{
                    try {{
                        const r = await fetch("{url}", {{
                            headers: {{
                                "Accept": "application/json",
                                "Referer": "https://www.sofascore.com/"
                            }}
                        }});
                        callback(await r.text());
                    }} catch(e) {{ callback("ERROR: " + e); }}
                }})();
            """)
            return json.loads(resp)

        base = f"https://api.sofascore.com/api/v1/player/{player_id}/unique-tournament/{tournament_id}/season/{season_id}"

        self.log("Fetching stats…")
        stats_data = browser_fetch(f"{base}/statistics/overall")

        self.log("Fetching heatmap…")
        heatmap_data = browser_fetch(f"{base}/heatmap/overall")

        self.log("Fetching characteristics…")
        char_data    = browser_fetch(f"https://api.sofascore.com/api/v1/player/{player_id}/characteristics")
        positions    = char_data.get("positions", [])
        position_str = ", ".join(positions) if positions else "N/A"
        self.log(f"Positions: {position_str}")

        self.log("Fetching career statistics…")
        career_data = browser_fetch(f"https://api.sofascore.com/api/v1/player/{player_id}/statistics")
        seasons     = career_data.get("seasons", [])
        self.log(f"Career seasons found: {len(seasons)}")

        self.log("Fetching full stats per season…")
        for i, s in enumerate(seasons):
            t_id = s["uniqueTournament"]["id"]
            s_id = s["season"]["id"]
            url  = f"https://api.sofascore.com/api/v1/player/{player_id}/unique-tournament/{t_id}/season/{s_id}/statistics/overall"
            try:
                full = browser_fetch(url)
                s["statistics_full"] = full.get("statistics", s["statistics"])
                self.log(f"  [{i}] {s['year']} | {s['team']['name']} → OK")
            except Exception as e:
                self.log(f"  [{i}] {s['year']} | {s['team']['name']} → failed: {e}")
                s["statistics_full"] = s["statistics"]

        if driver:
            driver.quit()
            self.log("Browser closed.")

        self.fetch_data = {
            "player_id":    player_id,
            "tournament_id": tournament_id,
            "season_id":    season_id,
            "player_name":  player_name,
            "positions":    positions,
            "position_str": position_str,
            "stats_data":   stats_data,
            "heatmap_data": heatmap_data,
            "career_data":  career_data,
            "seasons":      seasons,
        }

        self.root.after(0, self._populate_seasons)

    # ── Seasons panel ─────────────────────────────────────────────────────────

    def _populate_seasons(self):
        for w in self.seasons_inner.winfo_children():
            w.destroy()
        self.season_vars.clear()

        seasons = self.fetch_data.get("seasons", [])
        for i, s in enumerate(seasons):
            sf  = s.get("statistics_full", s["statistics"])
            yr  = s["year"]
            t   = s["team"]["name"]
            lg  = s["uniqueTournament"]["name"]
            app = sf.get("appearances", 0)
            g   = sf.get("goals", 0)
            a   = sf.get("assists", 0)
            r   = sf.get("rating", 0.0)

            var = tk.BooleanVar(value=True)
            label = f"[{i}] {yr}  {t}\n      {lg}\n      {app} apps  {g}G  {a}A  {r:.2f}★"
            ttk.Checkbutton(self.seasons_inner, text=label, variable=var).pack(
                anchor="w", padx=6, pady=3
            )
            self.season_vars.append((var, s))

        self.generate_btn.configure(state="normal")
        self.fetch_btn.configure(state="normal")
        self.status_var.set(f"Fetched {len(seasons)} season(s). Review seasons, then click Generate.")
        self.log(f"\n✓ {len(seasons)} seasons loaded. Uncheck any you want to exclude, then Generate.")

    # ── Generate JSON & Heatmap ───────────────────────────────────────────────

    def on_generate(self):
        if not self.fetch_data:
            messagebox.showwarning("No data", "Fetch player data first.")
            return

        kept = [s for var, s in self.season_vars if var.get()]
        self.fetch_data["career_data"]["seasons"] = kept
        self.log(f"\nGenerating with {len(kept)} season(s) kept…")
        self._generate()

    def _generate(self):
        d            = self.fetch_data
        player_id    = d["player_id"]
        tournament_id = d["tournament_id"]
        season_id    = d["season_id"]
        player_name  = d["player_name"]
        position_str = d["position_str"]
        stats_data   = d["stats_data"]
        heatmap_data = d["heatmap_data"]
        career_data  = d["career_data"]

        # Strip fieldTranslations recursively
        def strip_ft(obj):
            if isinstance(obj, dict):
                obj.pop("fieldTranslations", None)
                for v in obj.values():
                    strip_ft(v)
            elif isinstance(obj, list):
                for item in obj:
                    strip_ft(item)

        strip_ft(stats_data)
        strip_ft(heatmap_data)
        strip_ft(career_data)
        heatmap_data.pop("events", None)

        # Competition name
        competition_name = "Unknown_Competition"
        for s in career_data.get("seasons", []):
            if str(s["uniqueTournament"]["id"]) == str(tournament_id):
                raw = s["uniqueTournament"]["name"]
                competition_name = re.sub(r'_+', '_', re.sub(r'[^\w]', '_', raw)).strip("_")
                break

        stats  = stats_data["statistics"]
        team   = stats_data["team"]["name"]
        points = heatmap_data["points"]

        output_dir = Path("output")
        output_dir.mkdir(exist_ok=True)
        base_name = f"Player_{player_id}_{competition_name}_{tournament_id}_Season_{season_id}"

        combined = {
            "player_id":         player_id,
            "tournament_id":     tournament_id,
            "season_id":         season_id,
            "positions":         d["positions"],
            "statistics":        stats_data,
            "heatmap":           heatmap_data,
            "career_statistics": career_data,
        }

        json_path = output_dir / f"{base_name}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(combined, f, indent=4, ensure_ascii=False)
        self.log(f"Saved JSON → {json_path}")
        self.log(f"Player: {player_name} ({position_str}) | Team: {team}")
        self.log(f"Goals: {stats['goals']} | Assists: {stats['assists']} | Rating: {stats['rating']:.2f}")

        # Build heatmap grid
        grid = np.zeros((100, 100))
        for p in points:
            x, y, count = int(p["x"]), int(p["y"]), p["count"]
            x, y = min(x, 99), min(y, 99)
            if 0 <= x <= 99 and 0 <= y <= 99:
                grid[y, x] += count
        grid_smooth = gaussian_filter(grid, sigma=4)

        # Heatmap window
        win = tk.Toplevel(self.root)
        win.title(f"Heatmap — {player_name}")

        fig, ax = plt.subplots(figsize=(10, 7))
        fig.patch.set_facecolor("white")
        ax.set_facecolor("white")

        hm = ax.imshow(grid_smooth, origin="lower", extent=[0, 100, 0, 100],
                       cmap="Greens", alpha=0.85, aspect="auto")
        plt.colorbar(hm, ax=ax, label="Activity density")

        lc, lw = "black", 1.5
        ax.add_patch(patches.Rectangle((0, 0),       100,  100,  fill=False, edgecolor=lc, linewidth=lw))
        ax.axvline(50, color=lc, linewidth=lw)
        ax.add_patch(plt.Circle((50, 50), 9.15, color=lc, fill=False, linewidth=lw))
        ax.plot(50, 50, "o", color=lc, markersize=3)
        ax.add_patch(patches.Rectangle((0, 21.1),    16.5, 57.8, fill=False, edgecolor=lc, linewidth=lw))
        ax.add_patch(patches.Rectangle((83.5, 21.1), 16.5, 57.8, fill=False, edgecolor=lc, linewidth=lw))
        ax.add_patch(patches.Rectangle((0, 36.8),    5.5,  26.4, fill=False, edgecolor=lc, linewidth=lw))
        ax.add_patch(patches.Rectangle((94.5, 36.8), 5.5,  26.4, fill=False, edgecolor=lc, linewidth=lw))
        ax.add_patch(patches.Rectangle((-2, 44.2),   2,    11.6, fill=False, edgecolor=lc, linewidth=lw))
        ax.add_patch(patches.Rectangle((100, 44.2),  2,    11.6, fill=False, edgecolor=lc, linewidth=lw))
        ax.plot(11, 50, "o", color=lc, markersize=3)
        ax.plot(89, 50, "o", color=lc, markersize=3)
        ax.set_xlim(-3, 103)
        ax.set_ylim(-3, 103)
        ax.axis("off")
        ax.set_title(
            f"{player_name} ({position_str}) — Heatmap | {team}\n"
            f"Apps: {stats['appearances']}  |  Goals: {stats['goals']}  |  "
            f"Assists: {stats['assists']}  |  Rating: {stats['rating']:.2f}",
            color="black", fontsize=13, pad=12
        )
        plt.tight_layout()

        canvas = FigureCanvasTkAgg(fig, master=win)
        canvas.draw()
        NavigationToolbar2Tk(canvas, win).update()
        canvas.get_tk_widget().pack(fill="both", expand=True)

        png_path = output_dir / f"{base_name}_heatmap.png"
        fig.savefig(png_path, dpi=150, bbox_inches="tight")
        self.log(f"Saved heatmap → {png_path}")
        self.status_var.set(f"Done! → {json_path.name}")


if __name__ == "__main__":
    root = tk.Tk()
    PlayerScraperApp(root)
    root.mainloop()
