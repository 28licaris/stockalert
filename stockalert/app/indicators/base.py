from abc import ABC, abstractmethod
import pandas as pd

class Indicator(ABC):
    """Base class for all technical indicators"""
    
    def __init__(self):
        self.name = "indicator"
        self.period = None
    
    @abstractmethod
    def compute(self, close: pd.Series, high: pd.Series = None, low: pd.Series = None) -> pd.Series:
        """
        Compute the indicator values
        
        Args:
            close: Close prices
            high: High prices (optional)
            low: Low prices (optional)
            
        Returns:
            pd.Series: Indicator values
        """
        pass
    
    def __str__(self):
        return f"{self.name.upper()}(period={self.period})"
