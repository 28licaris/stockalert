"""Discretize PSAR / TSI / MACD into the conditioning state.

The reference charts condition on exactly these three indicators. Each is
reduced to a small number of bins and the three are combined into one integer
code in `0..31`:

    code = psar_bin * 16 + tsi_bin * 4 + macd_bin

Pure — takes Series, returns Series. No IO, no config.

**Causality.** Every component is computed from data at or before its bar, and
the state at bar `t` conditions the return from `t` to `t+1`. `estimate.py`
owns that alignment; this module only produces the per-bar state.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.indicators.macd import MACD
from app.indicators.psar import PSAR
from app.indicators.tsi import TSI
from app.services.ppl.schemas import ConditioningParams

N_STATES = 32

_PSAR_LABELS = ("psar_below", "psar_above")   # index = bin; "below" == price above SAR
_TSI_LABELS = (
    "tsi_below_falling",
    "tsi_below_rising",
    "tsi_above_falling",
    "tsi_above_rising",
)
_MACD_LABELS = (
    "macd_neg_falling",
    "macd_neg_rising",
    "macd_pos_falling",
    "macd_pos_rising",
)


def state_label(code: int) -> str:
    """Human-readable label for a state code — used in payloads and logs."""
    if not 0 <= code < N_STATES:
        raise ValueError(f"state code must be in [0, {N_STATES}), got {code}")
    psar_bin, rest = divmod(code, 16)
    tsi_bin, macd_bin = divmod(rest, 4)
    return f"{_PSAR_LABELS[psar_bin]}|{_TSI_LABELS[tsi_bin]}|{_MACD_LABELS[macd_bin]}"


def _macd_hist(close: pd.Series, params: ConditioningParams) -> pd.Series:
    """MACD histogram = MACD line − signal line."""
    return MACD(
        fast=params.macd_fast, slow=params.macd_slow, signal=params.macd_signal
    ).compute_histogram(close)


def compute_state_frame(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    params: ConditioningParams | None = None,
) -> pd.DataFrame:
    """Per-bar conditioning state.

    Returns a frame indexed like `close` with the component values (kept for
    the payload and for explainability) plus the combined `code`. Bars whose
    components are not yet defined carry `code = -1` and must be excluded from
    fitting — `estimate.py` does that rather than silently binning them into
    state 0.
    """
    params = params or ConditioningParams()

    psar = PSAR(af_start=params.psar_af_start, af_max=params.psar_af_max).compute(
        close, high, low
    )
    tsi = TSI(long=params.tsi_long, short=params.tsi_short).compute(close)
    tsi_signal = tsi.ewm(span=params.tsi_signal, adjust=False).mean()
    hist = _macd_hist(close, params)

    # PSAR: 1 when the level sits above price (bearish), 0 when below (bullish).
    psar_bin = (psar > close).astype("float64")

    # TSI: above/below its signal × rising/falling.
    tsi_above = (tsi > tsi_signal).astype("float64")
    tsi_rising = (tsi.diff() > 0).astype("float64")
    tsi_bin = tsi_above * 2 + tsi_rising

    # MACD: histogram sign × the sign of its change.
    hist_pos = (hist > 0).astype("float64")
    hist_rising = (hist.diff() > 0).astype("float64")
    macd_bin = hist_pos * 2 + hist_rising

    code = psar_bin * 16 + tsi_bin * 4 + macd_bin

    # Anything derived from a NaN component is undefined, not state 0. TSI is
    # seeded to 0 by its own implementation, so PSAR warmup and the first
    # diff() are what actually gate definedness here.
    undefined = psar.isna() | tsi.isna() | tsi.diff().isna() | hist.diff().isna()
    code = code.where(~undefined, -1.0)

    return pd.DataFrame(
        {
            "psar": psar,
            "tsi": tsi,
            "tsi_signal": tsi_signal,
            "macd_hist": hist,
            "code": code.astype("int64"),
        },
        index=close.index,
    )


def describe_state(row: pd.Series) -> dict:
    """Explainability payload for one row of `compute_state_frame`."""
    code = int(row["code"])
    return {
        "code": code,
        "label": state_label(code) if code >= 0 else "undefined",
        "psar_side": "above" if row["psar"] > row.get("close", np.nan) else "below",
        "psar_value": float(row["psar"]),
        "tsi_value": float(row["tsi"]),
        "tsi_signal_value": float(row["tsi_signal"]),
        "macd_hist": float(row["macd_hist"]),
    }
