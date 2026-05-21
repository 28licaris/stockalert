"""Unit tests for `scripts/polygon_history_backfill.py` CLI parsing.

Only covers the date-window resolver — the rest of the script
(Polygon S3 + Iceberg writes) is exercised by integration tests.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pytest

# Add scripts/ to sys.path so we can import the script as a module.
SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import polygon_history_backfill as cli  # noqa: E402


def _ns(**kw) -> argparse.Namespace:
    defaults = {
        "days": None, "since": None, "until": None,
        "concurrency": 4, "symbols": "", "force": False,
    }
    defaults.update(kw)
    return argparse.Namespace(**defaults)


def test_resolve_window_explicit_since_until():
    ns = _ns(since=date(2024, 1, 2), until=date(2024, 1, 10))
    assert cli._resolve_window(ns) == (date(2024, 1, 2), date(2024, 1, 10))


def test_resolve_window_days_anchors_on_until():
    """--days N --until X should produce [X - (N-1), X], inclusive."""
    ns = _ns(days=5, until=date(2024, 1, 10))
    start, end = cli._resolve_window(ns)
    assert end == date(2024, 1, 10)
    assert start == date(2024, 1, 6)
    assert (end - start).days == 4


def test_resolve_window_default_is_yesterday_only(monkeypatch):
    """No args = yesterday-only (matches the post-Phase-1B nightly cron
    contract — the script behaves as if it's the recurring job)."""
    fixed = date(2024, 5, 15)
    monkeypatch.setattr(cli, "yesterday_et", lambda: fixed)

    start, end = cli._resolve_window(_ns())
    assert start == fixed
    assert end == fixed


def test_resolve_window_rejects_inverted_window():
    ns = _ns(since=date(2024, 1, 10), until=date(2024, 1, 5))
    with pytest.raises(SystemExit):
        cli._resolve_window(ns)


def test_parse_date_accepts_explicit_iso():
    assert cli._parse_date("2024-01-15") == date(2024, 1, 15)


def test_parse_date_yesterday_is_et_anchored(monkeypatch):
    monkeypatch.setattr(cli, "yesterday_et", lambda: date(2024, 5, 14))
    assert cli._parse_date("yesterday") == date(2024, 5, 14)


def test_parse_date_today_uses_local_calendar():
    assert cli._parse_date("today") == date.today()


def test_argparser_includes_force_and_concurrency_flags():
    p = cli._build_parser()
    args = p.parse_args(["--concurrency", "8", "--force"])
    assert args.concurrency == 8
    assert args.force is True


def test_argparser_mutex_days_vs_since():
    p = cli._build_parser()
    with pytest.raises(SystemExit):
        p.parse_args(["--days", "3", "--since", "2024-01-01"])
