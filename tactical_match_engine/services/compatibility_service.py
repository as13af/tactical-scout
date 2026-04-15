import os
def get_available_players() -> list:
    """Return list of available player JSON file names (without .json)."""
    from tactical_match_engine.services.json_loader import get_available_players as _gp
    return _gp()

def get_available_clubs() -> list:
    """Return list of available club JSON file names (without .json)."""
    from tactical_match_engine.services.json_loader import get_available_clubs as _gc
    return _gc()
"""
Service for calculating full player–club compatibility.
"""
from typing import Dict, Any
from tactical_match_engine.models.player_model import Player
from tactical_match_engine.models.club_model import Club
from tactical_match_engine.engine.tactical_similarity import cosine_similarity
from tactical_match_engine.engine.statistical_match import calculate_statistical_match
from tactical_match_engine.engine.physical_adaptation import calculate_physical_adaptation, compute_league_discount
from tactical_match_engine.engine.development_fit import calculate_development_fit
from tactical_match_engine.engine.aggregation import aggregate_scores
from tactical_match_engine.engine.familiarity_bonus import apply_familiarity_bonus
from tactical_match_engine.engine.role_encoder import get_ordered_vectors
from tactical_match_engine.utils.logger import (
    log_score_breakdown, log_contender_impact, log_final_compatibility
)


class CompatibilityService:
    """
    Calculates all compatibility components and final score for a player and club.
    """
    def __init__(self, player: Player, club: Club):
        self.player = player
        self.club = club

    def calculate_full_compatibility(self) -> Dict[str, Any]:
        """
        Calculates all compatibility components, simulates contender impact,
        and generates explanations.
        Returns a structured JSON-serialisable dictionary.
        """
        # ── Tactical vectors (role-profile based) ────────────────────────────
        player_role_vector = self._get_player_role_vector()
        club_tactical_vector = self._get_club_tactical_vector()

        tactical_similarity = cosine_similarity(player_role_vector, club_tactical_vector)

        # ── Statistical match ─────────────────────────────────────────────────
        statistical_match = calculate_statistical_match(
            self.player.stats, self.club.team_average_stats
        )

        # ── Physical adaptation ───────────────────────────────────────────────
        physical_adaptation = calculate_physical_adaptation(
            self.player.current_league_intensity, self.club.league_intensity
        )

        # ── League quality discount ───────────────────────────────────────────
        league_discount = compute_league_discount(
            self.player.current_league_intensity, self.club.league_intensity
        )

        # ── Development fit ───────────────────────────────────────────────────
        dev_model = self.club.development_model
        development_fit = calculate_development_fit(
            self.player.age,
            dev_model["ideal_age_min"],
            dev_model["ideal_age_max"]
        )

        # ── Aggregate ─────────────────────────────────────────────────────────
        base_score = aggregate_scores(
            tactical_similarity, statistical_match,
            physical_adaptation, development_fit,
            league_discount=league_discount,
        )

        # ── Familiarity bonus ─────────────────────────────────────────────────
        formation = self.club.base_formation
        familiarity_dict = self.player.tactical_familiarity.get(
            "formation_experience", {}
        )
        final_score = apply_familiarity_bonus(base_score, formation, familiarity_dict)

        # ── Contender simulation ──────────────────────────────────────────────
        # SofaScore proxy keys (raw counts → converted to per-90):
        #   accurateFinalThirdPasses  ≈ progressive pass volume
        #   successfulDribbles        ≈ progressive carry volume
        # Legacy FBRef-style pre-computed per-90 keys are accepted as fallback.
        player_stats = self.player.stats
        club_stats   = self.club.team_average_stats
        minutes      = float(player_stats.get("minutesPlayed", 90) or 90)
        per90        = max(minutes / 90.0, 0.001)

        def _prog_index(stats: dict, per90_factor: float) -> float:
            # SofaScore raw counts → per-90
            ft  = float(stats.get("accurateFinalThirdPasses", 0.0) or 0.0)
            drb = float(stats.get("successfulDribbles", 0.0) or 0.0)
            if ft > 0 or drb > 0:
                return ((ft / per90_factor) + (drb / per90_factor)) / 2.0
            # FBRef pre-computed fallback
            pp = float(stats.get("progressive_passes_per90", 0.0) or 0.0)
            pc = float(stats.get("progressive_carries_per90", 0.0) or 0.0)
            return (pp + pc) / 2.0

        player_progression_index   = _prog_index(player_stats, per90)
        squad_average_progression  = _prog_index(club_stats, 1.0)  # club stats already per-90 or averaged

        from tactical_match_engine.engine.contender_simulation import simulate_contender_impact
        from tactical_match_engine.engine.explanation_generator import generate_explanation

        contender_simulation = simulate_contender_impact(
            player_progression_index=player_progression_index,
            squad_average_progression=squad_average_progression
        )

        # ── Prepare result ────────────────────────────────────────────────────
        scores = {
            "tactical_similarity": float(tactical_similarity),
            "statistical_match":   float(statistical_match),
            "physical_adaptation": float(physical_adaptation),
            "development_fit":     float(development_fit),
            "base_score":          float(base_score),
            "final_score":         float(final_score),
        }

        explanations = generate_explanation(scores, contender_simulation)

        log_score_breakdown(scores)
        log_contender_impact(contender_simulation)
        log_final_compatibility(scores["final_score"])

        return {
            "scores":               scores,
            "contender_simulation": contender_simulation,
            "explanations":         explanations,
        }

    # ── Vector helpers ────────────────────────────────────────────────────────

    def _get_player_role_vector(self):
        """
        Return the player's role-fitness category scores as a vector.

        Uses the Position Line role profile for the player's SofaScore position
        code.  Falls back to a zero-vector aligned with the club's profile keys
        when no matching role profile exists (e.g. GK).
        """
        position = getattr(self.player, "position", "MC")
        result = get_ordered_vectors(self.player.stats, position)

        if result is not None:
            player_vec, _ = result
            return player_vec

        # Fallback: map player stats to club tactical_profile keys
        keys = list(self.club.tactical_profile.keys())
        return [float(self.player.stats.get(k, 0.0)) for k in keys]

    def _get_club_tactical_vector(self):
        """
        Return the role-profile weight vector aligned with the player vector.

        The role's weight_distribution expresses what matters most for the
        position; multiplied by 100 to match the player vector's 0-100 scale.
        Falls back to the club's own tactical_profile values (×100).
        """
        position = getattr(self.player, "position", "MC")
        result = get_ordered_vectors(self.player.stats, position)

        if result is not None:
            _, weight_vec = result
            return weight_vec

        # Fallback: use club tactical_profile values scaled to 0-100
        return [float(v) * 100.0 for v in self.club.tactical_profile.values()]
