"""
Live trading bot — runs the strategy against live Coinbase data with real orders.

Mirrors paper/trader.py's interface but:
  - Reads live data from Coinbase (via CoinbaseClient) instead of Hyperliquid REST
  - Places REAL orders on Coinbase perpetual futures (not simulated)
  - Wraps every signal through RiskManager before execution
  - Writes to live_state.json in a dedicated directory (paper state untouched)
  - Dispatches alerts to Telegram on every trade, warning, and emergency

Usage:
    uv run live/trader.py --once --strategy strategies/30m-concentrated/strategy.py
    uv run live/trader.py --once --dry-run  # no real orders, logs only
    uv run live/trader.py --status          # print current state

CRITICAL: Defaults to --dry-run=False because when deployed to cron
we want it to actually trade. The deployment cron wrapper initially sets
it explicitly, and there's a DRY_RUN env var override below as a safety
gate that can be flipped without code changes.
"""
import argparse
import importlib.util
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Project root on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.prepare import BarData, Signal, PortfolioState, INTERVAL_CONFIG, LOOKBACK_BARS
from exchanges import CoinbaseClient
from risk import RiskManager
from risk.alerts import Alert, AlertType
from risk.flash_crash_guard import PositionSnapshot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("live-trader")


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

STARTING_CAPITAL = 10_000  # Test capital — live trader uses actual account balance


def derive_live_state_path(strategy_path: str, instance: str = "live") -> Path:
    """
    Compute where state lives for a given strategy + instance.

    Each instance is a separately-tracked trader with its own state file,
    equity curve, and trade log. Use different instances to run the same
    strategy at different timings on the same VM for A/B comparison.
    """
    strategy_name = Path(strategy_path).parent.name
    return PROJECT_ROOT / "live" / "state" / f"{strategy_name}_{instance}_state.json"


def load_state(state_file: Path, interval: str, initial_equity: float) -> dict:
    if state_file.exists():
        with open(state_file) as f:
            return json.load(f)
    return {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "interval": interval,
        "initial_equity": initial_equity,
        # paper_state.json-compatible fields (for dashboard)
        "cash": initial_equity,
        "positions": {},
        "entry_prices": {},
        "equity_curve": [{"ts": int(time.time() * 1000), "equity": initial_equity}],
        "trade_log": [],
        "last_bar_ts": {},
        "history_buffers": {},
        "peak_equity": initial_equity,
        "strategy_state": {},
    }


def save_state(state: dict, state_file: Path) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2, default=str)


COINBASE_FEE_BPS = 0.0003           # Per-trade notional fee component
COINBASE_FEE_PER_CONTRACT = 0.15    # Per-trade fixed fee component


def _model_fee(notional_usd: float, contracts: float) -> float:
    """Coinbase futures fee model: 3 bps of notional + $0.15/contract."""
    return abs(notional_usd) * COINBASE_FEE_BPS + abs(contracts) * COINBASE_FEE_PER_CONTRACT


def _enrich_trade_fields(
    state: dict, symbol: str, result, dry_run: bool
) -> tuple[float, float, float]:
    """
    Compute (pnl, fee, target_pos_notional) for an incoming fill using the
    per-symbol open-lot in state["sim_open_lots"]. Handles longs AND shorts.

    sim_open_lots[symbol] = [signed_contracts, entry_price, contract_size]
      - positive contracts = long
      - negative contracts = short
      - absent/zero = flat

    A trade that flips direction (e.g., SELL 10 while holding +5) is split
    into a close-5 (realize P&L) + open-short-5 (new entry).
    """
    sim = state.setdefault("sim_open_lots", {})
    contracts = float(result.contracts or 0)
    fill_price = float(result.fill_price or 0)
    notional = float(result.notional_usd or 0)
    cs = abs(notional) / (abs(contracts) * fill_price) if contracts and fill_price else 0.0

    has_live_fills = any(
        not t.get("dry_run", False) for t in state.get("trade_log", [])
    )
    is_live_instance = has_live_fills or not dry_run
    participates = (not dry_run) if is_live_instance else True

    pnl = 0.0
    fee = _model_fee(notional, contracts) if participates else 0.0

    if not participates:
        lot = sim.get(symbol)
        target_notional = (lot[0] * lot[2] * lot[1]) if lot else 0.0
        return 0.0, 0.0, target_notional

    existing = sim.get(symbol)  # [signed_contracts, entry, cs] or None
    existing_contracts = existing[0] if existing else 0.0

    # Compute new net position after this trade
    new_contracts = existing_contracts + contracts

    # Realize P&L on the portion being closed (direction reversal or reduction)
    if existing and existing_contracts != 0:
        # Closing portion = how many contracts changed direction
        if (existing_contracts > 0 and contracts < 0) or (existing_contracts < 0 and contracts > 0):
            close_qty = min(abs(existing_contracts), abs(contracts))
            entry = existing[1]
            entry_cs = existing[2] or cs
            if existing_contracts > 0:
                pnl = close_qty * entry_cs * (fill_price - entry)
            else:
                pnl = close_qty * entry_cs * (entry - fill_price)

    # Update sim lot
    if abs(new_contracts) < 1e-9:
        sim.pop(symbol, None)
    elif (existing_contracts > 0 and new_contracts > 0 and contracts > 0):
        # Adding to existing long — no-op for paper sims with same target
        if not is_live_instance and abs(existing_contracts - new_contracts) < 0.1:
            lot = sim.get(symbol)
            target_notional = (lot[0] * lot[2] * lot[1]) if lot else 0.0
            return 0.0, 0.0, target_notional
        sim[symbol] = [new_contracts, fill_price, cs]
    elif (existing_contracts < 0 and new_contracts < 0 and contracts < 0):
        # Adding to existing short — same no-op logic
        if not is_live_instance and abs(existing_contracts - new_contracts) < 0.1:
            lot = sim.get(symbol)
            target_notional = (lot[0] * lot[2] * lot[1]) if lot else 0.0
            return 0.0, 0.0, target_notional
        sim[symbol] = [new_contracts, fill_price, cs]
    else:
        # Direction flipped or fresh open
        sim[symbol] = [new_contracts, fill_price, cs]

    lot = sim.get(symbol)
    target_notional = (lot[0] * lot[2] * lot[1]) if lot else 0.0
    return pnl, fee, target_notional


