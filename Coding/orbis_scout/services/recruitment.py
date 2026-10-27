"""services/recruitment.py — rule-based recruitment nudge.

Turns a team's season statistics, style fingerprint, squad shape and current
transfer window into (a) the gaps that hurt them, (b) the gaps that specifically
undercut the style they play, and (c) the player archetypes that would close
both.

Deterministic by design: the same club data always produces the same nudge, so
the Excel sheet and the web panel can never disagree, and a league-wide export
costs nothing extra.  Nothing here calls out to a model.

The two blocks answer different questions:
  * priority_gaps    — "where are you simply worse than the league?"
  * style_conflicts  — "where does your own identity ask for something you
                        haven't got?"  A team can sit mid-table on crossing
                        accuracy and still have a conflict, if Wing Play is
                        their primary style.
"""

import math

from style_engine import METRIC_LABELS, STYLE_WEIGHTS


# ── Which display stats can signal a recruitable gap ─────────────────────────
# Volume metrics (passes, crosses, long balls, aerial duels, corners,
# possession) are deliberately excluded: a low count is a stylistic choice, not
# a weakness.  They still feed the archetype rules below as context — "you cross
# a lot AND badly" is a very different problem from "you rarely cross".
# 'saves' is excluded too: the stats table ranks more saves as better, which
# really just means the keeper is busier.
_GAP_FIELDS = (
    'goalsScored', 'goalsConceded', 'shots', 'shotsAgainst',
    'accuratePassesPercentage', 'accurateLongBallsPercentage',
    'accurateCrossesPercentage', 'aerialDuelsWonPercentage',
    'offsides', 'fouls', 'ballRecovery', 'interceptions', 'successfulDribbles',
    'cleanSheets',
)

# Position letters as they appear in transfers[].player.position
_POS_LABEL = {'G': 'goalkeeper', 'D': 'defender', 'M': 'midfielder', 'F': 'forward'}


class _Ctx:
    """Convenience view over one team's stats table row."""

    def __init__(self, stats_pm, stat_defs, league_avg, total_teams, radar):
        self.stats_pm    = stats_pm or {}
        self.league_avg  = league_avg or {}
        self.total       = total_teams or 0
        self.radar       = radar or {}
        self.label_of    = {d['field']: d['label'] for d in stat_defs}
        self.lower_better = {d['field']: d.get('lower_is_better', False) for d in stat_defs}

    def val(self, field):
        e = self.stats_pm.get(field) or {}
        return e.get('value')

    def rank(self, field):
        e = self.stats_pm.get(field) or {}
        return e.get('rank')

    def avg(self, field):
        return self.league_avg.get(field)

    def pct(self, field):
        """0.0 = best in league, 1.0 = worst.  None when unranked."""
        r = self.rank(field)
        if not r or self.total < 2:
            return None
        return (r - 1) / (self.total - 1)

    def weak(self, field, threshold=0.65):
        # 0.65 rather than a plain "bottom third": in an 18-team league that is
        # 13th or worse, which keeps genuinely marginal ranks (a team 12th by
        # +0.4 of a shot) out of a list that is supposed to be actionable.
        p = self.pct(field)
        return p is not None and p >= threshold

    def strong(self, field, threshold=0.35):
        p = self.pct(field)
        return p is not None and p <= threshold

    def above_avg(self, field):
        v, a = self.val(field), self.avg(field)
        return v is not None and a is not None and v >= a

    def z(self, metric_key):
        """Recover the z-score the radar was built from (50 + z*15)."""
        r = self.radar.get(metric_key)
        return None if r is None else (r - 50.0) / 15.0


# ── Archetype rules ──────────────────────────────────────────────────────────
# Each rule inspects the context and, when it fires, returns a profile dict.
# Order matters only for presentation — the most structural problems first.

