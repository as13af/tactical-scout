"""
heatmap_role_analyzer.py
────────────────────────────────────────────────────────────────────────────────
Spatial zone classification and natural-language explanation generator
based on SofaScore player heatmap data.

Coordinate system (SofaScore normalized, consistent with heatmap.py):
  x : 0 = own goal (defensive end) → 100 = opponent goal (attacking end)
  y : 0 = bottom touchline → 100 = top touchline  (50 = pitch-width centre)

  Key pitch landmarks (from heatmap.py):
    Own penalty spot   : x ≈ 11
    Opponent pen. spot : x ≈ 89
    Halfway line       : x = 50
    Defensive third    : x  0 – 33
    Midfield third     : x 33 – 67
    Attacking third    : x 67 – 100

  3-channel (lateral) cutoffs:
    Right channel      : y  0 – 35
    Central channel    : y 35 – 65
    Left channel       : y 65 – 100

  9-zone grid  (third × channel):
    Labels: "{left|central|right}_{def|mid|att}"
    e.g. "left_att" = Left Attacking Third

  5-lane system (equal fifths):
    right_wide      : y  0 – 20
    right_halfspace : y 20 – 40
    central_lane    : y 40 – 60
    left_halfspace  : y 60 – 80
    left_wide       : y 80 – 100

Public API
----------
    analyze(points, raw_stats, player_name)
        → {features, zone, explanation}

    extract_spatial_features(points)
        → feature dict  (now includes nine_zones, five_lanes, dominant_zone,
                          dominant_lane)

    classify_spatial_zone(features)
        → {zone_key, zone_label, qualified_label, lateral_bias, confidence}

    generate_explanation(features, zone_info, raw_stats, player_name)
        → {summary, narrative}
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple


# ── Zone boundary constants ────────────────────────────────────────────────────

# 3-channel lateral cutoffs (tighter central)
_Y_RIGHT_MAX  = 35    # y < 35  → right channel
_Y_LEFT_MIN   = 65    # y > 65  → left channel
# central = 35 ≤ y ≤ 65

# 5-lane equal-fifths cutoffs
_LANE_BOUNDARIES = [
    (0,   20,  "right_wide"),
    (20,  40,  "right_halfspace"),
    (40,  60,  "central_lane"),
    (60,  80,  "left_halfspace"),
    (80,  100, "left_wide"),
]

# Human-readable labels for each lane key
_LANE_LABELS: Dict[str, str] = {
    "right_wide":       "Right Wide",
    "right_halfspace":  "Right Half-Space",
    "central_lane":     "Central Lane",
    "left_halfspace":   "Left Half-Space",
    "left_wide":        "Left Wide",
}

# 9-zone grid keys (third_channel) and their human-readable labels
_NINE_ZONE_LABELS: Dict[str, str] = {
    "left_def":     "Left Defensive Third",
    "central_def":  "Central Defensive Third",
    "right_def":    "Right Defensive Third",
    "left_mid":     "Left Middle Third",
    "central_mid":  "Central Middle Third",
    "right_mid":    "Right Middle Third",
    "left_att":     "Left Attacking Third",
    "central_att":  "Central Attacking Third",
    "right_att":    "Right Attacking Third",
}


# ── Spatial feature extraction ─────────────────────────────────────────────────

def extract_spatial_features(points: List[Dict]) -> Dict[str, Any]:
    """
    Compute spatial features from raw heatmap point list.
    Each point is expected to be {"x": int, "y": int, "count": int}.

    Returns
    -------
    Feature dict including:
      Core metrics (12):
        x_centroid, y_centroid, x_std, y_std,
        def_third_pct, mid_third_pct, att_third_pct,
        left_pct, right_pct, central_pct,
        width_index, total_weight, lateral_bias

      9-zone grid (dict, key = "{left|central|right}_{def|mid|att}"):
        nine_zones        – % of touches in each of the 9 pitch zones
        dominant_zone     – key of the zone with highest % activity
        dominant_zone_label – human-readable label of dominant_zone

      5-lane system (dict, key = lane name):
        five_lanes        – % of touches in each of the 5 vertical lanes
        dominant_lane     – key of the lane with highest % activity
        dominant_lane_label – human-readable label of dominant_lane
    """
    if not points:
        return _zero_features()

    total_weight = sum(p.get("count", 1) for p in points)
    if total_weight == 0:
        return _zero_features()

    # ── Weighted centroids ─────────────────────────────────────────────────────
    x_mean = sum(p["x"] * p.get("count", 1) for p in points) / total_weight
    y_mean = sum(p["y"] * p.get("count", 1) for p in points) / total_weight

    # ── Weighted standard deviations ───────────────────────────────────────────
    x_var = sum(p.get("count", 1) * (p["x"] - x_mean) ** 2 for p in points) / total_weight
    y_var = sum(p.get("count", 1) * (p["y"] - y_mean) ** 2 for p in points) / total_weight

    # ── Depth-third distributions (x-axis) ────────────────────────────────────
    def_w = sum(p.get("count", 1) for p in points if p["x"] <= 33)
    mid_w = sum(p.get("count", 1) for p in points if 33 < p["x"] <= 67)
    att_w = sum(p.get("count", 1) for p in points if p["x"] > 67)

    # ── Lateral distributions (y-axis, tighter central: 35–65) ────────────────
    # SofaScore heatmap: origin="lower" → y=0 = bottom of image = RIGHT touchline
    #                                      y=100 = top of image  = LEFT touchline
    # So left = high y (y > 65), right = low y (y < 35).
    left_w    = sum(p.get("count", 1) for p in points if p["y"] > _Y_LEFT_MIN)
    right_w   = sum(p.get("count", 1) for p in points if p["y"] < _Y_RIGHT_MAX)
    central_w = total_weight - left_w - right_w

    # Wide-channel index: touchline-hugging activity (y < 20 or y > 80)
    wide_w = sum(p.get("count", 1) for p in points if p["y"] < 20 or p["y"] > 80)

    # ── 9-zone grid ────────────────────────────────────────────────────────────
    # Each zone key = "{channel}_{third}"
    def _channel(y: float) -> str:
        if y < _Y_RIGHT_MAX:  return "right"
        if y > _Y_LEFT_MIN:   return "left"
        return "central"

    def _third(x: float) -> str:
        if x <= 33:  return "def"
        if x <= 67:  return "mid"
        return "att"

    nine_zone_weights: Dict[str, float] = {k: 0.0 for k in _NINE_ZONE_LABELS}
    for p in points:
        key = f"{_channel(p['y'])}_{_third(p['x'])}"
        nine_zone_weights[key] += p.get("count", 1)

    nine_zones = {
        k: round(v / total_weight, 4)
        for k, v in nine_zone_weights.items()
    }
    dominant_zone = max(nine_zones, key=nine_zones.__getitem__)

    # ── 5-lane system (equal fifths) ───────────────────────────────────────────
    five_lane_weights: Dict[str, float] = {lane: 0.0 for _, _, lane in _LANE_BOUNDARIES}
    for p in points:
        y = p["y"]
        for lo, hi, lane in _LANE_BOUNDARIES:
            if lo <= y < hi or (hi == 100 and y == 100):
                five_lane_weights[lane] += p.get("count", 1)
                break

    five_lanes = {
        lane: round(five_lane_weights[lane] / total_weight, 4)
        for _, _, lane in _LANE_BOUNDARIES
    }
    dominant_lane = max(five_lanes, key=five_lanes.__getitem__)

    features: Dict[str, Any] = {
        # ── core metrics ──────────────────────────────────────────────────────
        "x_centroid":    round(x_mean, 2),
        "y_centroid":    round(y_mean, 2),
        "x_std":         round(math.sqrt(x_var), 2),
        "y_std":         round(math.sqrt(y_var), 2),
        "def_third_pct": round(def_w / total_weight, 4),
        "mid_third_pct": round(mid_w / total_weight, 4),
        "att_third_pct": round(att_w / total_weight, 4),
        "left_pct":      round(left_w    / total_weight, 4),
        "right_pct":     round(right_w   / total_weight, 4),
        "central_pct":   round(central_w / total_weight, 4),
        "width_index":   round(wide_w    / total_weight, 4),
        "total_weight":  total_weight,
        # ── 9-zone grid ───────────────────────────────────────────────────────
        "nine_zones":          nine_zones,
        "dominant_zone":       dominant_zone,
        "dominant_zone_label": _NINE_ZONE_LABELS[dominant_zone],
        # ── 5-lane system ─────────────────────────────────────────────────────
        "five_lanes":          five_lanes,
        "dominant_lane":       dominant_lane,
        "dominant_lane_label": _LANE_LABELS[dominant_lane],
    }

    # Lateral bias: require a 12-point gap to call a side dominant
    lp, rp = features["left_pct"], features["right_pct"]
    if lp > rp + 0.12:
        features["lateral_bias"] = "left"
    elif rp > lp + 0.12:
        features["lateral_bias"] = "right"
    else:
        features["lateral_bias"] = "central"

    return features


def _zero_features() -> Dict[str, Any]:
    nine_zones = {k: 0.0 for k in _NINE_ZONE_LABELS}
    five_lanes = {lane: 0.0 for _, _, lane in _LANE_BOUNDARIES}
    return {
        "x_centroid": 50.0, "y_centroid": 50.0, "x_std": 0.0, "y_std": 0.0,
        "def_third_pct": 0.0, "mid_third_pct": 0.0, "att_third_pct": 0.0,
        "left_pct": 0.0, "right_pct": 0.0, "central_pct": 0.0,
        "width_index": 0.0, "total_weight": 0, "lateral_bias": "central",
        "nine_zones":          nine_zones,
        "dominant_zone":       "central_mid",
        "dominant_zone_label": _NINE_ZONE_LABELS["central_mid"],
        "five_lanes":          five_lanes,
        "dominant_lane":       "central_lane",
        "dominant_lane_label": _LANE_LABELS["central_lane"],
    }


# ── Zone classification ────────────────────────────────────────────────────────

_ZONE_LABELS: Dict[str, str] = {
    "centre_forward":    "Centre Forward",
    "wide_forward":      "Wide Forward",
    "attacking_mid":     "Attacking Midfielder",
    "wide_mid":          "Wide Midfielder",
    "central_mid":       "Central Midfielder",
    "defensive_mid":     "Defensive Midfielder",
    "fullback_wingback": "Fullback / Wing-back",
    "central_defender":  "Central Defender",
}


def classify_spatial_zone(features: Dict[str, Any]) -> Dict[str, Any]:
    """
    Classify player into one of 8 broad spatial zones based on depth and
    lateral heatmap distribution. Returns zone metadata including a
    lateral-qualified label (e.g. 'Left-Channel Fullback / Wing-back').
    """
    xc  = features["x_centroid"]
    wi  = features["width_index"]
    dp  = features["def_third_pct"]
    mp  = features["mid_third_pct"]
    ap  = features["att_third_pct"]
    lat = features["lateral_bias"]

    # Priority-ordered zone assignment
    # Wide forwards share high attacking-third presence with centre forwards,
    # so check lateral bias first — a laterally-biased attacker is a wide forward.
    if ap > 0.42 and lat != "central":
        zone_key   = "wide_forward"
        confidence = min(100, int((ap + wi) * 95))

    elif ap > 0.42:
        zone_key   = "centre_forward"
        confidence = min(100, int(ap * 160))

    elif ap > 0.25 and (mp + ap) > 0.60 and wi > 0.38:
        zone_key   = "wide_forward"
        confidence = min(100, int((ap + wi) * 95))

    elif xc > 56 and wi < 0.42:
        zone_key   = "attacking_mid"
        confidence = min(100, int(xc * 1.25))

    elif 35 <= xc <= 65 and wi >= 0.38:
        zone_key   = "wide_mid"
        confidence = min(100, int(wi * 200))

    elif 35 <= xc <= 62 and wi < 0.38:
        zone_key   = "central_mid"
        confidence = min(100, int(mp * 200))

    elif 28 <= xc < 50 and (dp + mp) > 0.72 and wi < 0.42:
        zone_key   = "defensive_mid"
        confidence = min(100, int((dp + mp) * 95))

    elif xc < 42 and wi >= 0.34:
        zone_key   = "fullback_wingback"
        confidence = min(100, int(wi * 230))

    else:
        zone_key   = "central_defender"
        confidence = min(100, int(dp * 160))

    zone_label = _ZONE_LABELS[zone_key]

    if lat == "left":
        qualified_label = f"Left-Channel {zone_label}"
    elif lat == "right":
        qualified_label = f"Right-Channel {zone_label}"
    else:
        qualified_label = f"Centre-Channel {zone_label}"

    return {
        "zone_key":        zone_key,
        "zone_label":      zone_label,
        "qualified_label": qualified_label,
        "lateral_bias":    lat,
        "confidence":      confidence,
    }


# ── Zone-aware stat keys ───────────────────────────────────────────────────────
# Each entry: (sofascore_key, display_label, is_percentage)

_ZONE_STATS: Dict[str, List[Tuple[str, str, bool]]] = {
    "central_defender": [
        ("clearances",               "clearances",   False),
        ("interceptions",            "interceptions",False),
        ("aerialDuelsWonPercentage", "aerial win",   True),
        ("totalDuelsWonPercentage",  "duel win",     True),
    ],
    "fullback_wingback": [
        ("clearances",        "clearances",       False),
        ("accurateCrosses",   "accurate crosses", False),
        ("tackles",           "tackles",          False),
        ("ballRecovery",      "ball recoveries",  False),
    ],
    "defensive_mid": [
        ("ballRecovery",            "ball recoveries", False),
        ("tackles",                 "tackles",         False),
        ("interceptions",           "interceptions",   False),
        ("totalDuelsWonPercentage", "duel win",        True),
    ],
    "central_mid": [
        ("accuratePasses",     "accurate passes", False),
        ("keyPasses",          "key passes",      False),
        ("ballRecovery",       "ball recoveries", False),
        ("successfulDribbles", "dribbles",        False),
    ],
    "wide_mid": [
        ("accurateCrosses",    "accurate crosses", False),
        ("accuratePasses",     "accurate passes",  False),
        ("ballRecovery",       "ball recoveries",  False),
        ("successfulDribbles", "dribbles",         False),
    ],
    "attacking_mid": [
        ("keyPasses",          "key passes",  False),
        ("totalShots",         "shots",       False),
        ("successfulDribbles", "dribbles",    False),
        ("goals",              "goals",       False),
    ],
    "wide_forward": [
        ("successfulDribbles", "dribbles",         False),
        ("totalShots",         "shots",            False),
        ("keyPasses",          "key passes",       False),
        ("accurateCrosses",    "accurate crosses", False),
    ],
    "centre_forward": [
        ("goals",          "goals",           False),
        ("shotsOnTarget",  "shots on target", False),
        ("totalShots",     "shots",           False),
        ("keyPasses",      "key passes",      False),
    ],
}


# ── Explanation generator ──────────────────────────────────────────────────────

def generate_explanation(
    features:    Dict[str, Any],
    zone_info:   Dict[str, Any],
    raw_stats:   Dict[str, Any],
    player_name: str,
) -> Dict[str, str]:
    """
    Generate a one-paragraph summary and a fuller narrative from spatial
    features, zone classification, and per-90 statistics.

    All stats in the narrative are expressed per-90.

    Returns
    -------
    dict with keys:
        ``summary``   – 1–2 punchy sentences (always shown on card).
        ``narrative`` – 4–6 sentence expanded report (shown on toggle).
    """
    xc  = features["x_centroid"]
    yc  = features["y_centroid"]
    wi  = features["width_index"]
    dp  = features["def_third_pct"]
    mp  = features["mid_third_pct"]
    ap  = features["att_third_pct"]
    lat = features["lateral_bias"]
    lp  = features["left_pct"]
    rp  = features["right_pct"]
    cp  = features["central_pct"]

    # 9-zone and 5-lane data
    nine_zones          = features["nine_zones"]
    dominant_zone_label = features["dominant_zone_label"]
    five_lanes          = features["five_lanes"]
    dominant_lane_label = features["dominant_lane_label"]

    zone_key = zone_info["zone_key"]
    ql       = zone_info["qualified_label"]

    minutes = float(raw_stats.get("minutesPlayed", 0) or 0)

    # ── Dominant third ─────────────────────────────────────────────────────────
    thirds = {"defensive": dp, "midfield": mp, "attacking": ap}
    dominant_third = max(thirds, key=thirds.get)
    dominant_pct   = thirds[dominant_third]

    depth_descriptor = (
        "predominantly defensive"    if dp > 0.55 else
        "defensive-leaning"          if dp > 0.40 else
        "predominantly attacking"    if ap > 0.45 else
        "attacking-leaning"          if ap > 0.30 else
        "midfield-anchored"          if mp > 0.45 else
        "balanced across all thirds"
    )

    # ── Lateral description ────────────────────────────────────────────────────
    if lat == "left":
        lat_detail = (
            f"a clear left-channel bias — {int(lp * 100)}% of activity "
            f"concentrated in the left corridor of the pitch "
            f"(central: {int(cp * 100)}%, right: {int(rp * 100)}%)"
        )
        lat_short  = "left-channel"
    elif lat == "right":
        lat_detail = (
            f"a clear right-channel bias — {int(rp * 100)}% of activity "
            f"concentrated in the right corridor of the pitch "
            f"(central: {int(cp * 100)}%, left: {int(lp * 100)}%)"
        )
        lat_short  = "right-channel"
    else:
        lat_detail = (
            f"a central positional profile — {int(cp * 100)}% of activity "
            f"through the central corridor "
            f"(left: {int(lp * 100)}%, right: {int(rp * 100)}%)"
        )
        lat_short  = "centre-channel"

    # ── 9-zone breakdown sentence ──────────────────────────────────────────────
    # Show the top 3 zones by activity for conciseness
    top_zones = sorted(nine_zones.items(), key=lambda kv: kv[1], reverse=True)[:3]
    top_zone_parts = [
        f"{_NINE_ZONE_LABELS[k]} ({int(v * 100)}%)"
        for k, v in top_zones if v > 0
    ]
    nine_zone_sentence = (
        f"9-zone breakdown — dominant zone: {dominant_zone_label}. "
        f"Top areas by activity: {', '.join(top_zone_parts)}."
    )

    # ── 5-lane breakdown sentence ──────────────────────────────────────────────
    lane_order = ["left_wide", "left_halfspace", "central_lane",
                  "right_halfspace", "right_wide"]
    lane_parts = [
        f"{_LANE_LABELS[lane]}: {int(five_lanes[lane] * 100)}%"
        for lane in lane_order
    ]
    five_lane_sentence = (
        f"5-lane profile (left → right): {' · '.join(lane_parts)}. "
        f"Dominant lane: {dominant_lane_label}."
    )

    # ── Width sentence ─────────────────────────────────────────────────────────
    wi_pct = int(wi * 100)
    if wi > 0.45:
        width_sentence = (
            f"Wide-channel activity accounts for {wi_pct}% of all touches — "
            f"a genuinely wide operator whose influence extends close to the touchlines."
        )
    elif wi > 0.28:
        width_sentence = (
            f"Moderate flank presence ({wi_pct}% in wide channels) "
            f"points to a hybrid role between central and wide."
        )
    else:
        width_sentence = (
            f"Minimal wide-channel involvement ({wi_pct}% of touches) "
            f"reinforces a narrowly-channelled, central profile."
        )

    # ── Zone breakdown sentence ────────────────────────────────────────────────
    breakdown_sentence = (
        f"Zone breakdown: {int(dp * 100)}% defensive third · "
        f"{int(mp * 100)}% midfield · {int(ap * 100)}% attacking third."
    )

    # ── Stats corroboration ────────────────────────────────────────────────────
    stat_tuples = _ZONE_STATS.get(zone_key, _ZONE_STATS["central_mid"])
    stat_parts: List[str] = []
    for stat_key, label, is_pct in stat_tuples[:3]:
        raw_val = float(raw_stats.get(stat_key, 0) or 0)
        if raw_val == 0:
            continue
        if is_pct:
            stat_parts.append(f"{raw_val:.1f}% {label}")
        elif minutes > 0:
            p90_val = raw_val / minutes * 90
            stat_parts.append(f"{p90_val:.2f} {label}/90")

    stats_sentence = (
        "The numbers support the picture: " + ", ".join(stat_parts) + "."
        if stat_parts else ""
    )

    # ── Tactical inference ─────────────────────────────────────────────────────
    _TACTICAL: Dict[str, str] = {
        "central_defender": (
            "Heatmap profile is consistent with a stay-at-home defensive specialist — "
            "positional solidity and zonal coverage take clear priority over "
            "progressive ball-carrying from deep."
        ),
        "fullback_wingback": (
            "Activity pattern is characteristic of a wide defensive player who "
            "combines defensive discipline with measured width during possession phases."
        ),
        "defensive_mid": (
            "Deep midfield anchor — spatial data points to a player who screens "
            "the defensive line and recycles possession without committing forward."
        ),
        "central_mid": (
            "Balanced presence across all three thirds marks a genuine box-to-box "
            "contributor, comfortable in both defensive recovery and build-up phases."
        ),
        "wide_mid": (
            "The wide-yet-midfield-anchored profile suggests a player who provides "
            "width in possession while retaining defensive discipline when out of the ball."
        ),
        "attacking_mid": (
            "Half-space operation with a forward lean — consistent with an attacking "
            "midfield or second-striker role during the final build-up phase."
        ),
        "wide_forward": (
            "Wide, forward-biased positioning identifies a direct wide attacker "
            "who prioritises offensive territory over defensive tracking duties."
        ),
        "centre_forward": (
            "Almost exclusively occupies opponent territory — "
            "a classic penalty-area presence with minimal involvement in deeper build-up zones."
        ),
    }
    tactical_sentence = _TACTICAL.get(zone_key, "")

    # ── Assemble summary (1–2 sentences) ──────────────────────────────────────
    summary = (
        f"{ql}. "
        f"{int(dominant_pct * 100)}% of activity in the {dominant_third} third — "
        f"{depth_descriptor} role with a {lat_short} positional profile."
    )

    # ── Assemble narrative (6–8 sentences) ────────────────────────────────────
    parts = [
        (
            f"{player_name} operates predominantly as a {ql.lower()}, "
            f"with {int(dominant_pct * 100)}% of heatmap activity "
            f"registering in the {dominant_third} third."
        ),
        breakdown_sentence,
        f"Lateral positioning shows {lat_detail}.",
        width_sentence,
        nine_zone_sentence,
        five_lane_sentence,
    ]
    if stats_sentence:
        parts.append(stats_sentence)
    if tactical_sentence:
        parts.append(tactical_sentence)

    return {
        "summary":   summary,
        "narrative": " ".join(parts),
    }


# ── Public convenience entry-point ────────────────────────────────────────────

def analyze(
    points:      List[Dict],
    raw_stats:   Dict[str, Any],
    player_name: str = "Player",
) -> Dict[str, Any]:
    """
    Full pipeline: extract features → classify zone → generate explanation.

    Parameters
    ----------
    points      : raw heatmap point list from player JSON (heatmap.points)
    raw_stats   : statistics.statistics dict from player JSON
    player_name : player display name (used in narrative prose)

    Returns
    -------
    dict:
        ``features``    – all spatial metrics
        ``zone``        – zone_key, zone_label, qualified_label, lateral_bias, confidence
        ``explanation`` – summary (str), narrative (str)
    """
    features    = extract_spatial_features(points)
    zone_info   = classify_spatial_zone(features)
    explanation = generate_explanation(features, zone_info, raw_stats, player_name)

    return {
        "features":    features,
        "zone":        zone_info,
        "explanation": explanation,
    }