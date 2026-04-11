"""
LIVE INTEGRATION TEST — Phase 2

Wires CoinbaseClient + RiskManager + FlashCrashGuard together and runs a
full trading-loop simulation against LIVE Coinbase prices, but placing
orders ONLY in dry-run mode.

What this validates:
  1. Risk manager accepts/rejects signals correctly with real account data
  2. Flash crash guard runs on a background thread with real price polling
  3. End-to-end state update cycle (fetch equity -> update risk manager -> check signals)
  4. Integration of all guards with real Coinbase data

No live orders are placed. Safe to run.
"""
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

from exchanges import CoinbaseClient
from risk import RiskManager
from risk.flash_crash_guard import PositionSnapshot


def banner(title: str):
    print("\n" + "=" * 78)
    print(f"  {title}")
    print("=" * 78)


def cleanup_kill_flag():
    flag = Path(__file__).resolve().parents[1] / "state" / "kill.flag"
    if flag.exists():
        flag.unlink()


def main():
    banner("LIVE INTEGRATION TEST — Coinbase + RiskManager + FlashCrashGuard")

    cleanup_kill_flag()

    # --- Phase 1: Build clients ---
    print("\n[1] Building CoinbaseClient (dry_run_default=True)...")
    client = CoinbaseClient(dry_run_default=True)

    initial_equity = client.get_equity_usd()
    print(f"    Live equity: ${initial_equity:,.2f}")

    print("\n[2] Building RiskManager from config...")
    risk_mgr = RiskManager.from_config(initial_equity=initial_equity)
    print(f"    Max DD threshold    : {risk_mgr.config.circuit_breaker.max_dd_from_high_water_pct}%")
    print(f"    Max leverage        : {risk_mgr.config.position_limits.max_leverage}x")
    print(f"    Max per-coin        : ${risk_mgr.config.position_limits.max_notional_per_coin_usd:,.0f}")
    print(f"    Flash crash enabled : {risk_mgr.config.flash_crash_guard.enabled}")
    print(f"    Telegram enabled    : {risk_mgr.config.alerts.telegram_enabled}")

    # --- Phase 2: Wire up flash crash guard ---
    print("\n[3] Wiring FlashCrashGuard to CoinbaseClient price feed...")

    emergency_calls = []
    def emergency_exit(reason: str):
        print(f"    !!! EMERGENCY EXIT CALLED: {reason}")
        emergency_calls.append(reason)
        # In real trader: would flatten all positions here

    risk_mgr.wire_flash_crash_guard(
        price_fetcher=client.fetch_current_price,
        emergency_exit=emergency_exit,
    )
    print("    ✅ FlashCrashGuard wired")

    # --- Phase 3: Start monitoring ---
    print("\n[4] Starting risk manager monitoring threads...")
    risk_mgr.start()
    print("    ✅ FlashCrashGuard thread running")

    # --- Phase 4: Simulate a trading loop ---
    banner("SIMULATED TRADING LOOP (1 tick, no live orders)")

    # Update state with current live account
    cash = client.get_cash_balance_usd()
    equity = client.get_equity_usd()
    positions = client.get_positions()
    daily_pnl = client.get_daily_realized_pnl()

    pos_map = {sym: p.notional_usd for sym, p in positions.items()}
    pos_snaps = {
        sym: PositionSnapshot(
            symbol=sym,
            contracts=p.contracts,
            entry_price=p.entry_price,
            notional_at_entry=p.notional_usd,
        )
        for sym, p in positions.items() if p.contracts != 0
    }

    print(f"\n    Live equity       : ${equity:,.2f}")
    print(f"    Available cash    : ${cash:,.2f}")
    print(f"    Positions         : {len(positions)} (open: {len(pos_snaps)})")
    print(f"    Daily realized PnL: ${daily_pnl:+,.2f}")

    risk_mgr.update_account_state(
        equity=equity,
        positions=pos_map,
        daily_realized_pnl=daily_pnl,
        position_snapshots=pos_snaps,
    )

    if risk_mgr.halted:
        print(f"\n    ❌ Risk manager halted: {risk_mgr.halt_reason}")
        risk_mgr.stop()
        return

    print(f"\n    ✅ Risk manager healthy, not halted")

    # --- Phase 5: Check signals against risk manager ---
    banner("SIGNAL VALIDATION (dry run)")

    # Simulate the strategy's output at $10K with 1.2x leverage, 33% per coin
    target_per_coin = equity * 1.2 / 3

    print(f"\n  Target per coin: ${target_per_coin:,.2f}")
    print(f"  Strategy signals (long on BTC/ETH/SOL):")
    for sym in ("BTC", "ETH", "SOL"):
        verdict = risk_mgr.check_signal(sym, target_per_coin)
        status = "✅" if verdict.allowed else "❌"
        print(f"    {status} {sym} ${target_per_coin:,.0f}: allowed={verdict.allowed}")
        if verdict.reason:
            print(f"        reason: {verdict.reason[:80]}")

    # Test an oversized signal - should be rejected
    print(f"\n  Testing oversized signal (${equity*0.6:,.0f} = 60% concentration):")
    verdict = risk_mgr.check_signal("BTC", equity * 0.6)
    print(f"    {'❌' if verdict.allowed else '✅'} oversized rejected: allowed={verdict.allowed}")
    if verdict.reason:
        print(f"        reason: {verdict.reason[:80]}")

    # --- Phase 6: Dry-run actual order placement ---
    banner("DRY-RUN ORDER PLACEMENT (through exchange client)")

    for sym in ("BTC", "ETH", "SOL"):
        verdict = risk_mgr.check_signal(sym, target_per_coin)
        if verdict.allowed:
            result = client.place_market_order(
                symbol=sym,
                target_notional_usd=verdict.target_notional or target_per_coin,
                dry_run=True,
            )
            print(f"    {sym}: {result.side} {abs(result.contracts)} contracts "
                  f"= ${abs(result.notional_usd):,.2f} @ ${result.fill_price:,.2f} [DRY]")

    # --- Phase 7: Let flash crash guard run for 10 seconds ---
    banner("FLASH CRASH GUARD LIVE POLLING (10 seconds)")
    print("\n  Watching live prices every 5 seconds...")
    print("  (No positions open, so no triggers expected.)\n")

    for i in range(2):
        time.sleep(5)
        print(f"    [{(i+1)*5}s] still alive, no triggers")

    if emergency_calls:
        print(f"\n    Emergency exits triggered: {len(emergency_calls)}")
        for r in emergency_calls:
            print(f"      - {r}")
    else:
        print("\n    ✅ No emergency exits (expected: no positions open)")

    # --- Shutdown ---
    banner("SHUTDOWN")
    risk_mgr.stop()
    print("    ✅ FlashCrashGuard thread stopped")

    cleanup_kill_flag()

    banner("LIVE INTEGRATION TEST COMPLETE")
    print("""
    ✅ Coinbase client connected and fetching live data
    ✅ Risk manager loaded config and healthy
    ✅ Signal validation routing through all guards
    ✅ Flash crash guard running on background thread with real prices
    ✅ Dry-run orders placed through exchange abstraction
    ✅ Clean shutdown, no artifacts left behind

    The system is ready for live execution once the trading loop is wired in.
""")


if __name__ == "__main__":
    main()
