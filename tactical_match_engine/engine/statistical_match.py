"""
Statistical match calculation between player and club.
Implements z-score distance and score conversion.
"""
from typing import Dict
import math

def calculate_statistical_match(
	player_stats: Dict[str, float],
	team_stats: Dict[str, float],
	assumed_std: float = 1.0
) -> float:
	"""
	Calculates statistical match score between player and team stats.
	For each shared stat: z = (player_value - team_average) / assumed_std
	Euclidean distance = sqrt(sum(z^2))
	Score = 1 / (1 + distance), clamped to [0, 1].
	Ignores stats not shared in both dictionaries.
	"""
	shared_keys = set(player_stats.keys()) & set(team_stats.keys())
	if not shared_keys:
		return 0.0
	z_scores = [
		(player_stats[k] - team_stats[k]) / assumed_std
		for k in shared_keys
	]
	distance = math.sqrt(sum(z ** 2 for z in z_scores))
	score = 1 / (1 + distance)
	return max(0.0, min(1.0, score))
