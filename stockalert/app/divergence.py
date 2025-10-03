"""
Divergence Detection Module

This module implements detection algorithms for price-momentum divergences,
which are powerful technical analysis signals that indicate potential trend
reversals or continuations.

Divergence Types:
-----------------
1. Regular Bullish Divergence:
   - Price makes LOWER low
   - Indicator makes HIGHER low
   - Signals: Potential trend REVERSAL from downtrend to uptrend
   - Best used: At support levels in downtrends

2. Regular Bearish Divergence:
   - Price makes HIGHER high
   - Indicator makes LOWER high
   - Signals: Potential trend REVERSAL from uptrend to downtrend
   - Best used: At resistance levels in uptrends

3. Hidden Bullish Divergence:
   - Price makes HIGHER low
   - Indicator makes LOWER low
   - Signals: Trend CONTINUATION of existing uptrend
   - Best used: During pullbacks in uptrends

4. Hidden Bearish Divergence:
   - Price makes LOWER high
   - Indicator makes HIGHER high
   - Signals: Trend CONTINUATION of existing downtrend
   - Best used: During rallies in downtrends

Pivot Detection:
---------------
Pivots are local extrema (peaks/troughs) in price or indicator series.
A pivot is detected when a point is surrounded by k bars on each side
that are all higher (for low) or lower (for high).

Example with k=3:
  Pivot Low: Point is lower than 3 bars before AND 3 bars after
  [high, high, high, LOW, high, high, high]
                      ↑
                   pivot low

Trend Filter:
------------
Optional EMA-based trend filter ensures divergences align with broader trend:
- Bull divergence: Only triggered if price is above EMA (uptrend confirmation)
- Bear divergence: Only triggered if price is below EMA (downtrend confirmation)

This reduces false signals in choppy/ranging markets.

Configuration:
-------------
- PIVOT_K: Number of bars on each side for pivot detection (default: 3)
- LOOKBACK_BARS: How many recent bars to analyze (default: 60)
- USE_TREND_FILTER: Enable/disable EMA trend filter (default: true)
- EMA_PERIOD: Period for trend filter EMA (default: 50)

References:
----------
- Bulkowski, Thomas N. "Encyclopedia of Chart Patterns"
- Murphy, John J. "Technical Analysis of the Financial Markets"
- https://www.investopedia.com/terms/d/divergence.asp
"""

from __future__ import annotations
import pandas as pd
from typing import Optional, Dict, List
from app.config import settings


def _ema(series: pd.Series, period: int) -> pd.Series:
    """
    Calculate Exponential Moving Average.
    
    Used for trend filtering to ensure divergences align with broader market direction.
    
    Args:
        series: Price series to smooth
        period: EMA period (e.g., 50 for 50-bar EMA)
    
    Returns:
        pd.Series: Exponentially smoothed values
    """
    return series.ewm(span=period, adjust=False).mean()


def find_pivot_lows(close: pd.Series, k: int, strict: bool = True) -> List[pd.Timestamp]:
    """
    Find pivot lows (local minima/troughs) in a price series.
    
    A pivot low is a point that is lower than k bars before it AND k bars after it.
    These represent potential support levels or reversal points.
    
    Algorithm:
    ----------
    For each bar i (from k to n-k):
        1. Check if close[i] is the minimum in window [i-k, i+k]
        2. If strict=True: Also verify close[i] < all bars in left AND right windows
        3. If conditions met: i is a pivot low
    
    Example with k=3:
    ----------------
    Prices: [10.5, 10.3, 10.1, 9.8, 10.0, 10.2, 10.4]
                              ↑
                        Pivot low at 9.8
                        (lower than 3 bars before and after)
    
    Args:
        close: Series of closing prices
        k: Number of bars on each side for comparison (window size)
        strict: If True, pivot must be strictly lower than ALL surrounding bars
               If False, only needs to be minimum in window (allows flat pivots)
    
    Returns:
        List of timestamps where pivot lows occur
    
    Notes:
        - First k and last k bars cannot be pivots (insufficient context)
        - Larger k = more significant pivots but fewer detected
        - strict=False allows detection when prices are flat/choppy
    """
    idxs = []
    n = len(close)
    vals = close.values
    
    # Iterate through potential pivot points (excluding edges)
    for i in range(k, n - k):
        # Get surrounding bars
        left = close.iloc[i - k:i]      # k bars before
        right = close.iloc[i + 1:i + k + 1]  # k bars after
        c = vals[i]  # Current bar value
        
        # Check if this is the minimum in the window [i-k, i+k]
        if c == close.iloc[i - k:i + k + 1].min():
            # Strict mode: Must be LOWER than all left AND all right bars
            # Non-strict: Just being the minimum is sufficient
            if not strict or (c < left.min() and c < right.min()):
                idxs.append(close.index[i])
    
    return idxs