def _get_total_pnl_for_live(
    state: dict,
    coinbase_realized: float,
    unrealized: float,
) -> float:
    """
    Returns total P&L since instance start for LIVE instances.

    Coinbase's `daily_realized_pnl` from the CFM balance summary is
    inception-to-date (not daily-resetting) and INCLUDES fees. So total
    P&L is simply: coinbase_realized + unrealized. No rollover or fee
    tracking needed.

    Stores a snapshot for reset detection (in case Coinbase ever resets
    the field during maintenance or settlement).
    """
    prev_snapshot = float(state.get("last_coinbase_realized_snapshot", 0.0))

    # Reset detection: if the value jumps toward 0 while we had a large
    # negative, Coinbase may have zeroed the field. Capture the pre-reset
    # value as a frozen offset so we don't lose history.
    if prev_snapshot < -10 and coinbase_realized > prev_snapshot + 50:
        state["realized_frozen_offset"] = float(
            state.get("realized_frozen_offset", 0.0)
        ) + prev_snapshot
        logger.warning(
            "Coinbase daily_realized_pnl appears to have reset "
            f"({prev_snapshot:.2f} → {coinbase_realized:.2f}). "
            f"Captured offset: {state['realized_frozen_offset']:.2f}"
        )

    state["last_coinbase_realized_snapshot"] = coinbase_realized
    frozen = float(state.get("realized_frozen_offset", 0.0))

    total_pnl = frozen + coinbase_realized + unrealized
    state["cumulative_realized_pnl_total"] = round(frozen + coinbase_realized, 4)
    return total_pnl


