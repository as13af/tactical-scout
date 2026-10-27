"""
services/scatter.py
-------------------
Field catalogues, label maps, percentile helpers and export-filename builders
used by the scatter routes/exports.
Composite stat definitions live in composite_stats.py.
"""

import glob
import json
import os
import re
import unicodedata

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


# Human labels for player stats — kept word-for-word in sync with STAT_LABELS in
# templates/player_scatter.html so an exported filename reads the same as the
# axis dropdown the user picked from.
PLAYER_STAT_LABELS = {
    'goals': 'Goals', 'assists': 'Assists', 'shots': 'Shots',
    'totalShots': 'Total Shots', 'shotsOnTarget': 'Shots on Target',
    'keyPasses': 'Key Passes',
    'successfulDribbles': 'Successful Dribbles', 'dribbles': 'Dribbles',
    'dribblesSuccess': 'Successful Dribbles',
    'tackles': 'Tackles', 'tacklesWon': 'Tackles Won',
    'interceptions': 'Interceptions',
    'totalDuelsWon': 'Total Duels Won', 'duelsWon': 'Duels Won',
    'aerialDuelsWon': 'Aerial Duels Won',
    'accuratePasses': 'Accurate Passes',
    'accuratePassesPercentage': 'Pass Accuracy %',
    'accurateCrosses': 'Accurate Crosses',
    'accurateCrossesPercentage': 'Cross Accuracy %',
    'accurateLongBalls': 'Accurate Long Balls',
    'accurateLongBallsPercentage': 'Long Ball Accuracy %',
    'totalLongBalls': 'Total Long Balls', 'totalPasses': 'Total Passes',
    'totalCross': 'Total Crosses',
    'yellowCards': 'Yellow Cards', 'redCards': 'Red Cards',
    'minutesPlayed': 'Minutes Played', 'appearances': 'Appearances',
    'rating': 'Rating',
    'expectedGoals': 'Expected Goals (xG)',
    'expectedAssists': 'Expected Assists (xA)',
    'bigChancesCreated': 'Big Chances Created',
    'bigChancesMissed': 'Big Chances Missed',
    'clearances': 'Clearances', 'blockedShots': 'Blocked Shots',
    'ballRecovery': 'Ball Recoveries', 'saves': 'Saves',
    'fouls': 'Fouls', 'wasFouled': 'Was Fouled', 'offsides': 'Offsides',
    'touches': 'Touches', 'possessionLost': 'Possession Lost',
    'possessionWonAttThird': 'Poss. Won Att. Third',
    'dispossessed': 'Dispossessed',
    'penaltyGoals': 'Penalty Goals', 'penaltiesTaken': 'Penalties Taken',
    'goalConversionPercentage': 'Goal Conversion %',
    'penaltyConversion': 'Penalty Conversion %',
    'successfulDribblesPercentage': 'Dribble Success %',
    'totalDuelsWonPercentage': 'Duel Win %',
    'aerialDuelsWonPercentage': 'Aerial Duel Win %',
    'tacklesWonPercentage': 'Tackle Win %',
    'groundDuelsWon': 'Ground Duels Won',
    'groundDuelsWonPercentage': 'Ground Duel Win %',
    'errorLeadToGoal': 'Errors to Goal', 'errorLeadToShot': 'Errors to Shot',
    'headedGoals': 'Headed Goals', 'freeKickGoal': 'Free Kick Goals',
    'goalsFromInsideTheBox': 'Goals Inside Box',
    'goalsFromOutsideTheBox': 'Goals Outside Box',
    'matchesStarted': 'Matches Started',
    'dribbledPast': 'Times Dribbled Past', 'duelLost': 'Duels Lost',
    'goalsConceded': 'Goals Conceded', 'ownGoals': 'Own Goals',
    'passToAssist': 'Pass to Assist',
    'accurateFinalThirdPasses': 'Accurate Final Third Passes',
    'aerialLost': 'Aerial Duels Lost', 'inaccuratePasses': 'Inaccurate Passes',
    'cleanSheet': 'Clean Sheets',
    'attackingScore': 'Attacking Score', 'possessionScore': 'Possession Score',
    'defensiveScore': 'Defensive Score',
}


