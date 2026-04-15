import pytest
from tactical_match_engine.engine.explanation_generator import generate_explanation

def test_normal_case():
    scores = {"tactical_similarity": 0.9, "statistical_match": 0.7, "physical_adaptation": 0.8, "development_fit": 0.9, "base_score": 0.8, "final_score": 0.85}
    contender = {"progression_gain": 0.2, "xg_gain_per_match": 0.016, "season_xg_gain": 0.544, "goal_gain": 0.544, "points_gain": 0.4352, "title_probability_shift": 0.1}
    result = generate_explanation(scores, contender)
    assert isinstance(result, dict)
    assert "why_club_needs_player" in result

def test_edge_case_low_stats():
    scores = {"tactical_similarity": 0.5, "statistical_match": 0.4, "physical_adaptation": 0.6, "development_fit": 0.9, "base_score": 0.5, "final_score": 0.55}
    contender = {"progression_gain": -0.1, "xg_gain_per_match": -0.008, "season_xg_gain": -0.272, "goal_gain": -0.272, "points_gain": -0.2176, "title_probability_shift": -0.05}
    result = generate_explanation(scores, contender)
    assert "risk_assessment" in result

def test_invalid_input():
    with pytest.raises(Exception):
        generate_explanation({}, None)
