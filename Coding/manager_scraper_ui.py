"""
Manager Match Scraper — customtkinter UI
Run any SofaScore manager by ID or profile URL.
"""

import os
import queue
import re
import subprocess
import sys
import threading

import customtkinter as ctk
from tkinter import messagebox

from herdman_scraper import lookup_manager_name, run_scraper

# ── Theme ─────────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

_ID_FROM_URL = re.compile(r"/(\d+)(?:[/?#]|$)")


def _parse_id(text: str) -> int | None:
    """Return manager ID from a raw number string or a SofaScore URL."""
    text = text.strip()
    if text.isdigit():
        return int(text)
    m = _ID_FROM_URL.search(text)
    if m:
        return int(m.group(1))
    return None


# ── Main App ──────────────────────────────────────────────────────────────────

class ManagerScraperApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Manager Match Scraper")
        self.geometry("780x620")
        self.resizable(True, True)
        self.minsize(620, 500)

        self._log_queue: queue.Queue = queue.Queue()
        self._output_path: str | None = None

        self._build_ui()
        self._poll_queue()

    # ── UI Construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # ── Input card ───────────────────────────────────────────────────────
        card = ctk.CTkFrame(self, corner_radius=12)
        card.grid(row=0, column=0, padx=18, pady=(18, 8), sticky="ew")
        card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(card, text="Manager Match Scraper",
                     font=ctk.CTkFont(size=18, weight="bold")).grid(
            row=0, column=0, columnspan=3, padx=16, pady=(14, 10))

        ctk.CTkLabel(card, text="Manager ID or SofaScore URL:").grid(
            row=1, column=0, padx=(16, 6), pady=6, sticky="w")

        self._input_var = ctk.StringVar(value="53225")
        self._input_entry = ctk.CTkEntry(
            card, textvariable=self._input_var, width=320,
            placeholder_text="e.g. 53225  or  https://www.sofascore.com/manager/…/53225")
        self._input_entry.grid(row=1, column=1, padx=4, pady=6, sticky="ew")

        self._lookup_btn = ctk.CTkButton(
            card, text="Lookup", width=90, command=self._on_lookup)
        self._lookup_btn.grid(row=1, column=2, padx=(4, 16), pady=6)

        # resolved name label
        self._name_label = ctk.CTkLabel(
            card, text="", font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#4FC3F7")
        self._name_label.grid(row=2, column=0, columnspan=3,
                              padx=16, pady=(0, 12))

        # ── Action bar ───────────────────────────────────────────────────────
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=1, column=0, padx=18, pady=(0, 6), sticky="ew")
        bar.grid_columnconfigure(0, weight=1)

        self._run_btn = ctk.CTkButton(
            bar, text="▶  Run Scraper", width=160, height=38,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._on_run)
        self._run_btn.grid(row=0, column=0, sticky="w")

        self._open_btn = ctk.CTkButton(
            bar, text="📂  Open Output Folder", width=180, height=38,
            fg_color="#2E7D32", hover_color="#388E3C",
            command=self._on_open_folder, state="disabled")
        self._open_btn.grid(row=0, column=1, padx=(12, 0), sticky="w")

        self._progress = ctk.CTkProgressBar(bar, mode="indeterminate", height=10)
        self._progress.grid(row=0, column=2, padx=(18, 0), sticky="ew")
        bar.grid_columnconfigure(2, weight=1)
        self._progress.set(0)

        # ── Log area ─────────────────────────────────────────────────────────
        log_frame = ctk.CTkFrame(self, corner_radius=10)
        log_frame.grid(row=2, column=0, padx=18, pady=(0, 18), sticky="nsew")
        log_frame.grid_rowconfigure(0, weight=1)
        log_frame.grid_columnconfigure(0, weight=1)

        self._log_box = ctk.CTkTextbox(
            log_frame, font=ctk.CTkFont(family="Consolas", size=12),
            activate_scrollbars=True, wrap="word", state="disabled")
        self._log_box.grid(row=0, column=0, padx=8, pady=8, sticky="nsew")

        clear_btn = ctk.CTkButton(
            log_frame, text="Clear Log", width=90, height=26,
            fg_color="transparent", border_width=1,
            command=self._clear_log)
        clear_btn.grid(row=1, column=0, padx=8, pady=(0, 8), sticky="e")

    # ── Event Handlers ────────────────────────────────────────────────────────

    def _on_lookup(self):
        mid = _parse_id(self._input_var.get())
        if mid is None:
            messagebox.showerror("Invalid Input",
                                 "Enter a numeric Manager ID or a valid SofaScore manager URL.")
            return
        self._name_label.configure(text="Resolving…", text_color="#FFA726")
        self._lookup_btn.configure(state="disabled")

        def _worker():
            try:
                name = lookup_manager_name(mid)
                self._log_queue.put(("name", name))
            except Exception as exc:
                self._log_queue.put(("name_err", str(exc)))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_run(self):
        mid = _parse_id(self._input_var.get())
        if mid is None:
            messagebox.showerror("Invalid Input",
                                 "Enter a numeric Manager ID or a valid SofaScore manager URL.")
            return

        self._run_btn.configure(state="disabled")
        self._lookup_btn.configure(state="disabled")
        self._open_btn.configure(state="disabled")
        self._output_path = None
        self._progress.start()
        self._log("─" * 55)
        self._log(f"Starting scraper for Manager ID: {mid}")

        def _worker():
            try:
                path = run_scraper(mid, log_fn=lambda msg: self._log_queue.put(("log", msg)))
                self._log_queue.put(("done", path))
            except Exception as exc:
                self._log_queue.put(("error", str(exc)))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_open_folder(self):
        if self._output_path and os.path.exists(self._output_path):
            folder = os.path.dirname(self._output_path)
            subprocess.Popen(["explorer", os.path.abspath(folder)])

    def _clear_log(self):
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _log(self, msg: str):
        self._log_box.configure(state="normal")
        self._log_box.insert("end", msg + "\n")
        self._log_box.see("end")
        self._log_box.configure(state="disabled")

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self._log_queue.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "name":
                    self._name_label.configure(
                        text=f"✓  {payload}", text_color="#4FC3F7")
                    self._lookup_btn.configure(state="normal")
                elif kind == "name_err":
                    self._name_label.configure(
                        text=f"Error: {payload}", text_color="#EF5350")
                    self._lookup_btn.configure(state="normal")
                elif kind == "done":
                    self._output_path = payload
                    self._progress.stop()
                    self._progress.set(1)
                    self._run_btn.configure(state="normal")
                    self._lookup_btn.configure(state="normal")
                    self._open_btn.configure(state="normal")
                    self._log(f"\n✓ Complete → {payload}")
                elif kind == "error":
                    self._progress.stop()
                    self._progress.set(0)
                    self._run_btn.configure(state="normal")
                    self._lookup_btn.configure(state="normal")
                    self._log(f"\n✗ Error: {payload}")
                    messagebox.showerror("Scraper Error", payload)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = ManagerScraperApp()
    app.mainloop()
