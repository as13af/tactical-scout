"""
Service for generating PDF scouting reports from Club Fit Analysis results.

Usage::
    from tactical_match_engine.services.pdf_generator import generate_pdf
    pdf_bytes = generate_pdf(result_dict)
"""
from __future__ import annotations

import io
from datetime import date
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)
from reportlab.lib.colors import HexColor

# ── Palette ───────────────────────────────────────────────────────────────────
_BG       = HexColor('#0d1117')
_HEADER   = HexColor('#0d1b2a')
_ACCENT   = HexColor('#0ea5e9')
_GREEN    = HexColor('#22c55e')
_AMBER    = HexColor('#f59e0b')
_RED      = HexColor('#ef4444')
_TEXT     = HexColor('#e2e8f0')
_TEXT2    = HexColor('#94a3b8')
_BORDER   = HexColor('#1e293b')
_CARD     = HexColor('#0f172a')

_W, _H    = A4  # 595 × 842 pt

def _score_color(score: float) -> HexColor:
    if score >= 72:
        return _GREEN
    if score >= 44:
        return _AMBER
    return _RED


def _bar_table(label: str, score: float, max_score: float = 100.0, width: float = 380) -> Table:
    """Single horizontal score bar row."""
    filled  = max(0.0, min(1.0, score / max_score))
    bar_w   = width * filled
    empty_w = width * (1 - filled)
    col = _score_color(score)

    bar_cell = Table(
        [[' ']],
        colWidths=[bar_w if bar_w > 0 else 0.01],
        rowHeights=[10],
    )
    bar_cell.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), col),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))

    empty_cell = Table(
        [[' ']],
        colWidths=[empty_w if empty_w > 0 else 0.01],
        rowHeights=[10],
    ) if empty_w > 0 else Spacer(0, 0)
    if isinstance(empty_cell, Table):
        empty_cell.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), _BORDER),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ]))

    label_style = ParagraphStyle('bl', textColor=_TEXT, fontSize=9, leading=12)
    val_style   = ParagraphStyle('bv', textColor=col, fontSize=9, leading=12, alignment=TA_RIGHT)

    row = Table(
        [[Paragraph(label, label_style), Paragraph(f'{score:.1f}', val_style)]],
        colWidths=[width, 40],
        rowHeights=[14],
    )
    row.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))

    bar_row = Table(
        [[[bar_cell, empty_cell] if empty_w > 0 else [bar_cell]]],
        colWidths=[width + 40],
        rowHeights=[10],
    ) if False else _raw_bar(width + 40, filled, col)

    return Table(
        [[row], [bar_row], [Spacer(0, 6)]],
        colWidths=[width + 40],
    )


def _raw_bar(total_w: float, filled: float, col: HexColor) -> Table:
    """A single-row two-column table that renders a progress bar."""
    fw = max(1.0, total_w * filled)
    ew = max(1.0, total_w * (1 - filled))
    t  = Table(
        [[' ', ' ']],
        colWidths=[fw, ew],
        rowHeights=[10],
    )
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, 0), col),
        ('BACKGROUND', (1, 0), (1, 0), _BORDER),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
    return t