# Human labels for club stats — in sync with STAT_LABELS in templates/scatter.html.
CLUB_STAT_LABELS = {
    'goalsScored': 'Goals Scored', 'goalsConceded': 'Goals Conceded',
    'assists': 'Assists', 'shots': 'Shots',
    'penaltyGoals': 'Penalty Goals', 'penaltiesTaken': 'Penalties Taken',
    'successfulDribbles': 'Successful Dribbles',
    'dribbleAttempts': 'Dribble Attempts', 'corners': 'Corners',
    'averageBallPossession': 'Avg Possession %',
    'totalPasses': 'Total Passes', 'accuratePasses': 'Accurate Passes',
    'accuratePassesPercentage': 'Pass Accuracy %',
    'totalLongBalls': 'Total Long Balls',
    'accurateLongBalls': 'Accurate Long Balls',
    'accurateLongBallsPercentage': 'Long Ball Accuracy %',
    'totalCrosses': 'Total Crosses', 'accurateCrosses': 'Accurate Crosses',
    'accurateCrossesPercentage': 'Cross Accuracy %',
    'cleanSheets': 'Clean Sheets', 'interceptions': 'Interceptions',
    'saves': 'Saves', 'errorsLeadingToShot': 'Errors Leading to Shot',
    'totalDuels': 'Total Duels', 'duelsWon': 'Duels Won',
    'duelsWonPercentage': 'Duel Win %',
    'totalAerialDuels': 'Total Aerial Duels',
    'aerialDuelsWon': 'Aerial Duels Won',
    'aerialDuelsWonPercentage': 'Aerial Duel Win %',
    'offsides': 'Offsides', 'fouls': 'Fouls',
    'yellowCards': 'Yellow Cards', 'yellowRedCards': 'Yellow-Red Cards',
    'redCards': 'Red Cards', 'shotsAgainst': 'Shots Against',
    'goalKicks': 'Goal Kicks', 'ballRecovery': 'Ball Recoveries',
    'freeKicks': 'Free Kicks', 'matches': 'Matches Played',
    'attackingScore': 'Attacking Score', 'passingScore': 'Passing Score',
    'defendingScore': 'Defending Score',
}


def player_axis_label(key, p90=False):
    """Axis label for a player stat. Mirrors labelFor() in player_scatter.html:
    only p90-eligible keys get the suffix, so a percentage axis is never
    mislabelled as per-90 in a p90-mode export."""
    base = PLAYER_STAT_LABELS.get(key, key)
    return f'{base} p90' if p90 and key not in _P90_EXCLUDE else base


def club_axis_label(key, p90=False):
    """Axis label for a club stat. Mirrors labelFor() in scatter.html."""
    base = CLUB_STAT_LABELS.get(key, key)
    return f'{base} p90' if p90 and key not in _CLUB_P90_EXCLUDE else base


# Letters NFKD leaves intact (they are not accent + base pairs), which the
# ASCII pass would otherwise delete outright — Bodø/Glimt → Bod/Glimt.
_TRANSLIT = str.maketrans({
    'ø': 'o', 'Ø': 'O', 'æ': 'ae', 'Æ': 'AE', 'œ': 'oe', 'Œ': 'OE',
    'ð': 'd', 'Ð': 'D', 'þ': 'th', 'Þ': 'Th', 'ß': 'ss',
    'ł': 'l', 'Ł': 'L', 'đ': 'd', 'Đ': 'D', 'ı': 'i',
})


def _fname_token(text):
    """One filename field: ASCII, hyphen-joined, never containing '_'.

    '_' is reserved as the field separator, so competition names that already
    carry underscores ('Challenger_Pro_League') collapse to hyphens and the
    resulting name stays unambiguously parseable.
    """
    t = str(text).translate(_TRANSLIT)
    t = unicodedata.normalize('NFKD', t)
    t = t.encode('ascii', 'ignore').decode('ascii')   # Bodø → Bodo, → dropped
    t = re.sub(r'[^A-Za-z0-9]+', '-', t)
    return t.strip('-')


def scatter_export_filename(prefix, competition, x_label, y_label,
                            position=None, extras=()):
    """Build the .xlsx download name.

        <Prefix>_<Competition>[_<Pos>]_<X-Label>--<Y-Label>[_<extra>…].xlsx

    e.g. PlayerScatter_Challenger-Pro-League_MID_Successful-Dribbles-p90--Dribble-Success.xlsx
    """
    axes = f'{_fname_token(x_label)}--{_fname_token(y_label)}'
    parts = [_fname_token(prefix), _fname_token(competition)]
    if position:
        parts.append(_fname_token(position))
    parts.append(axes)
    parts += [_fname_token(e) for e in extras if e]
    stem = '_'.join(p for p in parts if p)
    return f'{stem[:180]}.xlsx'   # keep clear of the Windows path ceiling