def find_pivot_highs(close: pd.Series, k: int, strict: bool = True) -> List[pd.Timestamp]:
    """
    Find pivot highs (local maxima/peaks) in a price series.
    
    A pivot high is a point that is higher than k bars before it AND k bars after it.
    These represent potential resistance levels or reversal points.
    
    Algorithm:
    ----------
    For each bar i (from k to n-k):
        1. Check if close[i] is the maximum in window [i-k, i+k]
        2. If strict=True: Also verify close[i] > all bars in left AND right windows
        3. If conditions met: i is a pivot high
    
    Example with k=3:
    ----------------
    Prices: [10.0, 10.2, 10.4, 10.7, 10.5, 10.3, 10.1]
                              ↑
                        Pivot high at 10.7
                        (higher than 3 bars before and after)
    
    Args:
        close: Series of closing prices
        k: Number of bars on each side for comparison (window size)
        strict: If True, pivot must be strictly higher than ALL surrounding bars
               If False, only needs to be maximum in window (allows flat pivots)
    
    Returns:
        List of timestamps where pivot highs occur
    
    Notes:
        - First k and last k bars cannot be pivots (insufficient context)
        - Larger k = more significant pivots but fewer detected
        - strict=False allows detection when prices are flat/choppy
    """
    idxs = []
    n = len(close)
    vals = close.values
    
    # Iterate through potential pivot points (excluding edges)
    for i in range(k, n - k):
        # Get surrounding bars
        left = close.iloc[i - k:i]      # k bars before
        right = close.iloc[i + 1:i + k + 1]  # k bars after
        c = vals[i]  # Current bar value
        
        # Check if this is the maximum in the window [i-k, i+k]
        if c == close.iloc[i - k:i + k + 1].max():
            # Strict mode: Must be HIGHER than all left AND all right bars
            # Non-strict: Just being the maximum is sufficient
            if not strict or (c > left.max() and c > right.max()):
                idxs.append(close.index[i])
    
    return idxs


def _bull_trend_ok(close: pd.Series) -> bool:
    """
    Check if price is in bullish trend (above EMA).
    
    Bullish divergences are more reliable when price is already above
    the trend EMA, confirming upward momentum structure.
    
    Conditions:
    -----------
    1. If trend filter disabled: Always returns True (no filter)
    2. Sufficient data: Need at least EMA_PERIOD + 5 bars
    3. Current price > EMA: Confirms bullish trend structure
    
    Args:
        close: Price series to analyze
    
    Returns:
        bool: True if bullish trend confirmed (or filter disabled)
    
    Example:
        >>> close = pd.Series([100, 101, 102, 103, 104])
        >>> _bull_trend_ok(close)  # True if last price > 50 EMA
    """
    # Bypass if trend filter is disabled
    if not settings.use_trend_filter:
        return True
    
    # Remove NaN values
    s = close.dropna()
    
    # Ensure sufficient data for EMA calculation
    if len(s) < settings.ema_period + 5:
        return False
    
    # Calculate EMA and check if price is above it
    e = _ema(s, settings.ema_period)
    return s.iloc[-1] > e.iloc[-1]


