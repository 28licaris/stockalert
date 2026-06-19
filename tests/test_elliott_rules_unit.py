"""EW-2: the three hard rules + zigzag constraint, as pure predicates."""
from __future__ import annotations

from app.signals.elliott import rules


def test_textbook_impulse_passes_all():
    prices = [100, 120, 108, 150, 134, 154]
    passed = rules.evaluate_impulse(prices, "up")
    assert passed == {
        "rule1_w2_no_full_retrace": True,
        "rule2_w3_not_shortest": True,
        "rule3_w4_no_overlap": True,
    }


def test_rule1_rejects_deep_wave2():
    # wave 2 (98) drops below wave-1 origin (100) → >100% retrace
    assert rules.rule1_wave2_no_full_retrace([100, 120, 98], "up") is False


def test_rule2_rejects_short_wave3():
    # wave 3 (115-108=7) shorter than wave 1 (20) → provisional fail
    assert rules.evaluate_impulse([100, 120, 108, 115], "up")["rule2_w3_not_shortest"] is False


def test_rule3_rejects_wave4_overlap():
    # wave 4 low (118) enters wave-1 top (120) territory
    assert rules.rule3_wave4_no_overlap([100, 120, 108, 150, 118], "up") is False


def test_down_impulse_mirror():
    prices = [200, 180, 192, 150, 166, 146]  # reflection of the up textbook
    passed = rules.evaluate_impulse(prices, "down")
    assert all(passed.values())


def test_zigzag_b_no_origin_overshoot():
    # up-correction: B low (105) stays above A origin (100) → valid
    assert rules.evaluate_zigzag([100, 120, 105], "up")["zz_b_no_origin_overshoot"] is True
    # B low (98) breaks below origin (100) → invalid
    assert rules.evaluate_zigzag([100, 120, 98], "up")["zz_b_no_origin_overshoot"] is False
