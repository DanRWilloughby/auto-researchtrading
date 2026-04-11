"""
LIVE ORDER SMOKE TEST — places ONE real BTC perp contract order.

This is a one-time validation that the full order execution path works:
  1. Place market BUY for 1 BTC perp contract (~$731 notional, ~$180 margin)
  2. Wait 60 seconds
  3. Verify the position shows up in get_positions()
  4. Close the position with a market SELL
  5. Verify the position is back to zero
  6. Report realized P&L and fees

WARNING: This places REAL orders. Money will be moved. The expected
loss is ~1-5 bps of slippage + spread on ~$1,462 of round-trip notional
(~$0.15-$0.75). Worst case if something goes wrong, the whole $731 is
at risk to market movement while the position is open.

SAFETY:
  - Requires explicit CONFIRM=yes environment variable to actually run
  - Will not execute without it
  - Will only trade ONE contract, hardcoded
  - Only trades BTC
  - 60-second hold max, then forced close attempt
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from exchanges import CoinbaseClient

CONFIRM = os.environ.get("CONFIRM", "").lower()
if CONFIRM != "yes":
    print("=" * 70)
    print("  SAFETY GATE: This script places REAL orders.")
    print("=" * 70)
    print()
    print("  To actually run it, you must export CONFIRM=yes first:")
    print()
    print("      CONFIRM=yes python scripts/smoke_test_live_order.py")
    print()
    print("  What this will do:")
    print("    1. Place market BUY for 1 BTC perp contract (~$731 notional)")
    print("    2. Hold for 60 seconds")
    print("    3. Place market SELL to close")
    print("    4. Report realized P&L, fees, and position tracking")
    print()
    print("  Expected cost: ~$0.15-0.75 in spread + fees (~1-5 bps)")
    print("  Maximum risk: ~$731 notional briefly exposed to market moves")
    print()
    print("  Aborting. No orders placed.")
    sys.exit(0)


def banner(text):
    print("\n" + "=" * 70)
    print(f"  {text}")
    print("=" * 70)


def main():
    banner("LIVE ORDER SMOKE TEST — 1 BTC PERP CONTRACT")
    print("  Confirm=yes detected. Proceeding with LIVE order.\n")

    # IMPORTANT: dry_run_default=False for this test, but we still call
    # place_market_order with explicit dry_run=False on each call.
    client = CoinbaseClient(dry_run_default=False)

    # Pre-flight state
    banner("PRE-FLIGHT: ACCOUNT STATE")
    cash_before = client.get_cash_balance_usd()
    equity_before = client.get_equity_usd()
    pos_before = client.get_positions()
    btc_price = client.fetch_current_price("BTC")

    print(f"  Cash     : ${cash_before:,.2f}")
    print(f"  Equity   : ${equity_before:,.2f}")
    print(f"  BTC price: ${btc_price:,.2f}")
    print(f"  Positions: {len(pos_before)}")
    for sym, p in pos_before.items():
        print(f"    {sym}: {p.contracts:+.0f} contracts")

    if "BTC" in pos_before and pos_before["BTC"].contracts != 0:
        print(f"\n  ⚠️  BTC position already exists. Aborting — test requires flat BTC.")
        return

    # Target: 1 BTC contract long = 0.01 BTC × price notional
    contract_notional = 0.01 * btc_price
    print(f"\n  Target order: BUY 1 BTC contract = ${contract_notional:,.2f} notional")
    print(f"  Margin required: ~${contract_notional * 0.2456:,.2f} (24.56% overnight rate)")

    # --- Place BUY order ---
    banner("STEP 1: PLACE BUY ORDER")
    print(f"  Submitting: BUY 1 BTC contract at market")
    buy_result = client.place_market_order(
        symbol="BTC",
        target_notional_usd=contract_notional * 1.5,  # Ensures we round to 1 contract
        dry_run=False,
    )
    print(f"  Success     : {buy_result.success}")
    print(f"  Order ID    : {buy_result.order_id}")
    print(f"  Side        : {buy_result.side}")
    print(f"  Contracts   : {buy_result.contracts}")
    print(f"  Notional    : ${buy_result.notional_usd:+,.2f}")
    print(f"  Fill price  : ${buy_result.fill_price:,.2f}")
    if buy_result.error_message:
        print(f"  Error       : {buy_result.error_message}")

    if not buy_result.success:
        print("\n  ❌ BUY failed — aborting. No position to close.")
        return

    # --- Wait 60 seconds ---
    banner("STEP 2: HOLD 60 SECONDS")
    for i in range(6):
        time.sleep(10)
        try:
            pos = client.get_positions().get("BTC")
            if pos:
                mark = client.fetch_current_price("BTC")
                print(f"  t+{(i+1)*10}s: {pos.contracts:+.0f} contracts, "
                      f"mark=${mark:,.2f}, uPnL=${pos.unrealized_pnl_usd:+,.2f}")
            else:
                print(f"  t+{(i+1)*10}s: no position visible yet (may be settlement lag)")
        except Exception as e:
            print(f"  t+{(i+1)*10}s: error fetching position: {e}")

    # --- Verify position ---
    banner("STEP 3: VERIFY POSITION")
    pos_mid = client.get_positions()
    if "BTC" in pos_mid and pos_mid["BTC"].contracts != 0:
        p = pos_mid["BTC"]
        print(f"  ✅ Position confirmed")
        print(f"     Contracts  : {p.contracts:+.0f}")
        print(f"     Notional   : ${p.notional_usd:+,.2f}")
        print(f"     Entry price: ${p.entry_price:,.2f}")
        print(f"     Mark price : ${p.mark_price:,.2f}")
        print(f"     uPnL       : ${p.unrealized_pnl_usd:+,.2f}")
    else:
        print(f"  ⚠️  No BTC position visible — may be API settlement lag.")
        print(f"      Will still attempt close.")

    # --- Close position ---
    banner("STEP 4: CLOSE POSITION (SELL)")
    print(f"  Submitting: SELL to flatten BTC")
    sell_result = client.place_market_order(
        symbol="BTC",
        target_notional_usd=0.0,  # flat
        dry_run=False,
    )
    print(f"  Success     : {sell_result.success}")
    print(f"  Order ID    : {sell_result.order_id}")
    print(f"  Side        : {sell_result.side}")
    print(f"  Contracts   : {sell_result.contracts}")
    print(f"  Notional    : ${sell_result.notional_usd:+,.2f}")
    print(f"  Fill price  : ${sell_result.fill_price:,.2f}")
    if sell_result.error_message:
        print(f"  Error       : {sell_result.error_message}")

    # --- Post-flight ---
    time.sleep(5)  # let settlement catch up
    banner("POST-FLIGHT: ACCOUNT STATE")
    cash_after = client.get_cash_balance_usd()
    equity_after = client.get_equity_usd()
    pos_after = client.get_positions()

    print(f"  Cash     : ${cash_after:,.2f}  (delta: ${cash_after - cash_before:+,.2f})")
    print(f"  Equity   : ${equity_after:,.2f}  (delta: ${equity_after - equity_before:+,.2f})")
    print(f"  Positions: {len(pos_after)}")
    for sym, p in pos_after.items():
        print(f"    {sym}: {p.contracts:+.0f} contracts")

    # --- Summary ---
    banner("TEST SUMMARY")
    realized_pnl = equity_after - equity_before
    entry_price = buy_result.fill_price or 0
    exit_price = sell_result.fill_price or 0
    raw_pnl = (exit_price - entry_price) * 0.01 if entry_price and exit_price else 0
    implied_costs = raw_pnl - realized_pnl

    print(f"  Entry price : ${entry_price:,.2f}")
    print(f"  Exit price  : ${exit_price:,.2f}")
    print(f"  Raw P&L     : ${raw_pnl:+,.2f} (before fees)")
    print(f"  Realized P&L: ${realized_pnl:+,.2f} (after fees)")
    print(f"  Implied cost: ${implied_costs:+,.2f}")
    if entry_price > 0:
        cost_bps = implied_costs / (entry_price * 0.01) * 10000
        print(f"  Round-trip cost: {abs(cost_bps):.1f} bps")

    if buy_result.success and sell_result.success and not any(
        p.contracts != 0 for p in pos_after.values() if p.symbol == "BTC"
    ):
        print("\n  ✅ SMOKE TEST PASSED — full execution path working")
    else:
        print("\n  ⚠️  SMOKE TEST INCOMPLETE — review output above")


if __name__ == "__main__":
    main()
