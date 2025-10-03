#!/usr/bin/env python3
"""
Command-line tool for monitor control
Usage:
    python monitor_ctl.py start SPY QQQ
    python monitor_ctl.py stop SPY QQQ
    python monitor_ctl.py list
    python monitor_ctl.py stats
"""
import sys
import requests

BASE_URL = "http://localhost:8000"

def start(symbols):
    print(f"Starting monitor for: {', '.join(symbols)}")
    response = requests.post(f"{BASE_URL}/monitors/start", json={
        "tickers": symbols,
        "indicator": "rsi",
        "signal_type": "hidden_bullish_divergence"
    })
    print(f"✅ {response.json()}")

def stop(symbols):
    print(f"Stopping monitor for: {', '.join(symbols)}")
    response = requests.post(f"{BASE_URL}/monitors/stop", json={
        "tickers": symbols,
        "indicator": "rsi",
        "signal_type": "hidden_bullish_divergence"
    })
    print(f"✅ {response.json()}")

def list_monitors():
    response = requests.get(f"{BASE_URL}/monitors")
    monitors = response.json()
    if not monitors:
        print("No active monitors")
    else:
        print(f"Active monitors ({len(monitors)}):")
        for key in monitors:
            print(f"  - {key}")

def stats():
    response = requests.get(f"{BASE_URL}/stats")
    data = response.json()
    print(f"Bars: {data['total_bars']}")
    print(f"Signals: {data['total_signals']}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python monitor_ctl.py [start|stop|list|stats] [SYMBOLS...]")
        sys.exit(1)
    
    cmd = sys.argv[1]
    
    try:
        if cmd == "start":
            start(sys.argv[2:] or ["SPY"])
        elif cmd == "stop":
            stop(sys.argv[2:] or ["SPY"])
        elif cmd == "list":
            list_monitors()
        elif cmd == "stats":
            stats()
        else:
            print(f"Unknown command: {cmd}")
    except requests.exceptions.ConnectionError:
        print("❌ Can't connect to server. Is it running?")
        print("Start with: cd stockalert && uvicorn app.main_api:app --reload")