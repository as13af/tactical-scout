"""
Development fit calculation for player and club.
"""
def calculate_development_fit(
	age: int,
	ideal_min: int,
	ideal_max: int
) -> float:
	"""
	Calculates development fit score based on age and ideal range.
	If age within [ideal_min, ideal_max]: return 1.0
	Else: score = 1 - (distance / 10), clamped to [0, 1].
	"""
	if ideal_min <= age <= ideal_max:
		return 1.0
	distance = min(abs(age - ideal_min), abs(age - ideal_max))
	score = 1.0 - (distance / 10.0)
	return max(0.0, min(1.0, score))
