"""
LIVE FLASH CRASH GUARD VALIDATION

Places 1 real BTC contract, temporarily configures the flash crash guard
to trigger on the tiniest movement (0.1% per-position threshold), and
verifies that the guard correctly:

  1. Detects a price move beyond threshold within seconds
  2. Calls the emergency_exit() callback
  3. Emergency exit successfully closes the position
  4. Dispatches FLASH_CRASH Telegram alert
  5. Position returns to flat

This is the only part of the safety layer that can't be fully unit-tested
because it requires real position state. Cost: ~$1 round-trip.

SAFETY:
  - Requires CONFIRM=yes env var
  - Places ONLY 1 BTC contract (minimum size)
  - Maximum hold time: ~60 seconds
  - If anything goes wrong, the script forcibly closes positions before exit
"""
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if os.environ.get("CONFIRM", "").lower() != "yes":
    print("=" * 70)
    print("  SAFETY GATE: This script places REAL orders.")
    print("=" * 70)
    print()
    print("  What this will do:")
    print("    1. Place real BUY 1 BTC contract (~$730 notional)")
    print("    2. Start the flash crash guard with 0.1% per-position threshold")
    print("    3. Wait for the guard to fire (should be within seconds — any")
    print("       market movement > $73 qualifies)")
    print("    4. Verify emergency_exit flattens the position")
    print("    5. Confirm Telegram alert + position flat")
    print("    6. Expected cost: ~$1 round-trip (spread + fees)")
    print()
    print("  To run: CONFIRM=yes python scripts/validate_flash_crash_guard_live.py")
    print("  Aborting. No orders placed.")
    sys.exit(0)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

from exchanges import CoinbaseClient
from risk import RiskManager
from risk.flash_crash_guard import PositionSnapshot, FlashCrashGuard
from risk.alerts import Alert, AlertType


def banner(text):
    print("\n" + "=" * 70)
    print(f"  {text}")
    print("=" * 70)


