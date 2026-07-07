"""
Physical adaptation / league suitability calculation between player and club.

Uses **relative gap** normalization so that the same absolute CVS difference
penalises a lower-league player more than a higher-league player:
  relative_gap = (target - player) / player

Scoring semantics (0-1 output):
  1.0 — lateral or downward move (fully qualified)
  ~0.75–0.88 — mild upward step  (relative gap ≤ 8%)
  ~0.40–0.65 — moderate step      (relative gap 8–20%)
  ~0.10–0.35 — large jump         (relative gap 20–40%)
  ~0.05–0.10 — unrealistic gap    (relative gap > 40%)

Direction is ASYMMETRIC:
  Upward moves  → steep sigmoid penalty on relative gap
  Downward/lateral moves → always 1.0 (fully qualified)

The Opta CVS scale runs ~6 (weakest tracked) to ~93 (Premier League).
Values are 0-1 after dividing by 100 in json_loader.

A separate **league quality discount** captures stat-inflation across leagues.
It is NOT a suitability score but a multiplier applied to the aggregated
compatibility score to devalue raw statistical/tactical similarity when the
league quality gap is large.
"""
import math

_MIN_GAP = 0.03          # gaps below 3 CVS pts are treated as lateral

# ── Suitability sigmoid ──────────────────────────────────────────────────────
_K   = 12.0              # steepness
_MID = 0.18              # relative gap at which suitability ≈ 0.50

# ── League quality discount sigmoid ──────────────────────────────────────────
_DISCOUNT_K   = 8.0
_DISCOUNT_MID = 0.22


def compute_league_suitability(
    player_league_rating: float,
    target_league_rating: float,
) -> float:
    """
    Core suitability formula using relative gap normalization.

    Anchors (intensity values are cvs/100):
      Indo SL (~0.62)   → Eredivisie (~0.77): rel_gap ~0.244 → ~0.30–0.40
      Indo SL (~0.62)   → Eerste Div (~0.65): rel_gap ~0.048 → ~0.75–0.88
      Eredivisie (~0.77) → EPL (~0.91):       rel_gap ~0.175 → ~0.48–0.60
      Ligue 1  (~0.86)  → EPL (~0.91):        rel_gap ~0.058 → ~0.76–0.86
      EPL (~0.91) → Ligue 1 (~0.86):          downward → 1.0
    """
    rating_gap = target_league_rating - player_league_rating

    # Lateral, downward, or negligible gap → fully qualified.
    if rating_gap <= _MIN_GAP:
        return 1.0

    # Upward move — sigmoid penalty on relative gap.
    relative_gap = rating_gap / max(player_league_rating, 0.01)
    suitability = 1.0 / (1.0 + math.exp(_K * (relative_gap - _MID)))
    return round(max(0.05, min(1.0, suitability)), 4)


def compute_league_discount(
    player_league_rating: float,
    target_league_rating: float,
) -> float:
    """
    League quality discount multiplier (0-1).

    Applied to the aggregated compatibility score to penalise cross-league
    stat inflation.  A player whose raw stats look great in a weaker league
    should not carry that full value into a much stronger one.

    Returns 1.0 for lateral/downward moves.
    """
    rating_gap = target_league_rating - player_league_rating

    if rating_gap <= _MIN_GAP:
        return 1.0

    relative_gap = rating_gap / max(player_league_rating, 0.01)
    discount = 1.0 / (1.0 + math.exp(_DISCOUNT_K * (relative_gap - _DISCOUNT_MID)))
    return round(max(0.05, min(1.0, discount)), 4)


def calculate_physical_adaptation(
    player_league_intensity: float,
    club_league_intensity: float,
) -> float:
    """
    Calculates league suitability (called "physical adaptation" for backward compat).

    *player_league_intensity* and *club_league_intensity* are 0-1 Opta CVS fractions
    (i.e. seasonAverageRating / 100).
    """
    if club_league_intensity <= 0:
        return 0.5

    return compute_league_suitability(player_league_intensity, club_league_intensity)
