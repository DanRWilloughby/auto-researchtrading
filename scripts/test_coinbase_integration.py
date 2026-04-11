"""
End-to-end test of the CoinbaseClient.

Exercises all read operations and runs a dry-run place_market_order
to show exactly what the strategy WOULD trade right now — without
submitting anything live.

Run from repo root:
    source .venv/bin/activate
    python scripts/test_coinbase_integration.py
"""
import logging
import sys
import time
from pathlib import Path

# Add repo root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

from exchanges import CoinbaseClient


def banner(title: str):
    print("\n" + "=" * 78)
    print(f"  {title}")
    print("=" * 78)


def main():
    banner("COINBASE CLIENT INTEGRATION TEST")

    client = CoinbaseClient(dry_run_default=True)
    print(f"Client: {client.name}")

    # Contract specs
    banner("1. CONTRACT SPECS")
    specs = client.contract_specs()
    for sym, spec in specs.items():
        print(f"  {sym:4s} -> {spec.product_id:20s} "
              f"| size={spec.contract_size} "
              f"| overnight_margin={spec.overnight_margin_rate:.4f} "
              f"| intraday_margin={spec.intraday_margin_rate:.4f}")

    # Current prices
    banner("2. LIVE PRICES")
    prices: dict[str, float] = {}
    for sym in ("BTC", "ETH", "SOL"):
        p = client.fetch_current_price(sym)
        prices[sym] = p
        notional = specs[sym].contract_size * p
        print(f"  {sym:4s}  ${p:>12,.2f}  | 1 contract = ${notional:>10,.2f} notional")

    # Funding rates
    banner("3. FUNDING RATES (8h equivalent)")
    for sym in ("BTC", "ETH", "SOL"):
        f = client.fetch_funding_rate(sym)
        annualized = f * 3 * 365 * 100
        print(f"  {sym:4s}  {f*10000:>+8.2f} bps per 8h   |  {annualized:>+7.2f}% annualized (if held constant)")

    # Account state
    banner("4. ACCOUNT STATE")
    cash = client.get_cash_balance_usd()
    equity = client.get_equity_usd()
    positions = client.get_positions()
    print(f"  Cash available : ${cash:>12,.2f}")
    print(f"  Total equity   : ${equity:>12,.2f}")
    print(f"  Open positions : {len(positions)}")
    for sym, pos in positions.items():
        print(f"    • {sym}: {pos.contracts:+.0f} contracts "
              f"(${pos.notional_usd:+,.2f} notional) "
              f"entry={pos.entry_price:.2f} "
              f"mark={pos.mark_price:.2f} "
              f"uPnL=${pos.unrealized_pnl_usd:+,.2f}")
    if not positions:
        print("    (flat — no open positions)")

    # Historical candles
    banner("5. HISTORICAL CANDLES (last 24h of 30m bars, BTC)")
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - 24 * 3600 * 1000
    candles = client.fetch_candles("BTC", "30m", start_ms, end_ms)
    print(f"  Rows returned: {len(candles)}")
    if len(candles) > 0:
        print(f"  First: ts={candles.iloc[0]['timestamp']} close=${candles.iloc[0]['close']:,.2f}")
        print(f"  Last : ts={candles.iloc[-1]['timestamp']} close=${candles.iloc[-1]['close']:,.2f}")

    # Dry-run orders
    banner("6. DRY-RUN ORDER PLACEMENT")
    print("  Simulating what the strategy would trade with a $5K account")
    print("  at 1.2x leverage, 33% per coin (target notional = $1,650/coin).\n")
    print("  First pass: open full long positions in all 3 coins")
    print("  (this is what a fresh bull signal would look like)\n")

    target_notional_per_coin = 1650.0
    results = []
    for sym in ("BTC", "ETH", "SOL"):
        result = client.place_market_order(sym, target_notional_per_coin, dry_run=True)
        results.append(result)
        if result.success:
            if result.side == "SKIP":
                print(f"  {sym}: SKIP (already at target)")
            else:
                print(f"  {sym}: [DRY] {result.side} {abs(result.contracts):.0f} contracts "
                      f"= ${abs(result.notional_usd):,.2f} notional @ ${result.fill_price:,.2f}")
        else:
            print(f"  {sym}: FAILED - {result.error_message}")

    # Total committed
    total_committed = sum(abs(r.notional_usd) for r in results if r.success)
    target_total = target_notional_per_coin * 3
    rounding_slippage = target_total - total_committed
    print(f"\n  Target total notional   : ${target_total:>10,.2f}")
    print(f"  Actual rounded notional : ${total_committed:>10,.2f}")
    print(f"  Quantization loss       : ${rounding_slippage:>10,.2f} ({rounding_slippage/target_total*100:.1f}%)")
    print(f"  Effective leverage      : {total_committed/5000:.3f}x  (target: 1.20x × 99% = 1.188x)")

    # Short flip test
    banner("7. DRY-RUN LONG -> SHORT FLIP (critical for bidirectional strategy)")
    print("  Simulating a bearish signal that flips all positions to short.\n")
    for sym in ("BTC", "ETH", "SOL"):
        result = client.place_market_order(sym, -target_notional_per_coin, dry_run=True)
        if result.success and result.side != "SKIP":
            print(f"  {sym}: [DRY] {result.side} {abs(result.contracts):.0f} contracts "
                  f"= ${abs(result.notional_usd):,.2f} notional "
                  f"(flip from +{target_notional_per_coin/(specs[sym].contract_size*prices[sym]):.1f} "
                  f"to -{target_notional_per_coin/(specs[sym].contract_size*prices[sym]):.1f} contracts)")
        elif result.side == "SKIP":
            print(f"  {sym}: SKIP")
        else:
            print(f"  {sym}: FAILED - {result.error_message}")

    # Below-minimum test
    banner("8. MINIMUM-SIZE REJECT TEST")
    print("  Attempting $5 order (below $10 min notional) — should REJECT.\n")
    result = client.place_market_order("BTC", 5.0, dry_run=True)
    if result.success:
        print(f"  ⚠️  Should have failed but succeeded: {result}")
    else:
        print(f"  ✅ Correctly rejected: {result.error_message}")

    banner("INTEGRATION TEST COMPLETE")
    print("""
  If you see valid prices, funding rates, and dry-run orders above,
  the CoinbaseClient is working end-to-end.

  Next: Review the dry-run output. If the trades look right, we can
  enable live orders by flipping dry_run=False in the trading loop.
""")


if __name__ == "__main__":
    main()
