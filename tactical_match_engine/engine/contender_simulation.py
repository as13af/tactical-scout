
"""
Contender impact simulation logic.
"""

from typing import Dict
import math
from ..utils import constants

def simulate_contender_impact(
	player_progression_index: float,
	squad_average_progression: float,
	total_matches: int = 34
) -> Dict[str, float]:
	"""
	Simulates the impact of a player's progression ability on a club's season outcome.

	Football logic:
	- Progression gain: Difference between player and squad average progression indices.
	- xG gain per match: Each progression point translates to expected goals (xG) using a configurable multiplier.
	- Season xG gain: xG gain per match scaled by total matches.
	- Goal gain: Approximated as total season xG gain.
	- Points gain: Each goal is worth a configurable number of points on average.
	- Title probability shift: Logistic model to estimate probability change in title race.
	All outputs are clamped to reasonable football ranges.
	"""
	# Configurable constants
	XG_PER_PROGRESSION = getattr(constants, 'XG_PER_PROGRESSION', 0.08)
	POINTS_PER_GOAL = getattr(constants, 'POINTS_PER_GOAL', 0.8)
	MIN_PROB_SHIFT = -0.5
	MAX_PROB_SHIFT = 0.5

	# 1. Progression gain
	progression_gain = player_progression_index - squad_average_progression

	# 2. xG gain per match
	xg_gain_per_match = progression_gain * XG_PER_PROGRESSION

	# Clamp xG gain per match to plausible range
	xg_gain_per_match = max(min(xg_gain_per_match, 1.0), -1.0)

	# 3. Seasonal xG gain
	season_xg_gain = xg_gain_per_match * total_matches

	# Clamp season xG gain
	season_xg_gain = max(min(season_xg_gain, 20.0), -20.0)

	# 4. Goal gain (approximate)
	goal_gain = season_xg_gain

	# 5. Points gain
	points_gain = goal_gain * POINTS_PER_GOAL

	# Clamp points gain
	points_gain = max(min(points_gain, 16.0), -16.0)

	# 6. Title probability shift (logistic model)
	probability_shift = 1 / (1 + math.exp(-points_gain)) - 0.5
	probability_shift = max(min(probability_shift, MAX_PROB_SHIFT), MIN_PROB_SHIFT)

	return {
		"progression_gain": float(round(progression_gain, 4)),
		"xg_gain_per_match": float(round(xg_gain_per_match, 4)),
		"season_xg_gain": float(round(season_xg_gain, 4)),
		"goal_gain": float(round(goal_gain, 4)),
		"points_gain": float(round(points_gain, 4)),
		"title_probability_shift": float(round(probability_shift, 4))
	}
