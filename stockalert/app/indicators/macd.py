import pandas as pd
import numpy as np
from app.indicators.base import Indicator

class MACD(Indicator):
    """
    Moving Average Convergence Divergence (MACD)
    
    The MACD is a trend-following momentum indicator that shows the relationship
    between two exponential moving averages (EMAs) of a security's price.
    Developed by Gerald Appel in the late 1970s.
    
    Components:
    - MACD Line: Difference between fast EMA and slow EMA
    - Signal Line: EMA of the MACD line
    - Histogram: Difference between MACD line and Signal line
    
    Traditional interpretation:
    - MACD > Signal: Bullish momentum (histogram positive)
    - MACD < Signal: Bearish momentum (histogram negative)
    - MACD crosses above Signal: Buy signal
    - MACD crosses below Signal: Sell signal
    - Zero line crossover: Trend change confirmation
    - Divergences: Price vs MACD disagreement signals reversals
    
    Standard Parameters:
    - Fast EMA: 12 periods (more responsive to recent prices)
    - Slow EMA: 26 periods (smoother, less reactive)
    - Signal EMA: 9 periods (smooths the MACD line)
    
    Advantages:
    - Combines trend and momentum in one indicator
    - Clear visual signals (crossovers, divergences)
    - Works well in trending markets
    - Widely used and well-tested
    
    Limitations:
    - Lagging indicator (based on past prices)
    - Less effective in ranging/sideways markets
    - Can produce false signals during choppy periods
    
    Args:
        fast (int): The fast EMA period (default: 12)
                   Shorter period = more sensitive to price changes
        slow (int): The slow EMA period (default: 26)
                   Longer period = smoother, less noise
        signal (int): The signal line EMA period (default: 9)
                     Smooths the MACD line for clearer signals
    
    References:
    - Appel, Gerald (2005). "Technical Analysis: Power Tools for Active Investors"
    - https://www.investopedia.com/terms/m/macd.asp
    - https://school.stockcharts.com/doku.php?id=technical_indicators:moving_average_convergence_divergence_macd
    """
    
    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        super().__init__()
        self.fast = fast
        self.slow = slow
        self.signal = signal
        self.name = "macd"
        self.period = slow  # Use slow period as the main period
    
    def compute(self, close: pd.Series, high=None, low=None) -> pd.Series:
        """
        Calculate MACD line using exponential moving averages.
        
        The MACD line represents the momentum and direction of a trend by
        measuring the difference between two exponential moving averages.
        When the fast EMA is above the slow EMA, momentum is bullish.
        
        Mathematical Formula:
        ---------------------
        For each period:
        1. EMA_fast(i) = EMA(Close, fast_period)
        2. EMA_slow(i) = EMA(Close, slow_period)
        3. MACD_line(i) = EMA_fast(i) - EMA_slow(i)
        4. Signal_line(i) = EMA(MACD_line, signal_period)
        5. Histogram(i) = MACD_line(i) - Signal_line(i)
        
        Note: This method returns only the MACD line. Use compute_full()
        to get MACD line, Signal line, and Histogram together.
        
        EMA Calculation:
        ----------------
        EMA uses exponential weighting where recent prices have more influence:
        - Multiplier = 2 / (period + 1)
        - EMA_today = (Close_today × Multiplier) + (EMA_yesterday × (1 - Multiplier))
        
        This gives more weight to recent data while still considering historical context.
        
        Interpretation:
        ---------------
        - MACD > 0: Fast EMA above slow EMA (bullish, uptrend)
        - MACD < 0: Fast EMA below slow EMA (bearish, downtrend)
        - MACD increasing: Momentum strengthening
        - MACD decreasing: Momentum weakening
        - Large absolute values: Strong momentum
        - Near zero: Weak or transitioning momentum
        
        For Divergence Detection:
        -------------------------
        Use the MACD line (not signal or histogram) for divergence analysis:
        - Bullish divergence: Price makes lower low, MACD makes higher low
        - Bearish divergence: Price makes higher high, MACD makes lower high
        - Hidden bullish: Price makes higher low, MACD makes lower low
        - Hidden bearish: Price makes lower high, MACD makes higher high
        
        Args:
            close (pd.Series): Series of closing prices
            high (pd.Series, optional): Not used for MACD, included for interface consistency
            low (pd.Series, optional): Not used for MACD, included for interface consistency
        
        Returns:
            pd.Series: MACD line values (difference between fast and slow EMAs)
                      Positive values indicate bullish momentum
                      Negative values indicate bearish momentum
        
        Example:
            >>> macd = MACD(fast=12, slow=26, signal=9)
            >>> macd_line = macd.compute(df['close'])
            >>> print(macd_line.tail())
            2025-10-03 14:00:00    -1.25
            2025-10-03 14:01:00    -0.87
            2025-10-03 14:02:00    -0.45
            2025-10-03 14:03:00     0.12
            2025-10-03 14:04:00     0.58
            
            >>> # Detect zero-line crossovers (trend changes)
            >>> bullish_cross = (macd_line > 0) & (macd_line.shift(1) <= 0)
            >>> bearish_cross = (macd_line < 0) & (macd_line.shift(1) >= 0)
            
            >>> # Get full MACD with signal and histogram
            >>> macd_line, signal_line, histogram = macd.compute_full(df['close'])
        
        See Also:
        ---------
        compute_full() : Returns MACD line, signal line, and histogram
        compute_signal() : Returns only the signal line
        compute_histogram() : Returns only the histogram
        """
        # Step 1: Calculate fast EMA (12-period by default)
        # This EMA responds quickly to recent price changes
        # adjust=False ensures recursive calculation (standard MACD method)
        ema_fast = close.ewm(span=self.fast, adjust=False).mean()
        
        # Step 2: Calculate slow EMA (26-period by default)
        # This EMA is smoother and less reactive to short-term fluctuations
        ema_slow = close.ewm(span=self.slow, adjust=False).mean()
        
        # Step 3: Calculate MACD line
        # MACD = Fast EMA - Slow EMA
        # Positive: Fast EMA > Slow EMA (bullish momentum)
        # Negative: Fast EMA < Slow EMA (bearish momentum)
        # Zero: Fast EMA = Slow EMA (momentum shift)
        macd_line = ema_fast - ema_slow
        
        # Step 4: Handle initial NaN values
        # First 'slow' periods will be NaN due to insufficient data
        # Backfill to get first valid value, then fill remaining with 0
        return macd_line.bfill().fillna(0)
    
    def compute_signal(self, close: pd.Series) -> pd.Series:
        """
        Calculate the MACD signal line (EMA of MACD line).
        
        The signal line is used to generate buy/sell signals when
        the MACD line crosses above or below it.
        
        Args:
            close (pd.Series): Series of closing prices
        
        Returns:
            pd.Series: Signal line values
        
        Example:
            >>> macd = MACD()
            >>> signal = macd.compute_signal(df['close'])
        """
        macd_line = self.compute(close)
        signal_line = macd_line.ewm(span=self.signal, adjust=False).mean()
        return signal_line.bfill().fillna(0)
    
    def compute_histogram(self, close: pd.Series) -> pd.Series:
        """
        Calculate the MACD histogram (MACD line - Signal line).
        
        The histogram shows the distance between MACD and signal lines:
        - Growing histogram: Momentum strengthening
        - Shrinking histogram: Momentum weakening
        - Histogram crosses zero: MACD crosses signal (trade signal)
        
        Args:
            close (pd.Series): Series of closing prices
        
        Returns:
            pd.Series: Histogram values
        
        Example:
            >>> macd = MACD()
            >>> histogram = macd.compute_histogram(df['close'])
            >>> # Find when histogram crosses above zero (buy signal)
            >>> buy_signals = (histogram > 0) & (histogram.shift(1) <= 0)
        """
        macd_line = self.compute(close)
        signal_line = self.compute_signal(close)
        histogram = macd_line - signal_line
        return histogram.bfill().fillna(0)
    
    def compute_full(self, close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
        """
        Calculate all MACD components: line, signal, and histogram.
        
        This is the most efficient way to get all three components
        as it only calculates EMAs once.
        
        Args:
            close (pd.Series): Series of closing prices
        
        Returns:
            tuple: (macd_line, signal_line, histogram)
        
        Example:
            >>> macd = MACD(fast=12, slow=26, signal=9)
            >>> macd_line, signal_line, histogram = macd.compute_full(df['close'])
            >>> 
            >>> # Plot all components
            >>> import matplotlib.pyplot as plt
            >>> plt.figure(figsize=(12, 8))
            >>> plt.subplot(2, 1, 1)
            >>> plt.plot(df.index, df['close'], label='Close Price')
            >>> plt.subplot(2, 1, 2)
            >>> plt.plot(df.index, macd_line, label='MACD Line', color='blue')
            >>> plt.plot(df.index, signal_line, label='Signal Line', color='red')
            >>> plt.bar(df.index, histogram, label='Histogram', alpha=0.3)
            >>> plt.axhline(0, color='black', linewidth=0.5)
            >>> plt.legend()
            >>> plt.show()
        """
        macd_line = self.compute(close)
        signal_line = macd_line.ewm(span=self.signal, adjust=False).mean()
        histogram = macd_line - signal_line
        
        return (
            macd_line.bfill().fillna(0),
            signal_line.bfill().fillna(0),
            histogram.bfill().fillna(0)
        )
