"""passmap_engine.py — Positional passmap visualiser.

Generates a dark-pitch PNG showing backline / midfield / forward passing
zones annotated with P90 pass metrics per zone.

Visual conventions match zone_heatmap.py:
  - Background:  #0d0221 (dark navy)
  - Pitch lines: white
  - Zone colours: blue (backline), green (mid), red (forward)
  - White-outlined text for legibility

Entry points:
    generate_passmap_png(positional_passing, output_path, label, n)
        → saves PNG to disk, returns path

    generate_passmap_bytes(positional_passing, label, n)
        → returns raw PNG bytes (for streaming via Flask send_file)
"""

from __future__ import annotations

import gc
import io
import os
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import matplotlib.patheffects as pe

# ── Colour palette (consistent with zone_heatmap.py) ─────────────────────────
_BG    = "#0d0221"
_PITCH = "#0d0221"
_LINE  = "white"

# Per-zone accent colours
_GROUP_COLOURS: dict[str, str] = {
    "backline_passes": "#4361ee",   # blue
    "mid_passes":      "#2dc653",   # green
    "forward_passes":  "#e63946",   # red
}

# Display labels for each zone
_GROUP_LABELS: dict[str, str] = {
    "backline_passes": "Backline",
    "mid_passes":      "Midfield",
    "forward_passes":  "Forward",
}

# Pitch bands: x ranges in the 0-100 coordinate space
# left = defensive/backline, middle = mid, right = forward/attacking
_GROUP_BANDS: dict[str, tuple[float, float]] = {
    "backline_passes": (0.0,  33.3),
    "mid_passes":      (33.3, 66.6),
    "forward_passes":  (66.6, 100.0),
}

# Draw order (left to right on the pitch)
_DRAW_ORDER = ("backline_passes", "mid_passes", "forward_passes")


# ── Internal helpers ──────────────────────────────────────────────────────────

def _draw_pitch(ax: plt.Axes, lc: str = "white", lw: float = 1.4) -> None:
    """Draw standard pitch markings on ax (0-100 coordinate space)."""
    ax.set_facecolor(_PITCH)
    kw = dict(fill=False, edgecolor=lc, linewidth=lw, zorder=4)
    ax.add_patch(patches.Rectangle((0, 0), 100, 100, **kw))
    ax.axvline(50, color=lc, linewidth=lw, zorder=4)
    ax.add_patch(plt.Circle((50, 50), 9.15, color=lc, fill=False, linewidth=lw, zorder=4))
    ax.plot(50, 50, "o", color=lc, markersize=3, zorder=4)
    ax.add_patch(patches.Rectangle((0,    21.1), 16.5, 57.8, **kw))
    ax.add_patch(patches.Rectangle((83.5, 21.1), 16.5, 57.8, **kw))
    ax.add_patch(patches.Rectangle((0,    36.8),  5.5, 26.4, **kw))
    ax.add_patch(patches.Rectangle((94.5, 36.8),  5.5, 26.4, **kw))
    ax.plot(11, 50, "o", color=lc, markersize=3, zorder=4)
    ax.plot(89, 50, "o", color=lc, markersize=3, zorder=4)
    ax.set_xlim(-2, 102)
    ax.set_ylim(-2, 102)
    ax.axis("off")


def _text_outline(
    ax: plt.Axes,
    x: float,
    y: float,
    txt: str,
    fs: float,
    fc: str,
    *,
    ha: str = "center",
    va: str = "center",
    fw: str = "bold",
):
    """Render text with a dark outline for legibility on dark backgrounds."""
    t = ax.text(x, y, txt, fontsize=fs, fontweight=fw,
                color=fc, ha=ha, va=va, zorder=8)
    t.set_path_effects([
        pe.Stroke(linewidth=2.5, foreground=_BG),
        pe.Normal(),
    ])
    return t


