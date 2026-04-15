
"""
Explanation report generator for compatibility results.
"""

from typing import Dict

def generate_explanation(
	compatibility_results: Dict[str, float],
	contender_results: Dict[str, float]
) -> Dict[str, str]:
	"""
	Generates structured, analytical explanations for player-club compatibility and contender impact.

	Logic rules:
	- Strong tactical alignment if tactical_similarity > 0.85
	- Adjustment risk if statistical_match < 0.6
	- Adaptation period risk if physical_adaptation < 0.75
	- Highlight title impact if points_gain > 2
	- Highlight build-up improvement if progression_gain > 0
	Explanations are professional, analytical, and template-driven.
	"""
	tactical_similarity = compatibility_results.get("tactical_similarity", 0.0)
	statistical_match = compatibility_results.get("statistical_match", 0.0)
	physical_adaptation = compatibility_results.get("physical_adaptation", 0.0)
	points_gain = contender_results.get("points_gain", 0.0)
	progression_gain = contender_results.get("progression_gain", 0.0)

	# Why club needs player
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

	# Why player fits club
	if statistical_match < 0.6:
		why_player_fits_club = (
			"There is a statistical mismatch, suggesting the player may need time to adjust to the club's playing patterns."
		)
	else:
		why_player_fits_club = (
			"The player's statistical profile is compatible with the club's requirements, supporting a smooth integration."
		)
	if physical_adaptation < 0.75:
		why_player_fits_club += (
			" However, physical adaptation metrics indicate a potential adjustment period to reach optimal performance."
		)

	# Why club becomes contender
	if points_gain > 2:
		why_club_becomes_contender = (
			"The projected points gain from this transfer is significant, potentially elevating the club's title challenge."
		)
	else:
		why_club_becomes_contender = (
			"While the points gain is moderate, the transfer still offers incremental improvements to the club's competitive standing."
		)
	if progression_gain > 0:
		why_club_becomes_contender += (
			" The player's progression ability is expected to enhance the team's build-up and attacking threat."
		)

	# Risk assessment
	risk_factors = []
	if statistical_match < 0.6:
		risk_factors.append("statistical adjustment risk")
	if physical_adaptation < 0.75:
		risk_factors.append("physical adaptation period")
	if not risk_factors:
		risk_assessment = "No major risks identified; player profile fits well with club's demands."
	else:
		risk_assessment = (
			"Potential risks include: " + ", ".join(risk_factors) + ". These should be managed during integration."
		)

	return {
		"why_club_needs_player": why_club_needs_player,
		"why_player_fits_club": why_player_fits_club,
		"why_club_becomes_contender": why_club_becomes_contender,
		"risk_assessment": risk_assessment
	}
