"""DT-1 setup detectors + honest intraday simulation — synthetic-path contracts."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from daytrade_setups import (  # noqa: E402
    Trigger,
    detect_flush_reclaim,
    detect_flush_reclaim_v2,
    detect_first_pullback,
    detect_first_pullback_v2,
    detect_orb,
    detect_vwap_reclaim,
    run_symbol_day,
    simulate,
    simulate_trailing,
)


def _flat(n, px=100.0, vol=1e4):
    o = np.full(n, px); h = o + 0.05; l = o - 0.05; c = o.copy(); v = np.full(n, vol)
    return o, h, l, c, v


def test_orb_long_triggers_in_gap_direction_with_or_stop():
    o, h, l, c, v = _flat(60)
    h[:15] = 101.0; l[:15] = 99.0                     # opening range 99-101
    c[40] = 101.5; h[40] = 101.6                      # breakout close at bar 40
    trig = detect_orb(o, h, l, c, v, gap_pct=0.05)
    assert trig and trig.side == "long" and trig.i == 40 and trig.stop == 99.0


def test_orb_skipped_when_range_too_wide():
    o, h, l, c, v = _flat(60)
    h[:15] = 104.0; l[:15] = 96.0                     # 8% range > 3% cap
    c[40] = 104.5
    assert detect_orb(o, h, l, c, v, gap_pct=0.05) is None


def test_vwap_reclaim_long():
    n = 60
    o, h, l, c, v = _flat(n)
    c[:40] = 99.0; h[:40] = 99.1; l[:40] = 98.9       # well below vwap anchor
    c[0] = 101.0; h[0] = 101.1                        # first bar high pins vwap up
    c[40] = 100.5; h[40] = 100.6                      # reclaim close
    trig = detect_vwap_reclaim(o, h, l, c, v)
    assert trig and trig.side == "long" and trig.setup == "vwap_reclaim"
    assert trig.stop <= 98.9 + 1e-9


def test_first_pullback_long():
    n = 80
    o, h, l, c, v = _flat(n, px=100.0)
    for i in range(1, 20):                            # opening drive to +4%
        h[i] = 100 + 0.2 * i; c[i] = h[i] - 0.05; l[i] = h[i] - 0.1
    for i in range(20, 30):                           # pullback ~50% of drive
        h[i] = 103.0; l[i] = 102.0; c[i] = 102.2
    c[35] = 103.5; h[35] = 103.6                      # break of pullback high
    trig = detect_first_pullback(o, h, l, c, v)
    assert trig and trig.side == "long" and trig.i == 35 and trig.stop == 102.0


def test_flush_reclaim_long():
    n = 80
    o, h, l, c, v = _flat(n, px=100.0)
    for i in range(1, 50):                            # grind lower all morning
        l[i] = 100 - 0.05 * i; c[i] = l[i] + 0.02; h[i] = l[i] + 0.05
    v[50] = 1e5                                       # 10x volume flush bar
    l[50] = 96.0; h[50] = 97.7; c[50] = 96.2; o[50] = 97.6
    for i in range(51, 55):                           # hover below the flush high
        c[i] = 97.0; h[i] = 97.3; l[i] = 96.5
    c[55] = 97.9; h[55] = 98.0                        # reclaim above flush high
    trig = detect_flush_reclaim(o, h, l, c, v)
    assert trig and trig.setup == "flush_reclaim" and trig.i == 55
    assert trig.stop == 96.0


def test_simulate_stop_worst_case_and_gap_through_open():
    o, h, l, c, v = _flat(30)
    trig = Trigger("orb", "long", 5, entry=100.0, stop=98.0)
    l[10] = 97.5                                      # touches stop intra-bar
    r1 = simulate(trig, o, h, l, c, slip_mult=1.0)
    assert r1.exit_reason == "stop" and r1.exit_fill <= 98.0
    o2, h2, l2, c2, _ = _flat(30)
    o2[10] = 96.0; l2[10] = 95.5                      # gaps through the stop
    r2 = simulate(trig, o2, h2, l2, c2, slip_mult=1.0)
    assert r2.exit_fill <= 96.0                       # open fill, not the level
    assert r2.r_mult < -1.0                           # honest >1R loss on the gap


def test_simulate_target_is_resting_limit_and_eod_close():
    o, h, l, c, v = _flat(400)
    trig = Trigger("orb", "long", 5, entry=100.0, stop=99.0)
    h[20] = 103.5                                     # through the 2R target
    r = simulate(trig, o, h, l, c, slip_mult=1.0)
    assert r.exit_reason == "target" and abs(r.r_mult - 2.0) < 1e-9
    o3, h3, l3, c3, _ = _flat(400)
    r3 = simulate(trig, o3, h3, l3, c3, slip_mult=1.0)  # nothing hit → EOD
    assert r3.exit_reason == "eod" and r3.exit_i == 385


def test_truncation_invariance_of_trigger():
    # mutating bars AFTER the trigger must not change the trigger (no look-ahead)
    o, h, l, c, v = _flat(120)
    h[:15] = 101.0; l[:15] = 99.0; c[40] = 101.5; h[40] = 101.6
    t1 = detect_orb(o, h, l, c, v, 0.05)
    c2, h2 = c.copy(), h.copy()
    c2[60:] = 50.0; h2[60:] = 50.0                    # nuke the future
    t2 = detect_orb(o, h2, l, c2, v, 0.05)
    assert t1 == t2


def test_pullback_v2_timing_gate():
    # trigger inside the opening 30 minutes → v1 takes it, v2 refuses
    # (opening-window fades were −0.80R in the sandbox)
    n = 80
    o, h, l, c, v = _flat(n, px=100.0)
    for i in range(1, 15):
        h[i] = 100 + 0.25 * i; c[i] = h[i] - 0.05; l[i] = h[i] - 0.1
    for i in range(15, 22):
        h[i] = 102.5; l[i] = 101.8; c[i] = 102.0
    c[25] = 103.0; h[25] = 103.1                      # break at 09:55 (<10:00)
    assert detect_first_pullback(o, h, l, c, v).i == 25
    assert detect_first_pullback_v2(o, h, l, c, v) is None


def test_flush_v2_waits_for_second_test():
    n = 100
    o, h, l, c, v = _flat(n, px=100.0)
    for i in range(1, 50):
        l[i] = 100 - 0.05 * i; c[i] = l[i] + 0.02; h[i] = l[i] + 0.05
    v[50] = 1e5
    l[50] = 96.0; h[50] = 97.7; c[50] = 96.2; o[50] = 97.6   # flush bar
    c[51] = 97.8; h[51] = 97.9; l[51] = 97.1                  # v1's first reclaim
    for i in range(52, 60):                                   # bounce, no retest yet
        c[i] = 97.2; h[i] = 97.4; l[i] = 97.0
    l[60] = 96.05; h[60] = 96.6; c[60] = 96.3                 # second test holds low
    for i in range(61, 64):
        c[i] = 96.4; h[i] = 96.5; l[i] = 96.2
    c[64] = 96.8; h[64] = 96.9                                # close above retest high
    trig = detect_flush_reclaim_v2(o, h, l, c, v)
    assert trig and trig.i == 64 and trig.stop == 96.0
    # v1 (first reclaim) enters at bar 51 — v2 waited for the second test
    assert detect_flush_reclaim(o, h, l, c, v).i == 51


def test_trailing_sim_locks_gains_and_rides_trend():
    n = 400
    o, h, l, c, v = _flat(n, px=100.0)
    trig = Trigger("orb_v2", "long", 5, entry=100.0, stop=98.0)
    for j in range(6, 200):                                   # steady trend up
        px = 100.0 + 0.05 * (j - 5)
        o[j] = px; c[j] = px; h[j] = px + 0.02; l[j] = px - 0.02
    for j in range(200, n):                                   # steady break of trail
        px = max(90.0, 109.75 - 0.15 * (j - 199))
        o[j] = px; c[j] = px; h[j] = px + 0.02; l[j] = px - 0.02
    r = simulate_trailing(trig, o, h, l, c, slip_mult=1.0)
    # armed after +1R (=102), trailed under the rising 20-bar low → the break
    # at bar 200 stops it out far ABOVE entry (gains locked, not round-tripped)
    assert r.exit_reason == "stop" and r.r_mult > 1.0
    # and a clean trend into the close exits EOD, uncapped by any fixed target
    o2, h2, l2, c2, _ = _flat(n, px=100.0)
    for j in range(6, n):
        px = 100.0 + 0.05 * (j - 5)
        o2[j] = px; c2[j] = px; h2[j] = px + 0.02; l2[j] = px - 0.02
    r2 = simulate_trailing(trig, o2, h2, l2, c2, slip_mult=1.0)
    assert r2.exit_reason == "eod" and r2.r_mult > 2.0


def test_run_symbol_day_one_trigger_per_setup():
    o, h, l, c, v = _flat(400)
    h[:15] = 101.0; l[:15] = 99.0
    c[40] = 101.5; h[40] = 101.6
    c[41:] = 101.5; h[41:] = 101.6; l[41:] = 101.4    # stays up → EOD exit
    out = run_symbol_day(o, h, l, c, v, gap_pct=0.05)
    assert [r.setup for r in out].count("orb") == 1
