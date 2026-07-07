"""
Club model definition.
"""
from typing import Dict, Any

class Club:
    """Represents a football club for compatibility analysis."""
    def __init__(self, data: Dict[str, Any]):
        self.id: str = data["id"]
        self.name: str = data["name"]
        self.league_intensity: float = data["league_intensity"]
        if not (0.0 <= self.league_intensity <= 1.0):
            raise ValueError("Club league intensity must be between 0 and 1.")
        self.base_formation: str = data["base_formation"]
        self.tactical_profile: Dict[str, Any] = data["tactical_profile"]
        # Validate tactical profile values between 0 and 1
        for k, v in self.tactical_profile.items():
            if not (isinstance(v, (int, float)) and 0.0 <= v <= 1.0):
                raise ValueError(f"Tactical profile value '{k}' must be between 0 and 1.")
        self.team_average_stats: Dict[str, Any] = data["team_average_stats"]
        # Validate all stats numeric and >= 0
        for k, v in self.team_average_stats.items():
            if not (isinstance(v, (int, float)) and v >= 0):
                raise ValueError(f"Team stat '{k}' must be numeric and >= 0.")
        self.development_model: Dict[str, Any] = data["development_model"]
