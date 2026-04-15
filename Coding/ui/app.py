import customtkinter as ctk
from tkinter import filedialog
from pathlib import Path

from ui.tab_player    import PlayerTab
from ui.tab_standings import StandingsTab
from ui.tab_league    import LeagueTab

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("green")

def tlog(msg):
    print(msg)

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("SofaScore Scraper")
        self.geometry("860x740")
        self.resizable(True, True)

        self.tabs = ctk.CTkTabview(self, anchor="nw")
        self.tabs.pack(fill="both", expand=True, padx=12, pady=12)
        self.tabs.add("Player Scraper")
        self.tabs.add("League Standings")
        self.tabs.add("League Scraper")

        self.player_tab    = PlayerTab(self,    self.tabs.tab("Player Scraper"))
        self.standings_tab = StandingsTab(self, self.tabs.tab("League Standings"))
        self.league_tab    = LeagueTab(self,    self.tabs.tab("League Scraper"))

    # ── Shared UI helpers used by all tabs ────────────────────────────────────
    def dir_row(self, parent, var):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(row, text="Output folder:", width=130, anchor="w").pack(side="left")
        ctk.CTkEntry(row, textvariable=var, width=480).pack(side="left", padx=6)
        ctk.CTkButton(row, text="Browse", width=80,
                      command=lambda: var.set(filedialog.askdirectory() or var.get())
                      ).pack(side="left")

    def log_box(self, parent):
        box = ctk.CTkTextbox(parent, height=220, font=("Courier", 11))
        box.pack(fill="both", expand=True, pady=(6, 0))
        box.configure(state="disabled")
        return box

    def log(self, box, msg):
        tlog(msg)
        box.configure(state="normal")
        box.insert("end", msg + "\n")
        box.see("end")
        box.configure(state="disabled")