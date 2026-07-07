"""
Tactical familiarity bonus calculation.
"""
from typing import Dict

def apply_familiarity_bonus(
	base_score: float,
	formation: str,
	familiarity_dict: Dict[str, float]
) -> float:
	"""
	Applies tactical familiarity bonus to the base score.
	If formation in familiarity_dict: adjusted = base_score * (1 + bonus * 0.05)
	Else: returns base_score. Clamped to [0, 1].
	"""
	if formation in familiarity_dict:
		formation_bonus = familiarity_dict[formation]
		adjusted = base_score * (1 + formation_bonus * 0.05)
		return max(0.0, min(1.0, adjusted))
	return max(0.0, min(1.0, base_score))
