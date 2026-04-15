import json
import time
import re
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np
from scipy.ndimage import gaussian_filter
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager

# ── Setup Selenium ───────────────────────────────────────────────────────────
options = webdriver.ChromeOptions()
options.set_capability(
    'goog:loggingPrefs', {"performance": "ALL", "browser": "ALL"}
)

driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=options)
driver.set_page_load_timeout(15)

try:
    driver.get('https://www.sofascore.com/football/player/toshio-lake/929630#tab:season')
except:
    pass

time.sleep(3)
driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
time.sleep(2)

# ── Extract IDs from network logs ────────────────────────────────────────────
logs = driver.execute_script("return window.performance.getEntriesByType('resource');")

player_id = tournament_id = season_id = None
player_name = "Player"

for entry in logs:
    url = entry.get("name", "")
    if "/api/v1/player/" in url and "/statistics/" in url:
        parts = url.split("/")
        try:
            player_id     = parts[parts.index("player") + 1]
            tournament_id = parts[parts.index("unique-tournament") + 1]
            season_id     = parts[parts.index("season") + 1]
            print(f"Found: player={player_id}, tournament={tournament_id}, season={season_id}")
            break
        except (ValueError, IndexError):
            continue

# Try to get player name from the page URL
current_url = driver.current_url
match = re.search(r'/player/([^/]+)/\d+', current_url)
if match:
    player_name = match.group(1).replace("-", "_").title()

if not all([player_id, tournament_id, season_id]):
    print("Could not extract IDs, using defaults.")
    player_id, tournament_id, season_id = "929630", "9", "77849"

# ── Fetch via browser fetch() ─────────────────────────────────────────────────
def browser_fetch(driver, url):
    result = driver.execute_async_script(f"""
        const callback = arguments[arguments.length - 1];
        (async () => {{
            try {{
                const response = await fetch("{url}", {{
                    headers: {{
                        "Accept": "application/json",
                        "Referer": "https://www.sofascore.com/"
                    }}
                }});
                const text = await response.text();
                callback(text);
            }} catch(e) {{
                callback("ERROR: " + e.toString());
            }}
        }})();
    """)
    return json.loads(result)

base = f"https://api.sofascore.com/api/v1/player/{player_id}/unique-tournament/{tournament_id}/season/{season_id}"

print("Fetching stats...")
stats_data   = browser_fetch(driver, f"{base}/statistics/overall")
print("Fetching heatmap...")
heatmap_data = browser_fetch(driver, f"{base}/heatmap/overall")
print("Fetching characteristics...")
char_data    = browser_fetch(driver, f"https://api.sofascore.com/api/v1/player/{player_id}/characteristics")
positions    = char_data.get("positions", [])
position_str = ", ".join(positions) if positions else "N/A"
print(f"Positions: {position_str}")
print("Fetching career statistics...")
career_data  = browser_fetch(driver, f"https://api.sofascore.com/api/v1/player/{player_id}/statistics")
seasons      = career_data.get("seasons", [])
print(f"Career seasons found: {len(seasons)}")

# ── Fetch full detailed stats for each season ─────────────────────────────────
# The career endpoint returns a trimmed version of stats, so we loop through
# each season and call the detailed endpoint individually to get all fields.
print("Fetching full stats for each season...")
for i, s in enumerate(seasons):
    t_id = s["uniqueTournament"]["id"]
    s_id = s["season"]["id"]
    url  = f"https://api.sofascore.com/api/v1/player/{player_id}/unique-tournament/{t_id}/season/{s_id}/statistics/overall"
    try:
        full = browser_fetch(driver, url)
        s["statistics_full"] = full.get("statistics", s["statistics"])
        print(f"  [{i}] {s['year']} | {s['team']['name']} → full stats fetched")
    except Exception as e:
        print(f"  [{i}] {s['year']} | {s['team']['name']} → failed: {e}")
        s["statistics_full"] = s["statistics"]  # fallback to trimmed version

driver.quit()

# ── Show all seasons and let user choose what to remove ───────────────────────
print("\n── Career Seasons Found ────────────────────────────────────────────────")
for i, s in enumerate(seasons):
    t   = s["team"]["name"]
    lg  = s["uniqueTournament"]["name"]
    yr  = s["year"]
    g   = s["statistics_full"].get("goals",       s["statistics"].get("goals", 0))
    a   = s["statistics_full"].get("assists",      s["statistics"].get("assists", 0))
    app = s["statistics_full"].get("appearances",  s["statistics"].get("appearances", 0))
    r   = s["statistics_full"].get("rating",       s["statistics"].get("rating", 0))
    print(f"  [{i}] {yr} | {t} | {lg} → {app} apps, {g}G {a}A, {r:.2f} rating")

print("\nEnter the numbers of seasons to REMOVE (comma-separated), or press Enter to keep all:")
user_input = input("Remove: ").strip()

if user_input:
    remove_indices = {int(x.strip()) for x in user_input.split(",") if x.strip().isdigit()}
    seasons = [s for i, s in enumerate(seasons) if i not in remove_indices]
    print(f"\nKept {len(seasons)} season(s).")
else:
    print(f"\nKeeping all {len(seasons)} season(s).")

career_data["seasons"] = seasons

