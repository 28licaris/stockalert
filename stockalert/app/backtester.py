import argparse, asyncio
from app.services.backtest_service import run_backtest

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--tickers", nargs="+", required=True)
    p.add_argument("--indicator", choices=["rsi","macd","tsi"], default="rsi")
    p.add_argument("--signal-type", choices=["hidden_bullish_divergence","hidden_bearish_divergence","regular_bullish_divergence","regular_bearish_divergence"], default="hidden_bullish_divergence")
    p.add_argument("--horizons", nargs="+", type=int, default=[5,15,60])
    args = p.parse_args()
    res = asyncio.run(run_backtest(args.tickers, args.indicator, args["signal_type"] if isinstance(args, dict) else args.signal_type, tuple(args.horizons)))
    print(res)
