import json
import time
import re
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager

# ── Config ────────────────────────────────────────────────────────────────────
TOURNAMENT_ID = "692"
SEASON_ID     = "77849"
OUTPUT_DIR    = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)

# ── Setup Selenium ────────────────────────────────────────────────────────────
options = webdriver.ChromeOptions()
options.set_capability('goog:loggingPrefs', {"performance": "ALL", "browser": "ALL"})
driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=options)
driver.set_page_load_timeout(15)

try:
    driver.get("https://www.sofascore.com/football")
except:
    pass
time.sleep(4)

# ── Helpers ───────────────────────────────────────────────────────────────────
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
    if isinstance(result, str) and result.startswith("ERROR:"):
        raise Exception(result)
    return json.loads(result)

def strip_field_translations(obj):
    if isinstance(obj, dict):
        obj.pop("fieldTranslations", None)
        for v in obj.values():
            strip_field_translations(v)
    elif isinstance(obj, list):
        for item in obj:
            strip_field_translations(item)

def safe_name(s):
    s = re.sub(r'[^\w]', '_', s)
    return re.sub(r'_+', '_', s).strip("_")

# ── Fetch standings ───────────────────────────────────────────────────────────
print("Fetching standings...")
standings_data = browser_fetch(
    driver,
    f"https://api.sofascore.com/api/v1/tournament/{TOURNAMENT_ID}/season/{SEASON_ID}/standings/total"
)
driver.quit()

strip_field_translations(standings_data)

# ── Enrich rows with team_id at top level ─────────────────────────────────────
for group in standings_data.get("standings", []):
    for row in group.get("rows", []):
        row["team_id"] = row["team"]["id"]

# ── Extract competition name for filename ─────────────────────────────────────
competition_name = "Unknown_Competition"
for group in standings_data.get("standings", []):
    tourney = group.get("tournament", {}).get("uniqueTournament", {})
    if tourney.get("name"):
        competition_name = safe_name(tourney["name"])
        break

# ── Print summary ─────────────────────────────────────────────────────────────
print(f"\nCompetition: {competition_name}")
print(f"{'Pos':<4} {'Team':<35} {'ID':<10} {'P':<4} {'W':<4} {'D':<4} {'L':<4} {'GF':<5} {'GA':<5} {'GD':<6} {'Pts'}")
print("-" * 95)

for group in standings_data.get("standings", []):
    for row in group.get("rows", []):
        promotion  = row.get("promotion", {}).get("text", "")
        marker     = " ↑" if promotion == "Promotion" else " ↗" if "Playoffs" in promotion else " ↓" if "Relegation" in promotion else ""
        deductions = " *" if row.get("descriptions") else ""
        print(
            f"{row['position']:<4} "
            f"{row['team']['name'][:34]:<35} "
            f"{row['team_id']:<10} "
            f"{row['matches']:<4} "
            f"{row['wins']:<4} "
            f"{row['draws']:<4} "
            f"{row['losses']:<4} "
            f"{row['scoresFor']:<5} "
            f"{row['scoresAgainst']:<5} "
            f"{row['scoreDiffFormatted']:<6} "
            f"{row['points']}{marker}{deductions}"
        )

# ── Save JSON ─────────────────────────────────────────────────────────────────
out_path = OUTPUT_DIR / f"Standings_{competition_name}_{TOURNAMENT_ID}_Season_{SEASON_ID}.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(standings_data, f, indent=4, ensure_ascii=False)

print(f"\nSaved → {out_path}")