# ── Extract competition name dynamically from the stats API response ──────────
# Instead of hardcoding "Challenger_Pro_League", we pull the tournament name
# directly from the stats_data we already fetched, then sanitize it for use
# in a filename by replacing spaces with underscores and removing odd characters.
competition_name = (
    stats_data
    .get("team", {})          # the team block doesn't have it, so we check seasons
    and None                   # this path won't work — use career seasons instead
)

# The cleanest source is the matching season in career_data by tournament_id
competition_name = "Unknown_Competition"
for s in career_data.get("seasons", []):
    if str(s["uniqueTournament"]["id"]) == str(tournament_id):
        raw_name = s["uniqueTournament"]["name"]
        # Sanitize: replace spaces with underscores, remove non-alphanumeric chars
        competition_name = re.sub(r'[^\w]', '_', raw_name)
        competition_name = re.sub(r'_+', '_', competition_name).strip("_")
        break

print(f"\nCompetition: {competition_name}")

# ── Remove all fieldTranslations from the data ───────────────────────────────
def strip_field_translations(obj):
    if isinstance(obj, dict):
        obj.pop("fieldTranslations", None)
        for v in obj.values():
            strip_field_translations(v)
    elif isinstance(obj, list):
        for item in obj:
            strip_field_translations(item)

strip_field_translations(stats_data)
strip_field_translations(heatmap_data)
strip_field_translations(career_data)

heatmap_data.pop("events", None)

# ── Save JSON ─────────────────────────────────────────────────────────────────
stats  = stats_data["statistics"]
team   = stats_data["team"]["name"]
points = heatmap_data["points"]

output_dir = Path("output")
output_dir.mkdir(exist_ok=True)

json_filename = output_dir / f"Player_{player_id}_{competition_name}_{tournament_id}_Season_{season_id}.json"

combined = {
    "player_id": player_id,
    "tournament_id": tournament_id,
    "season_id": season_id,
    "positions": positions,
    "statistics": stats_data,
    "heatmap": heatmap_data,
    "career_statistics": career_data
}

with open(json_filename, "w", encoding="utf-8") as f:
    json.dump(combined, f, indent=4, ensure_ascii=False)

print(f"\nSaved JSON → {json_filename}")
print(f"Player: {player_name} ({position_str}) | Team: {team}")
print(f"Goals: {stats['goals']} | Assists: {stats['assists']} | Rating: {stats['rating']:.2f}")

# ── Build heatmap grid ────────────────────────────────────────────────────────
grid = np.zeros((100, 100))
for p in points:
    x, y, count = p["x"], p["y"], p["count"]
    x = min(int(x), 99)
    y = min(int(y), 99)
    if 0 <= x <= 99 and 0 <= y <= 99:
        grid[y, x] += count

grid_smooth = gaussian_filter(grid, sigma=4)

# ── Draw pitch + heatmap ──────────────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(12, 8))
fig.patch.set_facecolor("white")
ax.set_facecolor("white")

hm = ax.imshow(
    grid_smooth,
    origin="lower",
    extent=[0, 100, 0, 100],
    cmap="Greens",
    alpha=0.85,
    aspect="auto"
)
plt.colorbar(hm, ax=ax, label="Activity density")

line_color, lw = "black", 1.5

ax.add_patch(patches.Rectangle((0, 0),        100,  100,  fill=False, edgecolor=line_color, linewidth=lw))
ax.axvline(50, color=line_color, linewidth=lw)
ax.add_patch(plt.Circle((50, 50), 9.15, color=line_color, fill=False, linewidth=lw))
ax.plot(50, 50, "o", color=line_color, markersize=3)

ax.add_patch(patches.Rectangle((0, 21.1),     16.5, 57.8, fill=False, edgecolor=line_color, linewidth=lw))
ax.add_patch(patches.Rectangle((83.5, 21.1),  16.5, 57.8, fill=False, edgecolor=line_color, linewidth=lw))
ax.add_patch(patches.Rectangle((0, 36.8),     5.5,  26.4, fill=False, edgecolor=line_color, linewidth=lw))
ax.add_patch(patches.Rectangle((94.5, 36.8),  5.5,  26.4, fill=False, edgecolor=line_color, linewidth=lw))
ax.add_patch(patches.Rectangle((-2, 44.2),    2,    11.6, fill=False, edgecolor=line_color, linewidth=lw))
ax.add_patch(patches.Rectangle((100, 44.2),   2,    11.6, fill=False, edgecolor=line_color, linewidth=lw))
ax.plot(11, 50, "o", color=line_color, markersize=3)
ax.plot(89, 50, "o", color=line_color, markersize=3)

ax.set_xlim(-3, 103)
ax.set_ylim(-3, 103)
ax.axis("off")

ax.set_title(
    f"{player_name} ({position_str}) — Heatmap | {team}\n"
    f"Apps: {stats['appearances']}  |  Goals: {stats['goals']}  |  Assists: {stats['assists']}  |  Rating: {stats['rating']:.2f}",
    color="black", fontsize=13, pad=12
)

plt.tight_layout()

png_filename = output_dir / f"Player_{player_id}_{competition_name}_{tournament_id}_Season_{season_id}_heatmap.png"
plt.savefig(png_filename, dpi=150, bbox_inches="tight")
plt.show()
print(f"Saved heatmap → {png_filename}")