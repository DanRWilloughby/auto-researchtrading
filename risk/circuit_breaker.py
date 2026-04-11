"""
Circuit breaker — hard halts that flatten all positions and stop trading.

Triggers:
  1. Drawdown from high-water mark > threshold
  2. Rolling 24h drawdown > threshold
  3. Daily realized loss > threshold
  4. Manual kill flag (file exists)

When any trigger fires:
  - Emergency exit ALL positions (market orders)
  - Write kill flag to prevent restart without manual intervention
  - Dispatch critical alert
  - Set halted=True (trader polls this and stops)
"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .alerts import AlertDispatch
from .config import CircuitBreakerConfig, WatchdogConfig

logger = logging.getLogger(__name__)


@dataclass
class EquityPoint:
    timestamp: float
    equity: float


class CircuitBreaker:
    """Monitors equity and triggers hard halts on drawdown or manual kill."""

    def __init__(
        self,
        config: CircuitBreakerConfig,
        watchdog_config: WatchdogConfig,
        alerts: AlertDispatch,
        initial_equity: float,
    ):
        self.config = config
        self.watchdog_config = watchdog_config
        self.alerts = alerts
        self.high_water = initial_equity
        self.session_start_equity = initial_equity
        self.session_start_ts = time.time()
        self.equity_history: deque[EquityPoint] = deque(maxlen=2880)  # 24h of 30s samples
        self.equity_history.append(EquityPoint(time.time(), initial_equity))
        self._halted = False
        self._halt_reason: Optional[str] = None

        # Resolve kill flag path
        self.kill_flag = Path(watchdog_config.kill_flag_file)
        if not self.kill_flag.is_absolute():
            # Resolve relative to repo root
            repo_root = Path(__file__).resolve().parents[1]
            self.kill_flag = repo_root / watchdog_config.kill_flag_file
        self.kill_flag.parent.mkdir(parents=True, exist_ok=True)

    @property
    def halted(self) -> bool:
        return self._halted

    @property
    def halt_reason(self) -> Optional[str]:
        return self._halt_reason

    def check_kill_flag(self) -> bool:
        """Return True if manual kill flag file exists."""
        return self.kill_flag.exists()

    def update_equity(self, equity: float) -> None:
        """Call on every tick to record current equity."""
        now = time.time()
        self.equity_history.append(EquityPoint(now, equity))
        if equity > self.high_water:
            self.high_water = equity

        # Trim to 24h window
        cutoff = now - 24 * 3600
        while self.equity_history and self.equity_history[0].timestamp < cutoff:
            self.equity_history.popleft()

    def check(
        self,
        current_equity: float,
        daily_realized_pnl: float,
    ) -> tuple[bool, Optional[str]]:
        """
        Evaluate all circuit breaker conditions.

        Returns (should_halt, reason). If should_halt is True, the trader
        must flatten positions and stop. Call halt() to set the kill flag.
        """
        # Update state first
        self.update_equity(current_equity)

        # 1. Manual kill flag
        if self.check_kill_flag():
            return True, "manual kill flag detected"

        # 2. Drawdown from high-water mark
        dd_from_hw_pct = (self.high_water - current_equity) / self.high_water * 100
        if dd_from_hw_pct >= self.config.max_dd_from_high_water_pct:
            return True, (
                f"drawdown {dd_from_hw_pct:.2f}% from high-water "
                f"${self.high_water:,.2f} exceeds {self.config.max_dd_from_high_water_pct}% limit"
            )

        # 3. Rolling 24h drawdown
        if self.equity_history:
            peak_24h = max(p.equity for p in self.equity_history)
            dd_24h_pct = (peak_24h - current_equity) / peak_24h * 100 if peak_24h > 0 else 0
            if dd_24h_pct >= self.config.max_dd_24h_pct:
                return True, (
                    f"24h drawdown {dd_24h_pct:.2f}% from peak ${peak_24h:,.2f} "
                    f"exceeds {self.config.max_dd_24h_pct}% limit"
                )

        # 4. Daily realized loss (absolute dollar)
        if daily_realized_pnl <= -abs(self.config.max_loss_daily_usd):
            return True, (
                f"daily realized loss ${daily_realized_pnl:,.2f} exceeds "
                f"${self.config.max_loss_daily_usd:,.2f} limit"
            )

        return False, None

    def halt(self, reason: str, create_kill_flag: bool = True) -> None:
        """Mark the trader as halted. Optionally create the kill flag file."""
        if self._halted:
            return  # idempotent
        self._halted = True
        self._halt_reason = reason
        if create_kill_flag:
            try:
                self.kill_flag.touch()
                logger.info("Kill flag written to %s", self.kill_flag)
            except Exception as e:
                logger.error("Failed to write kill flag: %s", e)
        self.alerts.circuit_breaker(
            f"TRADING HALTED: {reason}",
            high_water=f"${self.high_water:,.2f}",
            session_start=f"${self.session_start_equity:,.2f}",
            current=f"${self.equity_history[-1].equity:,.2f}" if self.equity_history else "?",
            kill_flag_path=str(self.kill_flag),
        )

    def clear_halt(self) -> None:
        """Reset halt state. Does NOT delete kill flag — that's manual."""
        self._halted = False
        self._halt_reason = None
