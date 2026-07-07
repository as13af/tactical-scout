
"""
Explanation report generator for compatibility results.
"""

from typing import Dict, List, Optional


def generate_explanation(
    compatibility_results: Dict[str, float],
    contender_results: Dict[str, float],
    metric_deltas: Optional[List[dict]] = None,
    candidate_info: Optional[dict] = None,
    target_info: Optional[dict] = None,
) -> Dict[str, str]:
    """
    Generates structured, analytical explanations for player-club compatibility and contender impact.

    When metric_deltas, candidate_info, and target_info are provided, output is metric-grounded.
    Falls back to template text when optional arguments are absent (backwards-compatible).
    """
    tactical_similarity = compatibility_results.get("tactical_similarity", 0.0)
    statistical_match   = compatibility_results.get("statistical_match", 0.0)
    physical_adaptation = compatibility_results.get("physical_adaptation", 0.0)
    points_gain         = contender_results.get("points_gain", 0.0)
    progression_gain    = contender_results.get("progression_gain", 0.0)

    # ── Why club needs player ─────────────────────────────────────────────────
    why_club_needs_player = None
    if candidate_info and target_info:
        cand_cats  = candidate_info.get('category_scores', {})
        squad_cats = target_info.get('avg_category_scores', {})
        cat_deltas = sorted(
            [(cat, cand_cats[cat], squad_cats.get(cat, 0.0))
             for cat in cand_cats if cat in squad_cats],
            key=lambda x: x[1] - x[2],
            reverse=True
        )
        top_upgrades = [(cat, cs, ss) for cat, cs, ss in cat_deltas if cs - ss > 10][:2]
        if top_upgrades:
            sentences = [
                f"The club's {cat} output averages {ss:.0f} — the candidate scores {cs:.0f} "
                f"in the same area, offering a measurable upgrade."
                for cat, cs, ss in top_upgrades
            ]
            why_club_needs_player = " ".join(sentences)

    if not why_club_needs_player:
        if tactical_similarity > 0.85:
            why_club_needs_player = (
                "The club's tactical system aligns strongly with the player's style, "
                "indicating immediate value in key phases of play."
            )
        else:
            why_club_needs_player = (
                "The player offers tactical qualities that can address current gaps in the club's system, "
                "though some adaptation may be required."
            )

    # ── Why player fits club ──────────────────────────────────────────────────
    why_player_fits_club = None
    if metric_deltas:
        positive_deltas = sorted(
            [m for m in metric_deltas if m.get('score_delta', 0.0) > 0],
            key=lambda m: m['score_delta'],
            reverse=True
        )[:3]
        if positive_deltas:
            parts = ", ".join(
                f"{m['name']} (+{m['score_delta']:.1f})" for m in positive_deltas
            )
            why_player_fits_club = f"Key strengths relative to squad: {parts}."

    if not why_player_fits_club:
        if statistical_match < 0.6:
            why_player_fits_club = (
                "There is a statistical mismatch, suggesting the player may need time "
                "to adjust to the club's playing patterns."
            )
        else:
            why_player_fits_club = (
                "The player's statistical profile is compatible with the club's requirements, "
                "supporting a smooth integration."
            )

    if physical_adaptation < 0.75:
        why_player_fits_club += (
            " However, physical adaptation metrics indicate a potential adjustment period "
            "to reach optimal performance."
        )

    # ── Why club becomes contender ────────────────────────────────────────────
    if points_gain > 2:
        why_club_becomes_contender = (
            "The projected points gain from this transfer is significant, "
            "potentially elevating the club's title challenge."
        )
    else:
        why_club_becomes_contender = (
            "While the points gain is moderate, the transfer still offers incremental "
            "improvements to the club's competitive standing."
        )

    if progression_gain > 0.5:
        prog    = contender_results.get('player_progression_index', 0.0)
        sq_prog = contender_results.get('squad_average_progression', 0.0)
        why_club_becomes_contender += (
            f" The candidate's progressive contribution index ({prog:.2f}) exceeds "
            f"the squad average ({sq_prog:.2f}), suggesting an uplift in build-up threat."
        )
    elif progression_gain > 0:
        why_club_becomes_contender += (
            " The player's progression ability is expected to enhance "
            "the team's build-up and attacking threat."
        )

    # ── Risk assessment ───────────────────────────────────────────────────────
    risk_factors = []
    if statistical_match < 0.6:
        risk_factors.append("statistical adjustment risk")
    if physical_adaptation < 0.75:
        risk_factors.append("physical adaptation period")
    if not risk_factors:
        risk_assessment = "No major risks identified; player profile fits well with club's demands."
    else:
        risk_assessment = (
            "Potential risks include: " + ", ".join(risk_factors) +
            ". These should be managed during integration."
        )

    return {
        "why_club_needs_player":      why_club_needs_player,
        "why_player_fits_club":       why_player_fits_club,
        "why_club_becomes_contender": why_club_becomes_contender,
        "risk_assessment":            risk_assessment,
    }
