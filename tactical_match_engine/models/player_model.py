"""
Player model definition.
"""
from typing import Dict, Any

class Player:
    """Represents a football player for compatibility analysis."""
    def __init__(self, data: Dict[str, Any]):
        self.id: str = data["id"]
        self.name: str = data["name"]
        self.age: int = data["age"]
        if self.age <= 0:
            raise ValueError("Player age must be positive.")
        self.position: str = data["position"]
        self.preferred_foot: str = data["preferred_foot"]
        self.height_cm: int = data["height_cm"]
        self.weight_kg: int = data["weight_kg"]
        self.current_league_intensity: float = data["current_league_intensity"]
        if not (0.0 <= self.current_league_intensity <= 1.0):
            raise ValueError("League intensity must be between 0 and 1.")
        self.tactical_familiarity: Dict[str, Any] = data["tactical_familiarity"]
        # Validate all familiarity values between 0 and 1
        for fam_group in self.tactical_familiarity.values():
            if isinstance(fam_group, dict):
                for v in fam_group.values():
                    if not (0.0 <= v <= 1.0):
                        raise ValueError("All familiarity values must be between 0 and 1.")
        self.stats: Dict[str, Any] = data["stats"]
        # Validate all stats numeric and >= 0
        for k, v in self.stats.items():
            if not (isinstance(v, (int, float)) and v >= 0):
                raise ValueError(f"Stat '{k}' must be numeric and >= 0.")
