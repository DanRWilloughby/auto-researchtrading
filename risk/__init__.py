"""
Risk management layer.

The RiskManager wraps the entire trading loop. Every proposed order passes
through it before execution. Separate guards watch for different failure
modes and can independently halt trading.

Components:
  - RiskConfig: loads YAML config with all thresholds
  - RiskManager: orchestrator that runs guards against every signal
  - CircuitBreaker: drawdown + daily loss + manual kill
  - PositionLimits: leverage, per-coin cap, concentration
  - DataGuard: stale candles, price sanity, funding anomalies
  - OrderSafety: slippage, timeout, retries, fill verification
  - CorrelationGuard: detects correlated drawdown across positions
  - FlashCrashGuard: WebSocket inter-bar price monitoring (separate thread)
  - AlertDispatch: Telegram + log file + kill flag
  - Watchdog: external process health monitor
"""

from .config import RiskConfig
from .alerts import AlertDispatch, AlertType
from .circuit_breaker import CircuitBreaker
from .position_limits import PositionLimits
from .data_guard import DataGuard
from .order_safety import OrderSafety
from .correlation_guard import CorrelationGuard
from .manager import RiskManager, RiskVerdict

__all__ = [
    "RiskConfig",
    "RiskManager",
    "RiskVerdict",
    "AlertDispatch",
    "AlertType",
    "CircuitBreaker",
    "PositionLimits",
    "DataGuard",
    "OrderSafety",
    "CorrelationGuard",
]
