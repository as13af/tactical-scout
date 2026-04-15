import pytest
from tactical_match_engine.engine.familiarity_bonus import apply_familiarity_bonus

def test_normal_case():
    base_score = 0.7
    formation = "4-3-3"
    familiarity = {"4-3-3": 0.8}
    assert 0 <= apply_familiarity_bonus(base_score, formation, familiarity) <= 1

def test_edge_case_no_familiarity():
    base_score = 0.7
    formation = "4-2-3-1"
    familiarity = {}
    assert apply_familiarity_bonus(base_score, formation, familiarity) == base_score

def test_invalid_input():
    with pytest.raises(Exception):
        apply_familiarity_bonus(0.7, "4-3-3", {"4-3-3": -1})
