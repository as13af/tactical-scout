"""
Build Opta_League_CVS_2026.json from league_ranked.json,
enriched with IFFHS points / s_country / retention from
IFFHS_League_CVS_Scored_2025.json where a country match exists.

Each country's leagues are grouped together. Within each country,
leagues are sorted by globalRank ascending so the best league = tier 1.
The CVS value is taken from seasonAverageRating (0-100 Opta scale).
"""

import json
import os
from collections import defaultdict

HERE      = os.path.dirname(__file__)
IN_FILE   = os.path.join(HERE, "league_ranked.json")
IFFHS_FILE = os.path.join(HERE, "IFFHS_League_CVS_Scored_2025.json")
OUT_FILE  = os.path.join(HERE, "Opta_League_CVS_2026.json")

with open(IN_FILE, encoding="utf-8") as f:
    raw = json.load(f)

# ------------------------------------------------------------------
# Country name normalisation (align with standard football naming)
# ------------------------------------------------------------------
COUNTRY_MAP = {
    "Türkiye":    "Turkey",
    "Czechia":    "Czech Republic",
    "China PR":   "China",
}

def norm_country(name: str) -> str:
    return COUNTRY_MAP.get(name, name)

# ------------------------------------------------------------------
# 1. Filter to entries with valid globalRank and seasonAverageRating.
#    Also drop any entry whose countryName is suspiciously an ID
#    (Opta API sometimes sets countryName = countryId for unknown countries).
# ------------------------------------------------------------------
def looks_like_id(name: str) -> bool:
    """Return True if the name is all lowercase alphanumeric (an Opta ID)."""
    return len(name) > 8 and name.replace(" ", "").isalnum() and name == name.lower() and " " not in name

valid = [
    e for e in raw
    if e.get("globalRank") is not None
    and e.get("seasonAverageRating") is not None
    and e.get("seasonAverageRating") > 0
    and not looks_like_id(e.get("countryName", ""))
]

# Apply country name normalisation
for e in valid:
    e["countryName"] = norm_country(e["countryName"])

# ------------------------------------------------------------------
# 2. Load IFFHS data and build a lookup by lowercase country name
# ------------------------------------------------------------------
with open(IFFHS_FILE, encoding="utf-8") as f:
    iffhs_raw = json.load(f)

iffhs_lookup = {
    e["country"].lower(): e
    for e in iffhs_raw.get("rankings", [])
    if e.get("s_country") is not None
}

IFFHS_PARAMS = iffhs_raw.get("parameters", {})

# ------------------------------------------------------------------
# 3. Group by country, then sort each country's leagues by globalRank
# ------------------------------------------------------------------
by_country = defaultdict(list)
for e in valid:
    by_country[e["countryName"]].append(e)

# Sort each country's leagues: best globalRank (lowest number) = tier 1
for country in by_country:
    by_country[country].sort(key=lambda x: x["globalRank"])

# ------------------------------------------------------------------
# 4. Find best global rank to build country rankings
# ------------------------------------------------------------------
country_best = {
    c: leagues[0]["globalRank"]
    for c, leagues in by_country.items()
}
# Sort countries by the rank of their tier-1 league (best = rank 1)
sorted_countries = sorted(country_best.items(), key=lambda x: x[1])

# ------------------------------------------------------------------
# Helper: derive s_country and retention from Opta CVS when IFFHS
# has no entry for that country.
#   s_country = opta_cvs (Opta is already 0-100)
#   retention  = 1 - (delta_base - alpha * s_country / 100)
# ------------------------------------------------------------------
DELTA_BASE = IFFHS_PARAMS.get("delta_base", 0.55)
ALPHA      = IFFHS_PARAMS.get("alpha",      0.2)

def derive_retention(s_country: float) -> float:
    return round(1.0 - (DELTA_BASE - ALPHA * s_country / 100.0), 6)

# ------------------------------------------------------------------
# 5. Build output structure
# ------------------------------------------------------------------
rankings = []
iffhs_matched = 0
for rank_order, (country, _best_rank) in enumerate(sorted_countries, start=1):
    leagues = by_country[country]
    confederation = leagues[0].get("confederationName", "Unknown")

    # -- IFFHS merge --------------------------------------------------
    iffhs = iffhs_lookup.get(country.lower())
    if iffhs:
        iffhs_matched += 1
        iffhs_points    = iffhs.get("points")
        s_country       = round(float(iffhs["s_country"]), 4)
        retention       = round(float(iffhs["retention"]), 6)
        iffhs_rank      = iffhs.get("rank")
    else:
        # Derive from Opta tier-1 CVS
        tier1_cvs       = leagues[0]["seasonAverageRating"]
        iffhs_points    = None
        s_country       = round(tier1_cvs, 4)
        retention       = derive_retention(tier1_cvs)
        iffhs_rank      = None

    divisions = []
    for tier, league in enumerate(leagues, start=1):
        divisions.append({
            "tier": tier,
            "name": league["leagueName"],
            "cvs": round(league["seasonAverageRating"], 4),
            "opta_top10": round(league["top10Rating"], 4) if league.get("top10Rating") else None,
            "opta_top5":  round(league["top5Rating"],  4) if league.get("top5Rating")  else None,
            "global_rank": league["globalRank"],
            "confederation_rank": league.get("confederationRank"),
            "league_id": league["leagueId"]
        })

    entry = {
        "rank": rank_order,
        "country": country,
        "confederation": confederation,
        "s_country": s_country,
        "retention": retention,
        "divisions": divisions
    }
    if iffhs_points is not None:
        entry["iffhs_points"] = iffhs_points
    if iffhs_rank is not None:
        entry["iffhs_rank"] = iffhs_rank

    rankings.append(entry)

# ------------------------------------------------------------------
# 6. Write output
# ------------------------------------------------------------------
output = {
    "source": "Opta Power Rankings (The Analyst / Stats Perform)",
    "endpoint": "https://dataviz.theanalyst.com/opta-power-rankings/league-meta.json",
    "iffhs_source": "IFFHS_League_CVS_Scored_2025.json",
    "year": 2026,
    "note": (
        "cvs = seasonAverageRating (Opta, 0-100 scale). "
        "s_country / retention from IFFHS where available, "
        "otherwise derived: s_country = Opta tier-1 cvs; "
        "retention = 1 - (0.55 - 0.2 * s_country / 100)."
    ),
    "parameters": {
        "delta_base": DELTA_BASE,
        "alpha": ALPHA,
        "retention_formula": "1 - (delta_base - alpha * s_country / 100)"
    },
    "total_leagues": len(valid),
    "total_countries": len(rankings),
    "iffhs_matched_countries": iffhs_matched,
    "rankings": rankings
}

with open(OUT_FILE, "w", encoding="utf-8") as f:
    json.dump(output, f, indent=2, ensure_ascii=False)

print(f"Written {OUT_FILE}")
print(f"Countries: {len(rankings)}, Leagues: {len(valid)}, IFFHS matched: {iffhs_matched}")
print()
print("Top 20 countries (by tier-1 league rank):")
for entry in rankings[:20]:
    d = entry["divisions"][0]
    iffhs_str = f"  IFFHS pts={entry.get('iffhs_points', 'n/a')}  s={entry['s_country']}  ret={entry['retention']}"
    print(f"  {entry['rank']:3d}. [{entry['country']}] {d['name']} => CVS {d['cvs']:.4f}{iffhs_str}")