def _render_passmap(
    ax: plt.Axes,
    positional_passing: dict[str, Any | None],
    label: str,
    n: int,
) -> None:
    """Render the passmap onto an existing Axes object.

    Shared by both generate_passmap_png and generate_passmap_bytes.

    Args:
        ax: matplotlib Axes (already configured with dark background).
        positional_passing: dict keyed by group name
            (backline_passes, mid_passes, forward_passes), values are
            either a metrics dict or None.
        label: team name shown in the title.
        n: number of matches used, shown in subtitle.
    """
    _draw_pitch(ax)

    has_data = any(positional_passing.get(g) for g in _DRAW_ORDER)

    if not has_data:
        _text_outline(ax, 50, 50, "No match data available", fs=16, fc="white")
        ax.set_title(
            f"{label}\nPositional Passmap — No Data",
            color="white", fontsize=13, fontweight="bold", pad=12,
        )
        return

    for group_key in _DRAW_ORDER:
        x_min, x_max = _GROUP_BANDS[group_key]
        colour       = _GROUP_COLOURS[group_key]
        glabel       = _GROUP_LABELS[group_key]
        data         = positional_passing.get(group_key)
        x_centre     = (x_min + x_max) / 2.0

        # Coloured fill band (vertical stripe)
        ax.add_patch(patches.Rectangle(
            (x_min, 0), x_max - x_min, 100,
            facecolor=colour, alpha=0.18, zorder=2, linewidth=0,
        ))
        # Band boundary lines
        ax.axvline(x_min, color=colour, linewidth=1.2, alpha=0.65, zorder=3)
        ax.axvline(x_max, color=colour, linewidth=1.2, alpha=0.65, zorder=3)

        if data:
            total_p90 = data.get("total_passes_p90", 0.0) or 0.0
            acc_pct   = round((data.get("pass_accuracy", 0.0) or 0.0) * 100, 1)
            n_players = data.get("player_count", 0) or 0

            # Zone label (coloured) — upper third of each band
            _text_outline(ax, x_centre, 75, glabel, fs=13, fc=colour)
            # Metric annotation — lower third of each band
            _text_outline(
                ax, x_centre, 25,
                f"{acc_pct}% acc  |  {total_p90:.1f} passes P90",
                fs=10, fc="white", fw="normal",
            )
            # Player count badge (small, top-right corner of each band)
            if n_players:
                _text_outline(
                    ax, x_max - 2, 94, f"{n_players}p",
                    fs=8, fc="#aaaaaa", ha="right", fw="normal",
                )
        else:
            _text_outline(
                ax, x_centre, 50,
                f"{glabel} — no data", fs=11, fc="#666666",
            )

    ax.set_title(
        f"{label}\nPositional Passmap  (Last {n} matches)",
        color="white", fontsize=13, fontweight="bold", pad=12,
    )


# ── Public API ────────────────────────────────────────────────────────────────

def generate_passmap_png(
    positional_passing: dict[str, Any | None],
    output_path: str,
    label: str = "Team",
    n: int = 5,
) -> str:
    """Generate a positional passmap PNG and save it to disk.

    Args:
        positional_passing: output from form_engine.apply_positional_context
            for a single team (dict keyed by group name).
        output_path: absolute path for the output PNG file.
        label: team name shown in the chart title.
        n: number of matches used (shown in subtitle).

    Returns:
        output_path (str).
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    fig, ax = plt.subplots(figsize=(14, 9.5))
    fig.patch.set_facecolor(_BG)
    _render_passmap(ax, positional_passing, label, n)
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    gc.collect()
    return output_path


def generate_passmap_bytes(
    positional_passing: dict[str, Any | None],
    label: str = "Team",
    n: int = 5,
) -> bytes:
    """Generate a positional passmap PNG and return it as raw bytes.

    Suitable for streaming directly from a Flask route via send_file /
    io.BytesIO without writing to disk.

    Args:
        positional_passing: same as generate_passmap_png.
        label: team name shown in the chart title.
        n: number of matches used.

    Returns:
        Raw PNG bytes.
    """
    fig, ax = plt.subplots(figsize=(14, 9.5))
    fig.patch.set_facecolor(_BG)
    _render_passmap(ax, positional_passing, label, n)
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    gc.collect()
    buf.seek(0)
    return buf.read()
