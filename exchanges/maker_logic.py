"""Pure decision functions for the BTC maker pilot (Fix 6).

Extracted as pure (no I/O) functions so they're testable in isolation
without mocking the Coinbase SDK. The wrapper in coinbase_client.py
handles the actual order placement and polling.

All functions defensive: assume inputs may be malformed (zero prices,
None values) and return safe defaults rather than raise. The trader
must keep running even if maker logic decides to skip a trade.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# DD-approach disable: route to pure taker when DD is close to kill threshold
# ---------------------------------------------------------------------------

def should_disable_maker_for_dd(
    current_dd_pct: float,
    threshold_pct: float,
    approach_buffer_pct: float = 20.0,
) -> bool:
    """Return True if maker should be disabled because DD is approaching the kill threshold.

    Args:
        current_dd_pct: current drawdown percentage (e.g. 7.5 for 7.5%).
        threshold_pct: kill threshold (e.g. 10.0 for 10%).
        approach_buffer_pct: percentage of threshold considered "approaching"
            (default 20% — so disable maker when DD >= 80% of threshold).

    Rationale: a maker miss during a deteriorating DD could trigger the CB
    at the wrong moment. When close to the kill threshold, route to taker
    for guaranteed immediate fill so positions don't get stuck in maker queue.
    """
    if threshold_pct <= 0:
        return False
    disable_at = threshold_pct * (1.0 - approach_buffer_pct / 100.0)
    return current_dd_pct >= disable_at


# ---------------------------------------------------------------------------
# Vol-aware timeout: shorten the maker wait when realized vol is high
# ---------------------------------------------------------------------------

def compute_realized_vol_bps(prices: list[float]) -> float:
    """Compute trailing realized volatility in bps from a list of recent prices.

    Uses simple stdev of consecutive 1-period returns. Caller passes whatever
    window is appropriate (e.g. last 15 prices = ~15 minutes if 1-min sampled).

    Returns 0.0 if fewer than 3 valid prices (insufficient data).
    """
    valid = [p for p in prices if p is not None and p > 0]
    if len(valid) < 3:
        return 0.0
    returns = []
    for i in range(1, len(valid)):
        prev = valid[i - 1]
        cur = valid[i]
        if prev <= 0:
            continue
        returns.append((cur - prev) / prev)
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    stdev_pct = variance ** 0.5
    return stdev_pct * 10000.0  # convert to bps


def compute_vol_aware_timeout(
    base_timeout_sec: int,
    current_vol_bps: float,
    high_vol_threshold_bps: float = 30.0,
    extreme_vol_threshold_bps: float = 50.0,
    high_vol_timeout_sec: int = 60,
) -> Optional[int]:
    """Adjust maker timeout based on current realized vol, or skip maker entirely.

    Returns:
        None if vol is extreme (skip maker, route to taker)
        high_vol_timeout_sec if vol is high (use shorter timeout)
        base_timeout_sec otherwise
    """
    if current_vol_bps >= extreme_vol_threshold_bps:
        return None  # skip maker
    if current_vol_bps >= high_vol_threshold_bps:
        return high_vol_timeout_sec
    return base_timeout_sec


# ---------------------------------------------------------------------------
# Limit price selection
# ---------------------------------------------------------------------------

def compute_limit_price(
    bid: float,
    ask: float,
    side: str,
    aggressiveness: str = "passive",
) -> Optional[float]:
    """Compute the limit price for a maker order.

    Args:
        bid: current best bid.
        ask: current best ask.
        side: "BUY" or "SELL".
        aggressiveness:
            "passive" - top of book (BUY at bid, SELL at ask). Best fee, lowest fill rate.
            "mid"     - midpoint between bid/ask. Balanced.
            "aggressive" - cross to other side (BUY at ask, SELL at bid). Acts as taker.

    Returns None if bid/ask invalid.
    """
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return None
    side = side.upper()
    if side not in ("BUY", "SELL"):
        return None

    if aggressiveness == "passive":
        return bid if side == "BUY" else ask
    if aggressiveness == "mid":
        return (bid + ask) / 2.0
    if aggressiveness == "aggressive":
        return ask if side == "BUY" else bid
    return None


# ---------------------------------------------------------------------------
# Adverse price movement detection (per-trade fallback circuit breaker)
# ---------------------------------------------------------------------------

@dataclass
class FallbackDecision:
    proceed_to_taker: bool
    reason: str
    adverse_move_bps: float


def should_fallback_to_taker(
    original_limit_price: float,
    current_market_price: float,
    side: str,
    max_adverse_bps: float = 20.0,
) -> FallbackDecision:
    """Decide whether to fall back to taker after a maker timeout.

    If the market has moved adversely beyond max_adverse_bps from the original
    limit price, do NOT fall back — cancel the leg and accept partial position.
    This prevents chasing a runaway price that could cost more than the
    fee savings ever justified.

    Args:
        original_limit_price: the price the limit order was placed at.
        current_market_price: latest market price.
        side: "BUY" or "SELL".
        max_adverse_bps: cancel-without-fallback threshold (default 20 bps).

    Returns FallbackDecision with proceed_to_taker, reason, and observed adverse move.
    """
    if original_limit_price <= 0 or current_market_price <= 0:
        return FallbackDecision(False, "invalid prices", 0.0)
    side = side.upper()
    if side not in ("BUY", "SELL"):
        return FallbackDecision(False, f"unknown side {side}", 0.0)

    # For BUY: adverse = price went UP (we'd pay more)
    # For SELL: adverse = price went DOWN (we'd receive less)
    if side == "BUY":
        move_bps = (current_market_price - original_limit_price) / original_limit_price * 10000.0
    else:  # SELL
        move_bps = (original_limit_price - current_market_price) / original_limit_price * 10000.0

    if move_bps > max_adverse_bps:
        return FallbackDecision(
            proceed_to_taker=False,
            reason=f"price moved {move_bps:.2f} bps adversely (cancel threshold {max_adverse_bps} bps)",
            adverse_move_bps=move_bps,
        )
    return FallbackDecision(
        proceed_to_taker=True,
        reason="within tolerance",
        adverse_move_bps=move_bps,
    )


# ---------------------------------------------------------------------------
# Order splitting: divide a target into maker + taker legs
# ---------------------------------------------------------------------------

def split_target_for_pilot(
    target_notional_usd: float,
    current_notional_usd: float,
    maker_fraction: float = 0.5,
) -> tuple[float, float]:
    """Split a position adjustment into (taker_target, maker_target) for paired A/B.

    Both legs are placed sequentially against the SAME CB account:
      1. Taker leg fires first, advancing position by (1 - maker_fraction) * delta
      2. Maker leg fires next, completing position to the full target

    Returns (taker_target_notional, maker_target_notional) — absolute targets each
    leg should drive position toward. After both fill, position == target_notional_usd.

    If maker_fraction is out of (0, 1), returns (target, target) — both legs
    target the full position, taker fills it all, maker is no-op. Safe default.
    """
    if not 0.0 < maker_fraction < 1.0:
        return (target_notional_usd, target_notional_usd)

    delta = target_notional_usd - current_notional_usd
    taker_advance = delta * (1.0 - maker_fraction)
    taker_target = current_notional_usd + taker_advance
    maker_target = target_notional_usd  # maker completes to full target

    return (taker_target, maker_target)


# ---------------------------------------------------------------------------
# Maker-with-fallback state machine — testable orchestration
# ---------------------------------------------------------------------------

@dataclass
class LimitFillResult:
    """Outcome of a maker-with-fallback attempt."""
    success: bool
    final_state: str           # "filled_maker" | "filled_fallback" | "cancelled" | "error"
    fill_price: Optional[float]
    fill_time_sec: float       # seconds elapsed until fill (or cancel)
    is_fallback: bool          # True if filled via taker after timeout
    fallback_decision: Optional[FallbackDecision] = None
    error: Optional[str] = None


def run_limit_with_fallback(
    side: str,
    limit_price: float,
    timeout_sec: int,
    poll_interval_sec: float,
    fallback_max_adverse_bps: float,
    place_limit_fn,                # callable() -> str (order_id) or None on failure
    poll_status_fn,                # callable(order_id) -> ("filled" | "open" | "cancelled" | "error", fill_price | None)
    cancel_fn,                     # callable(order_id) -> bool (success)
    fetch_current_price_fn,        # callable() -> current market price
    place_market_fn,               # callable() -> (success, fill_price)
    sleep_fn=None,                 # callable(seconds) — defaults to time.sleep
    elapsed_fn=None,               # callable() -> seconds since start; defaults to monotonic
) -> LimitFillResult:
    """Pure orchestration of the maker-with-fallback flow. All I/O is injected.

    Sequence:
      1. Place limit at limit_price
      2. Poll status every poll_interval_sec until filled or timeout
      3. On fill: return success
      4. On timeout: cancel limit, then check current price vs limit
         - If current moved >fallback_max_adverse_bps adversely: return cancelled
         - Else: place market order, return fallback fill

    All side effects are passed in as callbacks so this function is fully
    testable in isolation. Real CoinbaseClient wires these to SDK calls.
    """
    import time as _time
    sleep_fn = sleep_fn or _time.sleep
    if elapsed_fn is None:
        start = _time.monotonic()
        def _elapsed():
            return _time.monotonic() - start
        elapsed_fn = _elapsed

    # 1. Place limit order
    order_id = place_limit_fn()
    if not order_id:
        return LimitFillResult(
            success=False, final_state="error",
            fill_price=None, fill_time_sec=0.0,
            is_fallback=False, error="failed to place limit",
        )

    # 2. Poll for fill
    while elapsed_fn() < timeout_sec:
        status, fill_price = poll_status_fn(order_id)
        if status == "filled":
            return LimitFillResult(
                success=True, final_state="filled_maker",
                fill_price=fill_price, fill_time_sec=elapsed_fn(),
                is_fallback=False,
            )
        if status == "cancelled":
            # External cancel — treat as failure, don't fall back
            return LimitFillResult(
                success=False, final_state="cancelled",
                fill_price=None, fill_time_sec=elapsed_fn(),
                is_fallback=False, error="limit was externally cancelled",
            )
        if status == "error":
            return LimitFillResult(
                success=False, final_state="error",
                fill_price=None, fill_time_sec=elapsed_fn(),
                is_fallback=False, error="poll returned error",
            )
        sleep_fn(poll_interval_sec)

    # 3. Timeout — cancel the limit
    cancel_fn(order_id)

    # 4. Decide whether to fall back to taker
    current_price = fetch_current_price_fn()
    decision = should_fallback_to_taker(
        original_limit_price=limit_price,
        current_market_price=current_price,
        side=side,
        max_adverse_bps=fallback_max_adverse_bps,
    )
    if not decision.proceed_to_taker:
        return LimitFillResult(
            success=False, final_state="cancelled",
            fill_price=None, fill_time_sec=elapsed_fn(),
            is_fallback=False, fallback_decision=decision,
            error=decision.reason,
        )

    # 5. Fall back to market order
    market_success, market_price = place_market_fn()
    if not market_success:
        return LimitFillResult(
            success=False, final_state="error",
            fill_price=None, fill_time_sec=elapsed_fn(),
            is_fallback=True, fallback_decision=decision,
            error="market fallback also failed",
        )
    return LimitFillResult(
        success=True, final_state="filled_fallback",
        fill_price=market_price, fill_time_sec=elapsed_fn(),
        is_fallback=True, fallback_decision=decision,
    )
