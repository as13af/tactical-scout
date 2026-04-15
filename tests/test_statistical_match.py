import pytest
from tactical_match_engine.engine.statistical_match import calculate_statistical_match

def test_normal_case():
    stats1 = {"goals": 10, "assists": 5}
    stats2 = {"goals": 8, "assists": 6}
    assert 0 <= calculate_statistical_match(stats1, stats2) <= 1

def test_edge_case_empty():
    assert calculate_statistical_match({}, {}) == 1.0

def test_invalid_input():
    with pytest.raises(Exception):
        calculate_statistical_match({"goals": "ten"}, {"goals": 8})
