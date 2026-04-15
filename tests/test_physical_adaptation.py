import pytest
from tactical_match_engine.engine.physical_adaptation import (
    calculate_physical_adaptation, compute_league_suitability, compute_league_discount
)

# ── compute_league_suitability anchor tests ───────────────────────────────────

def test_anchor_indo_sl_to_eredivisie():
    """Indonesian Super League → Eredivisie: rel_gap ~0.244 → 25–40%."""
    s = compute_league_suitability(0.6216, 0.7731)
    assert 0.25 <= s <= 0.40, f"Expected 25-40%, got {s*100:.1f}%"

def test_anchor_eredivisie_to_epl():
    """Eredivisie → Premier League: rel_gap ~0.175 → 45–60%."""
    s = compute_league_suitability(0.7731, 0.9086)
    assert 0.45 <= s <= 0.60, f"Expected 45-60%, got {s*100:.1f}%"

def test_anchor_ligue1_to_epl():
    """Ligue 1 → Premier League: rel_gap ~0.058 → 72–88%."""
    s = compute_league_suitability(0.86, 0.9086)
    assert 0.72 <= s <= 0.88, f"Expected 72-88%, got {s*100:.1f}%"

def test_anchor_saudi_to_mls():
    """Saudi Pro League → MLS: rel_gap ~0.258 → 20–38%."""
    s = compute_league_suitability(0.62, 0.78)
    assert 0.20 <= s <= 0.38, f"Expected 20-38%, got {s*100:.1f}%"

def test_anchor_brasileirao_to_laliga():
    """Brasileirão → La Liga: rel_gap ~0.101 → 62–80%."""
    s = compute_league_suitability(0.79, 0.87)
    assert 0.62 <= s <= 0.80, f"Expected 62-80%, got {s*100:.1f}%"

# ── Direction logic ───────────────────────────────────────────────────────────

def test_downward_move_returns_one():
    """Any downward move returns 1.0 (fully qualified)."""
    assert compute_league_suitability(0.91, 0.86) == 1.0
    assert compute_league_suitability(0.91, 0.50) == 1.0
    assert compute_league_suitability(0.91, 0.62) == 1.0

def test_lateral_move_returns_one():
    """Identical league quality → 1.0."""
    assert compute_league_suitability(0.78, 0.78) == 1.0

def test_negligible_gap_returns_one():
    """Gaps below 3 CVS pts are treated as lateral."""
    s = compute_league_suitability(0.62, 0.645)
    assert s == 1.0

def test_slight_step_up():
    """Small upward gap (rel_gap ~0.05) → should still score >= 0.70."""
    s = compute_league_suitability(0.80, 0.84)
    assert s >= 0.70

def test_extreme_leap():
    """Very large upward gap → should score ≤ 0.15."""
    s = compute_league_suitability(0.30, 0.91)
    assert s <= 0.15

def test_output_range():
    """Result always in [0.05, 1.0]."""
    for p, t in [(0.05, 1.0), (1.0, 0.05), (0.5, 0.5), (0.3, 0.9)]:
        s = compute_league_suitability(p, t)
        assert 0.05 <= s <= 1.0

# ── compute_league_discount tests ────────────────────────────────────────────

def test_discount_downward_returns_one():
    """Downward moves get no discount."""
    assert compute_league_discount(0.91, 0.62) == 1.0

def test_discount_lateral_returns_one():
    """Same-league gets no discount."""
    assert compute_league_discount(0.77, 0.77) == 1.0

def test_discount_indo_to_eredivisie():
    """Indo SL → Eredivisie: heavy discount (0.35–0.55)."""
    d = compute_league_discount(0.6216, 0.7731)
    assert 0.35 <= d <= 0.55, f"Expected 35-55%, got {d*100:.1f}%"

def test_discount_ligue1_to_epl():
    """Ligue 1 → EPL: mild discount (0.70–0.90)."""
    d = compute_league_discount(0.86, 0.9086)
    assert 0.70 <= d <= 0.90, f"Expected 70-90%, got {d*100:.1f}%"

# ── calculate_physical_adaptation (backward compat wrapper) ──────────────────

def test_physical_adaptation_no_age_param():
    """calculate_physical_adaptation takes only two args (no age)."""
    result = calculate_physical_adaptation(0.62, 0.77)
    assert 0.05 <= result <= 1.0

def test_zero_club_intensity_fallback():
    """club_league_intensity == 0 should return 0.5 (neutral)."""
    assert calculate_physical_adaptation(0.5, 0.0) == 0.5
