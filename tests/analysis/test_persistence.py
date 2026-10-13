"""
Tests for the persistence test, on made up PnL histories.
"""

import numpy as np
import pytest
from copytrader.analysis import persistence

DAY = persistence.DAY


def history(*points):
    """
    A PnL history from (day, pnl, account value) points.
    """
    return [(day * DAY, pnl, value) for day, pnl, value in points]


def test_periods_are_cut_back_from_the_latest_point_as_far_as_any_chart_goes():
    histories = {"0xa": history((0, 0, None), (100, 5, None)), "0xb": history((50, 0, None), (90, 1, None))}
    assert (persistence.bounds(histories, days=30) / DAY).tolist() == [10, 40, 70, 100]
    assert len(persistence.bounds(histories, days=30, periods=2)) == 3


def test_a_periods_pnl_is_read_off_the_chart_and_none_before_it_begins():
    gains, starts = persistence.period_pnl(history((0, 0, 100), (10, 10, 200)), np.array([-5, 0, 5, 10, 20]) * DAY)
    assert gains == [None, 5.0, 5.0, 0.0]       # Flat after the last point.
    assert starts == [None, 100.0, 150.0, 200.0]
    assert persistence.period_pnl(history((0, 0, None), (10, 10, None)), np.array([0, 10]) * DAY) == ([10.0], [None])


def test_compare_finds_the_top_doing_better_when_skill_lasts():
    before = list(range(-100, 101))
    found = persistence.compare(before, [b + 50 for b in before])
    assert found["n"] == 201 and found["rho"] == pytest.approx(1.0)
    assert found["top"]["positive"] == 1.0 and found["bottom"]["positive"] == 0.0 and found["top_n"]["n"] == persistence.TOP_N


def test_pairs_rank_only_traders_begun_and_moving_and_count_one_who_stops_at_nothing(monkeypatch):
    monkeypatch.setattr(persistence, "TOP_N", 1)
    cuts = np.array([0, 10, 20]) * DAY
    histories = {"0xwin": history((0, 0, 1e5), (10, 50, 1e5), (20, 100, 1e5)),
                 "0xstop": history((0, 0, 1e5), (10, 30, 1e5)),                     # Ranked first, then nothing.
                 "0xidle": history((0, 0, 1e5), (20, 0, 1e5)),                      # Never moved, so never ranked.
                 "0xlate": history((5, 0, 1e5), (20, 40, 1e5)),                     # Began after the first period did.
                 "0xsmall": history((0, 0, 100.0), (10, -1, 100.0), (20, 2, 100.0))}
    ((start, by_pnl),) = persistence.pairs(histories, cuts)
    assert start == 0 and by_pnl["n"] == 3
    assert by_pnl["top"]["median"] == 50.0 and by_pnl["all"]["positive"] == pytest.approx(2 / 3)
    ((_, by_roi),) = persistence.pairs(histories, cuts, by="roi")
    assert by_roi["n"] == 2         # 0xsmall is worth less than MIN_VALUE.


def test_the_report_has_a_line_a_period_and_the_averages():
    found = [(0.0, persistence.compare(list(range(200)), list(range(200))))]
    lines = persistence.report(found, "pnl")
    assert lines[2].startswith("1970-01-01") and lines[-2].startswith("average")
    assert lines[-1] == "the top 10% made money more often than everyone in 1 of 1 periods"


def test_a_gap_compares_a_period_with_one_further_on(monkeypatch):
    monkeypatch.setattr(persistence, "TOP_N", 1)
    cuts = np.array([0, 10, 20, 30]) * DAY
    histories = {f"0x{i}": history((0, 0, None), (10, i + 1, None), (20, i + 1, None), (30, 2 * i + 2, None)) for i in range(4)}
    ((start, found),) = persistence.pairs(histories, cuts, gap=1)
    assert start == 0 and found["rho"] == pytest.approx(1.0) and found["all"]["median"] == 2.5     # The third period, not the second's nothing.
    assert "the period 1 after the next" in persistence.report([(start, found)], "pnl", gap=1)[0]