def _bear_trend_ok(close: pd.Series) -> bool:
    """
    Check if price is in bearish trend (below EMA).
    
    Bearish divergences are more reliable when price is already below
    the trend EMA, confirming downward momentum structure.
    
    Conditions:
    -----------
    1. If trend filter disabled: Always returns True (no filter)
    2. Sufficient data: Need at least EMA_PERIOD + 5 bars
    3. Current price < EMA: Confirms bearish trend structure
    
    Args:
        close: Price series to analyze
    
    Returns:
        bool: True if bearish trend confirmed (or filter disabled)
    
    Example:
        >>> close = pd.Series([104, 103, 102, 101, 100])
        >>> _bear_trend_ok(close)  # True if last price < 50 EMA
    """
    # Bypass if trend filter is disabled
    if not settings.use_trend_filter:
        return True
    
    # Remove NaN values
    s = close.dropna()
    
    # Ensure sufficient data for EMA calculation
    if len(s) < settings.ema_period + 5:
        return False
    
    # Calculate EMA and check if price is below it
    e = _ema(s, settings.ema_period)
    return s.iloc[-1] < e.iloc[-1]


def detect_hidden_bullish(
    close: pd.Series, 
    ind: pd.Series, 
    lookback: int, 
    k: int
) -> Optional[Dict]:
    """
    Detect Hidden Bullish Divergence (Trend Continuation).
    
    Hidden bullish divergence occurs during pullbacks in an uptrend and
    suggests the uptrend will continue. It's a confirmation signal, not
    a reversal signal.
    
    Conditions:
    -----------
    1. Find at least 2 pivot lows in price
    2. Price: Second low HIGHER than first low (higher low)
    3. Indicator: Second low LOWER than first low (lower low)
    4. Trend check: Price should be above EMA (optional filter)
    
    Interpretation:
    ---------------
    - Price making higher lows = bulls maintaining control
    - Indicator making lower lows = temporary momentum weakness
    - Disagreement suggests: Strong buyers during pullback
    - Signal: Uptrend likely to resume after consolidation
    
    Visual Example:
    ---------------
    Price:     /\  /\      Higher low
              /  \/  \     (trend continuation)
    
    RSI:       /\  /\      Lower low
              /  \/   \    (momentum divergence)
    
    Args:
        close: Price series (close prices)
        ind: Indicator series (RSI, MACD, TSI, etc.)
        lookback: Number of recent bars to analyze (e.g., 60)
        k: Pivot detection window size (e.g., 3)
    
    Returns:
        Dictionary with divergence details if found:
        {
            'p1_ts': Timestamp of first pivot,
            'p2_ts': Timestamp of second pivot,
            'price': Price at second pivot,
            'indicator_value': Indicator value at second pivot
        }
        None if no divergence detected
    
    Example:
        >>> rsi = RSI(period=14).compute(df['close'])
        >>> div = detect_hidden_bullish(df['close'], rsi, lookback=60, k=3)
        >>> if div:
        >>>     print(f"Hidden bullish at {div['p2_ts']}: ${div['price']:.2f}")
    """
    # Get recent data window
    sub_close = close.tail(lookback)
    sub_ind = ind.reindex(sub_close.index)
    
    # Find pivot lows in price
    piv = find_pivot_lows(sub_close, k)
    
    # Need at least 2 pivots to compare
    if len(piv) < 2:
        return None
    
    # Get last two pivot points
    p1, p2 = piv[-2], piv[-1]
    
    # Check divergence conditions:
    # 1. Price: Higher low (p2 > p1) - bullish price structure
    # 2. Indicator: Lower low (p2 < p1) - momentum weakening
    if sub_close.loc[p2] > sub_close.loc[p1] and sub_ind.loc[p2] < sub_ind.loc[p1]:
        # Apply trend filter (ensure we're in uptrend)
        if not _bull_trend_ok(sub_close):
            return None
        
        return {
            "p1_ts": p1,
            "p2_ts": p2,
            "price": sub_close.loc[p2],
            "indicator_value": sub_ind.loc[p2]
        }
    
    return None


