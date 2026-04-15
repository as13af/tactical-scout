import pytest
from tactical_match_engine.engine.development_fit import calculate_development_fit

def test_normal_case():
    assert 0 <= calculate_development_fit(22, 20, 25) <= 1

def test_edge_case_min():
    assert calculate_development_fit(20, 20, 25) == 1.0

def test_invalid_input():
    with pytest.raises(Exception):
        calculate_development_fit(-5, 20, 25)