def _rules(c, styles_top):
    """Yield (priority, profile) for every rule that fires."""
    style_names = {s for s, _ in styles_top}
    out = []

    def add(priority, role, attributes, why):
        out.append((priority, {'role': role, 'attributes': attributes, 'why': why}))

    # ── Aerial ────────────────────────────────────────────────────────────────
    if c.weak('aerialDuelsWonPercentage'):
        win = c.val('aerialDuelsWonPercentage')
        crosses_heavy = c.above_avg('totalCrosses')
        why = (f"Aerial duels won {win}% ({_ord(c.rank('aerialDuelsWonPercentage'))}"
               f"/{c.total}, league {c.avg('aerialDuelsWonPercentage')}%)")
        if crosses_heavy:
            why += (f" while still putting in {c.val('totalCrosses')} crosses/match "
                    f"— the deliveries arrive but nobody wins them")
            add(1, 'Target centre-forward',
                ['aerial dominance', 'box positioning', 'hold-up play', 'height / leap'],
                why)
        add(2, 'Aerially commanding centre-back',
            ['defensive heading', 'timing in the air', 'set-piece presence', 'physicality'],
            f"Aerial duels won {win}% ({_ord(c.rank('aerialDuelsWonPercentage'))}/{c.total}, "
            f"league {c.avg('aerialDuelsWonPercentage')}%) — you lose the majority of "
            f"contested balls at both ends, including defending set pieces")

    # ── Distribution ─────────────────────────────────────────────────────────
    if c.weak('accurateLongBallsPercentage') and c.above_avg('totalLongBalls'):
        add(1, 'Ball-playing centre-back or distributing goalkeeper',
            ['long-range passing accuracy', 'range of pass', 'composure under pressure',
             'scanning'],
            f"Long-ball accuracy {c.val('accurateLongBallsPercentage')}% "
            f"({_ord(c.rank('accurateLongBallsPercentage'))}/{c.total}) on "
            f"{c.val('totalLongBalls')} long balls/match — you play direct often "
            f"but lose the ball doing it")

    if c.weak('accurateCrossesPercentage') and c.above_avg('totalCrosses'):
        add(2, 'Wide creator with better delivery',
            ['crossing accuracy', 'final-third decision making', 'whipped / cut-back variety'],
            f"Cross accuracy {c.val('accurateCrossesPercentage')}% "
            f"({_ord(c.rank('accurateCrossesPercentage'))}/{c.total}) from "
            f"{c.val('totalCrosses')} crosses/match — the volume is there, the quality is not")

    if c.weak('accuratePassesPercentage') and (
            {'Tiki-Taka', 'Control Possession', 'Vertical Tiki-Taka'} & style_names):
        add(2, 'Press-resistant deep-lying midfielder',
            ['first touch under pressure', 'short passing accuracy', 'body orientation',
             'press resistance'],
            f"Pass accuracy {c.val('accuratePassesPercentage')}% "
            f"({_ord(c.rank('accuratePassesPercentage'))}/{c.total}) is low for a "
            f"possession-based identity")

    # ── Discipline ───────────────────────────────────────────────────────────
    if c.weak('fouls'):
        add(3, 'Disciplined holding midfielder',
            ['positional defending', 'reading the game', 'clean tackling technique'],
            f"{c.val('fouls')} fouls/match ({_ord(c.rank('fouls'))}/{c.total} fewest) "
            f"— stopping play too often, usually a positioning problem rather than effort")

    if c.weak('offsides'):
        add(3, 'Forward with sharper movement',
            ['timing of runs', 'awareness of the last line', 'movement across defenders'],
            f"{c.val('offsides')} offsides/match ({_ord(c.rank('offsides'))}/{c.total} "
            f"fewest) — attacking runs are mistimed rather than badly intended")

    # ── Defensive solidity ───────────────────────────────────────────────────
    if c.weak('shotsAgainst'):
        add(1, 'Defensive-midfield screen',
            ['positional discipline', 'interceptions', 'covering the back line',
             'defensive stamina'],
            f"Conceding {c.val('shotsAgainst')} shots/match "
            f"({_ord(c.rank('shotsAgainst'))}/{c.total} fewest, league "
            f"{c.avg('shotsAgainst')}) — opponents reach your box too easily")

    if c.weak('goalsConceded') and c.weak('cleanSheets'):
        add(1, 'Commanding goalkeeper or organising centre-back',
            ['shot stopping', 'command of the box', 'defensive organisation', 'communication'],
            f"{c.val('goalsConceded')} conceded/match with a "
            f"{c.val('cleanSheets')} clean-sheet rate — leaking goals and rarely shutting "
            f"a game out")
    elif c.weak('cleanSheets'):
        add(2, 'Organising centre-back',
            ['defensive organisation', 'concentration', 'communication', 'game management'],
            f"Clean-sheet rate {c.val('cleanSheets')} "
            f"({_ord(c.rank('cleanSheets'))}/{c.total}) — goals conceded are around league "
            f"average, but games are rarely seen out")

    # ── Attacking output ─────────────────────────────────────────────────────
    if c.weak('goalsScored'):
        if c.weak('shots'):
            add(1, 'Creative attacking midfielder',
                ['chance creation', 'through balls', 'vision', 'shooting from range'],
                f"{c.val('shots')} shots/match and {c.val('goalsScored')} goals, both bottom "
                f"of the league — the problem is chance creation, not just finishing")
        else:
            add(1, 'Clinical finisher',
                ['finishing', 'shot selection', 'composure in the box', 'movement off the ball'],
                f"{c.val('shots')} shots/match ({_ord(c.rank('shots'))}/{c.total}) but only "
                f"{c.val('goalsScored')} goals — chances are being created and wasted")
    elif c.weak('shots'):
        add(2, 'Creative attacking midfielder',
            ['chance creation', 'final ball', 'vision', 'progressive passing'],
            f"Only {c.val('shots')} shots/match ({_ord(c.rank('shots'))}/{c.total}, league "
            f"{c.avg('shots')}) — scoring holds up for now, but the team works too few "
            f"openings to rely on it")

    if c.weak('successfulDribbles') and (
            {'Fluid Counter-Attack', 'Wing Play', 'Vertical Tiki-Taka'} & style_names):
        add(2, 'One-v-one ball carrier',
            ['dribbling in tight space', 'acceleration', 'carrying distance', 'courage on the ball'],
            f"{c.val('successfulDribbles')} successful dribbles/match "
            f"({_ord(c.rank('successfulDribbles'))}/{c.total}) is thin for a style built on "
            f"carrying the ball forward")

    if c.weak('ballRecovery') or c.weak('interceptions'):
        field = 'ballRecovery' if c.weak('ballRecovery') else 'interceptions'
        base = (f"{c.label_of.get(field, field)} {c.val(field)}/match "
                f"({_ord(c.rank(field))}/{c.total}, league {c.avg(field)})")
        if 'Gegenpress' in style_names:
            add(2, 'High-energy pressing forward or midfielder',
                ['pressing triggers', 'repeat-sprint capacity', 'counter-pressing', 'work rate'],
                base + " — the press is not winning the ball back often enough to sustain "
                       "a Gegenpress identity")
        else:
            # Style-agnostic: regaining possession badly is a problem whatever
            # the team is trying to play.
            add(2, 'Ball-winning midfielder',
                ['duel winning', 'interceptions', 'anticipation', 'defensive positioning'],
                base + " — the team struggles to regain possession, which shortens every "
                       "spell on the ball")

    return out


