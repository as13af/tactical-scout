
> `match_stats_scraper.py` and `match_stats_scraper_ui.py` open in the editor.

---

## Context

I have a SofaScore match statistics scraper built in Python. It consists of two files:

- `match_stats_scraper.py` — core scraping logic (HTTP, parsing, bulk runner)
- `match_stats_scraper_ui.py` — desktop GUI built with `customtkinter` + `tkinter.ttk`

I want a **full refactor** covering code quality, robustness, and a modernized UI.
Do NOT change what the tool does or which data it scrapes — only improve *how* it does it.

---

## Part 1 — Backend (`match_stats_scraper.py`)

### 1.1 Code Structure & Maintainability

- Split the file into logical modules if it exceeds ~300 lines:
  - `scraper/http_client.py` — requests session, retry logic, headers
  - `scraper/parser.py` — all JSON-to-dict extraction functions
  - `scraper/runner.py` — `run_scraper`, `run_bulk_scraper`
  - `scraper/models.py` — dataclasses or TypedDicts for `MatchResult`, `PlayerStat`, etc.
- Add full **type annotations** to every function signature (Python 3.10+ style, `X | None` not `Optional[X]`).
- Replace any bare `dict` return types with named `TypedDict` or `@dataclass` models.
- Replace f-string log messages scattered across functions with Python's `logging` module (use `logging.getLogger(__name__)`). Keep the `log_fn` callback for the UI but also emit to the logger.

### 1.2 HTTP & Resilience

- Wrap all HTTP calls in a `requests.Session` with:
  - Retry logic using `urllib3.util.retry.Retry` (3 retries, backoff factor 0.5, retry on 429/500/502/503/504).
  - A realistic browser `User-Agent` header.
  - A configurable `timeout` (default 15 s).
- Handle `requests.exceptions.RequestException` explicitly — log the error and return `None` rather than crashing.
- Add rate-limiting: insert a small `time.sleep(0.5)` between consecutive requests in bulk mode to avoid hammering the API.
- Detect HTTP 429 (rate limit) and back off exponentially before retrying.

### 1.3 Robustness & Safety

- Replace all `data["key"]` dict accesses with `data.get("key")` (or safe chained gets) to prevent `KeyError` crashes on unexpected API shapes.
- Validate that `extract_event_id` returns a positive integer; return `None` for anything that doesn't parse cleanly.
- Add a `validate_output(data: dict) -> bool` function that checks the scraped payload has the expected top-level keys before writing to disk.
- Never silently swallow exceptions — always log them with `logging.exception(...)`.

### 1.4 Output & Storage

- Accept an `output_dir: Path` parameter (not a string) everywhere file paths are constructed.
- Use `pathlib.Path` throughout instead of `os.path.join`.
- Write JSON with `indent=2` and `ensure_ascii=False` so Unicode team names are human-readable.
- Add an optional `dry_run: bool = False` flag to `run_bulk_scraper` that skips disk writes (useful for testing).

### 1.5 Testing Hooks

- Extract all pure data-transformation logic (parsers, ID extractors) so they can be unit-tested without network calls.
- Add docstrings to every public function explaining inputs, outputs, and possible exceptions.

---

## Part 2 — UI (`match_stats_scraper_ui.py`)

### 2.1 Architecture

- Move all business logic out of the UI class into the backend modules (Part 1). The UI should only call functions from `scraper/runner.py`.
- Replace the raw `queue.Queue` + `self.after(100, ...)` polling pattern with a cleaner `threading.Thread` + callback approach: use `self.after(0, callback)` to marshal results back to the main thread immediately when the worker finishes.
- Extract each tab into its own class that inherits from `ctk.CTkFrame`, e.g.:
  - `QueueTab(ctk.CTkFrame)`
  - `StatsPreviewTab(ctk.CTkFrame)`
  - `PlayersTab(ctk.CTkFrame)`
  - `FormationTab(ctk.CTkFrame)`
  - `LogTab(ctk.CTkFrame)`
- Define a shared `AppState` dataclass (or simple namespace) passed to each tab so they don't reach into each other's private attributes.

### 2.2 Visual Modernization

Keep `customtkinter` dark theme but apply these improvements:

- **Color palette** — define a module-level `PALETTE` dict:
  ```python
  PALETTE = {
      "accent":       "#4F8EF7",   # primary blue
      "accent_hover": "#3B7AE8",
      "success":      "#4CAF82",
      "danger":       "#E05C5C",
      "warning":      "#F0A020",
      "surface":      "#1A1B2E",
      "surface_alt":  "#12131F",
      "muted":        "#7B8499",
      "text":         "#E2E4ED",
  }
  ```
  Replace all hardcoded hex strings in the file with references to `PALETTE`.