# ── Document source: MongoDB first, file-system fallback ─────────────────────
#
# The scatter endpoints originally scanned OUTPUT_DIR directly and never went
# through the loader abstraction in context.py. That breaks the Vercel
# deployment outright, where .vercelignore excludes Coding/output/ so the
# directory does not exist and every scatter endpoint 404s.
#
# These helpers return the RAW documents (identical shape on both paths, since
# a Mongo player/club doc is the on-disk JSON plus _country/_competition/_club/
# _file/season_id). Callers keep their own stat-extraction logic unchanged.
#
# Season: Mongo holds several seasons of a league side by side, so a query
# filtered only by country+competition would merge them into one pool and
# double-count every player. Both paths therefore resolve to a single season —
# the requested one, else the latest.

def _mongo_db():
    """Handle for the active Mongo data layer, or None when it isn't in use.

    Resolved from context._loader (the loader the app actually selected) rather
    than importing mongo_loader directly — in production they are the same
    module, but going through the active loader keeps this honest if the app
    ever swaps it, and gives tests a single seam to patch.
    """
    try:
        import context as _ctx
        if not getattr(_ctx, '_USING_MONGO', False):
            return None
        loader = getattr(_ctx, '_loader', None)
        if loader is not None and hasattr(loader, '_db'):
            return loader._db()
        import mongo_loader as _ml
        return _ml._db()
    except Exception:                                 # noqa: BLE001
        return None


def _resolve_season_mongo(db, country, competition, season_id):
    """Season to query: the requested one, else the newest.

    Mirrors mongo_loader.get_competition_seasons — season_order ascending
    (0 = current), else numeric season_id descending. Falls back to the season
    ids on the club docs when a league has no standings document.
    """
    if season_id:
        return str(season_id)
    flt = {'_country': country, '_competition': competition}
    best = None
    try:
        for doc in db.standings.find(flt, {'season_id': 1, 'season_order': 1}):
            sid = str(doc.get('season_id') or '')
            if not sid:
                continue
            order = doc.get('season_order')
            key = (order if order is not None else 9999,
                   -(int(sid) if sid.isdigit() else 0))
            if best is None or key < best[0]:
                best = (key, sid)
    except Exception:                                 # noqa: BLE001
        best = None
    if best:
        return best[1]
    try:
        return _latest_season(
            d.get('season_id') for d in db.clubs.find(flt, {'season_id': 1}))
    except Exception:                                 # noqa: BLE001
        return None


def _latest_season(season_ids):
    """Newest season id from a set — numeric when possible, else lexical."""
    sids = {str(s) for s in season_ids if s not in (None, '', 'None')}
    if not sids:
        return None
    numeric = [s for s in sids if s.isdigit()]
    return max(numeric, key=int) if numeric else max(sids)


def scatter_seasons(output_dir, country, competition):
    """[{season_id, season_name}] newest-first for the competition, or []."""
    if _mongo_db() is not None:
        try:
            import mongo_loader as _ml
            return _ml.get_competition_seasons(output_dir, country, competition)
        except Exception:                             # noqa: BLE001
            pass
    return []


def _fs_pick_season(docs, season_id):
    """Filter file-system docs to one season. Every competition folder currently
    holds exactly one season, so this is a no-op today — it exists so a future
    multi-season folder cannot silently merge seasons into one pool."""
    sid = str(season_id) if season_id else _latest_season(
        d.get('season_id') for d in docs)
    if not sid:
        return docs, None
    return [d for d in docs if str(d.get('season_id')) == sid], sid