def detect_hidden_bearish(
    close: pd.Series, 
    ind: pd.Series, 
    lookback: int, 
    k: int
) -> Optional[Dict]:
    """
    Detect Hidden Bearish Divergence (Trend Continuation).
    
    Hidden bearish divergence occurs during rallies in a downtrend and
    suggests the downtrend will continue. It's a confirmation signal for
    existing downtrends.
    
    Conditions:
    -----------
    1. Find at least 2 pivot highs in price
    2. Price: Second high LOWER than first high (lower high)
    3. Indicator: Second high HIGHER than first high (higher high)
    4. Trend check: Price should be below EMA (optional filter)
    
    Interpretation:
    ---------------
    - Price making lower highs = bears maintaining control
    - Indicator making higher highs = temporary momentum strength
    - Disagreement suggests: Weak rally, sellers will return
    - Signal: Downtrend likely to resume after bounce
    
    Visual Example:
    ---------------
    Price:     \  /\  /    Lower high
                \/  \/     (trend continuation)
    
    RSI:       \    /\     Higher high
                \  /  \    (momentum divergence)
    
    Args:
        close: Price series (close prices)
        ind: Indicator series (RSI, MACD, TSI, etc.)
        lookback: Number of recent bars to analyze (e.g., 60)
        k: Pivot detection window size (e.g., 3)
    
    Returns:
        Dictionary with divergence details if found:
        {
            'p1_ts': Timestamp of first pivot,
            'p2_ts': Timestamp of second pivot,
            'price': Price at second pivot,
            'indicator_value': Indicator value at second pivot
        }
        None if no divergence detected
    
    Example:
        >>> rsi = RSI(period=14).compute(df['close'])
        >>> div = detect_hidden_bearish(df['close'], rsi, lookback=60, k=3)
        >>> if div:
        >>>     print(f"Hidden bearish at {div['p2_ts']}: ${div['price']:.2f}")
    """
    # Get recent data window
    sub_close = close.tail(lookback)
    sub_ind = ind.reindex(sub_close.index)
    
    # Find pivot highs in price
    piv = find_pivot_highs(sub_close, k)
    
    # Need at least 2 pivots to compare
    if len(piv) < 2:
        return None
    
    # Get last two pivot points
    p1, p2 = piv[-2], piv[-1]
    
    # Check divergence conditions:
    # 1. Price: Lower high (p2 < p1) - bearish price structure
    # 2. Indicator: Higher high (p2 > p1) - momentum strengthening
    if sub_close.loc[p2] < sub_close.loc[p1] and sub_ind.loc[p2] > sub_ind.loc[p1]:
        # Apply trend filter (ensure we're in downtrend)
        if not _bear_trend_ok(sub_close):
            return None
        
        return {
            "p1_ts": p1,
            "p2_ts": p2,
            "price": sub_close.loc[p2],
            "indicator_value": sub_ind.loc[p2]
        }
    
    return None