def _resync_state_post_orders(state: dict, client: "CoinbaseClient") -> None:
    """
    Refresh state from Coinbase *after* any new orders land, using HL-compatible
    semantics:

      cash = initial_equity + cum_realized − cum_fees − Σ positions
      equity = cash + Σ positions + unrealized
             = initial_equity + cum_realized − cum_fees + unrealized

    Writing `cash` this way means the dashboard's generic formula
    (`cash + positions + unrealized`) yields the right equity for both HL paper
    and CB instances without special-casing. Cumulative tracking survives
    the UTC midnight daily_* reset.
    """
    try:
        positions = client.get_positions()
        coinbase_daily_realized = client.get_daily_realized_pnl()
        coinbase_daily_fees = client.get_daily_total_fees()
    except Exception as e:
        logger.warning("Post-order state resync failed; state may be one tick stale: %s", e)
        return

    has_live_fills = any(
        not t.get("dry_run", False) for t in state.get("trade_log", [])
    )
    initial_equity = float(state.get("initial_equity") or STARTING_CAPITAL)

    if has_live_fills:
        # --- LIVE instance ---
        # Use available_margin (actual Coinbase cash) + unrealized as equity.
        # This is the real account value — no lag from daily_realized_pnl.
        pos_notionals = {sym: p.notional_usd for sym, p in positions.items()}
        total_exposure = sum(abs(v) for v in pos_notionals.values())
        unrealized_sum = sum(p.unrealized_pnl_usd for p in positions.values())
        entry_prices = {sym: p.entry_price for sym, p in positions.items()}

        try:
            available_margin = client.get_cash_balance_usd()
        except Exception:
            available_margin = initial_equity
        equity = available_margin  # settled value — matches Coinbase app base number
        state["cumulative_realized_pnl_total"] = round(
            available_margin - initial_equity, 4
        )
    else:
        # --- PAPER instance ---
        # Uses own sim_open_lots for positions; sums enriched pnl/fee from
        # the trade log for realized; fetches live prices for unrealized.
        sim_lots = state.get("sim_open_lots", {}) or {}
        pos_notionals = {}
        entry_prices = {}
        for sym, lot in sim_lots.items():
            oc, oe, ocs = lot
            if abs(oc) > 1e-12:
                pos_notionals[sym] = round(oc * ocs * oe, 4)
                entry_prices[sym] = round(oe, 6)
        total_exposure = sum(abs(v) for v in pos_notionals.values())
        unrealized_sum = 0.0
        for sym, lot in sim_lots.items():
            try:
                mark = float(client.fetch_current_price(sym))
            except Exception:
                mark = 0.0
            if mark > 0 and lot[1] > 0:
                sign = 1.0 if lot[0] > 0 else -1.0
                unrealized_sum += sign * abs(lot[0]) * lot[2] * (mark - lot[1])

        sim_realized = sum(float(t.get("pnl") or 0) for t in state.get("trade_log", []))
        sim_fees = sum(float(t.get("fee") or 0) for t in state.get("trade_log", []))
        equity = initial_equity + sim_realized - sim_fees  # settled value, no unrealized
        state["cumulative_realized_pnl_total"] = round(sim_realized, 4)
        state["cumulative_fees_total"] = round(sim_fees, 4)

    virtual_cash = equity - total_exposure - unrealized_sum
    state["cash"] = round(virtual_cash, 4)
    state["positions"] = pos_notionals
    state["entry_prices"] = entry_prices
    state["daily_price_pnl"] = round(coinbase_daily_realized, 4)
    state["daily_fees"] = round(coinbase_daily_fees, 4)
    state["daily_total_pnl"] = round(coinbase_daily_realized - coinbase_daily_fees, 4)

    now_ms = int(time.time() * 1000)
    state["equity_curve"].append({"ts": now_ms, "equity": round(equity, 4)})
    if equity > float(state.get("peak_equity") or 0):
        state["peak_equity"] = round(equity, 4)


def _execute_paper_sim(
    state: dict,
    client: "CoinbaseClient",
    symbol: str,
    target_notional: float,
    now_ms: int,
) -> dict | None:
    """
    Paper-sim order execution: compute the delta between sim_open_lots and
    the strategy's target, fetch a live price from Coinbase for the fill,
    update sim_open_lots, and return an HL-compatible trade record.

    Returns None if the sim is already at target (SKIP).
    """
    sim = state.setdefault("sim_open_lots", {})
    existing = sim.get(symbol)  # [signed_contracts, entry_price, contract_size]

    # Derive contract size from existing lot or standard defaults
    if existing:
        cs = existing[2]
    else:
        cs = {"BTC": 0.01, "ETH": 0.1, "SOL": 5.0}.get(symbol, 1.0)

    # Current sim position in notional USD (signed: positive=long, negative=short)
    current_notional = 0.0
    current_contracts = 0.0
    if existing:
        current_contracts = existing[0]
        current_notional = existing[0] * existing[2] * existing[1]

    # Target notional is signed: positive=long, negative=short, 0=flat
    # If target is flat (or near-flat), force full close to avoid dust
    if abs(target_notional) < 200 and abs(current_notional) > 0:
        target_notional = 0.0

    delta_notional = target_notional - current_notional

    # Skip if delta is negligible (< $10)
    if abs(delta_notional) < 200:
        logger.info("%s: paper sim already at target (%.0f ≈ %.0f), skip",
                    symbol, current_notional, target_notional)
        return None

    # Fetch current price for the simulated fill
    try:
        fill_price = client.fetch_current_price(symbol)
    except Exception as e:
        logger.warning("Paper sim: failed to fetch price for %s: %s", symbol, e)
        return None

    if fill_price <= 0:
        return None

    # Compute contracts for the delta
    delta_contracts = delta_notional / (cs * fill_price)
    delta_notional_exact = delta_contracts * cs * fill_price

    # Compute realized P&L on the closing portion
    pnl = 0.0
    if existing and current_contracts != 0:
        # Are we reducing or flipping?
        if (current_contracts > 0 and delta_contracts < 0) or \
           (current_contracts < 0 and delta_contracts > 0):
            close_contracts = min(abs(current_contracts), abs(delta_contracts))
            entry_price = existing[1]
            if current_contracts > 0:
                pnl = close_contracts * cs * (fill_price - entry_price)
            else:
                pnl = close_contracts * cs * (entry_price - fill_price)

    # Fee model
    fee = _model_fee(abs(delta_notional_exact), abs(delta_contracts))

    # Update sim_open_lots to reflect new target
    new_contracts = current_contracts + delta_contracts
    new_notional = abs(new_contracts) * cs * fill_price if fill_price > 0 else 0
    if abs(new_contracts) < 1e-9 or new_notional < 200:
        sim.pop(symbol, None)
        new_contracts = 0
    else:
        sim[symbol] = [new_contracts, fill_price, cs]

    # Determine action label
    if abs(target_notional) < 1 and abs(current_notional) > 1:
        action = "CLOSE"
    elif abs(current_notional) < 1:
        action = "OPEN_LONG" if target_notional > 0 else "OPEN_SHORT"
    else:
        action = "MODIFY"

    # HL-compatible target_pos (notional at new entry)
    lot = sim.get(symbol)
    target_pos = (lot[0] * lot[2] * lot[1]) if lot else 0.0

    return {
        "ts": now_ms,
        "time": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "action": action,
        "price": round(fill_price, 6),
        "size": round(delta_notional_exact, 4),
        "target_pos": round(target_pos, 2),
        "pnl": round(pnl, 4),
        "fee": round(fee, 4),
        "contracts": round(delta_contracts, 8),
        "notional_usd": round(abs(delta_notional_exact), 4),
        "fill_price": round(fill_price, 6),
        "fee_usd": 0.0,
        "order_id": "PAPER_SIM",
        "dry_run": True,
    }


