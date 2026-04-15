import pytest
from tactical_match_engine.engine.contender_simulation import simulate_contender_impact

def test_normal_case():
    result = simulate_contender_impact(0.7, 0.5)
    assert isinstance(result, dict)
    assert -1 <= result["xg_gain_per_match"] <= 1

def test_edge_case_zero_gain():
    result = simulate_contender_impact(0.5, 0.5)
    assert result["progression_gain"] == 0

def test_invalid_input():
    with pytest.raises(Exception):
        simulate_contender_impact("bad", 0.5)