def _ord(n):
    if not isinstance(n, int):
        return '—'
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


# ── Squad context ────────────────────────────────────────────────────────────

def _squad_notes(details, transfers):
    """Short factual observations about squad shape and the transfer window."""
    notes = []
    details  = details  or {}
    transfers = transfers or {}

    age  = details.get('average_age')
    size = details.get('squad_size')
    foreign = details.get('foreign_players')

    if isinstance(age, (int, float)):
        if age >= 28.5:
            notes.append(f"Squad averages {age} years — an ageing group; favour profiles "
                         f"under 26 so replacements arrive before decline, not after.")
        elif age <= 23.5:
            notes.append(f"Squad averages {age} years — very young; at least one "
                         f"experienced leader in the spine would balance it.")
        else:
            notes.append(f"Squad averages {age} years — a balanced age profile.")

    if isinstance(size, int) and size:
        extra = f", {foreign} of them foreign" if isinstance(foreign, int) else ""
        notes.append(f"Squad size {size}{extra}.")

    def _by_pos(rows):
        counts = {}
        for t in rows or []:
            p = ((t.get('player') or {}).get('position') or '').upper()
            if p in _POS_LABEL:
                counts[p] = counts.get(p, 0) + 1
        return counts

    ins  = _by_pos(transfers.get('transfersIn'))
    outs = _by_pos(transfers.get('transfersOut'))
    if ins:
        notes.append("Signed this window: " + ", ".join(
            f"{n} {_POS_LABEL[p]}{'s' if n > 1 else ''}" for p, n in sorted(ins.items())))
    if outs:
        notes.append("Departed this window: " + ", ".join(
            f"{n} {_POS_LABEL[p]}{'s' if n > 1 else ''}" for p, n in sorted(outs.items())))

    return notes, ins, outs


