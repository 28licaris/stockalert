"""
Monitor Manager - Manages multiple concurrent monitoring tasks.

Handles starting, stopping, and tracking multiple divergence monitors
(indicator-based) and Elliott Wave live scanners across symbols.
"""
import asyncio
import logging
from typing import Callable, Dict, Optional

from app.config import get_provider
from app.services.live.monitor_service import MonitorService

logger = logging.getLogger(__name__)


async def _run_wave_scanner(scanner, symbols: list, provider) -> None:
    """Long-running task: subscribe the scanner to live bars then heartbeat.

    Mirrors MonitorService.monitor() — subscribe first, then keep the
    coroutine alive so the asyncio.Task stays attached and can be cancelled.
    """
    provider.subscribe_bars(scanner.on_bar, symbols)
    logger.info("IntradayWaveScanner subscribed (%d symbols, interval=%s)",
                len(symbols), scanner.interval)
    try:
        while True:
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        logger.info("IntradayWaveScanner task cancelled")
        raise


class MonitorManager:
    """
    Manages multiple concurrent monitoring tasks.
    
    Tracks active monitors by a unique key (tickers + indicator + signal_type)
    and provides start/stop functionality.
    """
    
    def __init__(self):
        self.monitors: Dict[str, Dict] = {}  # Changed to store more info
        self.provider = None
    
    def _get_key(self, tickers: list[str], indicator: str, signal_type: str) -> str:
        """Generate unique key for a monitor configuration."""
        return f"{','.join(sorted(tickers))}:{indicator}:{signal_type}"
    
    def list_monitors(self) -> dict:
        """
        List all active monitors and their status.
        
        FIXED: Removed task.done() check which was causing hangs.
        Now uses exception handling to detect cancelled tasks.
        
        Returns:
            Dict mapping monitor keys to status info
        """
        result = {}
        
        for key, monitor_info in list(self.monitors.items()):
            task = monitor_info['task']
            
            # Check if task is still running without blocking
            try:
                # If task is done, this won't raise
                if task.done():
                    # Check if it failed
                    try:
                        exception = task.exception()
                        status = f"failed: {exception}" if exception else "completed"
                    except asyncio.CancelledError:
                        status = "cancelled"
                else:
                    status = "running"
            except Exception as e:
                logger.error(f"Error checking task status: {e}")
                status = "unknown"
            
            result[key] = {
                "status": status,
                "tickers": monitor_info['tickers'],
                "indicator": monitor_info['indicator'],
                "signal_type": monitor_info['signal_type'],
            }
        
        return result
    
    def start_monitor(
        self,
        tickers: list[str],
        indicator: str,
        signal_type: str,
        broadcast_cb=None
    ) -> dict:
        """
        Start a new monitoring task.
        
        Args:
            tickers: List of symbols to monitor
            indicator: Indicator name (rsi, macd, tsi)
            signal_type: Type of divergence to detect
            broadcast_cb: Optional callback for signal broadcasting
        
        Returns:
            Dict with status and details
        """
        key = self._get_key(tickers, indicator, signal_type)
        
        # Check if already running
        if key in self.monitors:
            task = self.monitors[key]['task']
            if not task.done():
                logger.warning(f"Monitor already running: {key}")
                return {
                    "status": "already_running",
                    "key": key,
                    "tickers": tickers
                }
            else:
                # Clean up old completed task
                logger.info(f"Replacing completed monitor: {key}")
                del self.monitors[key]
        
        # Initialize provider if needed
        if self.provider is None:
            self.provider = get_provider()
            logger.info("Data provider initialized")
        
        # Create monitor service
        try:
            monitor_service = MonitorService(
                self.provider,
                indicator,
                signal_type,
                broadcast_cb
            )
        except ValueError as e:
            logger.error(f"Failed to create monitor: {e}")
            return {
                "status": "error",
                "message": str(e)
            }
        
        # Create async task
        task = asyncio.create_task(monitor_service.monitor(tickers))
        
        # Store monitor info
        self.monitors[key] = {
            'task': task,
            'tickers': tickers,
            'indicator': indicator,
            'signal_type': signal_type,
        }
        
        logger.info(f"✅ Started monitor: {key}")
        return {
            "status": "started",
            "key": key,
            "tickers": tickers,
            "indicator": indicator,
            "signal_type": signal_type
        }
    
    def stop_monitor(
        self,
        tickers: list[str],
        indicator: str,
        signal_type: str
    ) -> dict:
        """
        Stop a running monitoring task.
        
        Args:
            tickers: List of symbols being monitored
            indicator: Indicator name
            signal_type: Divergence type
        
        Returns:
            Dict with status and details
        """
        key = self._get_key(tickers, indicator, signal_type)
        
        if key not in self.monitors:
            logger.warning(f"Monitor not found: {key}")
            return {
                "status": "not_found",
                "key": key
            }
        
        monitor_info = self.monitors[key]
        task = monitor_info['task']
        
        # Cancel if still running
        if not task.done():
            task.cancel()
            logger.info(f"Cancelled monitor task: {key}")
        
        # Unsubscribe from provider
        if self.provider:
            try:
                self.provider.unsubscribe_bars(monitor_info['tickers'])
            except Exception as e:
                logger.error(f"Error unsubscribing: {e}")
        
        # Remove from tracking
        del self.monitors[key]
        
        logger.info(f"🛑 Stopped monitor: {key}")
        return {
            "status": "stopped",
            "key": key,
            "tickers": tickers
        }
    
    # ── Elliott Wave live scanner (EW-7 live path) ──────────────────────────

    def _wave_key(self, symbols: list[str], interval: str) -> str:
        return f"wave:{interval}:{','.join(sorted(symbols))}"

    def start_wave_scanner(
        self,
        symbols: list[str],
        interval: str = "5m",
        broadcast_cb: Optional[Callable] = None,
        min_probability: float = 0.6,
        min_risk_reward: float = 2.0,
    ) -> dict:
        """Start a live intraday Elliott Wave scanner for `symbols`.

        Returns immediately — the scanner task runs in the background and
        calls `broadcast_cb(WaveAlert)` whenever a new setup fires.
        """
        from app.services.alerts.intraday import IntradayWaveScanner, INTRADAY_INTERVALS
        key = self._wave_key(symbols, interval)

        if interval not in INTRADAY_INTERVALS:
            return {"status": "error",
                    "message": f"interval must be one of {INTRADAY_INTERVALS}"}

        if key in self.monitors:
            task = self.monitors[key]["task"]
            if not task.done():
                return {"status": "already_running", "key": key, "symbols": symbols}
            del self.monitors[key]

        if self.provider is None:
            self.provider = get_provider()

        scanner = IntradayWaveScanner(
            symbols, interval,
            broadcast_cb=broadcast_cb,
            min_probability=min_probability,
            min_risk_reward=min_risk_reward,
        )
        task = asyncio.create_task(_run_wave_scanner(scanner, symbols, self.provider))
        self.monitors[key] = {
            "task": task,
            "tickers": symbols,
            "indicator": "elliott_wave",
            "signal_type": f"wave_{interval}",
        }
        logger.info("✅ Started wave scanner: %s (%d symbols)", key, len(symbols))
        return {"status": "started", "key": key, "symbols": symbols, "interval": interval}

    def stop_wave_scanner(self, symbols: list[str], interval: str = "5m") -> dict:
        """Stop a running wave scanner."""
        key = self._wave_key(symbols, interval)
        if key not in self.monitors:
            return {"status": "not_found", "key": key}

        info = self.monitors.pop(key)
        task = info["task"]
        if not task.done():
            task.cancel()

        if self.provider:
            try:
                self.provider.unsubscribe_bars(symbols)
            except Exception as e:
                logger.warning("wave scanner unsubscribe error: %s", e)

        logger.info("🛑 Stopped wave scanner: %s", key)
        return {"status": "stopped", "key": key, "symbols": symbols}

    def list_wave_scanners(self) -> dict:
        """Return active wave scanner entries (keys prefixed 'wave:')."""
        return {
            k: {
                "status": "running" if not v["task"].done() else "stopped",
                "symbols": v["tickers"],
                "signal_type": v["signal_type"],
            }
            for k, v in self.monitors.items()
            if k.startswith("wave:")
        }

    async def stop_all(self):
        """
        Stop all active monitors and cleanup.

        Called during application shutdown.
        """
        logger.info("Stopping all monitors...")
        
        # Cancel all tasks
        for key, monitor_info in list(self.monitors.items()):
            task = monitor_info['task']
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    logger.debug(f"Task cancelled: {key}")
                except Exception as e:
                    logger.error(f"Error stopping task {key}: {e}")
        
        # Stop data provider
        if self.provider:
            try:
                self.provider.stop_stream()
                logger.info("Data provider stopped")
            except Exception as e:
                logger.error(f"Error stopping provider: {e}")
        
        self.monitors.clear()
        logger.info("✅ All monitors stopped")


# Global singleton instance
monitor_manager = MonitorManager()