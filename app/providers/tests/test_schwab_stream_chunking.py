"""Chart-subscription chunking.

Regression for a silent, three-month data loss: Schwab delivered ticks for only
the first 300 keys of a 424-symbol subscription and dropped the rest with no
error, no REACHED_SYMBOL_LIMIT, and `/stream/status` still reporting all 424 as
subscribed.
"""
from __future__ import annotations

import pytest

from app.providers.schwab_provider import (
    MAX_STREAM_KEYS_PER_REQUEST as CAP,
    SERVICE_CHART_EQUITY,
    SchwabProvider,
)

FIELDS = "0,2,3,4,5,6,7"


def build(keys, **kw):
    return SchwabProvider.build_chart_subscribe_requests(
        SERVICE_CHART_EQUITY, keys, FIELDS, **kw
    )


def keys_of(reqs):
    out = []
    for r in reqs:
        out.extend(r["parameters"]["keys"].split(","))
    return out


def test_no_request_exceeds_the_measured_cap():
    """The whole point: Schwab truncates past 300 keys per subscription."""
    reqs = build([f"SYM{i}" for i in range(1000)])
    assert reqs
    for r in reqs:
        assert len(r["parameters"]["keys"].split(",")) <= CAP


def test_every_symbol_survives_chunking():
    syms = [f"SYM{i}" for i in range(424)]
    assert keys_of(build(syms)) == syms, "chunking must not drop or reorder"


def test_first_chunk_subscribes_and_the_rest_append():
    """SUBS REPLACES a service's subscription. Sending it per chunk would
    leave only the final chunk active — the exact failure being fixed."""
    reqs = build([f"SYM{i}" for i in range(700)])

    assert len(reqs) == 3
    assert [r["command"] for r in reqs] == ["SUBS", "ADD", "ADD"]


def test_a_universe_under_the_cap_is_a_single_subs():
    reqs = build([f"SYM{i}" for i in range(50)])
    assert len(reqs) == 1
    assert reqs[0]["command"] == "SUBS"


def test_request_ids_are_unique():
    """Duplicated ids make responses impossible to attribute to a request."""
    reqs = build([f"SYM{i}" for i in range(1000)])
    ids = [r["requestid"] for r in reqs]
    assert len(set(ids)) == len(ids)


def test_exact_cap_boundary_stays_one_request():
    assert len(build([f"S{i}" for i in range(CAP)])) == 1
    assert len(build([f"S{i}" for i in range(CAP + 1)])) == 2


def test_empty_input_produces_no_requests():
    assert build([]) == []


def test_client_ids_are_attached_when_supplied():
    ids = {"SchwabClientCustomerId": "cust", "SchwabClientCorrelId": "corr"}
    reqs = build([f"S{i}" for i in range(400)], ids=ids)
    assert len(reqs) == 2
    for r in reqs:
        assert r["SchwabClientCustomerId"] == "cust"
        assert r["SchwabClientCorrelId"] == "corr"


def test_equity_and_futures_ids_do_not_collide():
    """Both services are chunked in the same login batch; overlapping request
    ids would make their acknowledgements ambiguous."""
    eq = build([f"S{i}" for i in range(400)], first_request_id=2)
    fu = SchwabProvider.build_chart_subscribe_requests(
        "CHART_FUTURES", [f"/F{i}" for i in range(10)], FIELDS, first_request_id=100
    )
    assert not ({r["requestid"] for r in eq} & {r["requestid"] for r in fu})


def test_partition_keeps_futures_out_of_the_equity_service():
    eq, fu = SchwabProvider.partition_chart_stream_keys(["SPY", "/ES", "aapl", "/MNQ"])
    assert eq == ["SPY", "AAPL"] and fu == ["/ES", "/MNQ"]