- **Typography** — use a single font constant `APP_FONT = "Inter"` with fallback to `"Segoe UI"`. Define font presets:
  ```python
  FONT_TITLE  = ctk.CTkFont(family=APP_FONT, size=20, weight="bold")
  FONT_HEADER = ctk.CTkFont(family=APP_FONT, size=14, weight="bold")
  FONT_BODY   = ctk.CTkFont(family=APP_FONT, size=12)
  FONT_MONO   = ctk.CTkFont(family="Consolas", size=11)
  ```

- **Input card** — replace the separate single-add row and bulk textarea with a single unified input area:
  - A `CTkTextbox` (height 80) that accepts one-or-many URLs/IDs.
  - A single `+ Add to Queue` button that auto-detects single vs. bulk.
  - Show a small inline validation badge (green checkmark / red X) next to the entry after parsing.

- **Action bar** — redesign as a horizontal toolbar with icon buttons:
  - `▶ Scrape All` — accent-colored filled button (large, 44px height).
  - `✕ Clear Queue` — ghost/outline button.
  - `📂 Open Output` — success-colored, enabled only after a successful scrape.
  - A slim determinate `CTkProgressBar` below the buttons that shows per-match progress (0 → N/total) instead of an indeterminate spinner.
  - A status label that shows `Scraping 3 / 7…` during a run and `Done — 7 matches saved` on completion.

- **Queue tab** — add a drag-to-reorder hint label; color-code the status column badges as rounded pill-shaped labels (green = Done, red = Failed, yellow = Active, gray = Pending) using `ttk.Treeview` tag foreground rather than raw text.

- **Statistics Preview tab** — add a horizontal bar chart panel beneath the stats table that visualizes the currently selected statistic group. Use `tkinter.Canvas` to draw simple percentage bars (home vs away, blue vs amber). This should update whenever the user clicks a row in the stats tree.

- **Players tab**:
  - Add a search/filter `CTkEntry` above the table that filters rows in real time by player name.
  - Add column-sort on click (toggle ascending/descending, show a ▲/▼ indicator in the heading).
  - Add an `Export CSV` button alongside `Copy for Excel`.
  - Highlight the top-rated player row with a subtle left border accent using a canvas overlay or tag background.

- **Formation tab** — add a mini formation frequency bar chart (using `tkinter.Canvas`) below the results table, showing how many times each formation was used (e.g. 4-3-3 ×8, 4-2-3-1 ×3).

- **Log tab** — add log-level color coding:
  - Lines containing `✓` or `Done` → green (`#4CAF82`)
  - Lines containing `✗` or `Error` or `failed` → red (`#E05C5C`)
  - Lines containing `…` or `Scraping` → muted blue (`#7EB8F7`)
  - Use `CTkTextbox` tag configuration for this.
  - Add a `Copy Log` button alongside `Clear Log`.

### 2.3 UX Improvements

- Remember the last-used output directory across sessions using a small JSON config file at `~/.match_scraper_config.json` (load on startup, save on successful scrape).
- Add keyboard shortcuts:
  - `Ctrl+Enter` → Add to queue (when focus is in the input field)
  - `Ctrl+R` → Start scrape
  - `Delete` → Remove selected queue item
- Show a tooltip (use `tkinter.Toplevel`-based tooltip helper) on the `+ Add to Queue` button explaining accepted input formats.
- After a scrape completes, show a non-blocking `CTkFrame` toast notification at the bottom-right of the window for 3 seconds (e.g. "✓ 7 matches saved to match_output/").
- Add a window title that updates dynamically: `Match Scraper — Idle` → `Match Scraper — Scraping 3/7` → `Match Scraper — Done`.
- Prevent closing the window mid-scrape: override `protocol("WM_DELETE_WINDOW")` to show a confirmation dialog if a scrape is in progress.

### 2.4 Cross-Platform

- Replace all `subprocess.Popen(["explorer", ...])` calls with a cross-platform `open_in_file_manager(path: Path)` helper that uses:
  - Windows: `os.startfile(path)`
  - macOS: `subprocess.Popen(["open", str(path)])`
  - Linux: `subprocess.Popen(["xdg-open", str(path)])`
- Detect the platform with `sys.platform`.

---

## Part 3 — General

- Add a `requirements.txt` listing all dependencies with pinned minor versions (e.g. `customtkinter>=5.2`, `requests>=2.31`).
- Add a top-level `README.md` block comment in each file explaining:
  - What the file does.
  - How to run it.
  - Key dependencies.
- Ensure the app runs correctly on Python 3.10, 3.11, and 3.12.
- Remove the `valid_inputs` variable in `_on_run` that is computed but never used (dead code).

---

## Constraints

- Do NOT change the SofaScore API endpoints or the data fields being scraped.
- Do NOT replace `customtkinter` with another GUI framework.
- Do NOT introduce async/await (`asyncio`) — keep threading-based concurrency.
- Keep `match_stats_scraper_ui.py` as the single entry point (`if __name__ == "__main__"`).
- All new files must be importable without side effects at module level.
