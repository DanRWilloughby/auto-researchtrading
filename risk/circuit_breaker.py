"""
Circuit breaker — hard halts that stop trading on extreme conditions.

Triggers:
  1. Drawdown from high-water mark > threshold (uses MTM equity vs realized HWM)
  2. Rolling 24h drawdown > threshold (uses MTM equity vs realized peak)
  3. Daily realized loss > threshold
  4. Manual kill flag (file exists)

Fix 5 (HWM ratcheting on realized-only):
  - High-water mark ratchets ONLY on realized_equity. Transient MTM gains
    from in-flight settlement do not inflate HWM.
  - DD ratio uses MTM equity in the numerator (so unrealized losses still
    count as drawdown — preserves tail-risk protection).
  - Asymmetric on purpose: gains must be realized to be "yours"; losses
    count even when unrealized.

When any trigger fires:
  - Set halted=True (trader polls this and stops new orders)
  - Existing positions are NOT force-flattened (let strategy reach natural exits)
  - Write kill flag (until Fix 4 auto-reset cooldown is implemented)
  - Dispatch critical alert
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
    """A single equity sample for the 24h rolling window.

    `mtm_equity` is what the user sees as "current equity" (cash + unrealized).
    `realized_equity` is the settled portion (cash only / realized PnL).
    HWM ratchets on realized; DD ratio uses mtm. See module docstring.
    """
    timestamp: float
    mtm_equity: float
    realized_equity: float

    # Backward compat: older code may construct with single `equity` kwarg
    @property
    def equity(self) -> float:
        return self.mtm_equity


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
        self.high_water = initial_equity  # ratchets on realized_equity only (Fix 5)
        self.session_start_equity = initial_equity
        self.session_start_ts = time.time()
        self.equity_history: deque[EquityPoint] = deque(maxlen=2880)  # 24h of 30s samples
        self.equity_history.append(
            EquityPoint(time.time(), mtm_equity=initial_equity, realized_equity=initial_equity)
        )
        self._halted = False
        self._halt_reason: Optional[str] = None
        # Fix 4: track when last DD breach occurred. Auto-reset only fires
        # if cooldown has elapsed since this timestamp AND we set this (i.e.
        # the kill flag came from us, not external manual intervention).
        self.last_breach_ts: Optional[float] = None

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

    def update_equity(
        self,
        mtm_equity: float,
        realized_equity: Optional[float] = None,
    ) -> None:
        """Record current equity sample. Call on every tick.

        Args:
            mtm_equity: cash + unrealized PnL (current account value).
            realized_equity: settled equity (cash only / no unrealized).
                If None (legacy callers), uses mtm_equity for both — old
                behavior. New code MUST pass realized_equity for the Fix 5
                phantom-peak prevention to take effect.

        HWM ratchets ONLY on realized_equity. MTM is recorded for the 24h
        rolling window's "current equity" reads but does NOT influence HWM.
        See class docstring for asymmetry rationale.
        """
        if realized_equity is None:
            realized_equity = mtm_equity  # legacy fallback

        now = time.time()
        self.equity_history.append(
            EquityPoint(now, mtm_equity=mtm_equity, realized_equity=realized_equity)
        )
        # Fix 5: ratchet HWM on realized_equity only. Transient MTM gains
        # from in-flight settlement do not get locked into HWM.
        if realized_equity > self.high_water:
            self.high_water = realized_equity

        # Trim to 24h window
        cutoff = now - 24 * 3600
        while self.equity_history and self.equity_history[0].timestamp < cutoff:
            self.equity_history.popleft()

    def check(
        self,
        current_equity: Optional[float] = None,
        daily_realized_pnl: float = 0.0,
        *,
        mtm_equity: Optional[float] = None,
        realized_equity: Optional[float] = None,
    ) -> tuple[bool, Optional[str]]:
        """
        Evaluate all circuit breaker conditions.

        Args:
            current_equity: legacy single-arg API. If used, treated as both
                MTM and realized (no separation possible). Old code calling
                check(equity, daily_pnl) still works but loses Fix 5 benefit.
            daily_realized_pnl: realized PnL for current trading day.
            mtm_equity (kw-only): mark-to-market equity (cash + unrealized).
                Used in DD ratio so unrealized losses count.
            realized_equity (kw-only): settled equity (cash only). Used to
                ratchet HWM and 24h peak — phantom MTM peaks are excluded.

        Returns (should_halt, reason). If should_halt is True, the trader
        should stop opening new positions (existing positions remain — see
        Fix 3). Call halt() to set the kill flag.
        """
        # Resolve which API was used. New kw-only args take precedence.
        if mtm_equity is None and current_equity is None:
            raise ValueError("Must pass either current_equity (legacy) or mtm_equity")
        if mtm_equity is None:
            mtm_equity = current_equity
        if realized_equity is None:
            # Either: caller used legacy single-arg, or only passed mtm_equity.
            # Conservative fallback: treat mtm as realized (old behavior, with
            # the phantom-peak bug). Issue a warning so we can find old callers.
            realized_equity = mtm_equity

        # Update state first
        self.update_equity(mtm_equity=mtm_equity, realized_equity=realized_equity)

        # Evaluate DD conditions first (before kill flag check) so re-breach
        # during a halted state still updates last_breach_ts (Fix 4 needs this
        # to extend cooldown on every fresh breach, not just the first one).
        dd_breach_reason = None

        # 2. Drawdown from high-water mark
        # Fix 5: HWM is realized-only (set by update_equity). Numerator is MTM
        # so unrealized losses still trigger.
        dd_from_hw_pct = (self.high_water - mtm_equity) / self.high_water * 100 if self.high_water > 0 else 0
        if dd_from_hw_pct >= self.config.max_dd_from_high_water_pct:
            dd_breach_reason = (
                f"drawdown {dd_from_hw_pct:.2f}% from high-water "
                f"${self.high_water:,.2f} exceeds {self.config.max_dd_from_high_water_pct}% limit"
            )

        # 3. Rolling 24h drawdown
        # Fix 5: 24h peak uses realized_equity only.
        if dd_breach_reason is None and self.equity_history:
            peak_24h = max(p.realized_equity for p in self.equity_history)
            dd_24h_pct = (peak_24h - mtm_equity) / peak_24h * 100 if peak_24h > 0 else 0
            if dd_24h_pct >= self.config.max_dd_24h_pct:
                dd_breach_reason = (
                    f"24h drawdown {dd_24h_pct:.2f}% from peak ${peak_24h:,.2f} "
                    f"exceeds {self.config.max_dd_24h_pct}% limit"
                )

        # 4. Daily realized loss (absolute dollar)
        if dd_breach_reason is None and daily_realized_pnl <= -abs(self.config.max_loss_daily_usd):
            dd_breach_reason = (
                f"daily realized loss ${daily_realized_pnl:,.2f} exceeds "
                f"${self.config.max_loss_daily_usd:,.2f} limit"
            )

        # Fix 4: any real DD breach updates last_breach_ts, even when already halted.
        # This keeps the auto-reset cooldown rolling forward as long as the
        # account is actually in a bad state.
        if dd_breach_reason is not None:
            self.last_breach_ts = time.time()

        # 1. Manual kill flag (checked AFTER DD so re-breach updates timer)
        if self.check_kill_flag():
            return True, "manual kill flag detected"

        # No kill flag: return the DD result if any
        if dd_breach_reason is not None:
            return True, dd_breach_reason

        return False, None

    def try_auto_reset(
        self,
        mtm_equity: float,
        realized_equity: float,
    ) -> bool:
        """Attempt to auto-clear the kill flag if cooldown elapsed AND DD recovered.

        Fix 4: replaces manual kill-flag deletion with automatic clearing when:
          1. Cooldown duration has elapsed since last_breach_ts
          2. Current MTM-based DD is below threshold
          3. Kill flag exists (created by US — last_breach_ts is set)

        Returns True iff the flag was cleared.

        SAFETY: Externally created kill flags (last_breach_ts is None) are
        NEVER auto-cleared. Manual operator action stays sticky until manual
        removal. This prevents the auto-reset from undoing a deliberate halt.
        """
        if self.config.auto_reset_cooldown_sec <= 0:
            return False  # disabled
        if not self.kill_flag.exists():
            return False  # nothing to reset
        if self.last_breach_ts is None:
            return False  # external flag — manual only

        now = time.time()
        if now - self.last_breach_ts < self.config.auto_reset_cooldown_sec:
            return False  # cooldown not elapsed

        # Verify current DD is below threshold (don't reset into deteriorating state)
        if self.high_water > 0:
            dd_pct = (self.high_water - mtm_equity) / self.high_water * 100
            if dd_pct >= self.config.max_dd_from_high_water_pct:
                return False  # still in DD — don't reset

        # All conditions met — clear flag and halt state
        try:
            self.kill_flag.unlink()
            logger.info("Auto-reset: kill flag cleared after cooldown")
        except Exception as e:
            logger.error("Auto-reset: failed to remove kill flag: %s", e)
            return False
        self.clear_halt()
        self.last_breach_ts = None  # reset for next cycle
        return True

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
