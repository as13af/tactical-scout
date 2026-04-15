"""
Normalization utilities for player and club data.

Three strategies supported:
  1. percentile_score  – real percentile from a population list (preferred)
  2. range_score       – clamps value against a reference maximum, 0→100
  3. sigmoid_score     – logistic curve, useful when only a midpoint is known
"""
import math
from typing import List, Optional


def percentile_score(value: float, population: List[float],
                     higher_is_better: bool = True) -> float:
    """
    Convert *value* to a 0-100 percentile within *population*.
    Uses the mid-point convention: score = (below + 0.5 * equal) / n * 100.
    If higher_is_better is False the score is inverted (100 - pct).
    Falls back to 50.0 when population is empty.
    """
    if not population:
        return 50.0
    n = len(population)
    below = sum(1 for x in population if x < value)
    equal = sum(1 for x in population if x == value)
    pct = (below + 0.5 * equal) / n * 100.0
    return pct if higher_is_better else (100.0 - pct)


def range_score(value: float, reference_max: float,
                higher_is_better: bool = True) -> float:
    """
    Normalize *value* to 0-100 by dividing by *reference_max*.
    Values above reference_max are clamped to 100.
    If higher_is_better is False the score is inverted.
    """
    if reference_max <= 0:
        return 50.0
    raw = max(0.0, min(1.0, value / reference_max)) * 100.0
    return raw if higher_is_better else (100.0 - raw)


def sigmoid_score(value: float, midpoint: float,
                  steepness: float = 1.0,
                  higher_is_better: bool = True) -> float:
    """
    Map *value* to 0-100 using a logistic sigmoid centred on *midpoint*.
    score = 100 / (1 + exp(-steepness * (value - midpoint)))
    """
    raw = 100.0 / (1.0 + math.exp(-steepness * (value - midpoint)))
    return raw if higher_is_better else (100.0 - raw)

