import pytest
from tactical_match_engine.models.player_model import Player
from tactical_match_engine.models.club_model import Club
from tactical_match_engine.services.compatibility_service import CompatibilityService

def test_full_compatibility_normal():
    player_data = {
        "id": "p1",
        "name": "Test Player",
        "age": 24,
        "position": "CM",
        "preferred_foot": "right",
        "height_cm": 180,
        "weight_kg": 75,
        "current_league_intensity": 0.8,
        "tactical_familiarity": {"formation_experience": {"4-3-3": 0.9}},
        "stats": {"progressive_passes_per90": 7, "progressive_carries_per90": 5, "goals": 8, "assists": 6}
    }
    club_data = {
        "id": "c1",
        "name": "Test Club",
        "league_intensity": 0.8,
        "base_formation": "4-3-3",
        "tactical_profile": {"progressive_passes_per90": 1, "progressive_carries_per90": 1},
        "team_average_stats": {"progressive_passes_per90": 5, "progressive_carries_per90": 4, "goals": 6, "assists": 5},
        "development_model": {"ideal_age_min": 20, "ideal_age_max": 27}
    }
    player = Player(player_data)
    club = Club(club_data)
    service = CompatibilityService(player, club)
    result = service.calculate_full_compatibility()
    assert isinstance(result, dict)
    assert "scores" in result
    assert "contender_simulation" in result
    assert "explanations" in result

def test_full_compatibility_invalid():
    player_data = {"id": "p1", "name": "Test Player", "age": -1, "position": "CM", "preferred_foot": "right", "height_cm": 180, "weight_kg": 75, "current_league_intensity": 0.8, "tactical_familiarity": {"formation_experience": {"4-3-3": 0.9}}, "stats": {"progressive_passes_per90": 7, "progressive_carries_per90": 5, "goals": 8, "assists": 6}}
    club_data = {"id": "c1", "name": "Test Club", "league_intensity": 0.8, "base_formation": "4-3-3", "tactical_profile": {"progressive_passes_per90": 1, "progressive_carries_per90": 1}, "team_average_stats": {"progressive_passes_per90": 5, "progressive_carries_per90": 4, "goals": 6, "assists": 5}, "development_model": {"ideal_age_min": 20, "ideal_age_max": 27}}
    with pytest.raises(ValueError):
        player = Player(player_data)
        club = Club(club_data)
        service = CompatibilityService(player, club)
        service.calculate_full_compatibility()