def log_trade(trade: dict, logs_dir: Path) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = logs_dir / f"trades_{date_str}.jsonl"
    with open(log_path, "a") as f:
        f.write(json.dumps(trade, default=str) + "\n")


# ---------------------------------------------------------------------------
# Strategy loading (same pattern as paper/trader.py)
# ---------------------------------------------------------------------------

def load_strategy(path: str):
    spec = importlib.util.spec_from_file_location("strategy_module", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Strategy()


# ---------------------------------------------------------------------------
# Main tick
# ---------------------------------------------------------------------------

def run_one_tick(
    strategy,
    client: CoinbaseClient,
    risk_mgr: RiskManager,
    state: dict,
    symbols: list[str],
    interval: str,
    dry_run: bool,
) -> dict:
    """Execute one live trading tick: fetch data, run strategy, place real orders."""
    now_ms = int(time.time() * 1000)
    interval_min = INTERVAL_CONFIG[interval]["minutes"]
    interval_ms = interval_min * 60 * 1000

    # --- 1. Fetch live account state from Coinbase ---
    try:
        cash = client.get_cash_balance_usd()
        positions = client.get_positions()
        # NOTE: get_daily_total_pnl includes fees (daily_realized_pnl - total_fees).
        # The raw daily_realized_pnl from Coinbase EXCLUDES fees, which can hide
        # real daily losses from the circuit breaker. We sum fees from filled
        # orders separately and combine.
        daily_price_pnl = client.get_daily_realized_pnl()
        daily_fees = client.get_daily_total_fees()
        daily_total_pnl = daily_price_pnl - daily_fees
    except Exception as e:
        logger.error("Failed to fetch account state from Coinbase: %s", e)
        risk_mgr.alerts.warning(f"Account fetch failed: {e}")
        return state

    # Use available_margin (actual Coinbase cash) + unrealized for equity.
    unrealized_sum = sum(p.unrealized_pnl_usd for p in positions.values())
    equity = cash + unrealized_sum

    # Fix 5: realized_equity uses CB's settled cash balance only (excludes
    # unrealized PnL). Passed to risk manager so circuit breaker HWM ratchets
    # only on settled gains, not on transient MTM spikes during settlement.
    # Without this separation, HWM gets locked to phantom peaks that never
    # existed as real account value (cause of Apr 13/14 cascade events).
    realized_equity = cash

    # Use the complete P&L (including fees) for risk monitoring
    daily_pnl = daily_total_pnl

    logger.info(
        f"Account: equity=${equity:,.2f} (realized=${realized_equity:,.2f}) "
        f"cash=${cash:,.2f} positions={len(positions)} "
        f"daily_pnl=${daily_pnl:+,.2f} "
        f"(price=${daily_price_pnl:+,.2f} fees=${daily_fees:,.2f})"
    )

    # --- 2. Build position snapshots (for flash crash guard) ---
    pos_notionals: dict[str, float] = {sym: p.notional_usd for sym, p in positions.items()}
    pos_snaps: dict[str, PositionSnapshot] = {
        sym: PositionSnapshot(
            symbol=sym,
            contracts=p.contracts,
            entry_price=p.entry_price,
            notional_at_entry=p.notional_usd,
        )
        for sym, p in positions.items() if p.contracts != 0
    }

    # --- 3. Update risk manager with latest account state ---
    # Fix 5: pass realized_equity separately so HWM ratchets on settled value
    # only. equity (=MTM) is still used for the DD ratio so unrealized losses
    # contribute to drawdown — see PLANNED_FIXES.md Fix 5 for the asymmetry rationale.
    risk_mgr.update_account_state(
        equity=equity,
        positions=pos_notionals,
        daily_realized_pnl=daily_pnl,
        position_snapshots=pos_snaps,
        realized_equity=realized_equity,
    )

    # NOTE: Dashboard-visible state (cash/positions/equity_curve) is written at
    # the END of the tick via _resync_state_post_orders(), after any new fills
    # have settled on Coinbase. Writing here would snapshot the pre-tick view
    # and the saved state file would always lag one tick behind reality (e.g.
    # showing positions={} right after a BUY because we queried *before* the
    # order was placed).

    # --- 4. Check if risk manager has halted us ---
    if risk_mgr.halted:
        logger.error("Risk manager HALTED: %s", risk_mgr.halt_reason)
        return state

    # --- 5. Build portfolio state for strategy ---
    # Live instances: strategy sees real Coinbase positions.
    # Paper instances: strategy sees its OWN simulated positions from
    # sim_open_lots so it makes decisions independent of the sibling
    # live account that shares the same Coinbase credentials.
    if dry_run:
        sim_lots = state.get("sim_open_lots") or {}
        sim_pos = {}
        sim_entries = {}
        for sym, lot in sim_lots.items():
            oc, oe, ocs = lot
            if oc > 1e-12:
                sim_pos[sym] = round(oc * ocs * oe, 4)
                sim_entries[sym] = oe
        sim_exposure = sum(abs(v) for v in sim_pos.values())
        sim_equity = float(state.get("initial_equity", STARTING_CAPITAL)) + float(
            state.get("cumulative_realized_pnl_total", 0.0)
        ) - float(state.get("cumulative_fees_total", 0.0))
        # Add unrealized from current prices vs sim entries
        for sym, lot in sim_lots.items():
            try:
                mark = client.fetch_current_price(sym)
                sim_equity += (mark - lot[1]) * lot[0] * lot[2]
            except Exception:
                pass
        sim_cash = sim_equity - sim_exposure
        portfolio = PortfolioState(
            cash=sim_cash,
            positions=sim_pos,
            entry_prices=sim_entries,
            equity=sim_equity,
            timestamp=now_ms,
        )
        logger.info(
            f"Paper sim state: equity=${sim_equity:,.2f} positions={sim_pos} "
            f"entries={sim_entries}"
        )
    else:
        entry_prices = {sym: p.entry_price for sym, p in positions.items()}
        portfolio = PortfolioState(
            cash=cash,
            positions=pos_notionals,
            entry_prices=entry_prices,
            equity=equity,
            timestamp=now_ms,
        )

    bar_data: dict[str, BarData] = {}
    for symbol in symbols:
        try:
            # Fetch enough bars for strategy history buffer
            start_ms = now_ms - (LOOKBACK_BARS + 10) * interval_ms
            candles = client.fetch_candles(symbol, interval, start_ms, now_ms)
        except Exception as e:
            logger.error("Failed to fetch %s candles: %s", symbol, e)
            continue

        if candles is None or len(candles) == 0:
            continue

        # Check stale-data guard
        latest_ts = int(candles["timestamp"].iloc[-1])
        age_ms = now_ms - latest_ts
        max_age_ms = risk_mgr.config.data_guard.stale_candle_max_age_intervals * interval_ms
        if age_ms > max_age_ms:
            risk_mgr.alerts.warning(
                f"{symbol} candle is {age_ms/60000:.1f} min old — skipping tick",
                symbol=symbol,
            )
            continue

        # De-dup by bar timestamp
        last_seen = state["last_bar_ts"].get(symbol, 0)
        if latest_ts <= last_seen:
            logger.info("%s: already processed bar %d", symbol, latest_ts)
            continue

        # Add funding rate (hourly from Coinbase, expressed as 8h equivalent)
        try:
            funding = client.fetch_funding_rate(symbol)
        except Exception:
            funding = 0.0
        candles = candles.copy()
        candles["funding_rate"] = funding

        latest = candles.iloc[-1]
        bar_data[symbol] = BarData(
            symbol=symbol,
            timestamp=latest_ts,
            open=float(latest["open"]),
            high=float(latest["high"]),
            low=float(latest["low"]),
            close=float(latest["close"]),
            volume=float(latest["volume"]),
            funding_rate=funding,
            history=candles,
        )

        state["last_bar_ts"][symbol] = latest_ts

    if not bar_data:
        logger.info("No new bar data for any symbol — nothing to do")
        return state

    # --- 6. Run strategy to get signals ---
    try:
        signals: list[Signal] = strategy.on_bar(bar_data, portfolio)
    except Exception as e:
        logger.error("Strategy.on_bar raised: %s", e, exc_info=True)
        risk_mgr.alerts.warning(f"Strategy error: {e}")
        return state

    if not signals:
        logger.info("Strategy returned no signals — nothing to do")
        return state

    logger.info("Strategy returned %d signals", len(signals))

    # --- 7. Route each signal through RiskManager then execute ---
    for signal in signals:
        symbol = signal.symbol
        target = signal.target_position

        verdict = risk_mgr.check_signal(symbol, target)
        if not verdict.allowed:
            logger.warning(
                f"BLOCKED by RiskManager: {symbol} ${target:+,.0f} — {verdict.reason}"
            )
            continue

        effective_target = verdict.target_notional if verdict.target_notional is not None else target

        if dry_run:
            # --- PAPER SIM: compute delta from sim_open_lots directly ---
            # Don't call place_market_order (which doesn't know sim state
            # and would return the full order size, causing accumulation).
            trade_record = _execute_paper_sim(
                state, client, symbol, effective_target, now_ms
            )
            if trade_record is None:
                continue
            trade_record["signal_metadata"] = signal.metadata if hasattr(signal, "metadata") else None
        else:
            # --- LIVE: place real order on Coinbase ---
            try:
                # Fix 1: pass skip_tolerance from risk config so live and paper
                # use the same threshold and stay in sync.
                result = client.place_market_order(
                    symbol=symbol,
                    target_notional_usd=effective_target,
                    dry_run=False,
                    skip_tolerance_usd=risk_mgr.config.order_safety.skip_tolerance_usd,
                )
            except Exception as e:
                logger.error("Order placement exception: %s", e, exc_info=True)
                risk_mgr.alerts.order_error(f"Order exception: {e}", symbol=symbol)
                continue

            if not result.success:
                logger.error("Order failed: %s — %s", symbol, result.error_message)
                risk_mgr.alerts.order_error(
                    f"Order failed: {result.error_message}",
                    symbol=symbol,
                    target=effective_target,
                )
                continue

            if result.side == "SKIP":
                logger.info("%s: already at target, skip", symbol)
                continue

            # --- Reconciliation: get ACTUAL fill details from Coinbase ---
            # place_market_order uses the pre-order price as fill_price.
            # The real execution price and fee come from get_order() after
            # the fill settles. This closes the gap between "what we
            # recorded" and "what actually happened."
            actual_price = result.fill_price
            actual_fee = 0.0
            actual_contracts = abs(result.contracts)
            if result.order_id:
                time.sleep(0.5)  # brief pause for Coinbase settlement
                ap, af, ac = client.get_order_fill_details(result.order_id)
                if ap > 0:
                    actual_price = ap
                    logger.info(
                        f"Reconciled {symbol}: price ${result.fill_price:,.2f} → "
                        f"${ap:,.2f}, fee ${af:.4f}"
                    )
                if af > 0:
                    actual_fee = af
                if ac > 0:
                    actual_contracts = ac

            # Compute notional from ACTUAL fill price
            spec = client._product_specs.get(symbol)
            cs = spec.contract_size if spec else 0.01
            actual_notional = actual_contracts * cs * actual_price
            signed_notional = actual_notional if result.contracts >= 0 else -actual_notional

            # Compute P&L from ACTUAL Coinbase entry price (pre-order)
            # and actual fill price (post-order reconciliation).
            # This replaces the FIFO sim_open_lots estimate with real data.
            result.fill_price = actual_price
            result.notional_usd = signed_notional
            result.fee_usd = actual_fee

            # Still update sim_open_lots for position tracking
            _, _, target_notional = _enrich_trade_fields(
                state, symbol, result, dry_run
            )

            # Actual PnL from Coinbase entry vs fill price
            trade_pnl = 0.0
            pre_order_pos = positions.get(symbol)
            if pre_order_pos and pre_order_pos.contracts != 0:
                cb_entry = pre_order_pos.entry_price
                # Closing/reducing: PnL = (exit - entry) * closed_qty * cs
                if (pre_order_pos.contracts > 0 and result.contracts < 0) or \
                   (pre_order_pos.contracts < 0 and result.contracts > 0):
                    close_qty = min(abs(pre_order_pos.contracts), actual_contracts)
                    if pre_order_pos.contracts > 0:
                        trade_pnl = close_qty * cs * (actual_price - cb_entry)
                    else:
                        trade_pnl = close_qty * cs * (cb_entry - actual_price)

            trade_record = {
                "ts": now_ms,
                "time": datetime.now(timezone.utc).isoformat(),
                "symbol": symbol,
                "action": f"{result.side}_LIVE",
                "price": round(actual_price, 6),
                "size": round(signed_notional, 4),
                "target_pos": round(target_notional, 2),
                "pnl": round(trade_pnl, 4),
                "fee": round(actual_fee, 4),
                "contracts": result.contracts,
                "notional_usd": round(abs(signed_notional), 4),
                "fill_price": round(actual_price, 6),
                "fee_usd": round(actual_fee, 4),
                "order_id": result.order_id,
                "dry_run": False,
                "signal_metadata": signal.metadata if hasattr(signal, "metadata") else None,
            }

        state["trade_log"].append(trade_record)
        log_trade(trade_record, PROJECT_ROOT / "live" / "logs")

        side_label = trade_record["action"].split("_")[0]
        contracts_val = abs(trade_record.get("contracts", 0))
        price_val = trade_record.get("price", 0)
        notional_val = abs(trade_record.get("notional_usd", trade_record.get("size", 0)))
        tag = " [DRY RUN]" if dry_run else ""
        logger.info(
            f"{side_label} {symbol} {contracts_val} contracts "
            f"(${notional_val:+,.2f} notional) @ ${price_val:,.2f}{tag}"
        )

        # Telegram alert for LIVE trades only
        if not dry_run:
            pnl_str = f" PnL: ${trade_record.get('pnl', 0):+,.2f}" if trade_record.get("pnl", 0) != 0 else ""
            risk_mgr.alerts.trade(
                f"{side_label} {symbol} {contracts_val} contracts "
                f"(${notional_val:,.2f}) @ ${price_val:,.2f}{pnl_str}",
                symbol=symbol,
                side=side_label,
                contracts=contracts_val,
                notional=notional_val,
                price=price_val,
                pnl=trade_record.get("pnl", 0),
                fee=trade_record.get("fee", 0),
            )

    return state


def show_status(state_file: Path, client: CoinbaseClient) -> None:
    """Print current state for operator inspection."""
    print("=" * 70)
    print("  LIVE TRADER STATUS")
    print("=" * 70)

    if state_file.exists():
        with open(state_file) as f:
            state = json.load(f)
        ec = state.get("equity_curve", [])
        start = state.get("initial_equity", 0)
        current = ec[-1]["equity"] if ec else 0
        peak = state.get("peak_equity", 0)
        print(f"  Started        : {state.get('started_at', '?')}")
        print(f"  Initial equity : ${start:,.2f}")
        print(f"  Current equity : ${current:,.2f}")
        print(f"  Peak equity    : ${peak:,.2f}")
        print(f"  Trades logged  : {len(state.get('trade_log', []))}")
    else:
        print("  No state file yet.")

    print()
    print("  Live Coinbase state:")
    print(f"    Equity    : ${client.get_equity_usd():,.2f}")
    print(f"    Cash      : ${client.get_cash_balance_usd():,.2f}")
    positions = client.get_positions()
    print(f"    Positions : {len(positions)}")
    for sym, p in positions.items():
        print(f"      {sym}: {p.contracts:+.0f} contracts "
              f"(${p.notional_usd:+,.2f}) "
              f"entry=${p.entry_price:,.2f} "
              f"mark=${p.mark_price:,.2f}")


def main():
    parser = argparse.ArgumentParser(description="Live crypto trading on Coinbase perps")
    parser.add_argument("--strategy", default="strategies/30m-concentrated/strategy.py",
                        help="Path to strategy file")
    parser.add_argument("--interval", default="30m", choices=list(INTERVAL_CONFIG.keys()))
    parser.add_argument("--symbols", nargs="+", default=["BTC", "ETH", "SOL"])
    parser.add_argument("--once", action="store_true", help="Run one tick and exit (for cron)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Log orders but don't submit them to Coinbase")
    parser.add_argument("--status", action="store_true", help="Print status and exit")
    parser.add_argument("--instance", default="live",
                        help="Instance label (separates state file from other concurrent instances). "
                             "Default 'live'. Use 'paper-cb-early' for an earlier-timing dry-run variant.")
    args = parser.parse_args()

    # Resolve strategy path (relative to project root if not absolute)
    strategy_path = args.strategy
    if not os.path.isabs(strategy_path):
        strategy_path = str(PROJECT_ROOT / strategy_path)

    # Override dry_run from env var if set (safety gate for ops)
    dry_run = args.dry_run or os.environ.get("LIVE_TRADER_DRY_RUN", "").lower() in ("1", "true", "yes")

    # Build clients
    client = CoinbaseClient(dry_run_default=dry_run)

    if args.status:
        state_file = derive_live_state_path(strategy_path, args.instance)
        show_status(state_file, client)
        return

    # Load state FIRST so we can restore cross-tick risk state.
    # The circuit breaker's high-water mark and 24h rolling equity window
    # must persist across cron fires — otherwise each fresh process sees
    # only a single data point and the drawdown check never fires.
    state_file = derive_live_state_path(strategy_path, args.instance)
    initial_equity = client.get_equity_usd()
    state = load_state(state_file, args.interval, initial_equity)

    # Build risk manager, then RESTORE circuit breaker history from state
    risk_mgr = RiskManager.from_config(initial_equity=initial_equity, config_path=os.environ.get("RISK_CONFIG_PATH"))

    # Restore high-water mark from persisted state (fix for cross-tick CB bug)
    state_peak = float(state.get("peak_equity", initial_equity))
    risk_mgr.circuit_breaker.high_water = max(state_peak, initial_equity)

    # Restore 24h rolling equity window from the state's equity_curve
    from risk.circuit_breaker import EquityPoint
    now_sec = time.time()
    cutoff_sec = now_sec - 24 * 3600
    equity_history_restored = 0
    for point in state.get("equity_curve", []):
        ts_ms = point.get("ts", 0)
        ts_sec = ts_ms / 1000 if ts_ms else 0
        if ts_sec >= cutoff_sec:
            eq = point.get("equity", 0)
            risk_mgr.circuit_breaker.equity_history.append(EquityPoint(ts_sec, eq))
            equity_history_restored += 1

    logger.info(
        f"Risk state restored: high_water=${risk_mgr.circuit_breaker.high_water:,.2f} "
        f"(from peak_equity={state_peak:,.2f}), "
        f"24h window={equity_history_restored} points"
    )

    # Wire flash crash guard to the live price feed
    emergency_exit_called = [False]  # closure-captured flag
    def emergency_exit(reason: str):
        logger.critical("EMERGENCY EXIT TRIGGERED: %s", reason)
        emergency_exit_called[0] = True
        # Flatten all positions via market orders
        for sym in args.symbols:
            try:
                result = client.place_market_order(sym, 0.0, dry_run=dry_run)
                logger.info("Emergency close %s: %s", sym, result.side)
            except Exception as e:
                logger.critical("Emergency close FAILED for %s: %s", sym, e)

    risk_mgr.wire_flash_crash_guard(
        price_fetcher=client.fetch_current_price,
        emergency_exit=emergency_exit,
    )

    # Load strategy
    strategy = load_strategy(strategy_path)
    logger.info(f"Loaded strategy: {type(strategy).__name__}")
    logger.info(f"Instance: {args.instance}")
    logger.info(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    logger.info(f"Symbols: {args.symbols}")
    logger.info(f"Interval: {args.interval}")
    logger.info(f"Initial equity: ${initial_equity:,.2f}")

    # Alert startup (include instance label so we can tell them apart in Telegram)
    # Only send Telegram alerts for LIVE instances — paper/sim are too noisy
    if dry_run:
        risk_mgr.alerts.config.telegram_enabled = False

    risk_mgr.alerts.dispatch(Alert(
        AlertType.STARTUP,
        f"[{args.instance}] started ({'DRY RUN' if dry_run else 'LIVE'})",
        data={
            "instance": args.instance,
            "strategy": Path(strategy_path).parent.name,
            "equity": f"${initial_equity:,.2f}",
            "high_water": f"${risk_mgr.circuit_breaker.high_water:,.2f}",
            "symbols": ",".join(args.symbols),
        },
    ))

    # state_file and state were loaded earlier (before risk manager build)
    if args.once:
        # Single tick mode (cron)
        try:
            state = run_one_tick(strategy, client, risk_mgr, state,
                                 args.symbols, args.interval, dry_run)
        finally:
            # Wait for Coinbase to settle fills into the cash balance before
            # querying. Without this delay, available_margin reads a mid-
            # settlement value that's $20-30 below the fully-settled number.
            time.sleep(10)  # 10s settlement delay (was 5s — caused false circuit breaker at 18:14 Apr 13)
            _resync_state_post_orders(state, client)
            save_state(state, state_file)
            risk_mgr.stop()
        return

    # Continuous mode (not used in cron, but useful for local testing)
    logger.info("Starting continuous mode. Ctrl+C to stop.")
    risk_mgr.start()
    try:
        while True:
            try:
                state = run_one_tick(strategy, client, risk_mgr, state,
                                     args.symbols, args.interval, dry_run)
                _resync_state_post_orders(state, client)
                save_state(state, state_file)
            except Exception as e:
                logger.error("Tick error: %s", e, exc_info=True)
            # Sleep until next bar
            time.sleep(60 * 5)  # 5 min poll in continuous mode
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        risk_mgr.stop()
        _resync_state_post_orders(state, client)
        save_state(state, state_file)


if __name__ == "__main__":
    main()
