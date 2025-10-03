import pandas as pd
import numpy as np
from app.indicators.base import Indicator

class TSI(Indicator):
    """
    True Strength Index (TSI)
    
    The TSI is a momentum oscillator that uses double-smoothed price momentum
    to identify trend direction, overbought/oversold conditions, and divergences.
    It was developed by William Blau in the early 1990s.
    
    Traditional interpretation:
    - TSI > 25: Strong bullish momentum (overbought)
    - TSI > 0: Bullish momentum (uptrend)
    - TSI < 0: Bearish momentum (downtrend)
    - TSI < -25: Strong bearish momentum (oversold)
    - Zero line crossovers: Potential trend changes
    - Divergences: Price vs TSI disagreement signals reversals
    
    Advantages over RSI:
    - More responsive to price changes due to double smoothing
    - Better at identifying trend direction
    - Clearer divergence signals
    - Less prone to false signals in ranging markets
    
    Calculation Method (Double Exponential Smoothing):
    1. Calculate price momentum (price change)
    2. Apply first EMA to momentum (long period)
    3. Apply second EMA to smoothed momentum (short period)
    4. Repeat for absolute momentum
    5. TSI = 100 × (Double Smoothed Momentum / Double Smoothed Absolute Momentum)
    
    Args:
        long (int): The long EMA period (default: 25)
                   Controls sensitivity to long-term momentum
        short (int): The short EMA period (default: 13)
                    Controls responsiveness to recent changes
    
    References:
    - Blau, William (1995). "Momentum, Direction, and Divergence"
    - https://www.investopedia.com/terms/t/tsi.asp
    - https://school.stockcharts.com/doku.php?id=technical_indicators:true_strength_index
    """
    
    def __init__(self, long: int = 25, short: int = 13):
        super().__init__()
        self.long = long
        self.short = short
        self.name = "tsi"
        self.period = long  # Use long period as the main period
    
    def compute(self, close: pd.Series, high=None, low=None) -> pd.Series:
        """
        Calculate TSI using double exponential smoothing.
        
        The TSI applies two successive exponential moving averages to both the
        price momentum and its absolute value, creating a normalized oscillator
        that ranges approximately between -100 and +100.
        
        Mathematical Formula:
        ---------------------
        For each period:
        1. Momentum(i) = Close(i) - Close(i-1)
        2. AbsMomentum(i) = |Momentum(i)|
        3. EMA1_Momentum = EMA(Momentum, long_period)
        4. EMA2_Momentum = EMA(EMA1_Momentum, short_period)  [Double smoothed]
        5. EMA1_AbsMomentum = EMA(AbsMomentum, long_period)
        6. EMA2_AbsMomentum = EMA(EMA1_AbsMomentum, short_period)  [Double smoothed]
        7. TSI(i) = 100 × (EMA2_Momentum / EMA2_AbsMomentum)
        
        Why Double Smoothing?
        --------------------
        - First EMA (long): Reduces noise from short-term volatility
        - Second EMA (short): Further smooths while maintaining responsiveness
        - Result: Cleaner signal with less whipsaws than single smoothing
        
        Normalization:
        --------------
        Dividing by absolute momentum normalizes the indicator, making it:
        - Comparable across different price levels
        - Bounded approximately between -100 and +100
        - Independent of the asset's price magnitude
        
        Special Cases:
        --------------
        - When EMA2_AbsMomentum = 0: Set TSI = 0 (no momentum)
        - Initial values: Set to 0 until sufficient data for smoothing
        - Extreme values (±100): Indicate very strong directional momentum
        
        Args:
            close (pd.Series): Series of closing prices
            high (pd.Series, optional): Not used for TSI, included for interface consistency
            low (pd.Series, optional): Not used for TSI, included for interface consistency
        
        Returns:
            pd.Series: TSI values typically ranging from -100 to +100
                      Positive values indicate bullish momentum
                      Negative values indicate bearish momentum
        
        Example:
            >>> tsi = TSI(long=25, short=13)
            >>> tsi_values = tsi.compute(df['close'])
            >>> print(tsi_values.tail())
            2025-10-03 14:00:00    -12.5
            2025-10-03 14:01:00    -10.2
            2025-10-03 14:02:00     -8.7
            2025-10-03 14:03:00     -5.3
            2025-10-03 14:04:00     -2.1
            
            >>> # Find zero-line crossovers (potential trend changes)
            >>> crossovers = (tsi_values > 0) & (tsi_values.shift(1) <= 0)
            >>> print(crossovers[crossovers].index)
        
        Signal Interpretation:
        ---------------------
        - Bullish signal: TSI crosses above 0 or above -25
        - Bearish signal: TSI crosses below 0 or below +25
        - Divergence: Price makes new high/low but TSI doesn't confirm
        - Overbought: TSI > +25 (consider taking profits)
        - Oversold: TSI < -25 (consider buying opportunity)
        """
        # Step 1: Calculate price momentum (change in price)
        # momentum[i] = close[i] - close[i-1]
        momentum = close.diff()
        
        # Step 2: Calculate absolute momentum for normalization
        # abs_momentum[i] = |momentum[i]|
        abs_momentum = momentum.abs()
        
        # Step 3: Apply first EMA to momentum (long period smoothing)
        # This reduces short-term noise and volatility
        ema1_momentum = momentum.ewm(
            span=self.long,
            adjust=False  # Use recursive calculation for consistency
        ).mean()
        
        # Step 4: Apply second EMA to first smoothed momentum (short period)
        # This is the "double smoothing" that makes TSI unique
        # Results in a very smooth momentum measure
        ema2_momentum = ema1_momentum.ewm(
            span=self.short,
            adjust=False
        ).mean()
        
        # Step 5: Apply first EMA to absolute momentum (long period)
        # Same smoothing process but on absolute values for normalization
        ema1_abs_momentum = abs_momentum.ewm(
            span=self.long,
            adjust=False
        ).mean()
        
        # Step 6: Apply second EMA to first smoothed absolute momentum (short period)
        # Double smoothing of absolute momentum for normalization denominator
        ema2_abs_momentum = ema1_abs_momentum.ewm(
            span=self.short,
            adjust=False
        ).mean()
        
        # Step 7: Calculate TSI by normalizing double-smoothed momentum
        # TSI = 100 × (Double Smoothed Momentum / Double Smoothed Absolute Momentum)
        # Multiplied by 100 to scale to percentage-like values
        # Replace 0 with NaN to avoid division by zero
        tsi = 100 * (ema2_momentum / ema2_abs_momentum.replace(0, np.nan))
        
        # Step 8: Handle NaN values
        # - Initial values are NaN due to insufficient data for double smoothing
        # - Set to 0 (neutral momentum) rather than backfilling
        # - This is more appropriate for TSI as it represents "no momentum" state
        return tsi.bfill().fillna(0)