# Which transfer position letter each archetype belongs to, so a profile can be
# flagged when the club has already reinforced that area this window.  Midfield
# is tested first because several roles name both ("defensive-midfield screen",
# "ball-playing centre-back or distributing goalkeeper" is deliberately D).
def _role_position(role):
    r = role.lower()
    if 'midfield' in r or 'carrier' in r or 'creator' in r or 'wide' in r:
        return 'M'
    if 'centre-back' in r:
        return 'D'
    if 'goalkeeper' in r:
        return 'G'
    if 'forward' in r or 'finisher' in r or 'striker' in r:
        return 'F'
    return None


# ── Public entry point ───────────────────────────────────────────────────────

def build_nudge(stats_pm, stat_defs, league_avg, total_teams,
                style_scores, primary_style, radar,
                details=None, transfers=None, max_gaps=5, max_profiles=5):
    """Build the recruitment nudge for one team.

    Returns a dict with style_summary, priority_gaps, style_conflicts,
    profiles and squad_notes.  Every list may be empty — a balanced team with
    no bottom-third weaknesses legitimately has nothing to flag.
    """
    c = _Ctx(stats_pm, stat_defs, league_avg, total_teams, radar)

    styles_top = sorted((style_scores or {}).items(), key=lambda kv: kv[1], reverse=True)[:3]

    # ── Block A — priority gaps ──────────────────────────────────────────────
    gaps = []
    for field in _GAP_FIELDS:
        if not c.weak(field):
            continue
        v, a, r = c.val(field), c.avg(field), c.rank(field)
        if v is None or r is None:
            continue
        gaps.append({
            'field': field,
            'label': c.label_of.get(field, field),
            'value': v,
            'avg':   a,
            'diff':  round(v - a, 2) if a is not None else None,
            'rank':  r,
            'total': c.total,
            'lower_is_better': c.lower_better.get(field, False),
        })
    gaps.sort(key=lambda g: -g['rank'])
    gaps = gaps[:max_gaps]

    # ── Block B — conflicts with the team's own style ────────────────────────
    conflicts = []
    seen_metric = set()
    for style_name, score in styles_top:
        if score < 0.55:
            continue
        for metric, weight in (STYLE_WEIGHTS.get(style_name) or {}).items():
            zz = c.z(metric)
            if zz is None or metric in seen_metric:
                continue
            # Positive weight = the style wants a HIGH value here.  Negative
            # weight = it wants a LOW one.  A conflict is the style pulling one
            # way while the team sits the other side of league average.
            if weight > 0 and zz <= -0.4:
                direction = 'needs more'
            elif weight < 0 and zz >= 0.4:
                direction = 'needs less'
            else:
                continue
            seen_metric.add(metric)
            conflicts.append({
                'metric':    metric,
                'label':     METRIC_LABELS.get(metric, metric),
                'style':     style_name,
                'style_pct': round(score * 100),
                'direction': direction,
                'z':         round(zz, 2),
                'severity':  round(abs(weight) * abs(zz), 2),
            })
    conflicts.sort(key=lambda x: -x['severity'])
    conflicts = conflicts[:max_gaps]

    # ── Archetypes ───────────────────────────────────────────────────────────
    fired = _rules(c, styles_top)
    fired.sort(key=lambda t: t[0])
    profiles, seen_role = [], set()
    for priority, prof in fired:
        if prof['role'] in seen_role:
            continue
        seen_role.add(prof['role'])
        prof = dict(prof)
        prof['priority'] = {1: 'High', 2: 'Medium'}.get(priority, 'Low')
        profiles.append(prof)
        if len(profiles) >= max_profiles:
            break

    notes, ins, outs = _squad_notes(details, transfers)

    # Flag archetypes for an area the club has genuinely strengthened this
    # window.  Net, not gross: a club that signed six defenders and lost six
    # has replaced its back line, not reinforced it, and telling them they
    # "already added six defenders" would be actively misleading.
    for prof in profiles:
        pos = _role_position(prof['role'])
        net = ins.get(pos, 0) - outs.get(pos, 0) if pos else 0
        if net > 0:
            prof['caveat'] = (f"net +{net} {_POS_LABEL[pos]}"
                              f"{'s' if net > 1 else ''} this window — check whether the "
                              f"gap is quality rather than quantity")

    return {
        'style_summary':   [{'style': s, 'pct': round(v * 100)} for s, v in styles_top],
        'priority_gaps':   gaps,
        'style_conflicts': conflicts,
        'profiles':        profiles,
        'squad_notes':     notes,
    }