def detect_regular_bullish(
    close: pd.Series, 
    ind: pd.Series, 
    lookback: int, 
    k: int
) -> Optional[Dict]:
    """
    Detect Regular Bullish Divergence (Trend Reversal).
    
    Regular bullish divergence occurs at the end of a downtrend and
    suggests a potential reversal to uptrend. It's a reversal signal.
    
    Conditions:
    -----------
    1. Find at least 2 pivot lows in price
    2. Price: Second low LOWER than first low (lower low)
    3. Indicator: Second low HIGHER than first low (higher low)
    4. Trend check: Price should be above EMA (optional filter)
    
    Interpretation:
    ---------------
    - Price making lower lows = downtrend still in place
    - Indicator making higher lows = momentum improving (divergence)
    - Disagreement suggests: Selling pressure weakening
    - Signal: Potential trend reversal from down to up
    
    Visual Example:
    ---------------
    Price:     \  /\       Lower low
                \/  \      (still downtrend)
    
    RSI:       \    /\     Higher low
                \  /  \    (momentum improving)
                           ↑ REVERSAL SIGNAL
    
    Args:
        close: Price series (close prices)
        ind: Indicator series (RSI, MACD, TSI, etc.)
        lookback: Number of recent bars to analyze (e.g., 60)
        k: Pivot detection window size (e.g., 3)
    
    Returns:
        Dictionary with divergence details if found, None otherwise
    
    Example:
        >>> rsi = RSI(period=14).compute(df['close'])
        >>> div = detect_regular_bullish(df['close'], rsi, lookback=60, k=3)
        >>> if div:
        >>>     print(f"🚨 Potential reversal at {div['p2_ts']}")
    """
    # Get recent data window
    sub_close = close.tail(lookback)
    sub_ind = ind.reindex(sub_close.index)
    
    # Find pivot lows in price
    piv = find_pivot_lows(sub_close, k)
    
    # Need at least 2 pivots to compare
    if len(piv) < 2:
        return None
    
    # Get last two pivot points
    p1, p2 = piv[-2], piv[-1]
    
    # Check divergence conditions:
    # 1. Price: Lower low (p2 < p1) - still in downtrend
    # 2. Indicator: Higher low (p2 > p1) - momentum improving
    if sub_close.loc[p2] < sub_close.loc[p1] and sub_ind.loc[p2] > sub_ind.loc[p1]:
        # Apply trend filter
        if not _bull_trend_ok(sub_close):
            return None
        
        return {
            "p1_ts": p1,
            "p2_ts": p2,
            "price": sub_close.loc[p2],
            "indicator_value": sub_ind.loc[p2]
        }
    
    return None


def detect_regular_bearish(
    close: pd.Series, 
    ind: pd.Series, 
    lookback: int, 
    k: int
) -> Optional[Dict]:
    """
    Detect Regular Bearish Divergence (Trend Reversal).
    
    Regular bearish divergence occurs at the end of an uptrend and
    suggests a potential reversal to downtrend. It's a reversal signal.
    
    Conditions:
    -----------
    1. Find at least 2 pivot highs in price
    2. Price: Second high HIGHER than first high (higher high)
    3. Indicator: Second high LOWER than first high (lower high)
    4. Trend check: Price should be below EMA (optional filter)
    
    Interpretation:
    ---------------
    - Price making higher highs = uptrend still in place
    - Indicator making lower highs = momentum weakening (divergence)
    - Disagreement suggests: Buying pressure exhausting
    - Signal: Potential trend reversal from up to down
    
    Visual Example:
    ---------------
    Price:       /\  /\    Higher high
                /  \/  \   (still uptrend)
    
    RSI:       /\  /\      Lower high
              /  \/  \     (momentum weakening)
                           ↑ REVERSAL SIGNAL
    
    Args:
        close: Price series (close prices)
        ind: Indicator series (RSI, MACD, TSI, etc.)
        lookback: Number of recent bars to analyze (e.g., 60)
        k: Pivot detection window size (e.g., 3)
    
    Returns:
        Dictionary with divergence details if found, None otherwise
    
    Example:
        >>> rsi = RSI(period=14).compute(df['close'])
        >>> div = detect_regular_bearish(df['close'], rsi, lookback=60, k=3)
        >>> if div:
        >>>     print(f"🚨 Potential reversal at {div['p2_ts']}")
    """
    # Get recent data window
    sub_close = close.tail(lookback)
    sub_ind = ind.reindex(sub_close.index)
    
    # Find pivot highs in price
    piv = find_pivot_highs(sub_close, k)
    
    # Need at least 2 pivots to compare
    if len(piv) < 2:
        return None
    
    # Get last two pivot points
    p1, p2 = piv[-2], piv[-1]
    
    # Check divergence conditions:
    # 1. Price: Higher high (p2 > p1) - still in uptrend
    # 2. Indicator: Lower high (p2 < p1) - momentum weakening
    if sub_close.loc[p2] > sub_close.loc[p1] and sub_ind.loc[p2] < sub_ind.loc[p1]:
        # Apply trend filter
        if not _bear_trend_ok(sub_close):
            return None
        
        return {
            "p1_ts": p1,
            "p2_ts": p2,
            "price": sub_close.loc[p2],
            "indicator_value": sub_ind.loc[p2]
        }
    
    return None
