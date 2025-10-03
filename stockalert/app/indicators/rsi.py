import pandas as pd
import numpy as np
from app.indicators.base import Indicator

class RSI(Indicator):
    """
    Relative Strength Index (RSI)
    
    The RSI is a momentum oscillator that measures the speed and magnitude of 
    recent price changes to evaluate overbought or oversold conditions.
    
    Traditional interpretation:
    - RSI > 70: Overbought (potential sell signal)
    - RSI < 30: Oversold (potential buy signal)
    - RSI = 50: Neutral momentum
    
    Calculation Method (Wilder's Smoothing / EMA):
    1. Calculate price changes (delta = close[i] - close[i-1])
    2. Separate into gains (positive deltas) and losses (absolute negative deltas)
    3. Calculate exponential moving average of gains and losses over period
    4. Compute Relative Strength: RS = Average Gain / Average Loss
    5. Convert to RSI: RSI = 100 - (100 / (1 + RS))
    
    This implementation uses Exponential Moving Average (EMA) which is equivalent
    to Wilder's smoothing method when span = period.
    
    Args:
        period (int): The lookback period for RSI calculation (default: 14)
                     Wilder originally used 14 periods
    
    References:
    - Wilder, J. Welles (1978). "New Concepts in Technical Trading Systems"
    - https://www.investopedia.com/terms/r/rsi.asp
    """
    
    def __init__(self, period: int = 14):
        super().__init__()
        self.period = period
        self.name = "rsi"
    
    def compute(self, close: pd.Series, high=None, low=None) -> pd.Series:
        """
        Calculate RSI using the Exponential Moving Average (EMA) method.
        
        This is Wilder's original RSI calculation approach, which uses an 
        exponential smoothing technique equivalent to EMA with span=period.
        
        Mathematical Formula:
        ---------------------
        For each period:
        1. Δ(i) = Close(i) - Close(i-1)
        2. Gain(i) = Δ(i) if Δ(i) > 0, else 0
        3. Loss(i) = |Δ(i)| if Δ(i) < 0, else 0
        4. AvgGain(i) = EMA(Gain, period)
        5. AvgLoss(i) = EMA(Loss, period)
        6. RS(i) = AvgGain(i) / AvgLoss(i)
        7. RSI(i) = 100 - (100 / (1 + RS(i)))
        
        Special Cases:
        --------------
        - When AvgLoss = 0: RS → ∞, RSI → 100
        - When AvgGain = 0: RS = 0, RSI → 0
        - Initial values (before period bars): backfilled then set to 50 (neutral)
        
        Args:
            close (pd.Series): Series of closing prices
            high (pd.Series, optional): Not used for RSI, included for interface consistency
            low (pd.Series, optional): Not used for RSI, included for interface consistency
        
        Returns:
            pd.Series: RSI values ranging from 0 to 100
        
        Example:
            >>> rsi = RSI(period=14)
            >>> rsi_values = rsi.compute(df['close'])
            >>> print(rsi_values.tail())
            2025-10-03 14:00:00    45.2
            2025-10-03 14:01:00    46.8
            2025-10-03 14:02:00    48.1
            2025-10-03 14:03:00    47.5
            2025-10-03 14:04:00    49.3
        """
        # Step 1: Calculate price changes (deltas)
        # delta[i] = close[i] - close[i-1]
        delta = close.diff()
        
        # Step 2: Separate gains and losses
        # If price went up: gain = delta, loss = 0
        # If price went down: gain = 0, loss = |delta|
        gain = np.where(delta > 0, delta, 0.0)
        loss = np.where(delta < 0, -delta, 0.0)
        
        # Step 3: Calculate exponential moving average of gains and losses
        # Using span=period gives Wilder's smoothing:
        # EMA_today = (Value_today * (2 / (period + 1))) + (EMA_yesterday * (1 - (2 / (period + 1))))
        gain_ema = pd.Series(gain, index=close.index).ewm(
            span=self.period, 
            adjust=False  # Use recursive calculation (Wilder's method)
        ).mean()
        
        loss_ema = pd.Series(loss, index=close.index).ewm(
            span=self.period, 
            adjust=False
        ).mean()
        
        # Step 4: Calculate Relative Strength (RS)
        # RS = Average Gain / Average Loss
        # Handle division by zero: replace 0 with NaN to avoid infinity
        rs = gain_ema / (loss_ema.replace(0, np.nan))
        
        # Step 5: Convert RS to RSI
        # RSI = 100 - (100 / (1 + RS))
        # This formula ensures:
        #   - When RS = 0 (no gains): RSI = 0
        #   - When RS → ∞ (no losses): RSI → 100
        #   - When RS = 1 (equal gains/losses): RSI = 50
        rsi = 100 - (100 / (1 + rs))
        
        # Step 6: Handle NaN values
        # - First 'period' values are NaN due to insufficient data
        # - Use backfill to propagate first valid value backwards
        # - Any remaining NaN (at start) set to 50 (neutral RSI)
        return rsi.bfill().fillna(50)
