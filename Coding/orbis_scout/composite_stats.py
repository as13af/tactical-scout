"""
composite_stats.py
------------------
Weighted composite stat definitions for the player scatter.

Each entry in COMPOSITE_PLAYER_STATS:
    (output_key, [(component_field, weight), ...])

Positive weights reward a behaviour; negative weights penalise it.
Missing components are treated as 0 (never raises KeyError).
p90 = composite_score / minutes × 90.

DESIGN NOTE (non-overlap rule):
Every raw field appears in exactly ONE composite below. This keeps the
three scores measuring distinct axes of play instead of partially
re-scoring the same actions under different weights:

  - Attacking  -> shooting + direct chance creation
  - Possession -> passing/build-up volume and accuracy
  - Defensive  -> stopping actions, duels won/lost, recoveries, and costly errors

If you need a field in more than one score, do it deliberately and
consistently (same weight, documented reason) rather than by accident.
"""

_ATTACKING_COMPONENTS = [
    ('goals',                   10.0),   # highest-value action in football
    ('shotsOnTarget',            3.0),   # threatens the goal
    ('totalShots',                1.0),   # base shooting volume
    ('keyPasses',                 3.0),   # directly creates a chance
    ('assists',                   7.0),   # second-highest value action
    ('passToAssist',              4.0),   # one step from an assist
    ('accurateCrosses',           2.0),   # successful delivery into the box
    ('successfulDribbles',        2.0),   # beats a man in a dangerous area
    ('wasFouled',                 1.0),   # draws fouls, keeps possession
    ('penaltyGoals',              5.0),   # penalty goals still count
    ('offsides',                 -1.0),   # wasteful run, breaks attacking phase
]

_POSSESSION_COMPONENTS = [
    ('accuratePasses',            1.0),   # base distribution volume
    ('accurateFinalThirdPasses',  2.0),   # progresses play into final third
    ('accurateLongBalls',         1.5),   # riskier pass, reward success
    ('inaccuratePasses',         -1.0),   # penalise wasteful passing
]

_DEFENSIVE_COMPONENTS = [
    ('clearances',                2.0),   # primary job — removes danger directly
    ('blockedShots',              2.0),   # stops a shot on goal
    ('aerialDuelsWon',            1.5),   # wins physical contest in the air
    ('totalDuelsWon',             1.0),   # ground + aerial battles won overall
    ('ballRecovery',              1.5),   # breaks up opponent's move, wins the ball back
    ('dribbledPast',             -2.0),   # beaten 1v1 — very bad for DC
    ('errorLeadToShot',          -4.0),   # most dangerous failure for a CB
    ('cleanSheet',                3.0),   # ultimate defensive outcome
    ('fouls',                    -1.0),   # gives away set pieces
    ('ownGoals',                 -5.0),   # catastrophic failure
    ('duelLost',                 -0.5),   # lost a physical/ground contest
    ('aerialLost',               -1.0),   # lost an aerial contest — worse for a defender
]

COMPOSITE_PLAYER_STATS = [
    ('attackingScore',  _ATTACKING_COMPONENTS),
    ('possessionScore', _POSSESSION_COMPONENTS),
    ('defensiveScore',  _DEFENSIVE_COMPONENTS),
]


def compute_player_composites(stats, stats_p90, minutes):
    """Inject composite stats into stats / stats_p90 dicts in-place.

    Missing components default to 0; p90 = score / minutes × 90.
    Scores are rounded to 2 decimal places.
    """
    try:
        mins = float(minutes)
    except (TypeError, ValueError):
        mins = 0.0

    for key, components in COMPOSITE_PLAYER_STATS:
        score = round(
            sum((stats.get(field) or 0) * weight for field, weight in components),
            2,
        )
        stats[key] = score
        if mins > 0:
            stats_p90[key] = round(score / mins * 90, 2)