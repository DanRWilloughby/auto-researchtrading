"""
Data guard — validates market data before the strategy trades on it.

Checks:
  - Stale candle detection (is the latest bar within expected time window?)
  - Price sanity (does price deviate absurdly from last bar?)
  - Funding rate anomaly (alert on unusual funding)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

from .alerts import AlertDispatch
from .config import DataGuardConfig

logger = logging.getLogger(__name__)


@dataclass
class DataCheckResult:
    ok: bool
    reason: Optional[str] = None
    should_alert: bool = False


class DataGuard:
    def __init__(self, config: DataGuardConfig, alerts: AlertDispatch):
        self.config = config
        self.alerts = alerts
        self._last_prices: dict[str, float] = {}

    def check_candle_freshness(
        self,
        symbol: str,
        latest_candle_ts_ms: int,
        interval_ms: int,
    ) -> DataCheckResult:
        """Verify the latest candle is recent enough to trade on."""
        now_ms = int(time.time() * 1000)
        age_ms = now_ms - latest_candle_ts_ms
        max_age_ms = self.config.stale_candle_max_age_intervals * interval_ms
        if age_ms > max_age_ms:
            age_min = age_ms / 60000
            msg = (
                f"{symbol} latest candle is {age_min:.1f} min old "
                f"(max allowed: {max_age_ms/60000:.1f} min)"
            )
            self.alerts.warning(msg, symbol=symbol, age_minutes=age_min)
            return DataCheckResult(ok=False, reason=msg, should_alert=True)
        return DataCheckResult(ok=True)

    def check_price_sanity(
        self,
        symbol: str,
        current_price: float,
        last_bar_close: Optional[float] = None,
    ) -> DataCheckResult:
        """Reject price data that looks corrupted (gap > threshold from last bar)."""
        if current_price <= 0:
            msg = f"{symbol} price is non-positive: {current_price}"
            return DataCheckResult(ok=False, reason=msg, should_alert=True)

        prev = last_bar_close if last_bar_close is not None else self._last_prices.get(symbol)
        if prev is None or prev <= 0:
            # First observation, store and allow
            self._last_prices[symbol] = current_price
            return DataCheckResult(ok=True)

        deviation_pct = abs(current_price - prev) / prev * 100
        if deviation_pct > self.config.price_deviation_pct:
            msg = (
                f"{symbol} price jumped {deviation_pct:.2f}% "
                f"(${prev:,.2f} -> ${current_price:,.2f}) — likely bad data"
            )
            self.alerts.warning(msg, symbol=symbol, deviation_pct=deviation_pct)
            return DataCheckResult(ok=False, reason=msg, should_alert=True)

        self._last_prices[symbol] = current_price
        return DataCheckResult(ok=True)

    def check_funding_rate(
        self,
        symbol: str,
        funding_rate_8h: float,
    ) -> DataCheckResult:
        """Alert if funding rate is outside normal bounds."""
        bps = funding_rate_8h * 10000
        threshold = self.config.funding_alert_per_8h_bps
        if abs(bps) > threshold:
            msg = (
                f"{symbol} funding rate is {bps:+.1f} bps per 8h "
                f"(threshold: ±{threshold} bps)"
            )
            self.alerts.warning(msg, symbol=symbol, funding_bps=bps)
            return DataCheckResult(ok=True, reason=msg, should_alert=True)
        return DataCheckResult(ok=True)
