import pytest
from tactical_match_engine.engine.aggregation import aggregate_scores

def test_normal_case():
    assert 0 <= aggregate_scores(0.8, 0.7, 0.6, 0.9) <= 1

def test_edge_case_all_zero():
    assert aggregate_scores(0, 0, 0, 0) == 0

def test_all_ones():
    assert aggregate_scores(1.0, 1.0, 1.0, 1.0) == 1.0

def test_league_discount_reduces_score():
    """league_discount < 1.0 should reduce the final score."""
    full = aggregate_scores(0.8, 0.7, 0.8, 0.9)
    discounted = aggregate_scores(0.8, 0.7, 0.8, 0.9, league_discount=0.5)
    assert discounted < full
    assert abs(discounted - full * 0.5) < 0.01

def test_league_discount_default_is_one():
    """Default league_discount=1.0 should not change the score."""
    a = aggregate_scores(0.8, 0.7, 0.8, 0.9)
    b = aggregate_scores(0.8, 0.7, 0.8, 0.9, league_discount=1.0)
    assert a == b

def test_gate_caps_score_when_physical_below_threshold():
    """Physical < 0.50 triggers the gate, capping at 0.40."""
    score = aggregate_scores(1.0, 1.0, 0.3, 1.0)
    assert score <= 0.40

def test_gate_does_not_trigger_above_threshold():
    """Physical >= 0.50 should not be capped."""
    score = aggregate_scores(1.0, 1.0, 0.6, 1.0)
    assert score > 0.40
