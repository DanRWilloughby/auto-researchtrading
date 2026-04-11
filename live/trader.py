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


def derive_live_state_path(strategy_path: str) -> Path:
    """Compute where live state lives for a given strategy."""
    strategy_name = Path(strategy_path).parent.name
    return PROJECT_ROOT / "live" / "state" / f"{strategy_name}_live_state.json"


def load_state(state_file: Path, interval: str, initial_equity: float) -> dict:
    if state_file.exists():
        with open(state_file) as f:
            return json.load(f)
    return {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "interval": interval,
        "initial_equity": initial_equity,
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
        equity = client.get_equity_usd()
        cash = client.get_cash_balance_usd()
        positions = client.get_positions()
        daily_pnl = client.get_daily_realized_pnl()
    except Exception as e:
        logger.error("Failed to fetch account state from Coinbase: %s", e)
        risk_mgr.alerts.warning(f"Account fetch failed: {e}")
        return state

    logger.info(
        f"Account: equity=${equity:,.2f} cash=${cash:,.2f} "
        f"positions={len(positions)} daily_pnl=${daily_pnl:+,.2f}"
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
    risk_mgr.update_account_state(
        equity=equity,
        positions=pos_notionals,
        daily_realized_pnl=daily_pnl,
        position_snapshots=pos_snaps,
    )

    # Track equity curve
    state["equity_curve"].append({"ts": now_ms, "equity": equity})
    if equity > state["peak_equity"]:
        state["peak_equity"] = equity

    # --- 4. Check if risk manager has halted us ---
    if risk_mgr.halted:
        logger.error("Risk manager HALTED: %s", risk_mgr.halt_reason)
        return state

    # --- 5. Fetch candles for strategy ---
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

        # Place the order (dry_run flag propagates to exchange client)
        try:
            result = client.place_market_order(
                symbol=symbol,
                target_notional_usd=effective_target,
                dry_run=dry_run,
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

        # Record the trade
        trade_record = {
            "ts": now_ms,
            "time": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "action": f"{result.side}_LIVE" if not dry_run else f"{result.side}_DRYRUN",
            "contracts": result.contracts,
            "notional_usd": result.notional_usd,
            "fill_price": result.fill_price,
            "fee_usd": result.fee_usd,
            "order_id": result.order_id,
            "dry_run": dry_run,
        }
        state["trade_log"].append(trade_record)
        log_trade(trade_record, PROJECT_ROOT / "live" / "logs")

        risk_mgr.alerts.trade(
            f"{result.side} {symbol} {abs(result.contracts)} contracts @ ${result.fill_price:,.2f}",
            symbol=symbol,
            side=result.side,
            contracts=abs(result.contracts),
            notional_usd=f"${abs(result.notional_usd):,.2f}",
            fill_price=f"${result.fill_price:,.2f}",
            dry_run=dry_run,
        )

        dry_tag = " [DRY RUN]" if dry_run else ""
        logger.info(
            f"{result.side} {symbol} {abs(result.contracts)} contracts "
            f"(${result.notional_usd:+,.2f} notional) @ ${result.fill_price:,.2f}{dry_tag}"
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
        state_file = derive_live_state_path(strategy_path)
        show_status(state_file, client)
        return

    # Build risk manager with live equity as baseline
    initial_equity = client.get_equity_usd()
    risk_mgr = RiskManager.from_config(initial_equity=initial_equity)

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
    logger.info(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    logger.info(f"Symbols: {args.symbols}")
    logger.info(f"Interval: {args.interval}")
    logger.info(f"Initial equity: ${initial_equity:,.2f}")

    # Alert startup
    risk_mgr.alerts.dispatch(Alert(
        AlertType.STARTUP,
        f"Live trader started ({'DRY RUN' if dry_run else 'LIVE'})",
        data={
            "strategy": Path(strategy_path).parent.name,
            "equity": f"${initial_equity:,.2f}",
            "symbols": ",".join(args.symbols),
        },
    ))

    # Load state
    state_file = derive_live_state_path(strategy_path)
    state = load_state(state_file, args.interval, initial_equity)

    if args.once:
        # Single tick mode (cron)
        try:
            state = run_one_tick(strategy, client, risk_mgr, state,
                                 args.symbols, args.interval, dry_run)
        finally:
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
                save_state(state, state_file)
            except Exception as e:
                logger.error("Tick error: %s", e, exc_info=True)
            # Sleep until next bar
            time.sleep(60 * 5)  # 5 min poll in continuous mode
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        risk_mgr.stop()
        save_state(state, state_file)


if __name__ == "__main__":
    main()
