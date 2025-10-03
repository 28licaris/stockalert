#!/usr/bin/env python3
"""
Easy-to-use script for testing monitor start/stop
Run this AFTER starting the FastAPI server
"""
import requests
import time
import sys
from typing import List

BASE_URL = "http://localhost:8000"

class MonitorTester:
    def __init__(self, base_url: str = BASE_URL):
        self.base_url = base_url
        
    def check_server(self) -> bool:
        """Check if server is running"""
        try:
            response = requests.get(f"{self.base_url}/", timeout=2)
            return response.status_code == 200
        except requests.exceptions.ConnectionError:
            return False
    
    def list_monitors(self):
        """List all active monitors"""
        print("\n📊 Listing active monitors...")
        try:
            response = requests.get(f"{self.base_url}/monitors")
            response.raise_for_status()
            monitors = response.json()
            
            if not monitors:
                print("   No active monitors")
            else:
                print(f"   Active monitors: {len(monitors)}")
                for key, monitor in monitors.items():
                    print(f"   - {key}")
            return monitors
        except Exception as e:
            print(f"   ❌ Error: {e}")
            return {}
    
    def start_monitor(self, tickers: List[str], indicator: str = "rsi", 
                     signal_type: str = "hidden_bullish_divergence"):
        """Start monitoring symbols"""
        print(f"\n🟢 Starting monitor...")
        print(f"   Symbols: {', '.join(tickers)}")
        print(f"   Indicator: {indicator}")
        print(f"   Signal Type: {signal_type}")
        
        try:
            response = requests.post(
                f"{self.base_url}/monitors/start",
                json={
                    "tickers": tickers,
                    "indicator": indicator,
                    "signal_type": signal_type
                }
            )
            response.raise_for_status()
            result = response.json()
            print(f"   ✅ {result.get('message', 'Monitor started')}")
            return result
        except Exception as e:
            print(f"   ❌ Error: {e}")
            return None
    
    def stop_monitor(self, tickers: List[str], indicator: str = "rsi",
                    signal_type: str = "hidden_bullish_divergence"):
        """Stop monitoring symbols"""
        print(f"\n🔴 Stopping monitor...")
        print(f"   Symbols: {', '.join(tickers)}")
        print(f"   Indicator: {indicator}")
        
        try:
            response = requests.post(
                f"{self.base_url}/monitors/stop",
                json={
                    "tickers": tickers,
                    "indicator": indicator,
                    "signal_type": signal_type
                }
            )
            response.raise_for_status()
            result = response.json()
            print(f"   ✅ {result.get('message', 'Monitor stopped')}")
            return result
        except Exception as e:
            print(f"   ❌ Error: {e}")
            return None
    
    def get_stats(self):
        """Get database statistics"""
        print("\n📈 Database Statistics...")
        try:
            response = requests.get(f"{self.base_url}/stats")
            response.raise_for_status()
            stats = response.json()
            print(f"   Total bars: {stats.get('total_bars', 0)}")
            print(f"   Total signals: {stats.get('total_signals', 0)}")
            
            recent = stats.get('recent_signals', [])
            if recent:
                print(f"\n   Recent signals:")
                for sig in recent[:3]:
                    print(f"   - {sig['symbol']}: {sig['type']} @ ${sig['price']:.2f}")
            return stats
        except Exception as e:
            print(f"   ❌ Error: {e}")
            return None

def interactive_menu():
    """Interactive menu for testing"""
    tester = MonitorTester()
    
    print("=" * 60)
    print("📡 STOCKALERT MONITOR TESTER")
    print("=" * 60)
    
    # Check server
    if not tester.check_server():
        print("\n❌ Server not running!")
        print("Start the server first:")
        print("   cd stockalert")
        print("   uvicorn app.main_api:app --reload")
        sys.exit(1)
    
    print("✅ Server is running\n")
    
    while True:
        print("\n" + "=" * 60)
        print("OPTIONS:")
        print("  1. List active monitors")
        print("  2. Start monitor (SPY)")
        print("  3. Start monitor (SPY, QQQ, AAPL)")
        print("  4. Stop monitor (SPY)")
        print("  5. Stop monitor (SPY, QQQ, AAPL)")
        print("  6. Get database stats")
        print("  7. Quick test (start → wait 30s → stop)")
        print("  0. Exit")
        print("=" * 60)
        
        choice = input("\nEnter choice: ").strip()
        
        if choice == "0":
            print("\n👋 Goodbye!")
            break
            
        elif choice == "1":
            tester.list_monitors()
            
        elif choice == "2":
            tester.start_monitor(["SPY"])
            
        elif choice == "3":
            tester.start_monitor(["SPY", "QQQ", "AAPL"])
            
        elif choice == "4":
            tester.stop_monitor(["SPY"])
            
        elif choice == "5":
            tester.stop_monitor(["SPY", "QQQ", "AAPL"])
            
        elif choice == "6":
            tester.get_stats()
            
        elif choice == "7":
            print("\n🧪 Running quick test...")
            tester.start_monitor(["SPY"])
            time.sleep(2)
            tester.list_monitors()
            
            print("\n⏳ Waiting 30 seconds for data collection...")
            for i in range(30, 0, -5):
                print(f"   {i} seconds remaining...")
                time.sleep(5)
            
            tester.get_stats()
            tester.stop_monitor(["SPY"])
            print("\n✅ Quick test complete!")
            
        else:
            print("❌ Invalid choice")

def quick_test():
    """Quick automated test"""
    tester = MonitorTester()
    
    if not tester.check_server():
        print("❌ Server not running on http://localhost:8000")
        print("Start with: cd stockalert && uvicorn app.main_api:app --reload")
        sys.exit(1)
    
    print("🧪 QUICK MONITOR TEST")
    print("=" * 60)
    
    # Test sequence
    tester.list_monitors()
    tester.start_monitor(["SPY", "QQQ"])
    time.sleep(2)
    tester.list_monitors()
    
    print("\n⏳ Collecting data for 15 seconds...")
    time.sleep(15)
    
    tester.get_stats()
    tester.stop_monitor(["SPY", "QQQ"])
    tester.list_monitors()
    
    print("\n✅ Test complete!")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--quick":
        quick_test()
    else:
        interactive_menu()