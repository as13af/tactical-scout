"""
SofaScore Match Statistics Scraper — customtkinter UI.

Supports single and bulk mode. Drop any number of match URLs / event IDs into
the queue, run the scraper, and get one combined JSON file in match_output/.
"""

import json
import os
import queue
import re
import subprocess
import sys
import threading

import customtkinter as ctk
from tkinter import messagebox, ttk
import tkinter as tk

from match_stats_scraper import (
    extract_event_id,
    run_scraper,
    run_bulk_scraper,
)
from team_formation_scraper import (
    extract_team_id,
    extract_league_ids,
    scrape_team_formations,
)

# ── Theme ─────────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

_OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "match_output")

# ── Module-level helpers ──────────────────────────────────────────────────────

def _open_in_file_manager(path: str, select: bool = False) -> None:
    """Open *path* in the OS file manager.  Works on Windows, macOS, and Linux."""
    if sys.platform == "win32":
        if select:
            subprocess.Popen(["explorer", "/select,", path])
        else:
            os.startfile(path)
    elif sys.platform == "darwin":
        args = ["open", "-R", path] if select else ["open", os.path.dirname(path)]
        subprocess.Popen(args)
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path)])


def _sanitise_filename(name: str) -> str:
    """Strip characters that are illegal in filenames on Windows/macOS/Linux."""
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = name.strip(". ")
    return name or "output"

# Treeview row tags
_TAG_GROUP  = "group_header"
_TAG_EVEN   = "row_even"
_TAG_DONE   = "status_done"
_TAG_FAIL   = "status_fail"
_TAG_PEND   = "status_pend"
_TAG_ACTIVE = "status_active"


# ── App ───────────────────────────────────────────────────────────────────────

