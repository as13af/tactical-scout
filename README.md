# Tactical Compatibility Player

> **© 2026 [as13af](https://github.com/as13af). All rights reserved.**
> This project is proprietary. No permission is granted to copy, use, modify, or distribute any part of this work. See [LICENSE](LICENSE) for details.

A practical football recruitment web application that estimates how well a player fits a club.

It combines tactical fit, statistical fit, physical adaptation, and development fit into one final compatibility score, then explains the result in plain language, projects season-level impact, and presents all data in a browser-based interface backed by live scraped data.

---

## 1) Executive Summary (Non-Technical)

### What this project does
- Compares one player against one club.
- Produces a **Final Compatibility Score** (0% to 100%).
- Shows *why* the score is high or low — not just a number, but a full scouting narrative.
- Estimates possible season impact (xG gain, goal gain, points gain, title probability shift).
- Ranks players inside their own league using **percentile stats** (top 10%, top 25%, etc.).
- Shows how every competition compares globally using **IFFHS CVS league strength scores**.
- Generates a downloadable **CSV scouting report** per player/club analysis.

### Why this matters for decision-making
- Gives a quick first-screen before expensive scouting steps.
- Makes transfer discussions more objective and repeatable.
- Helps identify fit risk early (tactical mismatch, adaptation risk, league step-up difficulty).

### What a manager can do with it
- Browse leagues, clubs and player profiles from a live data set.
- Run a tactical compatibility check or a club fit analysis in seconds.
- Download scouting reports as CSV for use in spreadsheets or reports.
- Navigate directly from a player profile to any analysis tool with one click.

---

## 2) How the Score Is Built (Simple Explanation)

The final score combines 4 core components and 2 modifiers:

### Core Components

1. **Tactical Similarity (25% weight)**
   - Checks whether the player's style matches the club's tactical demands.
   - Uses vector similarity (cosine similarity) against an 18-role profile library.

2. **Statistical Match (20% weight)**
   - Compares player numbers vs team profile.
   - Converts differences into a compatibility score using range/sigmoid normalisation.

3. **League Suitability (40% weight)** — formerly "Physical Adaptation"
   - Compares league intensity level the player is used to vs the target club's league.
   - League intensity is sourced from Opta CVS scores (Premier League ≈ 90.9).
   - Uses **relative gap** normalisation: `gap / player_league_rating`.
   - This means the same absolute point gap penalises a player from a weaker league more than one from a stronger league — matching real-world football logic.
   - **Downward or lateral moves always score 1.0** (a Premier League player is fully qualified for any lower league).
   - **Upward moves** are scored with a steep sigmoid — small steps (e.g. Ligue 1 → EPL) score well; large jumps (e.g. Indonesian Super League → Eredivisie) score very low.

4. **Development Fit (15% weight)**
   - Checks if player age fits the club's development window.

### Modifiers

5. **League Quality Discount**
   - A separate multiplier (0–1) applied to the weighted sum.
   - Captures **cross-league stat inflation**: raw statistics from a weaker league are not directly comparable to a stronger league.
   - A player with great stats in a weaker competition receives a discount when matched against a club in a much stronger competition.
   - Returns 1.0 (no discount) for same-league or downward moves.

6. **Formation Familiarity Bonus**
   - Up to +5% effect on the base score if the player already knows the club's formation.

### Safeguard: League Gate

If league suitability is below 50%, the final score is **hard-capped at 40%** regardless of how well other dimensions score. A player simply cannot be "highly compatible" with a club when the league gap is unrealistic.

### Formula (high-level)
```
weighted_sum = 0.25×tactical + 0.20×statistical + 0.40×league_suitability + 0.15×development
base_score   = weighted_sum × league_quality_discount
               (capped at 0.40 if league_suitability < 0.50)
final_score  = base_score × (1 + formation_familiarity×0.05)
```

### Example Scenarios

| Transfer Direction | League Suitability | Discount | Typical Overall |
|---|---|---|---|
| Same league (e.g. Ajax → PSV) | 1.00 | 1.00 | 85–95% |
| Ligue 1 → Premier League | ~0.82 | ~0.79 | 60–70% |
| Eredivisie → Premier League | ~0.51 | ~0.59 | 35–45% |
| Indonesian SL → Eredivisie | ~0.32 | ~0.45 | 20–30% |
| Premier League → Indonesian SL | 1.00 | 1.00 | 85–95% |

---

## 3) Score Interpretation Guide

| Range | Meaning |
|---|---|
| **85-100%** | Strong fit — ready impact likely (same league or very close level) |
| **65-84%** | Good fit — mild adaptation period, use with role/price context |
| **45-64%** | Medium fit — noticeable league step, needs development plan |
| **25-44%** | High-risk — significant league gap, long adaptation period expected |
| **Below 25%** | Unrealistic — league quality gap too large for reliable projection |

> This tool is a decision support system, not a final decision maker.

---

## 4) Web Application - Pages & Features

The webapp runs on **Flask** and is the primary interface for the tool.

### Navigation

| Page | URL | Description |
|---|---|---|
| Home | `/` | Dashboard with database stats (players, clubs, competitions) |
| Competitions | `/competitions` | Browse all available leagues by country |
| Competition detail | `/competition/<country>/<competition>` | Club table, standings, CVS league badge |
| Club overview | `/competition/<country>/<competition>/<club>` | Squad roster with stats per player |
| Player profile | `/player/<country>/<competition>/<club>/<player>` | Full stats, heatmap, CVS badge, quick-links |
| Player search | `/players` | Cross-league player search with position/club filters |
| Compare | `/compare` | Side-by-side stat comparison between two players |
| Tactical Compatibility | `/compatibility` | Player-vs-role compatibility calculator |
| Club Fit Analysis | `/club_compatibility` | Full player-vs-club fit analysis with narrative + CSV export |
| League Rankings | `/league_rankings` | IFFHS global league rankings browser |
| Scatter Analysis | `/scatter` | Scatter plot of any two club statistics per league |
| Data Export | `/export` | Bulk export of filtered player data to CSV |

### Key Features

#### Club Fit Scouting Narrative
- The **Club Fit Analysis** page renders a 4-section written narrative for every result:
  - Tactical Profile summary
  - Statistical Fit summary
  - Physical Adaptation summary
  - Development Fit summary
- Generated by the `explanation_generator` engine module.

#### Contender Projection Panel
- Club Fit results include a **Contender Impact** grid showing:
  - xG gain per match
  - Projected season xG
  - Goal gain
  - Points gain
  - Title probability shift
- Powered by the `contender_simulation` engine module.

#### Download Scouting Report (CSV)
- A **Download Scouting Report** button on the Club Fit page exports a CSV containing:
  - Summary scores (Tactical, Statistical, Physical, Development, Final)
  - Contender projection figures
  - All 4 narrative text sections
  - Full metric-by-metric delta table

#### Player Percentile Stats Panel
- Each player profile shows a **percentile rank panel** comparing the player against all same-position-line players in their competition.
- Bars are colour-coded (green = top 25%, amber = above median, grey = below median).
- Stats where lower is better (yellow cards, fouls, etc.) are handled correctly.

#### IFFHS League Rankings Browser
- A dedicated **Rankings** page (`/league_rankings`) shows global league strength data from the IFFHS CVS 2025 dataset.
- Filterable by confederation (UEFA, CONMEBOL, AFC, CAF, CONCACAF, OFC).
- Searchable by country name.
- Expandable accordion rows show individual division CVS scores with visual bars.

#### CVS League Strength Badge
- Every **competition page** and **player profile** displays the league''s IFFHS CVS score as a colour-coded badge (green >= 70, amber >= 40, red < 40).
- Gives instant context about the quality level of the competition the player is coming from.

#### Player Quick-Links
- Player profile pages include one-click buttons:
  - **Tactical Compatibility** - jumps to `/compatibility` pre-filled with the player
  - **Club Fit Analysis** - jumps to `/club_compatibility` pre-filled with the player

#### Scraper Integration
- The webapp includes a live **scrape trigger** (`/api/scrape`) that runs the SofaScore scraper as a background job and streams progress back to the browser.

---

## 5) Engine Modules

All scoring logic lives in `tactical_match_engine/engine/`.

| Module | Purpose |
|---|---|
| `tactical_similarity.py` | Cosine similarity between player vector and role profile |
| `statistical_match.py` | Stat-by-stat delta scoring |
| `physical_adaptation.py` | League-to-league suitability scoring (relative-gap sigmoid) + league quality discount |
| `development_fit.py` | Age window fit scoring |
| `familiarity_bonus.py` | Formation familiarity multiplier |
| `normalization.py` | `percentile_score()`, `range_score()`, `sigmoid_score()` |
| `aggregation.py` | Weighted combination of component scores with league discount and gate |
| `explanation_generator.py` | `generate_explanation()` - 4 narrative strings per analysis |
| `contender_simulation.py` | `simulate_contender_impact()` - xG/goal/points/title projection |
| `role_encoder.py` | Maps player to closest role profile vector |

---

## 6) Data Sources

### Live scraped data (SofaScore via Selenium)
- Stored in `Coding/output/<Country>/<Competition>/<Club>/`
- Player JSON files include: profile, statistics, heatmap path, positions
- Club JSON files include: season statistics, standings

### Opta / IFFHS League Rankings
- `Resources/Top_Rankings/Opta_League_CVS_2026.json` — Opta CVS power rankings per division (Premier League ≈ 90.9)
- `Resources/Top_Rankings/IFFHS_League_CVS_Scored_2025.json` — IFFHS global league strength index
- Used for league suitability scoring, league quality discount, and CVS badges throughout the app

### Role Profile Library
18 role templates organised by line in `Resources/Position Line/`:

- **Back-Line** (`1. Back-Line/`): Central Defender, Ball-Playing Defender, Wing-Back, Full-Back, Inverted Wing-Back, Inverted Full-Back
- **Mid-Line** (`2. Mid-Line/`): Defensive Midfielder, Deep-Lying Playmaker, Central Midfielder, Box-to-Box Midfielder, Advanced Playmaker, Attacking Midfielder
- **Front-Line** (`3. Front-Line/`): Winger, Inside Forward, Advanced Forward, Target Forward, False Nine, Poacher

### Sample engine data
- `tactical_match_engine/data/players/` - sample player JSON for engine testing
- `tactical_match_engine/data/clubs/` - sample club JSON for engine testing

---

## 7) How to Run (Windows)

### Requirements
- Python 3.10+ recommended
- Google Chrome + ChromeDriver (for scraper only)

### Install dependencies

From the project root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Run the Web Application

```powershell
cd Coding\webapp
python app.py
```

Then open `http://127.0.0.1:5000` in a browser.

### Run the Desktop UI App

```powershell
python -m tactical_match_engine.main
```

### Run the Example Script (console output)

```powershell
python example_runner.py
```

### Run Tests

```powershell
pytest -q
```

---

## 8) Web App API Reference

### Player & Data APIs

| Endpoint | Method | Description |
|---|---|---|
| `/api/players` | GET | List/filter players (competition, club, position) |
| `/api/player_search` | GET | Full-text player name search |
| `/api/player_detail` | GET | Full player stat object by path |
| `/api/player_percentiles` | GET | Per-stat percentile rank vs same-position peers in competition |
| `/api/league_position_avg` | GET | Average stats by position line for a competition |

### Compatibility & Fit APIs

| Endpoint | Method | Description |
|---|---|---|
| `/api/compatibility` | POST | Tactical compatibility score (player vs role) |
| `/api/compatibility_avg` | POST | Compatibility against position-line average |
| `/api/club_compatibility` | POST | Full club fit analysis (scores + narrative + contender projection) |
| `/api/club_search` | GET | Search available clubs |
| `/api/club_suitability` | GET | Club suitability breakdown |
| `/api/league_suitability` | GET | League suitability for a player |

### Export & Rankings APIs

| Endpoint | Method | Description |
|---|---|---|
| `/api/export/csv` | GET | Bulk player export to CSV |
| `/api/export_club_fit_csv` | GET | Club Fit scouting report CSV download |
| `/api/league_rankings` | GET | IFFHS global rankings JSON (optional `?confederation=` filter) |
| `/api/scatter_data` | GET | Club stat scatter data for a competition |

### Scraper APIs

| Endpoint | Method | Description |
|---|---|---|
| `/api/scrape` | POST | Start a background scrape job |
| `/api/scrape/<job_id>` | GET | Poll scrape job progress |

---

## 9) Workflow for a Regular Business User

1. Open `http://127.0.0.1:5000` in a browser.
2. Browse to **Competitions** - select a league - select a club - open a player profile.
3. Note the **CVS badge** to understand the league quality level.
4. Check the **Percentile Stats panel** to see where the player ranks in their competition.
5. Click **Club Fit Analysis** on the player page to go straight to the Club Fit tool with the player pre-filled.
6. Select the target club and click **Run Analysis**.
7. Review:
   - Final compatibility score and verdict
   - Component breakdown (Tactical / Statistical / Physical / Development)
   - The 4-section written narrative
   - Contender projection (xG, goals, points, title probability)
8. Click **Download Scouting Report (CSV)** to save the full analysis.
9. Use result as a shortlist filter - not a final decision.

---

## 10) Current Project Status

### Working
- Core compatibility calculation pipeline (all 4 components + familiarity bonus)
- Tactical/statistical/physical/development engine modules
- Explanation generation (4-section narrative)
- Contender simulation (xG / goals / points / title probability)
- Normalisation utilities (percentile, range, sigmoid)
- Full Flask webapp with 13 page templates
- Live data scraper (SofaScore / Selenium) integrated into webapp
- IFFHS CVS league rankings browser
- Player percentile stats panel
- CVS badges on all competition and player pages
- Player to analysis quick-links
- CSV scouting report export
- Scatter plot explorer for club statistics
- Desktop UI with charts (customtkinter)
- Unit tests for all core engine modules

### Placeholders / Not Yet Implemented
- `tactical_match_engine/services/pdf_generator.py` - PDF report generator (stub only)
- Batch comparison mode (many players vs one club in a ranked table)

---

## 11) Key Assumptions and Limitations

- Model quality depends on data quality - scrape accuracy matters.
- Tactical vector mapping relies on the 18-role profile library; accuracy improves with better role calibration.
- League suitability uses Opta CVS as a league intensity proxy with relative-gap normalization — this is a structural estimate, not a precise physiological measure.
- Advanced football context (injuries, dressing-room fit, salary constraints, fee strategy) is outside the current model scope.
- Always use results alongside scouting and coaching judgment.

---

## 12) Project Structure (Quick Map)

```
tactical_match_engine/        <- core scoring engine (Python package)
  engine/                     <- all scoring modules
  models/                     <- player/club data models
  services/                   <- compatibility service, JSON loader, PDF stub
  ui/                         <- desktop app components (customtkinter)
  data/                       <- sample player/club JSON for engine tests
  utils/                      <- constants, helpers, logger

Coding/
  webapp/                     <- Flask web application
    app.py                    <- all routes and API endpoints
    data_loader.py            <- filesystem helpers for output/ data
    templates/                <- 13 Jinja2 HTML templates
    static/                   <- CSS, JS, fonts
  output/                     <- scraped SofaScore data (live data)
    <Country>/<Competition>/<Club>/
      Club_*_Season_*.json
      Players/
        <PlayerName>_*.json

Resources/
  Position Line/              <- 18 role profile templates (3 lines x 6 roles)
  Top_Rankings/
    IFFHS_League_CVS_Scored_2025.json
    current.yml

tests/                        <- pytest unit tests for engine modules
```

---

## 13) One-Sentence Summary for Leadership

**This project is an explainable player-club fit engine with a full browser-based scouting interface that turns live scraped data into compatibility scores, impact projections, scouting narratives, and downloadable CSV reports — improving transfer decision speed and consistency.**