def load_club_docs(output_dir, country, competition, season_id=None):
    """(docs, resolved_season_id, source) — raw club docs, each carrying _club."""
    db = _mongo_db()
    if db is not None:
        try:
            sid = _resolve_season_mongo(db, country, competition, season_id)
            flt = {'_country': country, '_competition': competition}
            if sid:
                flt['season_id'] = sid
            docs = []
            for doc in db.clubs.find(flt):
                doc.pop('_id', None)
                if doc.get('_club'):
                    docs.append(doc)
            if docs:
                docs.sort(key=lambda d: d.get('_club') or '')
                return docs, sid, 'mongo'
        except Exception as exc:                      # noqa: BLE001
            _log_source_fallback('clubs', country, competition, exc)

    league_dir = os.path.join(output_dir, country, competition)
    if not os.path.isdir(league_dir):
        return [], None, 'filesystem'
    docs = []
    for entry in sorted(os.scandir(league_dir), key=lambda e: e.name):
        if not entry.is_dir():
            continue
        hits = glob.glob(os.path.join(entry.path, 'Club_*_Season_*.json'))
        if not hits:
            continue
        try:
            with open(hits[0], encoding='utf-8') as fh:
                raw = json.load(fh)
        except Exception:                             # noqa: BLE001
            continue
        raw['_club'] = entry.name
        docs.append(raw)
    docs, sid = _fs_pick_season(docs, season_id)
    return docs, sid, 'filesystem'


def load_player_docs(output_dir, country, competition, season_id=None):
    """(docs, resolved_season_id, source) — raw player docs with _club/_file."""
    db = _mongo_db()
    if db is not None:
        try:
            sid = _resolve_season_mongo(db, country, competition, season_id)
            flt = {'_country': country, '_competition': competition}
            if sid:
                flt['season_id'] = sid
            docs = []
            for doc in db.players.find(flt):
                doc.pop('_id', None)
                if doc.get('_club') and doc.get('_file'):
                    docs.append(doc)
            if docs:
                docs.sort(key=lambda d: (d.get('_club') or '', d.get('_file') or ''))
                return docs, sid, 'mongo'
        except Exception as exc:                      # noqa: BLE001
            _log_source_fallback('players', country, competition, exc)

    league_dir = os.path.join(output_dir, country, competition)
    if not os.path.isdir(league_dir):
        return [], None, 'filesystem'
    docs = []
    for entry in sorted(os.scandir(league_dir), key=lambda e: e.name):
        if not entry.is_dir():
            continue
        pdir = os.path.join(entry.path, 'Players')
        if not os.path.isdir(pdir):
            continue
        for pf in sorted(os.scandir(pdir), key=lambda e: e.name):
            if not pf.name.endswith('.json'):
                continue
            try:
                with open(pf.path, encoding='utf-8') as fh:
                    raw = json.load(fh)
            except Exception:                         # noqa: BLE001
                continue
            raw['_club'] = entry.name
            raw['_file'] = pf.name
            docs.append(raw)
    docs, sid = _fs_pick_season(docs, season_id)
    return docs, sid, 'filesystem'


def load_standings_docs(output_dir, country, competition, season_id=None):
    """Raw standings documents for a competition season (may be empty)."""
    db = _mongo_db()
    if db is not None:
        try:
            sid = _resolve_season_mongo(db, country, competition, season_id)
            flt = {'_country': country, '_competition': competition}
            if sid:
                flt['season_id'] = sid
            docs = []
            for doc in db.standings.find(flt):
                doc.pop('_id', None)
                docs.append(doc)
            if docs:
                return docs
        except Exception as exc:                      # noqa: BLE001
            _log_source_fallback('standings', country, competition, exc)

    league_dir = os.path.join(output_dir, country, competition)
    if not os.path.isdir(league_dir):
        return []

    def _wanted(path, doc):
        """Standings JSON carries no season_id field (unlike club/player docs),
        so fall back to the Season_<id> filename. An unlabelled file is kept —
        dropping it would silently empty the Team Profile sheet."""
        if not season_id:
            return True
        sid = doc.get('season_id')
        if sid not in (None, ''):
            return str(sid) == str(season_id)
        name = os.path.basename(path)
        if 'Season_' in name:
            return f'Season_{season_id}' in name
        return True

    docs = []
    for path in glob.glob(os.path.join(league_dir, 'Standings_*_Season_*.json')):
        try:
            with open(path, encoding='utf-8') as fh:
                doc = json.load(fh)
        except Exception:                             # noqa: BLE001
            continue
        if _wanted(path, doc):
            docs.append(doc)
    return docs


def _log_source_fallback(kind, country, competition, exc):
    """Mongo was expected to serve this but failed — record it, then fall back."""
    try:
        from context import _logger
        _logger.warning(
            f'scatter: Mongo {kind} query failed for {country}/{competition} '
            f'({type(exc).__name__}: {exc}) — falling back to file-system JSON')
    except Exception:                                 # noqa: BLE001
        pass


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
