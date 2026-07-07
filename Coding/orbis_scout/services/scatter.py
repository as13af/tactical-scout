"""
services/scatter.py
-------------------
Field catalogues and percentile helpers used by the scatter routes/exports.
Composite stat definitions live in composite_stats.py.
"""

from composite_stats import COMPOSITE_PLAYER_STATS, compute_player_composites  # noqa: F401

CLUB_STAT_FIELDS = [
    'goalsScored', 'goalsConceded', 'assists', 'shots', 'penaltyGoals',
    'penaltiesTaken', 'successfulDribbles', 'dribbleAttempts', 'corners',
    'averageBallPossession', 'totalPasses', 'accuratePasses',
    'accuratePassesPercentage', 'totalLongBalls', 'accurateLongBalls',
    'accurateLongBallsPercentage', 'totalCrosses', 'accurateCrosses',
    'accurateCrossesPercentage', 'cleanSheets', 'interceptions', 'saves',
    'errorsLeadingToShot', 'totalDuels', 'duelsWon', 'duelsWonPercentage',
    'totalAerialDuels', 'aerialDuelsWon', 'aerialDuelsWonPercentage',
    'offsides', 'fouls', 'yellowCards', 'yellowRedCards', 'redCards',
    'shotsAgainst', 'goalKicks', 'ballRecovery', 'freeKicks', 'matches',
]


_CLUB_P90_EXCLUDE = frozenset([
    'accuratePassesPercentage', 'accurateLongBallsPercentage',
    'accurateCrossesPercentage', 'duelsWonPercentage',
    'aerialDuelsWonPercentage', 'averageBallPossession',
])


PLAYER_STAT_FIELDS = [
    'goals', 'assists', 'shots', 'totalShots', 'shotsOnTarget', 'keyPasses',
    'successfulDribbles', 'dribbles', 'dribblesSuccess',
    'tackles', 'tacklesWon', 'interceptions',
    'totalDuelsWon', 'duelsWon', 'aerialDuelsWon',
    'accuratePasses', 'accuratePassesPercentage',
    'accurateCrosses', 'accurateCrossesPercentage',
    'accurateLongBalls', 'accurateLongBallsPercentage', 'totalLongBalls',
    'totalPasses', 'totalCross',
    'yellowCards', 'redCards',
    'minutesPlayed', 'appearances', 'rating',
    'expectedGoals', 'expectedAssists',
    'bigChancesCreated', 'bigChancesMissed',
    'clearances', 'blockedShots', 'ballRecovery', 'saves',
    'fouls', 'wasFouled', 'offsides', 'touches',
    'possessionLost', 'possessionWonAttThird', 'dispossessed',
    'penaltyGoals', 'penaltiesTaken',
    'goalConversionPercentage', 'penaltyConversion',
    'successfulDribblesPercentage', 'totalDuelsWonPercentage',
    'aerialDuelsWonPercentage', 'tacklesWonPercentage',
    'groundDuelsWon', 'groundDuelsWonPercentage',
    'errorLeadToGoal', 'errorLeadToShot',
    'headedGoals', 'freeKickGoal',
    'goalsFromInsideTheBox', 'goalsFromOutsideTheBox',
    'matchesStarted',
    # raw fields used as composite components or standalone stats
    'dribbledPast', 'duelLost', 'goalsConceded', 'ownGoals',
    'passToAssist', 'accurateFinalThirdPasses', 'aerialLost',
    'inaccuratePasses', 'cleanSheet',
    # computed composite stats (not raw JSON fields)
    'attackingScore', 'possessionScore', 'defensiveScore',
]


_P90_EXCLUDE = frozenset([
    'accuratePassesPercentage', 'accurateLongBallsPercentage',
    'accurateCrossesPercentage', 'successfulDribblesPercentage',
    'totalDuelsWonPercentage', 'aerialDuelsWonPercentage',
    'goalConversionPercentage', 'penaltyConversion', 'rating',
    'tacklesWonPercentage', 'groundDuelsWonPercentage',
])


_CAT_TO_GROUP = {'G': 'GK', 'D': 'DEF', 'M': 'MID', 'F': 'FWD'}


def _percentile(sorted_vals, q):
    """Linear-interpolated percentile. `sorted_vals` ascending, `q` in [0, 1]."""
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    if n == 1:
        return float(sorted_vals[0])
    idx = q * (n - 1)
    lo  = int(idx)
    hi  = min(lo + 1, n - 1)
    return float(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (idx - lo))