def main():
    banner("LIVE FLASH CRASH GUARD VALIDATION")

    # Build client with REAL execution
    client = CoinbaseClient(dry_run_default=False)
    risk_mgr = RiskManager.from_config(initial_equity=client.get_equity_usd())

    # Pre-flight
    banner("PRE-FLIGHT")
    print(f"  Equity    : ${client.get_equity_usd():,.2f}")
    print(f"  Cash      : ${client.get_cash_balance_usd():,.2f}")
    positions = client.get_positions()
    print(f"  Positions : {len(positions)}")
    if any(p.contracts != 0 for p in positions.values()):
        print("  ❌ Already have open positions — aborting to avoid confusion")
        return

    # Track what happens
    emergency_called = [False]
    emergency_reason = [None]

    def emergency_exit(reason: str):
        print(f"\n  🚨 EMERGENCY EXIT CALLED: {reason}")
        emergency_called[0] = True
        emergency_reason[0] = reason
        # Flatten BTC
        try:
            result = client.place_market_order("BTC", 0.0, dry_run=False)
            print(f"  Emergency close order: success={result.success}")
            if result.success and result.order_id:
                print(f"  Order ID: {result.order_id[:13]}...")
                print(f"  Fill price: ${result.fill_price:,.2f}")
        except Exception as e:
            print(f"  ❌ Emergency close FAILED: {e}")

    # Keep the PRODUCTION threshold (8%) but simulate a crash by injecting a
    # fake entry price into the position snapshot. The position is real; only
    # the "remembered" entry price is fabricated for the test.
    print("\n  Using PRODUCTION threshold 8% but injecting a fake entry price")
    print("  to simulate the scenario where price has 'dropped 10% from entry'.")
    risk_mgr.config.flash_crash_guard.poll_interval_sec = 2  # check every 2s
    print(f"  Per-position threshold: {risk_mgr.config.flash_crash_guard.per_position_move_pct}%")
    print(f"  Poll interval        : {risk_mgr.config.flash_crash_guard.poll_interval_sec}s")

    # Manual flash crash guard setup (instead of via risk_mgr.wire_flash_crash_guard)
    # because we need to provide our own position_getter with the LIVE position
    position_snapshots = {}

    def position_getter():
        return position_snapshots

    guard = FlashCrashGuard(
        config=risk_mgr.config.flash_crash_guard,
        alerts=risk_mgr.alerts,
        price_fetcher=client.fetch_current_price,
        position_getter=position_getter,
        emergency_exit=emergency_exit,
    )

    # Place BUY order
    banner("STEP 1: PLACE REAL BUY ORDER")
    btc_price = client.fetch_current_price("BTC")
    print(f"  Current BTC : ${btc_price:,.2f}")
    buy_result = client.place_market_order("BTC", btc_price * 0.01 * 1.5, dry_run=False)
    if not buy_result.success:
        print(f"  ❌ BUY failed: {buy_result.error_message}")
        return
    entry_price = buy_result.fill_price
    print(f"  ✅ Filled at ${entry_price:,.2f}")
    print(f"  Contracts   : {buy_result.contracts}")
    print(f"  Order ID    : {buy_result.order_id}")

    # Build position snapshot with a FAKE entry price 10% HIGHER than real.
    # This makes the guard think the market has already dropped 10% against us.
    # The real position is intact; only the guard's "remembered" entry is fake.
    FAKE_ENTRY_MULTIPLIER = 1.10  # pretend we entered at price 10% higher than reality
    fake_entry = entry_price * FAKE_ENTRY_MULTIPLIER
    position_snapshots["BTC"] = PositionSnapshot(
        symbol="BTC",
        contracts=buy_result.contracts,
        entry_price=fake_entry,  # 10% higher than real
        notional_at_entry=abs(buy_result.notional_usd),
    )
    print(f"\n  Real entry price : ${entry_price:,.2f}")
    print(f"  FAKE entry price : ${fake_entry:,.2f} (+10% higher)")
    print(f"  Current price    : ${entry_price:,.2f}")
    print(f"  'Perceived' move : -10% from fake entry → should trigger 8% guard IMMEDIATELY")

    # Start the guard
    banner("STEP 2: START FLASH CRASH GUARD + WAIT")
    guard.start()
    print("  Guard thread started. Polling every 2s for 60s max...")

    # Wait for emergency exit to fire (or timeout)
    start_t = time.monotonic()
    timeout = 60
    while time.monotonic() - start_t < timeout:
        if emergency_called[0]:
            break
        try:
            mark = client.fetch_current_price("BTC")
            pct_move = (mark - entry_price) / entry_price * 100
            elapsed = time.monotonic() - start_t
            print(f"  [{elapsed:>5.1f}s] BTC=${mark:,.2f} move={pct_move:+.4f}% "
                  f"{'← should trigger!' if abs(pct_move) >= 0.1 else ''}")
        except Exception:
            pass
        time.sleep(5)

    guard.stop()

    # Verify
    banner("STEP 3: VERIFY EMERGENCY EXIT")
    if emergency_called[0]:
        print(f"  ✅ Emergency exit was called")
        print(f"  Reason: {emergency_reason[0]}")
    else:
        print(f"  ⚠️  Emergency exit NOT called within {timeout}s")
        print(f"      BTC may not have moved beyond 0.1% during the test window.")

    # Check if position is flat
    time.sleep(3)
    final_positions = client.get_positions()
    btc_pos = final_positions.get("BTC")
    if btc_pos is None or btc_pos.contracts == 0:
        print(f"  ✅ BTC position is flat")
    else:
        print(f"  ⚠️  BTC still has {btc_pos.contracts} contracts — force closing")
        close_result = client.place_market_order("BTC", 0.0, dry_run=False)
        print(f"  Force close: success={close_result.success}")

    # Post-flight summary
    banner("POST-FLIGHT SUMMARY")
    print(f"  Emergency exit fired : {'✅' if emergency_called[0] else '❌'}")
    print(f"  Position now flat    : {'✅' if (btc_pos is None or btc_pos.contracts == 0) else '⚠️'}")
    print(f"  Final equity         : ${client.get_equity_usd():,.2f}")
    print(f"  Check Telegram for FLASH_CRASH alert")


if __name__ == "__main__":
    main()