def generate_pdf(result: dict[str, Any]) -> bytes:
    """Generate a PDF scouting report and return raw bytes.

    Args:
        result: The full Club Fit Analysis result dict as returned by
                ``/api/club_compatibility`` (keys: candidate, target, scores,
                verdict, metric_deltas, explanations, contender_simulation).

    Returns:
        Raw PDF bytes suitable for ``send_file(io.BytesIO(bytes), ...)``.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=1.8 * cm,
        rightMargin=1.8 * cm,
        topMargin=1.4 * cm,
        bottomMargin=1.4 * cm,
    )

    styles  = getSampleStyleSheet()
    content = []
    COL_W   = _W - 3.6 * cm  # usable width

    # ── Helpers ───────────────────────────────────────────────────────────────
    def P(text: str, style: ParagraphStyle) -> Paragraph:
        return Paragraph(str(text), style)

    s_title  = ParagraphStyle('title',  textColor=_TEXT,  fontSize=18, leading=22, fontName='Helvetica-Bold')
    s_sub    = ParagraphStyle('sub',    textColor=_TEXT2, fontSize=10, leading=14)
    s_label  = ParagraphStyle('label',  textColor=_ACCENT, fontSize=10, leading=13, fontName='Helvetica-Bold')
    s_body   = ParagraphStyle('body',   textColor=_TEXT,  fontSize=9,  leading=13)
    s_body2  = ParagraphStyle('body2',  textColor=_TEXT2, fontSize=8,  leading=12)
    s_th     = ParagraphStyle('th',     textColor=_TEXT,  fontSize=8,  fontName='Helvetica-Bold', alignment=TA_CENTER)
    s_td     = ParagraphStyle('td',     textColor=_TEXT2, fontSize=8,  alignment=TA_CENTER)
    s_td_l   = ParagraphStyle('tdl',    textColor=_TEXT2, fontSize=8)
    s_foot   = ParagraphStyle('foot',   textColor=_TEXT2, fontSize=7,  alignment=TA_CENTER)
    s_verdict = ParagraphStyle('verd',  textColor=_TEXT,  fontSize=11, fontName='Helvetica-Bold', alignment=TA_CENTER)
    s_small  = ParagraphStyle('small',  textColor=_TEXT2, fontSize=8,  leading=12)

    # ── Unpack result ─────────────────────────────────────────────────────────
    cand       = result.get('candidate', {})
    target     = result.get('target', {})
    scores_d   = result.get('scores', {})
    verdict    = result.get('verdict', '')
    metrics    = result.get('metric_deltas', [])
    expl       = result.get('explanations', {})
    contender  = result.get('contender_simulation', {})

    player_name = cand.get('name', 'Unknown Player')
    club_name   = target.get('club', 'Unknown Club')
    competition = target.get('competition', '')
    position    = cand.get('position', '')
    age         = cand.get('age', '')

    combined    = float(scores_d.get('combined_score', 0))
    role_fit    = float(scores_d.get('role_fitness', 0))
    squad_imp   = float(scores_d.get('squad_impact_norm', 50))
    league_adp  = float(scores_d.get('league_adaptation', 0))

    # ── Section 1: Header ─────────────────────────────────────────────────────
    header_data = [[
        P('TACTICAL COMPATIBILITY REPORT', s_title),
        P(date.today().strftime('%d %b %Y'), ParagraphStyle('dr', textColor=_TEXT2, fontSize=9, alignment=TA_RIGHT)),
    ]]
    header_tbl = Table(header_data, colWidths=[COL_W * 0.75, COL_W * 0.25])
    header_tbl.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    content.append(header_tbl)

    meta_data = [[
        P(f'{player_name}  →  {club_name}', ParagraphStyle('meta', textColor=_ACCENT, fontSize=13, fontName='Helvetica-Bold')),
    ]]
    meta_tbl = Table(meta_data, colWidths=[COL_W])
    meta_tbl.setStyle(TableStyle([('TOPPADDING', (0, 0), (-1, -1), 4)]))
    content.append(meta_tbl)

    detail_parts = []
    if position:
        detail_parts.append(position)
    if age:
        detail_parts.append(f'Age {age}')
    if competition:
        detail_parts.append(competition)
    if cand.get('league_name'):
        detail_parts.append(f'from {cand["league_name"]}')
    content.append(P(' · '.join(detail_parts), s_sub))
    content.append(Spacer(0, 10))
    content.append(HRFlowable(width=COL_W, thickness=1, color=_BORDER))
    content.append(Spacer(0, 10))

    # ── Section 2: Score summary ──────────────────────────────────────────────
    content.append(P('SCORE SUMMARY', s_label))
    content.append(Spacer(0, 6))

    vcol = _score_color(combined)
    verdict_tbl = Table(
        [[P(f'  {combined:.1f} / 100  ', ParagraphStyle('vs', textColor=vcol, fontSize=22,
             fontName='Helvetica-Bold')),
          P(verdict, ParagraphStyle('vv', textColor=vcol, fontSize=13,
             fontName='Helvetica-Bold', alignment=TA_RIGHT))]],
        colWidths=[COL_W * 0.4, COL_W * 0.6],
    )
    verdict_tbl.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE')]))
    content.append(verdict_tbl)
    content.append(Spacer(0, 8))

    bar_w = COL_W - 44
    for lbl, val in [
        ('Role Fitness',      role_fit),
        ('Squad Impact',      squad_imp),
        ('League Adaptation', league_adp),
    ]:
        col = _score_color(val)
        content.append(Table(
            [[P(lbl, ParagraphStyle('lb', textColor=_TEXT, fontSize=9)),
              P(f'{val:.1f}', ParagraphStyle('lv', textColor=col, fontSize=9, alignment=TA_RIGHT))]],
            colWidths=[bar_w, 44],
        ))
        content.append(_raw_bar(COL_W, val / 100.0, col))
        content.append(Spacer(0, 5))

    content.append(Spacer(0, 6))
    content.append(HRFlowable(width=COL_W, thickness=1, color=_BORDER))
    content.append(Spacer(0, 10))

    # ── Section 3: Contender projection ──────────────────────────────────────
    content.append(P('CONTENDER PROJECTION', s_label))
    content.append(Spacer(0, 6))

    proj_cells = [
        ('xG Gain / Match',       contender.get('xg_gain_per_match', 0),    '+{:.3f}'),
        ('Season xG Gain',        contender.get('season_xg_gain', 0),       '+{:.2f}'),
        ('Goal Gain',             contender.get('goal_gain', 0),            '+{:.2f}'),
        ('Points Gain',           contender.get('points_gain', 0),          '+{:.2f} pts'),
        ('Title Prob. Shift',     contender.get('title_probability_shift', 0), '{:+.4f}'),
        ('Progression Gain',      contender.get('progression_gain', 0),    '+{:.4f}'),
    ]
    s_proj_l = ParagraphStyle('pl', textColor=_TEXT2, fontSize=8, alignment=TA_CENTER)
    s_proj_v = ParagraphStyle('pv', textColor=_ACCENT, fontSize=11, fontName='Helvetica-Bold', alignment=TA_CENTER)

    proj_row1 = [[P(lbl, s_proj_l)] for lbl, val, fmt in proj_cells[:3]]
    proj_val1 = [[P(fmt.format(val), s_proj_v)] for lbl, val, fmt in proj_cells[:3]]
    proj_row2 = [[P(lbl, s_proj_l)] for lbl, val, fmt in proj_cells[3:]]
    proj_val2 = [[P(fmt.format(val), s_proj_v)] for lbl, val, fmt in proj_cells[3:]]

    cell_w = COL_W / 3
    proj_tbl = Table(
        [
            [_proj_cell(proj_cells[i][0], proj_cells[i][1], proj_cells[i][2]) for i in range(3)],
            [_proj_cell(proj_cells[i][0], proj_cells[i][1], proj_cells[i][2]) for i in range(3, 6)],
        ],
        colWidths=[cell_w, cell_w, cell_w],
        rowHeights=[45, 45],
    )
    proj_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), _CARD),
        ('BOX',        (0, 0), (-1, -1), 1, _BORDER),
        ('INNERGRID',  (0, 0), (-1, -1), 0.5, _BORDER),
        ('VALIGN',     (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN',      (0, 0), (-1, -1), 'CENTER'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    content.append(proj_tbl)
    content.append(Spacer(0, 10))
    content.append(HRFlowable(width=COL_W, thickness=1, color=_BORDER))
    content.append(Spacer(0, 10))

    # ── Section 4: Narrative ──────────────────────────────────────────────────
    content.append(P('SCOUTING NARRATIVE', s_label))
    content.append(Spacer(0, 6))

    narrative_map = [
        ('Why the club needs this player',   expl.get('why_club_needs_player', '')),
        ('Why the player fits the club',     expl.get('why_player_fits_club', '')),
        ('Contender projection',             expl.get('why_club_becomes_contender', '')),
        ('Risk assessment',                  expl.get('risk_assessment', '')),
    ]
    for heading, text in narrative_map:
        if text:
            content.append(P(heading, ParagraphStyle('nh', textColor=_ACCENT, fontSize=9,
                                                      fontName='Helvetica-Bold', spaceBefore=4)))
            content.append(P(text, s_body))
            content.append(Spacer(0, 4))

    content.append(Spacer(0, 4))
    content.append(HRFlowable(width=COL_W, thickness=1, color=_BORDER))
    content.append(Spacer(0, 10))

    # ── Section 5: Metric delta table ─────────────────────────────────────────
    content.append(P('METRIC BREAKDOWN', s_label))
    content.append(Spacer(0, 6))

    header_row = [
        P('Metric',      s_th),
        P('Category',    s_th),
        P('Player /90',  s_th),
        P('Squad Avg',   s_th),
        P('Delta',       s_th),
        P('Score',       s_th),
    ]
    rows_data = [header_row]
    for m in metrics[:30]:
        hib   = m.get('higher_is_better', True)
        delta = m.get('raw_delta', 0.0)
        pos   = (delta > 0) == hib
        d_col = _GREEN if pos else _RED if delta != 0 else _TEXT2
        rows_data.append([
            P(m.get('name', ''),             s_td_l),
            P(m.get('category', ''),         s_td),
            P(f"{m.get('candidate_raw', 0):.3f}", s_td),
            P(f"{m.get('squad_avg_raw', 0):.3f}", s_td),
            P(f"{delta:+.3f}",               ParagraphStyle('dt', textColor=d_col, fontSize=8, alignment=TA_CENTER)),
            P(f"{m.get('candidate_score', 0):.1f}", s_td),
        ])

    col_ws = [COL_W * 0.28, COL_W * 0.16, COL_W * 0.14, COL_W * 0.14, COL_W * 0.14, COL_W * 0.14]
    metric_tbl = Table(rows_data, colWidths=col_ws)
    metric_tbl.setStyle(TableStyle([
        ('BACKGROUND',   (0, 0), (-1, 0),  _HEADER),
        ('BACKGROUND',   (0, 1), (-1, -1), _CARD),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [_CARD, _BG]),
        ('BOX',          (0, 0), (-1, -1), 0.5, _BORDER),
        ('INNERGRID',    (0, 0), (-1, -1), 0.5, _BORDER),
        ('VALIGN',       (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING',   (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING',  (0, 0), (-1, -1), 4),
    ]))
    content.append(metric_tbl)
    content.append(Spacer(0, 12))

    # ── Footer ────────────────────────────────────────────────────────────────
    content.append(HRFlowable(width=COL_W, thickness=1, color=_BORDER))
    content.append(Spacer(0, 4))
    content.append(P(
        'Generated by Tactical Compatibility Player — decision support only',
        s_foot,
    ))

    doc.build(content)
    return buf.getvalue()


def _proj_cell(label: str, value: float, fmt: str) -> Table:
    """A single projection grid cell with label + value."""
    s_lbl = ParagraphStyle('pcl', textColor=_TEXT2, fontSize=8, alignment=TA_CENTER)
    s_val = ParagraphStyle('pcv', textColor=_ACCENT, fontSize=11,
                            fontName='Helvetica-Bold', alignment=TA_CENTER)
    t = Table(
        [[P(fmt.format(value), s_val)], [P(label, s_lbl)]],
        rowHeights=[24, 16],
    )
    t.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN',  (0, 0), (-1, -1), 'CENTER'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    return t
