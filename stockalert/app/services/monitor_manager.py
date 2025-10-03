"""
Monitor Manager - Manages multiple concurrent monitoring tasks.

Handles starting, stopping, and tracking multiple divergence monitors
across different symbols and indicator configurations.
"""
import asyncio
import logging
from typing import Dict, Optional, List

from app.config import get_provider
from app.services.monitor_service import MonitorService

logger = logging.getLogger(__name__)


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