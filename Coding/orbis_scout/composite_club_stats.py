"""
composite_club_stats.py
-----------------------
Weighted composite stat definitions for the club scatter.

Three scores per club:
  attackingScore  — goals, conversion, shot volume, set pieces, dribbles
  passingScore    — accuracy percentages (possession, pass, long ball, cross)
  defendingScore  — clean sheets, saves, interceptions, recoveries, errors

Raw mode   (stats)      — counting stats used as season totals; rates as-is.
Per-game mode (stats_p90) — counting stats divided by matches; rates as-is;
                            cleanSheets and errorsLeadingToShot remain totals.

PenConv   = penaltyGoals / penaltiesTaken × 100  (0 if no penalties taken).
cleanSheets and errorsLeadingToShot are season totals in both modes.
"""

CLUB_COMPOSITE_KEYS = ['attackingScore', 'passingScore', 'defendingScore']


def _pen_conv(stats):
    taken = stats.get('penaltiesTaken') or 0
    if not taken:
        return 0.0
    return (stats.get('penaltyGoals') or 0) / taken * 100


def compute_club_composites(stats, stats_p90, matches):
    """Inject club composite stats into stats / stats_p90 dicts in-place.

    stats      — raw season totals (from JSON)
    stats_p90  — per-match dict being built by the route
    matches    — number of matches played (int/float/str — coerced to float)
    """
    try:
        m = float(matches)
    except (TypeError, ValueError):
        m = 0.0

    pen_conv = _pen_conv(stats)

    def _g(field):
        return stats.get(field) or 0

    # ── Attacking ─────────────────────────────────────────────────────────────
    att_raw = round(
        _g('goalsScored')        * 3.0
        + pen_conv               * 1.5
        + _g('shots')            * 0.5
        + _g('corners')          * 0.4
        + _g('successfulDribbles') * 0.3
        + _g('freeKicks')        * 0.2,
        2,
    )
    stats['attackingScore'] = att_raw
    if m > 0:
        att_p90 = round(
            _g('goalsScored')        / m * 3.0
            + pen_conv               * 1.5   # rate — unchanged
            + _g('shots')            / m * 0.5
            + _g('corners')          / m * 0.4
            + _g('successfulDribbles') / m * 0.3
            + _g('freeKicks')        / m * 0.2,
            2,
        )
        stats_p90['attackingScore'] = att_p90

    # ── Passing (all percentages — raw == per-game) ───────────────────────────
    passing = round(
        _g('accuratePassesPercentage')    * 1.5
        + _g('accurateLongBallsPercentage') * 1.0
        + _g('averageBallPossession')     * 0.8
        + _g('accurateCrossesPercentage') * 0.7,
        2,
    )
    stats['passingScore'] = passing
    if m > 0:
        stats_p90['passingScore'] = passing   # percentages don't change per-match

    # ── Defending ─────────────────────────────────────────────────────────────
    # cleanSheets and errorsLeadingToShot stay as season totals in both modes.
    def_raw = round(
        _g('cleanSheets')          * 2.0
        + _g('saves')              * 0.6
        + _g('interceptions')      * 0.5
        + _g('ballRecovery')       * 0.4
        - _g('goalsConceded')      * 2.5
        - _g('errorsLeadingToShot') * 0.1,
        2,
    )
    stats['defendingScore'] = def_raw
    if m > 0:
        def_p90 = round(
            _g('cleanSheets')          * 2.0      # total
            + _g('saves')        / m   * 0.6
            + _g('interceptions') / m  * 0.5
            + _g('ballRecovery') / m   * 0.4
            - _g('goalsConceded') / m  * 2.5
            - _g('errorsLeadingToShot') * 0.1,    # total
            2,
        )
        stats_p90['defendingScore'] = def_p90
