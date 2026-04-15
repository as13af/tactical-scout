import json
import time
import re

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

def fetch_retry(driver, url, retries=3, delay=3):
    for attempt in range(retries):
        try:
            return browser_fetch(driver, url)
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                raise

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

def extract_competition_info(standings_data):
    competition_name    = "Unknown_Competition"
    competition_country = "Unknown_Country"
    for group in standings_data.get("standings", []):
        tourney  = group.get("tournament", {}).get("uniqueTournament", {})
        category = group.get("tournament", {}).get("category", {})
        if tourney.get("name"):
            competition_name    = safe_name(tourney["name"])
            competition_country = safe_name(category.get("name", "Unknown_Country"))
            break
    return competition_name, competition_country