#!/usr/bin/env python3
"""
Universal provider-adjustment probe runner.

Iterates every registered probe in `app.services.silver.probes` and
runs them against the **same** ProbeSpec, so all providers are
compared apples-to-apples. Adding a new provider = drop a new file in
`app/services/silver/probes/` — this runner picks it up automatically.

**Run:**

    poetry run python scripts/probe_provider_adjustment.py

**Pick a different known split** (when a provider's history doesn't
reach back to the AAPL 2020 default):

    poetry run python scripts/probe_provider_adjustment.py --probe nvda_2024_10for1

**List available probes:**

    poetry run python scripts/probe_provider_adjustment.py --list-probes

**Write a structured JSON report (for CI):**

    poetry run python scripts/probe_provider_adjustment.py --out-json probe.json

See `app/services/silver/probes/README.md` for the full framework
docs + how to onboard a new provider.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from app.services.silver.probes import (  # noqa: E402
    DEFAULT_PROBE_SPEC,
    KNOWN_PROBES,
    ProbeResult,
    ProbeSpec,
    build_all_probes,
    list_registered_probes,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Output formatting
# ─────────────────────────────────────────────────────────────────────


def _format_table(results: list[ProbeResult], spec: ProbeSpec) -> str:
    """Pretty terminal table of results."""
    lines = []
    header = (
        f"{'PROVIDER':<10} {'ENDPOINT':<46} {'DATE':<12} "
        f"{'CLOSE':>10}  {'EXP-RAW':>10}  {'EXP-ADJ':>10}  CLASSIFICATION"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for r in results:
        expected = (
            spec.expected_pre
            if r.probe_date == spec.pre_split_date
            else spec.expected_post
        )
        close_str = (
            f"{r.returned_close:.2f}" if r.returned_close is not None else "—"
        )
        lines.append(
            f"{r.provider:<10} {r.endpoint:<46} {r.probe_date!s:<12} "
            f"{close_str:>10}  {expected.raw:>10.2f}  "
            f"{expected.split_adjusted:>10.2f}  {r.classification}"
        )
        if r.error:
            lines.append(f"             ↳ note: {r.error}")
    return "\n".join(lines)


def _summarize(results: list[ProbeResult], spec: ProbeSpec) -> dict[str, str]:
    """Per-endpoint final verdict.

    Uses the pre-split date only — that's the diagnostic one. Post-split
    is the sanity check (raw == adjusted there).
    """
    by_endpoint: dict[str, list[ProbeResult]] = {}
    for r in results:
        by_endpoint.setdefault(f"{r.provider}/{r.endpoint}", []).append(r)

    summary: dict[str, str] = {}
    for endpoint_key, rs in by_endpoint.items():
        pre = next((r for r in rs if r.probe_date == spec.pre_split_date), None)
        summary[endpoint_key] = pre.classification if pre else "unknown"
    return summary


_VERDICT_MARKERS = {
    "raw": "🟢 RAW",
    "split_adjusted": "🟡 SPLIT_ADJUSTED",
    "other": "🔴 OTHER (investigate!)",
    "no_data": "⚪ NO_DATA",
    "error": "❌ ERROR",
}


# ─────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────


async def run(spec: ProbeSpec, write_json: Optional[Path]) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    registered = list_registered_probes()
    logger.info("Registered probes: %s", registered)
    logger.info(
        "Probe spec: %s %s pre-split=%s (raw=%.2f, adj=%.2f) post-split=%s factor=%.1f",
        spec.symbol,
        f"({spec.split_factor:.0f}-for-1)",
        spec.pre_split_date,
        spec.expected_pre.raw,
        spec.expected_pre.split_adjusted,
        spec.post_split_date,
        spec.split_factor,
    )

    probes = build_all_probes()
    all_results: list[ProbeResult] = []
    for probe in probes:
        provider_results = await probe.probe(spec)
        all_results.extend(provider_results)

    print()
    print(_format_table(all_results, spec))
    print()

    summary = _summarize(all_results, spec)
    print("Verdict per endpoint (based on the pre-split date):")
    for endpoint, verdict in summary.items():
        marker = _VERDICT_MARKERS.get(verdict, "❓ UNKNOWN")
        print(f"  {endpoint:<58} {marker}")
    print()

    # JSON report (CI-friendly)
    if write_json is not None:
        payload = {
            "probed_at": datetime.now(timezone.utc).isoformat(),
            "probe": {
                "symbol": spec.symbol,
                "pre_split_date": spec.pre_split_date.isoformat(),
                "post_split_date": spec.post_split_date.isoformat(),
                "split_factor": spec.split_factor,
                "expected_pre_raw": spec.expected_pre.raw,
                "expected_pre_split_adjusted": spec.expected_pre.split_adjusted,
                "expected_post_raw": spec.expected_post.raw,
                "expected_post_split_adjusted": spec.expected_post.split_adjusted,
            },
            "registered_probes": registered,
            "summary": summary,
            "results": [
                {**asdict(r), "probe_date": r.probe_date.isoformat()}
                for r in all_results
            ],
        }
        write_json.write_text(json.dumps(payload, indent=2, default=str))
        print(f"JSON report → {write_json}")

    # Exit code: 0 if every endpoint classified as raw / split_adjusted /
    # no_data; non-zero if anything classified as other or error.
    bad = [e for e, v in summary.items() if v in ("other", "error")]
    if bad:
        print(f"⚠️  Unclassified endpoints: {bad}")
        return 2
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--probe",
        choices=sorted(KNOWN_PROBES),
        default=None,
        help=(
            "Which known-split probe to use (default: aapl_2020_4for1). "
            "Pick a newer one when a provider's history doesn't reach back "
            "to 2020."
        ),
    )
    p.add_argument(
        "--list-probes",
        action="store_true",
        help="Print available probes and exit.",
    )
    p.add_argument(
        "--out-json",
        type=Path,
        default=None,
        help="Write structured report to this path (stdout-only otherwise).",
    )
    return p


def main() -> int:
    args = _build_parser().parse_args()

    if args.list_probes:
        print("Available probes:")
        for name, spec in KNOWN_PROBES.items():
            print(
                f"  {name:<22} {spec.symbol:<6} "
                f"{spec.pre_split_date} → {spec.post_split_date}  "
                f"({spec.split_factor:.0f}-for-1)"
            )
        print(f"\nRegistered providers: {list_registered_probes()}")
        return 0

    spec = (
        KNOWN_PROBES[args.probe] if args.probe is not None else DEFAULT_PROBE_SPEC
    )
    return asyncio.run(run(spec, args.out_json))


if __name__ == "__main__":
    sys.exit(main())
