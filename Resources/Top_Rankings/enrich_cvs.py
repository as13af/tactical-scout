"""
Enrich Opta_League_CVS_2026.json with IFFHS fields.

For every country in the Opta file:
  - If found in IFFHS: add iffhs_points, iffhs_rank, s_country, retention
    (real IFFHS values)
  - If not found: derive s_country from Opta tier-1 CVS and compute
    retention via the IFFHS formula: 1 - (delta_base - alpha * s_country/100)
"""

import json
import os

HERE       = os.path.dirname(os.path.abspath(__file__))
OPTA_FILE  = os.path.join(HERE, "Opta_League_CVS_2026.json")
IFFHS_FILE = os.path.join(HERE, "IFFHS_League_CVS_Scored_2025.json")

# ── Load both files ──────────────────────────────────────────────
with open(OPTA_FILE,  encoding="utf-8") as f:
    opta = json.load(f)

with open(IFFHS_FILE, encoding="utf-8") as f:
    iffhs_raw = json.load(f)

# ── Build IFFHS lookup (lowercase country name) ──────────────────
iffhs_lookup = {
    e["country"].lower(): e
    for e in iffhs_raw.get("rankings", [])
    if e.get("s_country") is not None
}

PARAMS     = iffhs_raw.get("parameters", {})
DELTA_BASE = PARAMS.get("delta_base", 0.55)
ALPHA      = PARAMS.get("alpha",      0.2)

def derive_retention(s_country: float) -> float:
    return round(1.0 - (DELTA_BASE - ALPHA * s_country / 100.0), 6)

# ── Enrich each country entry ────────────────────────────────────
matched = 0
for entry in opta["rankings"]:
    country_lc = entry["country"].lower()
    iffhs = iffhs_lookup.get(country_lc)

    if iffhs:
        matched += 1
        entry["s_country"]    = round(float(iffhs["s_country"]), 4)
        entry["retention"]    = round(float(iffhs["retention"]), 6)
        entry["iffhs_points"] = iffhs.get("points")
        entry["iffhs_rank"]   = iffhs.get("rank")
    else:
        # Derive from Opta tier-1 CVS
        tier1_cvs = entry["divisions"][0]["cvs"] if entry.get("divisions") else 50.0
        s_country = round(float(tier1_cvs), 4)
        entry["s_country"] = s_country
        entry["retention"] = derive_retention(s_country)
        # No IFFHS data: leave iffhs_points / iffhs_rank absent

# ── Update file-level metadata ───────────────────────────────────
opta["iffhs_source"]             = "IFFHS_League_CVS_Scored_2025.json"
opta["iffhs_matched_countries"]  = matched
opta["parameters"] = {
    "delta_base": DELTA_BASE,
    "alpha": ALPHA,
    "retention_formula": "1 - (delta_base - alpha * s_country / 100)"
}
opta["note"] = (
    "cvs = seasonAverageRating (Opta, 0-100 scale). "
    "s_country / retention from IFFHS where available, "
    "otherwise derived: s_country = Opta tier-1 cvs; "
    "retention = 1 - (0.55 - 0.2 * s_country / 100)."
)

# ── Write output ─────────────────────────────────────────────────
with open(OPTA_FILE, "w", encoding="utf-8") as f:
    json.dump(opta, f, indent=2, ensure_ascii=False)

print(f"Updated: {OPTA_FILE}")
print(f"Countries enriched: {len(opta['rankings'])}  (IFFHS matched: {matched}, derived: {len(opta['rankings']) - matched})")
print()
print("Sample — Top 10:")
for e in opta["rankings"][:10]:
    src = "IFFHS" if e.get("iffhs_points") is not None else "derived"
    print(f"  {e['rank']:3d}. [{e['country']}]  s_country={e['s_country']}  retention={e['retention']}  [{src}]")