class MatchStatsScraperApp(ctk.CTk):
    """Main application window for the SofaScore match statistics scraper."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Match Statistics Scraper — SofaScore")
        self.geometry("1100x760")
        self.resizable(True, True)
        self.minsize(820, 580)

        self._log_queue: queue.Queue = queue.Queue()
        self._output_path: str | None = None
        self._scraped_data: dict | None = None   # last bulk payload
        self._current_match: dict | None = None
        self._is_scraping: bool = False
        self._cancel_event = threading.Event()

        self._apply_tree_style()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Control-Return>", lambda _: self._on_run())
        self.bind("<Control-r>",      lambda _: self._on_run())
        self.bind("<Delete>",         lambda _: self._on_remove_selected())
        self._poll_queue()

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_input_card()
        self._build_action_bar()
        self._build_tab_area()

    # ── Input card ────────────────────────────────────────────────────────────

    def _build_input_card(self) -> None:
        card = ctk.CTkFrame(self, corner_radius=12)
        card.grid(row=0, column=0, padx=18, pady=(18, 6), sticky="ew")
        card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            card, text="Match Statistics Scraper — Bulk Mode",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, columnspan=4, padx=16, pady=(14, 6))

        # ── Single-add row ────────────────────────────────────────────────
        ctk.CTkLabel(card, text="Add Match:", font=ctk.CTkFont(size=13)).grid(
            row=1, column=0, padx=(16, 8), pady=4, sticky="w")

        self._input_var = ctk.StringVar()
        self._input_entry = ctk.CTkEntry(
            card, textvariable=self._input_var, height=34,
            placeholder_text=(
                "Event ID  or  https://www.sofascore.com/…#id:XXXXXXX"
            ),
        )
        self._input_entry.grid(row=1, column=1, padx=4, pady=4, sticky="ew")
        self._input_entry.bind("<Return>", lambda _: self._on_add_single())

        self._add_btn = ctk.CTkButton(
            card, text="+ Add", width=80, height=34,
            command=self._on_add_single,
        )
        self._add_btn.grid(row=1, column=2, padx=(6, 4), pady=4)

        # ── Paste-bulk row ────────────────────────────────────────────────
        ctk.CTkLabel(
            card,
            text="Paste multiple IDs/URLs (one per line) then click Add All:",
            font=ctk.CTkFont(size=11),
            text_color="#90A4AE",
        ).grid(row=2, column=0, columnspan=4, padx=16, pady=(4, 2), sticky="w")

        self._bulk_text = ctk.CTkTextbox(card, height=70, font=ctk.CTkFont(size=11))
        self._bulk_text.grid(row=3, column=0, columnspan=3, padx=16, pady=(2, 8), sticky="ew")

        self._add_all_btn = ctk.CTkButton(
            card, text="Add All", width=80,
            command=self._on_add_bulk,
        )
        self._add_all_btn.grid(row=3, column=3, padx=(4, 16), pady=(2, 8), sticky="n")

        # ── Output filename ───────────────────────────────────────────────
        ctk.CTkLabel(card, text="Output filename:", font=ctk.CTkFont(size=12)).grid(
            row=4, column=0, padx=(16, 8), pady=(0, 10), sticky="w")
        self._filename_var = ctk.StringVar(value="")
        ctk.CTkEntry(
            card, textvariable=self._filename_var, height=30,
            placeholder_text="Leave blank for auto  (bulk_N_matches_YYYYMMDD_HHMMSS.json)",
            font=ctk.CTkFont(size=11),
        ).grid(row=4, column=1, columnspan=2, padx=4, pady=(0, 10), sticky="ew")

        card.grid_columnconfigure(1, weight=1)

    # ── Action bar ────────────────────────────────────────────────────────────

    def _build_action_bar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=1, column=0, padx=18, pady=(0, 6), sticky="ew")
        bar.grid_columnconfigure(3, weight=1)

        self._run_btn = ctk.CTkButton(
            bar, text="▶  Scrape All",
            width=160, height=38,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._on_run,
        )
        self._run_btn.grid(row=0, column=0, sticky="w")

        self._clear_queue_btn = ctk.CTkButton(
            bar, text="Clear Queue", width=120, height=38,
            fg_color="transparent", border_width=1,
            command=self._on_clear_queue,
        )
        self._clear_queue_btn.grid(row=0, column=1, padx=(10, 0), sticky="w")

        self._open_btn = ctk.CTkButton(
            bar, text="📂  Open Output Folder",
            width=185, height=38,
            fg_color="#2E7D32", hover_color="#388E3C",
            command=self._on_open_folder, state="disabled",
        )
        self._open_btn.grid(row=0, column=2, padx=(10, 0), sticky="w")

        self._progress = ctk.CTkProgressBar(bar, mode="indeterminate", height=8)
        self._progress.grid(row=0, column=3, padx=(18, 0), sticky="ew")
        self._progress.set(0)

        # queue summary label
        self._queue_label = ctk.CTkLabel(
            bar, text="Queue: 0 match(es)",
            font=ctk.CTkFont(size=11), text_color="#90A4AE",
        )
        self._queue_label.grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 0))

    # ── Tab area ──────────────────────────────────────────────────────────────

    def _build_tab_area(self) -> None:
        self._tabs = ctk.CTkTabview(self, corner_radius=10)
        self._tabs.grid(row=2, column=0, padx=18, pady=(0, 18), sticky="nsew")

        self._tabs.add("Queue")
        self._tabs.add("Statistics Preview")
        self._tabs.add("Players")
        self._tabs.add("Team Formation")
        self._tabs.add("Log")

        self._build_queue_tab(self._tabs.tab("Queue"))
        self._build_preview_tab(self._tabs.tab("Statistics Preview"))
        self._build_players_tab(self._tabs.tab("Players"))
        self._build_formation_tab(self._tabs.tab("Team Formation"))
        self._build_log_tab(self._tabs.tab("Log"))

    # ── Queue tab ─────────────────────────────────────────────────────────────

    def _build_queue_tab(self, tab: ctk.CTkFrame) -> None:
        tab.grid_rowconfigure(0, weight=1)
        tab.grid_columnconfigure(0, weight=1)

        self._queue_tree = ttk.Treeview(
            tab, style="Stats.Treeview",
            columns=("idx", "input", "event_id", "status"),
            show="headings",
            selectmode="browse",
        )
        self._queue_tree.heading("idx",      text="#",         anchor="center")
        self._queue_tree.heading("input",    text="Input",     anchor="w")
        self._queue_tree.heading("event_id", text="Event ID",  anchor="center")
        self._queue_tree.heading("status",   text="Status",    anchor="center")

        self._queue_tree.column("idx",      width=40,  minwidth=40,  anchor="center", stretch=False)
        self._queue_tree.column("input",    width=500, minwidth=200, anchor="w")
        self._queue_tree.column("event_id", width=110, minwidth=90,  anchor="center", stretch=False)
        self._queue_tree.column("status",   width=120, minwidth=80,  anchor="center", stretch=False)

        qsb = ttk.Scrollbar(tab, orient="vertical", command=self._queue_tree.yview)
        self._queue_tree.configure(yscrollcommand=qsb.set)
        self._queue_tree.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        qsb.grid(row=0, column=1, sticky="ns", pady=6)

        # Remove-selected button
        ctk.CTkButton(
            tab, text="Remove Selected", width=140, height=26,
            fg_color="transparent", border_width=1,
            command=self._on_remove_selected,
        ).grid(row=1, column=0, padx=6, pady=(0, 6), sticky="w")

        self._queue_tree.tag_configure(_TAG_DONE,   foreground="#66BB6A")
        self._queue_tree.tag_configure(_TAG_FAIL,   foreground="#EF5350")
        self._queue_tree.tag_configure(_TAG_PEND,   foreground="#90A4AE")
        self._queue_tree.tag_configure(_TAG_ACTIVE, foreground="#FFD54F")

    # ── Statistics Preview tab ────────────────────────────────────────────────

    def _build_preview_tab(self, tab: ctk.CTkFrame) -> None:
        tab.grid_rowconfigure(3, weight=1)
        tab.grid_columnconfigure(0, weight=1)

        # Match selector (populated after scrape)
        sel_row = ctk.CTkFrame(tab, fg_color="transparent")
        sel_row.grid(row=0, column=0, padx=0, pady=(4, 2), sticky="ew")

        ctk.CTkLabel(sel_row, text="Match:", font=ctk.CTkFont(size=13)).pack(
            side="left", padx=(4, 8))
        self._match_var = ctk.StringVar(value="—")
        self._match_menu = ctk.CTkOptionMenu(
            sel_row, values=["—"], variable=self._match_var,
            width=380, command=self._on_match_selected,
        )
        self._match_menu.pack(side="left")

        # Match banner
        self._match_banner = ctk.CTkFrame(tab, corner_radius=8, fg_color=("#1a2744", "#121929"))
        self._match_banner.grid(row=1, column=0, padx=0, pady=(2, 4), sticky="ew")
        self._match_banner.grid_columnconfigure(1, weight=1)

        self._lbl_home = ctk.CTkLabel(
            self._match_banner, text="—",
            font=ctk.CTkFont(size=15, weight="bold"), anchor="e",
        )
        self._lbl_home.grid(row=0, column=0, padx=(16, 10), pady=(8, 2), sticky="e")

        score_col = ctk.CTkFrame(self._match_banner, fg_color="transparent")
        score_col.grid(row=0, column=1)
        self._lbl_score = ctk.CTkLabel(
            score_col, text="– : –",
            font=ctk.CTkFont(size=22, weight="bold"), text_color="#80DEEA",
        )
        self._lbl_score.pack()
        self._lbl_meta = ctk.CTkLabel(
            score_col, text="",
            font=ctk.CTkFont(size=11), text_color="#90A4AE",
        )
        self._lbl_meta.pack()

        self._lbl_away = ctk.CTkLabel(
            self._match_banner, text="—",
            font=ctk.CTkFont(size=15, weight="bold"), anchor="w",
        )
        self._lbl_away.grid(row=0, column=2, padx=(10, 16), pady=(8, 2), sticky="w")

        self._lbl_formations = ctk.CTkLabel(
            self._match_banner, text="",
            font=ctk.CTkFont(size=11), text_color="#B0BEC5",
        )
        self._lbl_formations.grid(row=1, column=0, columnspan=3, pady=(0, 6))

        # Period selector
        ctrl_row = ctk.CTkFrame(tab, fg_color="transparent")
        ctrl_row.grid(row=2, column=0, padx=0, pady=(0, 4), sticky="ew")
        ctk.CTkLabel(ctrl_row, text="Period:", font=ctk.CTkFont(size=13)).pack(
            side="left", padx=(4, 8))
        self._period_var = ctk.StringVar(value="ALL")
        self._period_menu = ctk.CTkOptionMenu(
            ctrl_row, values=["ALL", "1ST", "2ND"],
            variable=self._period_var, width=110,
            command=self._refresh_table,
        )
        self._period_menu.pack(side="left")

        # Stats Treeview
        tree_frame = ctk.CTkFrame(tab, corner_radius=8)
        tree_frame.grid(row=3, column=0, padx=0, pady=(0, 4), sticky="nsew")
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        self._tree = ttk.Treeview(
            tree_frame, style="Stats.Treeview",
            columns=("stat", "home", "away"),
            show="headings", selectmode="none",
        )
        self._tree.heading("stat",  text="Statistic", anchor="w")
        self._tree.heading("home",  text="Home",       anchor="center")
        self._tree.heading("away",  text="Away",       anchor="center")
        self._tree.column("stat",  width=330, minwidth=200, anchor="w")
        self._tree.column("home",  width=160, minwidth=90,  anchor="center")
        self._tree.column("away",  width=160, minwidth=90,  anchor="center")

        vsb = ttk.Scrollbar(tree_frame, orient="vertical",   command=self._tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._tree.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        vsb.grid(row=0, column=1, sticky="ns",  pady=6)
        hsb.grid(row=1, column=0, sticky="ew",  padx=(6, 0))

    @staticmethod
    def _apply_tree_style() -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Stats.Treeview",
            background="#1e1e2e", fieldbackground="#1e1e2e", foreground="#e0e0e0",
            rowheight=28, font=("Segoe UI", 11), borderwidth=0,
        )
        style.configure(
            "Stats.Treeview.Heading",
            background="#12122a", foreground="#90caf9",
            font=("Segoe UI", 11, "bold"), borderwidth=0, relief="flat",
        )
        style.map(
            "Stats.Treeview",
            background=[("selected", "#1565C0")],
            foreground=[("selected", "#ffffff")],
        )

    # ── Players tab ───────────────────────────────────────────────────────────

    # Column layout: (treeview_id, header_label, width, anchor)
    _PLAYER_COLS: list[tuple[str, str, int, str]] = [
        ("jersey",          "#",        40,  "center"),
        ("name",            "Name",     185, "w"),
        ("pos",             "Pos",      40,  "center"),
        ("sub",             "Sub",      40,  "center"),
        ("mins",            "Mins",     50,  "center"),
        ("rating",          "Rating",   60,  "center"),
        ("goals",           "Goals",    55,  "center"),
        ("assists",         "Ast",      45,  "center"),
        ("xG",              "xG",       55,  "center"),
        ("xA",              "xA",       55,  "center"),
        ("key_passes",      "KP",         45,  "center"),
        ("shots_total",     "Shots",      55,  "center"),
        ("passes_acc",      "Pass Acc",   65,  "center"),
        ("passes_tot",      "Pass Tot",   65,  "center"),
        ("passes_pct",      "Pass %",     60,  "center"),
        ("touches",         "Touch",      55,  "center"),
        ("duels_won",       "Duel W",   60,  "center"),
        ("tackles_total",   "Tkl",      45,  "center"),
        ("interceptions",   "Int",      45,  "center"),
        ("clearances",      "Clr",      45,  "center"),
        ("ball_recoveries", "BR",       45,  "center"),
        ("saves",           "Saves",    55,  "center"),
    ]

    def _build_players_tab(self, tab: ctk.CTkFrame) -> None:
        tab.grid_rowconfigure(1, weight=1)
        tab.grid_columnconfigure(0, weight=1)

        # Controls row: match selector + home/away side toggle
        ctrl = ctk.CTkFrame(tab, fg_color="transparent")
        ctrl.grid(row=0, column=0, padx=0, pady=(4, 4), sticky="ew")

        ctk.CTkLabel(ctrl, text="Match:", font=ctk.CTkFont(size=13)).pack(
            side="left", padx=(4, 6))
        self._player_match_var = ctk.StringVar(value="—")
        self._player_match_menu = ctk.CTkOptionMenu(
            ctrl, values=["—"], variable=self._player_match_var,
            width=340, command=self._on_player_match_selected,
        )
        self._player_match_menu.pack(side="left")

        ctk.CTkLabel(ctrl, text="Side:", font=ctk.CTkFont(size=13)).pack(
            side="left", padx=(18, 6))
        self._player_side_btn = ctk.CTkSegmentedButton(
            ctrl,
            values=["Home", "Away"],
            command=lambda _v: self._refresh_player_table(),
        )
        self._player_side_btn.set("Home")
        self._player_side_btn.pack(side="left")

        self._copy_players_btn = ctk.CTkButton(
            ctrl, text="📋  Copy for Excel",
            width=145, height=30,
            fg_color="#1565C0", hover_color="#1976D2",
            command=self._copy_players_to_clipboard,
        )
        self._copy_players_btn.pack(side="left", padx=(18, 0))

        # Player Treeview
        tree_frame = ctk.CTkFrame(tab, corner_radius=8)
        tree_frame.grid(row=1, column=0, padx=0, pady=(0, 4), sticky="nsew")
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        col_ids = tuple(c[0] for c in self._PLAYER_COLS)
        self._player_tree = ttk.Treeview(
            tree_frame, style="Stats.Treeview",
            columns=col_ids, show="headings", selectmode="none",
        )
        for col_id, label, width, anchor in self._PLAYER_COLS:
            self._player_tree.heading(col_id, text=label, anchor=anchor)
            self._player_tree.column(
                col_id, width=width, minwidth=width, anchor=anchor, stretch=False,
            )

        pvsb = ttk.Scrollbar(tree_frame, orient="vertical",   command=self._player_tree.yview)
        phsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self._player_tree.xview)
        self._player_tree.configure(yscrollcommand=pvsb.set, xscrollcommand=phsb.set)
        self._player_tree.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        pvsb.grid(row=0, column=1, sticky="ns",  pady=6)
        phsb.grid(row=1, column=0, sticky="ew",  padx=(6, 0))

        self._player_tree.tag_configure("starter",       background="#1e1e2e")
        self._player_tree.tag_configure("sub_used",     background="#1a2030")
        self._player_tree.tag_configure("even_row",     background="#242438")
        self._player_tree.tag_configure("summary_sep",  background="#0a0a14")
        self._player_tree.tag_configure("summary_total",background="#0d2118", foreground="#a5d6a7")
        self._player_tree.tag_configure("summary_avg",  background="#0d1828", foreground="#90caf9")
        self._player_tree.tag_configure("summary_max",  background="#281a0d", foreground="#ffcc80")
        self._player_tree.tag_configure("summary_top",  background="#1a1228", foreground="#ce93d8")

    def _on_player_match_selected(self, choice: str) -> None:
        if not self._scraped_data or choice == "—":
            return
        matches = self._scraped_data.get("matches", [])
        labels  = list(self._player_match_menu.cget("values"))
        try:
            idx = labels.index(choice)
        except ValueError:
            return
        if idx < len(matches):
            self._current_match = matches[idx]
            self._refresh_player_table()

    def _refresh_player_table(self, *_) -> None:
        """Populate the player Treeview for the current match and selected side."""
        for item in self._player_tree.get_children():
            self._player_tree.delete(item)

        match = self._current_match
        if not match:
            return

        side = self._player_side_btn.get().lower()   # "home" or "away"
        team_name = match.get("match", {}).get(f"{side}_team", side.title())
        players: list[dict] = match.get("player_statistics", {}).get(side, [])

        if not players:
            self._player_tree.insert(
                "", "end",
                values=("No player data.",) + ("",) * (len(self._PLAYER_COLS) - 1),
            )
            return

        def _fmt(val, decimals: int = 0) -> str:
            if val is None:
                return ""
            if decimals and isinstance(val, float):
                return f"{val:.{decimals}f}"
            return str(val)

        def _fmt_num(v: float) -> str:
            return str(int(v)) if v == int(v) else f"{v:.1f}"

        # Identify which column indices are numeric (can be summed / averaged)
        _NON_NUMERIC = {"jersey", "name", "pos", "sub"}
        _col_ids = [c[0] for c in self._PLAYER_COLS]
        _numeric_idx = [
            i for i, c in enumerate(self._PLAYER_COLS) if c[0] not in _NON_NUMERIC
        ]
        # summary_vals[col_idx] = list of (float_value, display_name)
        summary_vals: dict[int, list[tuple]] = {i: [] for i in _numeric_idx}

        for row_idx, p in enumerate(players):
            s = p.get("stats", {})
            is_sub = p.get("substitute", False)
            display_name = p.get("short_name") or p.get("name", "")

            passes_pct_raw = (
                round(s["passes_accurate"] / s["passes_total"] * 100, 1)
                if s.get("passes_total")
                else None
            )

            row_values = (
                p.get("jersey_number", ""),
                p.get("name", ""),
                p.get("position", ""),
                "Sub" if is_sub else "●",
                _fmt(s.get("minutes_played")),
                _fmt(s.get("rating"), 1),
                _fmt(s.get("goals")),
                _fmt(s.get("assists")),
                _fmt(s.get("xG"), 2),
                _fmt(s.get("xA"), 2),
                _fmt(s.get("key_passes")),
                _fmt(s.get("shots_total")),
                _fmt(s.get("passes_accurate")),
                _fmt(s.get("passes_total")),
                _fmt(passes_pct_raw, 1),
                _fmt(s.get("touches")),
                _fmt(s.get("duels_won")),
                _fmt(s.get("tackles_total")),
                _fmt(s.get("interceptions")),
                _fmt(s.get("clearances")),
                _fmt(s.get("ball_recoveries")),
                _fmt(s.get("saves")),
            )

            # Collect numeric values for summary rows
            for i in _numeric_idx:
                try:
                    summary_vals[i].append((float(row_values[i]), display_name))
                except (ValueError, TypeError):
                    pass

            tag = "sub_used" if is_sub else ("even_row" if row_idx % 2 == 0 else "starter")
            self._player_tree.insert("", "end", values=row_values, tags=(tag,))

        self._player_tree.heading("name", text=f"{team_name} — Name")

        # ── Summary rows ──────────────────────────────────────────────────────
        def _make_summary_row(label: str, fn) -> tuple:
            row = [""] * len(self._PLAYER_COLS)
            row[_col_ids.index("name")] = label
            for i in _numeric_idx:
                if summary_vals[i]:
                    row[i] = fn(summary_vals[i])
            return tuple(row)

        sep       = ("",) * len(self._PLAYER_COLS)
        total_row = _make_summary_row(
            "▶  TOTAL",
            lambda vs: _fmt_num(sum(v for v, _ in vs)),
        )
        avg_row   = _make_summary_row(
            "▶  AVERAGE",
            lambda vs: _fmt_num(sum(v for v, _ in vs) / len(vs)),
        )
        max_row   = _make_summary_row(
            "▶  MAX",
            lambda vs: _fmt_num(max(v for v, _ in vs)),
        )
        top_row   = _make_summary_row(
            "↑  Player",
            lambda vs: max(vs, key=lambda x: x[0])[1],
        )

        self._player_tree.insert("", "end", values=sep,       tags=("summary_sep",))
        self._player_tree.insert("", "end", values=total_row, tags=("summary_total",))
        self._player_tree.insert("", "end", values=avg_row,   tags=("summary_avg",))
        self._player_tree.insert("", "end", values=max_row,   tags=("summary_max",))
        self._player_tree.insert("", "end", values=top_row,   tags=("summary_top",))

    def _copy_players_to_clipboard(self) -> None:
        """Copy current player table to clipboard as TSV for Excel."""
        rows: list[list[str]] = []

        # Header row — use the full column labels
        header = [col[1] for col in self._PLAYER_COLS]
        rows.append(header)

        # Data rows from the Treeview
        for item in self._player_tree.get_children():
            rows.append(list(self._player_tree.item(item, "values")))

        if len(rows) <= 1:  # header only → nothing scraped yet
            return

        tsv = "\n".join("\t".join(str(cell) for cell in row) for row in rows)

        self.clipboard_clear()
        self.clipboard_append(tsv)

        # Brief visual feedback on the button
        self._copy_players_btn.configure(text="✓  Copied!", fg_color="#2E7D32")
        self.after(1800, lambda: self._copy_players_btn.configure(
            text="📋  Copy for Excel", fg_color="#1565C0"))

    # ── Team Formation tab ────────────────────────────────────────────────────

    def _build_formation_tab(self, tab: ctk.CTkFrame) -> None:
        tab.grid_rowconfigure(2, weight=1)
        tab.grid_columnconfigure(0, weight=1)

        # ── Input card ────────────────────────────────────────────────────
        card = ctk.CTkFrame(tab, corner_radius=10)
        card.grid(row=0, column=0, padx=0, pady=(6, 4), sticky="ew")
        card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            card, text="Team Formation Scraper",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).grid(row=0, column=0, columnspan=4, padx=14, pady=(10, 6), sticky="w")

        ctk.CTkLabel(card, text="Team URL / ID:", font=ctk.CTkFont(size=12)).grid(
            row=1, column=0, padx=(14, 8), pady=4, sticky="w")
        self._ft_team_entry = ctk.CTkEntry(
            card, height=32,
            placeholder_text="e.g. 2817  or  https://www.sofascore.com/football/team/barcelona/2817",
        )
        self._ft_team_entry.grid(row=1, column=1, columnspan=3, padx=(0, 14), pady=4, sticky="ew")

        ctk.CTkLabel(card, text="League URL / IDs:", font=ctk.CTkFont(size=12)).grid(
            row=2, column=0, padx=(14, 8), pady=4, sticky="w")
        self._ft_league_entry = ctk.CTkEntry(
            card, height=32,
            placeholder_text="e.g. 8/77559  or  https://www.sofascore.com/football/tournament/…/8#id:77559",
        )
        self._ft_league_entry.grid(row=2, column=1, columnspan=2, padx=0, pady=4, sticky="ew")

        self._ft_run_btn = ctk.CTkButton(
            card, text="▶  Scrape",
            width=110, height=32,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._on_formation_run,
        )
        self._ft_run_btn.grid(row=2, column=3, padx=(8, 14), pady=4)

        # ── Status bar ────────────────────────────────────────────────────
        status_row = ctk.CTkFrame(tab, fg_color="transparent")
        status_row.grid(row=1, column=0, padx=0, pady=(0, 4), sticky="ew")
        status_row.grid_columnconfigure(0, weight=1)

        self._ft_progress = ctk.CTkProgressBar(status_row, mode="indeterminate", height=6)
        self._ft_progress.grid(row=0, column=0, padx=0, pady=(0, 2), sticky="ew")
        self._ft_progress.set(0)

        self._ft_summary_lbl = ctk.CTkLabel(
            status_row, text="",
            font=ctk.CTkFont(size=11), text_color="#90A4AE",
        )
        self._ft_summary_lbl.grid(row=1, column=0, padx=4, sticky="w")

        self._ft_export_btn = ctk.CTkButton(
            status_row, text="📥  Open Excel",
            width=110, height=26,
            fg_color="#1565C0", hover_color="#1976D2",
            state="disabled",
            command=self._on_formation_export_csv,
        )
        self._ft_export_btn.grid(row=1, column=1, padx=(8, 0), pady=2, sticky="e")

        self._ft_open_btn = ctk.CTkButton(
            status_row, text="📂  Open Folder",
            width=120, height=26,
            fg_color="#2E7D32", hover_color="#388E3C",
            state="disabled",
            command=self._on_formation_open_folder,
        )
        self._ft_open_btn.grid(row=1, column=2, padx=(8, 0), pady=2, sticky="e")

        # ── Results Treeview ──────────────────────────────────────────────
        tree_frame = ctk.CTkFrame(tab, corner_radius=8)
        tree_frame.grid(row=2, column=0, padx=0, pady=(0, 4), sticky="nsew")
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        self._ft_tree = ttk.Treeview(
            tree_frame, style="Stats.Treeview",
            columns=("date", "home", "away", "ha", "formation", "opp_formation", "score", "result"),
            show="headings", selectmode="none",
        )
        for col, label, width, anchor in [
            ("date",          "Date",        90,  "center"),
            ("home",          "Home",        160, "w"),
            ("away",          "Away",        160, "w"),
            ("ha",            "H/A",         38,  "center"),
            ("formation",     "Formation",   100, "center"),
            ("opp_formation", "Opp Form.",   100, "center"),
            ("score",         "Score",       60,  "center"),
            ("result",        "Result",      50,  "center"),
        ]:
            self._ft_tree.heading(col, text=label, anchor=anchor)
            self._ft_tree.column(col, width=width, minwidth=width, anchor=anchor, stretch=(col == "home" or col == "away"))

        ft_vsb = ttk.Scrollbar(tree_frame, orient="vertical",   command=self._ft_tree.yview)
        ft_hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self._ft_tree.xview)
        self._ft_tree.configure(yscrollcommand=ft_vsb.set, xscrollcommand=ft_hsb.set)
        self._ft_tree.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        ft_vsb.grid(row=0, column=1, sticky="ns",  pady=6)
        ft_hsb.grid(row=1, column=0, sticky="ew",  padx=(6, 0))

        self._ft_tree.tag_configure("win",     foreground="#66BB6A")
        self._ft_tree.tag_configure("loss",    foreground="#EF5350")
        self._ft_tree.tag_configure("draw",    foreground="#FFD54F")
        self._ft_tree.tag_configure("even",    background="#242438")
        self._ft_tree.tag_configure("summary", background="#12122a", foreground="#80DEEA",
                                    font=("Segoe UI", 11, "bold"))

        # Internal state
        self._ft_data:     dict | None = None
        self._ft_xlsx_path: str | None = None
        self._ft_json_path: str | None = None
        self._ft_log_queue: queue.Queue = queue.Queue()
        self._poll_formation_queue()

    # ── Formation tab callbacks ───────────────────────────────────────────────

    def _on_formation_run(self) -> None:
        team_raw   = self._ft_team_entry.get().strip()
        league_raw = self._ft_league_entry.get().strip()

        if not team_raw or not league_raw:
            messagebox.showerror("Missing Input", "Please fill in both Team and League fields.")
            return

        team_id = extract_team_id(team_raw)
        if team_id is None:
            messagebox.showerror(
                "Invalid Team",
                f"Could not parse a team ID from:\n{team_raw}\n\n"
                "Use a plain integer (e.g. 2817) or a SofaScore team URL.",
            )
            return

        league = extract_league_ids(league_raw)
        if league is None:
            messagebox.showerror(
                "Invalid League",
                f"Could not parse league/season IDs from:\n{league_raw}\n\n"
                "Use  uniq_tid/season_id  (e.g. 8/77559) or a SofaScore league URL.",
            )
            return

        uniq_tid, season_id = league

        self._ft_run_btn.configure(state="disabled")
        self._ft_export_btn.configure(state="disabled")
        self._ft_open_btn.configure(state="disabled")
        self._ft_data = None
        self._ft_xlsx_path = None
        self._ft_json_path = None
        self._ft_summary_lbl.configure(text=f"Scraping {team_id} in tournament {uniq_tid} / season {season_id}…")
        self._ft_progress.start()

        # Clear old results
        for item in self._ft_tree.get_children():
            self._ft_tree.delete(item)

        def _worker() -> None:
            try:
                json_path, xlsx_path, data = scrape_team_formations(
                    team_id, uniq_tid, season_id,
                    output_dir=_OUTPUT_DIR,
                    log_fn=lambda msg: self._ft_log_queue.put(("log", msg)),
                )
                self._ft_log_queue.put(("done", (json_path, xlsx_path, data)))
            except OSError as exc:
                self._ft_log_queue.put(("error", f"File I/O error: {exc}"))
            except RuntimeError as exc:
                self._ft_log_queue.put(("error", str(exc)))
            except Exception as exc:
                self._ft_log_queue.put(("error", f"Unexpected error: {exc}"))

        threading.Thread(target=_worker, daemon=True).start()

    def _poll_formation_queue(self) -> None:
        try:
            while True:
                kind, payload = self._ft_log_queue.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "done":
                    json_path, xlsx_path, data = payload
                    self._ft_json_path = json_path
                    self._ft_xlsx_path = xlsx_path
                    self._ft_data      = data
                    self._ft_progress.stop()
                    self._ft_progress.set(1)
                    self._ft_run_btn.configure(state="normal")
                    self._ft_export_btn.configure(state="normal")
                    self._ft_open_btn.configure(state="normal")
                    self._populate_formation_tree(data)
                    self._tabs.set("Team Formation")
                elif kind == "error":
                    self._ft_progress.stop()
                    self._ft_progress.set(0)
                    self._ft_run_btn.configure(state="normal")
                    self._ft_summary_lbl.configure(text="Scrape failed.")
                    self._log(f"\n✗  Formation scrape error: {payload}")
                    messagebox.showerror("Formation Scraper Error", payload)
        except queue.Empty:
            pass
        self.after(100, self._poll_formation_queue)

    def _populate_formation_tree(self, data: dict) -> None:
        for item in self._ft_tree.get_children():
            self._ft_tree.delete(item)

        matches = data.get("matches", [])
        summary = data.get("formation_summary", {})
        team    = data.get("team_name", "")
        season  = data.get("season_name", "")

        for idx, m in enumerate(matches):
            result = m.get("result", "")
            tag = ("win" if result == "W" else "loss" if result == "L" else "draw")
            if idx % 2 == 0:
                tag = (tag, "even")
            self._ft_tree.insert(
                "", "end",
                values=(
                    m.get("date", ""),
                    m.get("home_team", ""),
                    m.get("away_team", ""),
                    m.get("home_away", ""),
                    m.get("team_formation", ""),
                    m.get("opponent_formation", ""),
                    m.get("score", ""),
                    result,
                ),
                tags=(tag,) if isinstance(tag, str) else tag,
            )

        # Summary row
        summary_parts = "   ".join(f"{f}  ×{n}" for f, n in summary.items())
        self._ft_summary_lbl.configure(
            text=f"{team}  ·  {season}  ·  {len(matches)} matches     {summary_parts}"
        )

    def _on_formation_export_csv(self) -> None:
        if not self._ft_xlsx_path or not os.path.exists(self._ft_xlsx_path):
            messagebox.showinfo("No Excel file", "Run a scrape first to generate the Excel file.")
            return
        _open_in_file_manager(self._ft_xlsx_path, select=True)

    def _on_formation_open_folder(self) -> None:
        if self._ft_json_path and os.path.exists(self._ft_json_path):
            _open_in_file_manager(os.path.dirname(self._ft_json_path))

    def _on_close(self) -> None:
        if self._is_scraping:
            if not messagebox.askyesno(
                "Scrape in progress",
                "A scrape is currently running.\nCancel it and exit?",
            ):
                return
            self._cancel_event.set()
        self.destroy()

    # ── Log tab ───────────────────────────────────────────────────────────────

    def _build_log_tab(self, tab: ctk.CTkFrame) -> None:
        tab.grid_rowconfigure(0, weight=1)
        tab.grid_columnconfigure(0, weight=1)

        self._log_box = ctk.CTkTextbox(
            tab, font=ctk.CTkFont(family="Consolas", size=12),
            activate_scrollbars=True, wrap="word", state="disabled",
        )
        self._log_box.grid(row=0, column=0, padx=0, pady=(4, 4), sticky="nsew")

        ctk.CTkButton(
            tab, text="Clear Log", width=90, height=26,
            fg_color="transparent", border_width=1,
            command=self._clear_log,
        ).grid(row=1, column=0, padx=4, pady=(0, 6), sticky="e")

    # ── Queue management ──────────────────────────────────────────────────────

    def _add_to_queue(self, raw: str) -> bool:
        """Add one entry to the queue. Returns True if successfully added."""
        raw = raw.strip()
        if not raw:
            return False
        event_id = extract_event_id(raw)
        id_str = str(event_id) if event_id else "?"
        count = len(self._queue_tree.get_children())
        tag = _TAG_PEND if event_id else _TAG_FAIL
        self._queue_tree.insert(
            "", "end",
            iid=str(count),
            values=(count + 1, raw, id_str, "Pending" if event_id else "Invalid ID"),
            tags=(tag,),
        )
        self._queue_label.configure(text=f"Queue: {count + 1} match(es)")
        return event_id is not None

    def _on_add_single(self) -> None:
        raw = self._input_var.get().strip()
        if not raw:
            return
        ok = self._add_to_queue(raw)
        if not ok:
            messagebox.showwarning(
                "Invalid Input",
                f"Could not detect an event ID from:\n{raw}\n\n"
                "It has been added as 'Invalid ID' and will be skipped during scraping.",
            )
        self._input_var.set("")
        self._tabs.set("Queue")

    def _on_add_bulk(self) -> None:
        text = self._bulk_text.get("1.0", "end").strip()
        if not text:
            return
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        added = skipped = 0
        for line in lines:
            ok = self._add_to_queue(line)
            if ok:
                added += 1
            else:
                skipped += 1
        self._bulk_text.delete("1.0", "end")
        self._tabs.set("Queue")
        if skipped:
            messagebox.showwarning(
                "Some Inputs Invalid",
                f"Added: {added}    Invalid (will be skipped): {skipped}",
            )

    def _on_remove_selected(self) -> None:
        selected = self._queue_tree.selection()
        if not selected:
            return
        for iid in selected:
            self._queue_tree.delete(iid)
        # Re-number remaining rows
        for new_idx, iid in enumerate(self._queue_tree.get_children(), 1):
            vals = list(self._queue_tree.item(iid, "values"))
            vals[0] = new_idx
            self._queue_tree.item(iid, values=vals)
        count = len(self._queue_tree.get_children())
        self._queue_label.configure(text=f"Queue: {count} match(es)")

    def _on_clear_queue(self) -> None:
        for iid in self._queue_tree.get_children():
            self._queue_tree.delete(iid)
        self._queue_label.configure(text="Queue: 0 match(es)")

    # ── Run ───────────────────────────────────────────────────────────────────

    def _on_run(self) -> None:
        # Collect only parseable entries directly from the Treeview
        parseable = [
            self._queue_tree.item(iid, "values")[1]
            for iid in self._queue_tree.get_children()
            if extract_event_id(self._queue_tree.item(iid, "values")[1]) is not None
        ]
        if not parseable:
            messagebox.showerror(
                "Empty Queue",
                "No valid match IDs in the queue. Add at least one match first.",
            )
            return

        raw_filename = self._filename_var.get().strip()
        filename = _sanitise_filename(raw_filename) if raw_filename else None
        if filename and not filename.endswith(".json"):
            filename += ".json"

        self._run_btn.configure(state="disabled")
        self._clear_queue_btn.configure(state="disabled")
        self._open_btn.configure(state="disabled")
        self._output_path  = None
        self._scraped_data = None
        self._is_scraping  = True
        self._progress.start()
        self._tabs.set("Log")

        # Reset all queue row statuses to Pending
        for iid in self._queue_tree.get_children():
            row_vals = list(self._queue_tree.item(iid, "values"))
            if extract_event_id(row_vals[1]) is not None:
                row_vals[3] = "Pending"
                self._queue_tree.item(iid, values=row_vals, tags=(_TAG_PEND,))

        self._log("─" * 60)
        self._log(f"Queued : {len(parseable)} match(es)")
        self._log(f"Output : {_OUTPUT_DIR}")
        self._log("─" * 60)

        def _worker() -> None:
            try:
                path, data = run_bulk_scraper(
                    parseable,
                    output_dir=_OUTPUT_DIR,
                    output_filename=filename,
                    log_fn=lambda msg: self._log_queue.put(("log", msg)),
                )
                self._log_queue.put(("done", (path, data)))
            except OSError as exc:
                self._log_queue.put(("error", f"File I/O error: {exc}"))
            except ValueError as exc:
                self._log_queue.put(("error", f"Input error: {exc}"))
            except RuntimeError as exc:
                self._log_queue.put(("error", str(exc)))
            except Exception as exc:
                self._log_queue.put(("error", f"Unexpected error: {exc}"))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_open_folder(self) -> None:
        if self._output_path and os.path.exists(self._output_path):
            _open_in_file_manager(os.path.dirname(self._output_path))

    # ── Preview helpers ───────────────────────────────────────────────────────

    def _populate_match_selector(self) -> None:
        """Fill the match dropdown from scraped bulk data."""
        if not self._scraped_data:
            return
        matches = self._scraped_data.get("matches", [])
        labels = [
            f"{m['match']['home_team']} vs {m['match']['away_team']}  "
            f"({m['match']['date']})  [{m['event_id']}]"
            for m in matches
        ]
        self._match_menu.configure(values=labels if labels else ["—"])
        self._player_match_menu.configure(values=labels if labels else ["—"])
        if labels:
            self._match_var.set(labels[0])
            self._player_match_var.set(labels[0])
            self._on_match_selected(labels[0])

    def _on_match_selected(self, choice: str) -> None:
        if not self._scraped_data or choice == "—":
            return
        matches = self._scraped_data.get("matches", [])
        labels = self._match_menu.cget("values")
        try:
            idx = labels.index(choice)
        except ValueError:
            return
        if idx >= len(matches):
            return
        self._current_match = matches[idx]
        self._update_banner()
        self._update_period_menu()
        self._refresh_table()
        self._refresh_player_table()

    def _update_banner(self) -> None:
        m = (self._current_match or {}).get("match", {})
        home  = m.get("home_team", "—")
        away  = m.get("away_team", "—")
        hs    = m.get("home_score", "–")
        as_   = m.get("away_score", "–")
        date  = m.get("date", "")
        comp  = m.get("competition", "")
        hform = m.get("home_formation", "")
        aform = m.get("away_formation", "")

        self._lbl_home.configure(text=home)
        self._lbl_away.configure(text=away)
        self._lbl_score.configure(text=f"{hs}  :  {as_}")
        self._lbl_meta.configure(
            text="  ·  ".join(p for p in (date, comp) if p)
        )
        self._lbl_formations.configure(
            text=f"{hform or '?'}  ↔  {aform or '?'}" if (hform or aform) else ""
        )
        self._tree.heading("home", text=home)
        self._tree.heading("away", text=away)

    def _update_period_menu(self) -> None:
        stats = (self._current_match or {}).get("statistics", {})
        periods = list(stats.keys())
        canonical = ["ALL", "1ST", "2ND"]
        ordered = [p for p in canonical if p in periods]
        ordered += [p for p in periods if p not in canonical]
        self._period_menu.configure(values=ordered or ["ALL"])
        if self._period_var.get() not in ordered:
            self._period_var.set(ordered[0] if ordered else "ALL")

    def _refresh_table(self, *_) -> None:
        for item in self._tree.get_children():
            self._tree.delete(item)

        match = getattr(self, "_current_match", None)
        if not match:
            return

        period = self._period_var.get()
        groups = match.get("statistics", {}).get(period, [])

        if not groups:
            self._tree.insert("", "end", values=("No data for this period.", "", ""))
            return

        row_idx = 0
        for group in groups:
            self._tree.insert(
                "", "end",
                values=(f"  {group['group'].upper()}", "", ""),
                tags=(_TAG_GROUP,),
            )
            for item in group.get("items", []):
                tag = _TAG_EVEN if row_idx % 2 == 0 else ""
                self._tree.insert(
                    "", "end",
                    values=(
                        f"    {item['name']}",
                        item.get("home", ""),
                        item.get("away", ""),
                    ),
                    tags=(tag,),
                )
                row_idx += 1

        self._tree.tag_configure(
            _TAG_GROUP,
            foreground="#80DEEA",
            font=("Segoe UI", 11, "bold"),
            background="#12122a",
        )
        self._tree.tag_configure(_TAG_EVEN, background="#242438")

    # ── Queue status syncing ──────────────────────────────────────────────────

    def _sync_queue_statuses(self) -> None:
        """Update queue Treeview rows based on bulk output errors list."""
        if not self._scraped_data:
            return
        failed_ids = {
            e["event_id"] for e in self._scraped_data.get("errors", [])
        }
        succeeded_ids = {
            m["event_id"] for m in self._scraped_data.get("matches", [])
        }
        for iid in self._queue_tree.get_children():
            vals = list(self._queue_tree.item(iid, "values"))
            try:
                eid = int(vals[2])
            except (ValueError, IndexError):
                continue
            if eid in succeeded_ids:
                vals[3] = "Done ✓"
                self._queue_tree.item(iid, values=vals, tags=(_TAG_DONE,))
            elif eid in failed_ids:
                vals[3] = "Failed ✗"
                self._queue_tree.item(iid, values=vals, tags=(_TAG_FAIL,))

    # ── Log helpers ───────────────────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        self._log_box.configure(state="normal")
        self._log_box.insert("end", msg + "\n")
        self._log_box.see("end")
        self._log_box.configure(state="disabled")

    def _clear_log(self) -> None:
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")

    # ── Background queue polling ──────────────────────────────────────────────

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self._log_queue.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "done":
                    path, data = payload
                    self._output_path  = path
                    self._scraped_data = data
                    self._is_scraping  = False
                    self._progress.stop()
                    self._progress.set(1)
                    self._run_btn.configure(state="normal")
                    self._clear_queue_btn.configure(state="normal")
                    self._open_btn.configure(state="normal")
                    self._log(f"\n✓  Saved → {path}")
                    self._sync_queue_statuses()
                    self._populate_match_selector()
                    self._tabs.set("Statistics Preview")
                elif kind == "error":
                    self._is_scraping  = False
                    self._progress.stop()
                    self._progress.set(0)
                    self._run_btn.configure(state="normal")
                    self._clear_queue_btn.configure(state="normal")
                    self._log(f"\n✗  Error: {payload}")
                    messagebox.showerror("Scraper Error", payload)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = MatchStatsScraperApp()
    app.mainloop()

