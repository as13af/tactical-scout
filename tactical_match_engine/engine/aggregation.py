"""
Final aggregation of compatibility scores.
"""
from typing import Optional
from tactical_match_engine.utils.constants import TACTICAL_WEIGHT, STATISTICAL_WEIGHT, PHYSICAL_WEIGHT, DEVELOPMENT_WEIGHT

# If league suitability (physical_adaptation) is below this threshold the
# overall score is capped regardless of how well other dimensions score.
# A player simply cannot be "highly compatible" with a club when the league
# gap is unrealistic.
_LEAGUE_GATE_THRESHOLD = 0.50   # < 50% suitability
_LEAGUE_GATE_CAP       = 0.40   # hard cap at 40%

def aggregate_scores(
	tactical: float,
	statistical: float,
	physical: float,
	development: float,
	league_discount: float = 1.0,
) -> float:
	"""
	Aggregates component scores using weighted sum, with a league-suitability gate
	and a league quality discount.

	final_score =
		(TACTICAL_WEIGHT * tactical +
		 STATISTICAL_WEIGHT * statistical +
		 PHYSICAL_WEIGHT * physical +
		 DEVELOPMENT_WEIGHT * development) * league_discount

	Gate: if physical (league suitability) < 0.50, the result is capped at 0.40
	so that an unrealistic league gap cannot be masked by high scores elsewhere.

	league_discount (0-1): multiplier that devalues cross-league stat inflation.
	Defaults to 1.0 (no discount) for same-league or downward moves.

	Returns value between 0 and 1.
	"""
	score = (
		TACTICAL_WEIGHT * tactical +
		STATISTICAL_WEIGHT * statistical +
		PHYSICAL_WEIGHT * physical +
		DEVELOPMENT_WEIGHT * development
	)
	score = max(0.0, min(1.0, score))

	# Apply league quality discount
	score *= max(0.0, min(1.0, league_discount))

	if physical < _LEAGUE_GATE_THRESHOLD:
		score = min(score, _LEAGUE_GATE_CAP)

	return max(0.0, min(1.0, score))
