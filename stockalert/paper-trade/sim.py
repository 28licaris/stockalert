class Strategy(ABC):
    """
    Base strategy that can be:
    - Rule-based (RSI divergence)
    - ML-based (trained model)
    - LLM-based (GPT-4 analyzing patterns)
    """
    
    @abstractmethod
    async def generate_signals(self, bar: Bar, context: dict) -> Optional[Signal]:
        """
        Given a new bar, should we trade?
        
        Args:
            bar: Latest price bar
            context: Historical data, indicators, positions
        
        Returns:
            Signal or None
        """
        pass
    
    @abstractmethod
    def get_position_size(self, signal: Signal, portfolio: Portfolio) -> float:
        """
        How much to trade?
        
        Args:
            signal: Trading signal
            portfolio: Current portfolio state
        
        Returns:
            Number of shares to trade
        """
        pass



class SignalRouter:
    """
    Decides whether to act on signals.
    
    Applies filters:
    - Max positions limit
    - Sector exposure
    - Correlation checks
    - Risk limits
    """
    
    async def route_signal(self, signal: Signal, portfolio: Portfolio) -> Optional[Order]:
        """
        Convert signal to order (or reject it).
        
        Returns:
            Order if signal passes filters, else None
        """
        pass


class OrderManager:
    """
    Simulates realistic order execution.
    
    Features:
    - Market orders (instant fill with slippage)
    - Limit orders (fill if price reached)
    - Stop-loss orders
    - Partial fills
    """
    
    async def execute_order(self, order: Order, current_bar: Bar) -> Fill:
        """
        Simulate order execution.
        
        Returns:
            Fill with realistic price and quantity
        """
        pass


class PortfolioManager:
    """
    Tracks all positions, cash, and performance.
    
    Maintains:
    - Open positions
    - Cash balance
    - Equity curve
    - Drawdown
    - Sharpe ratio
    """
    
    async def update_position(self, fill: Fill):
        """Update portfolio state after fill"""
        pass
    
    async def mark_to_market(self, bar: Bar):
        """Update position values"""
        pass
    
    def get_metrics(self) -> dict:
        """Calculate performance metrics"""
        pass



class PaperTrade(Base):
    """
    Record of each simulated trade.
    """
    __tablename__ = "paper_trades"
    
    id = Column(Integer, primary_key=True)
    strategy_name = Column(String, nullable=False)  # "rsi_divergence"
    symbol = Column(String, nullable=False)
    
    # Signal that triggered trade
    signal_id = Column(Integer, ForeignKey("signals.id"))
    
    # Order details
    side = Column(String)  # "buy" or "sell"
    quantity = Column(Integer)
    entry_price = Column(Float)
    entry_time = Column(DateTime(timezone=True))
    
    # Exit details (if closed)
    exit_price = Column(Float, nullable=True)
    exit_time = Column(DateTime(timezone=True), nullable=True)
    
    # P&L
    pnl = Column(Float, nullable=True)
    pnl_percent = Column(Float, nullable=True)
    
    # Costs
    commission = Column(Float, default=0.0)
    slippage = Column(Float, default=0.0)


class PaperPosition(Base):
    """
    Current open positions in paper trading.
    """
    __tablename__ = "paper_positions"
    
    id = Column(Integer, primary_key=True)
    strategy_name = Column(String, nullable=False)
    symbol = Column(String, nullable=False)
    
    quantity = Column(Integer)
    avg_entry_price = Column(Float)
    current_price = Column(Float)
    
    unrealized_pnl = Column(Float)
    unrealized_pnl_percent = Column(Float)
    
    updated_at = Column(DateTime(timezone=True))


class PaperPortfolio(Base):
    """
    Snapshot of portfolio state over time.
    """
    __tablename__ = "paper_portfolio"
    
    id = Column(Integer, primary_key=True)
    strategy_name = Column(String, nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False)
    
    # Capital
    cash = Column(Float)
    positions_value = Column(Float)
    total_equity = Column(Float)
    
    # Performance
    total_pnl = Column(Float)
    total_pnl_percent = Column(Float)
    
    # Risk metrics
    max_drawdown = Column(Float)
    sharpe_ratio = Column(Float, nullable=True)
    win_rate = Column(Float, nullable=True)


class PaperPosition(Base):
    """
    Current open positions in paper trading.
    """
    __tablename__ = "paper_positions"
    
    id = Column(Integer, primary_key=True)
    strategy_name = Column(String, nullable=False)
    symbol = Column(String, nullable=False)
    
    quantity = Column(Integer)
    avg_entry_price = Column(Float)
    current_price = Column(Float)
    
    unrealized_pnl = Column(Float)
    unrealized_pnl_percent = Column(Float)
    
    updated_at = Column(DateTime(timezone=True))


class PaperPortfolio(Base):
    """
    Snapshot of portfolio state over time.
    """
    __tablename__ = "paper_portfolio"
    
    id = Column(Integer, primary_key=True)
    strategy_name = Column(String, nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False)
    
    # Capital
    cash = Column(Float)
    positions_value = Column(Float)
    total_equity = Column(Float)
    
    # Performance
    total_pnl = Column(Float)
    total_pnl_percent = Column(Float)
    
    # Risk metrics
    max_drawdown = Column(Float)
    sharpe_ratio = Column(Float, nullable=True)
    win_rate = Column(Float, nullable=True)



class LLMStrategy(Strategy):
    """
    Future: LLM analyzes technical patterns and decides trades.
    """
    
    async def generate_signals(self, bar: Bar, context: dict) -> Optional[Signal]:
        # Build prompt
        prompt = f"""
        Analyze this trading setup:
        
        Symbol: {bar.symbol}
        Current Price: ${bar.close}
        RSI: {context['rsi'][-1]}
        MACD: {context['macd'][-1]}
        Recent divergence: {context.get('last_divergence')}
        
        Current positions: {context['positions']}
        Available capital: ${context['cash']}
        
        Should I trade? If yes, provide:
        - Action (buy/sell)
        - Size (% of capital)
        - Reasoning
        """
        
        response = await llm.generate(prompt)
        
        # Parse LLM response into Signal
        return self._parse_llm_response(response)
    



# Load historical data
bars = await historical_loader.load_bars("SPY", limit=10000)

# Initialize paper trading
strategy = RSIDivergenceStrategy()
portfolio = Portfolio(initial_cash=100000)
simulator = PaperTradingSimulator(strategy, portfolio)

# Run through historical bars
for i, (ts, bar) in enumerate(bars.iterrows()):
    await simulator.process_bar(bar)

# Get results
metrics = portfolio.get_metrics()
print(f"Final equity: ${metrics['total_equity']}")
print(f"Total return: {metrics['total_pnl_percent']:.2f}%")
print(f"Sharpe ratio: {metrics['sharpe_ratio']:.2f}")




# Attach to live monitor
strategy = RSIDivergenceStrategy()
portfolio = Portfolio(initial_cash=100000)
simulator = PaperTradingSimulator(strategy, portfolio)

# Monitor feeds bars to simulator
monitor = MonitorService(provider, "rsi", "hidden_bullish_divergence")
monitor.add_callback(simulator.process_bar)

# Simulator trades in real-time based on live data
await monitor.start(["SPY", "QQQ", "AAPL"])