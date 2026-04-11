"""
INSTRUMENTED MARGIN RELEASE TEST

Places 1 BTC contract, then immediately closes it, while continuously
polling `initial_margin` and `available_margin` to measure EXACTLY how
fast margin releases on position close.

This is the critical validation: can the strategy rapidly flip positions
without hitting settlement lag?

Cost: same as the first smoke test (~$0.89 round trip)

Output: a timeline showing margin fields at each step, so we can see
whether initial_margin drops to $0 instantly or with a delay.

Requires CONFIRM=yes to run.
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from exchanges import CoinbaseClient

if os.environ.get("CONFIRM", "").lower() != "yes":
    print("SAFETY GATE: set CONFIRM=yes to run this test")
    print("  CONFIRM=yes python scripts/test_margin_release.py")
    sys.exit(0)


def snapshot(client: CoinbaseClient, label: str) -> dict:
    """Query + print the margin fields of interest."""
    summary = client._get_futures_balance_summary()
    sv = client._summary_value

    state = {
        "label": label,
        "t": time.monotonic(),
        "initial_margin": sv(summary, "initial_margin"),
        "available_margin": sv(summary, "available_margin"),
        "total_usd_balance": sv(summary, "total_usd_balance"),
        "unrealized_pnl": sv(summary, "unrealized_pnl"),
        "daily_realized_pnl": sv(summary, "daily_realized_pnl"),
        "pending_transfers": sv(summary, "total_pending_transfers_amount"),
        "positions": len(client.get_positions()),
    }
    return state


def print_state(s: dict, baseline_t: float):
    dt = s["t"] - baseline_t
    print(
        f"  [{dt:6.2f}s] {s['label']:<28s} "
        f"| init_margin=${s['initial_margin']:>8.2f} "
        f"| avail=${s['available_margin']:>10.2f} "
        f"| total=${s['total_usd_balance']:>10.2f} "
        f"| uPnL=${s['unrealized_pnl']:>+7.2f} "
        f"| pending=${s['pending_transfers']:>8.2f} "
        f"| pos={s['positions']}"
    )


def main():
    print("=" * 110)
    print("  INSTRUMENTED MARGIN RELEASE TEST")
    print("=" * 110)

    client = CoinbaseClient(dry_run_default=False)

    # --- Baseline ---
    baseline = snapshot(client, "BASELINE (before buy)")
    baseline_t = baseline["t"]
    print_state(baseline, baseline_t)

    if baseline["initial_margin"] > 0:
        print(f"\n  ⚠️  initial_margin is already ${baseline['initial_margin']} - account has "
              f"pre-existing margin. Aborting to avoid confusion.")
        return

    btc_price = client.fetch_current_price("BTC")
    print(f"\n  BTC price: ${btc_price:,.2f}")
    print(f"  Target: BUY 1 contract (~${btc_price * 0.01:.2f} notional, ~${btc_price * 0.01 * 0.2456:.2f} margin)\n")

    # --- BUY ---
    print("  >>> SUBMITTING BUY ORDER")
    buy_result = client.place_market_order(
        symbol="BTC",
        target_notional_usd=btc_price * 0.01 * 1.5,  # ensures round-to-1-contract
        dry_run=False,
    )
    if not buy_result.success:
        print(f"  ❌ BUY failed: {buy_result.error_message}")
        return
    print(f"  BUY filled: order={buy_result.order_id[:13]}... price=${buy_result.fill_price:,.2f}")

    # Immediate snapshot after buy
    post_buy = snapshot(client, "after BUY (immediate)")
    print_state(post_buy, baseline_t)

    # Poll during the hold
    time.sleep(1)
    print_state(snapshot(client, "hold t+1s"), baseline_t)
    time.sleep(2)
    print_state(snapshot(client, "hold t+3s"), baseline_t)
    time.sleep(2)
    print_state(snapshot(client, "hold t+5s"), baseline_t)

    # --- SELL ---
    print("\n  >>> SUBMITTING SELL ORDER (close)")
    sell_result = client.place_market_order(
        symbol="BTC",
        target_notional_usd=0.0,
        dry_run=False,
    )
    if not sell_result.success:
        print(f"  ❌ SELL failed: {sell_result.error_message}")
        return
    print(f"  SELL filled: order={sell_result.order_id[:13]}... price=${sell_result.fill_price:,.2f}")

    # === THE CRITICAL SECTION ===
    # Poll rapidly right after sell to see when initial_margin drops to 0
    print("\n  POLLING POST-CLOSE MARGIN RELEASE (this is the key measurement):\n")
    post_sell_samples = []
    t_sell_fill = time.monotonic()

    # Immediate
    s = snapshot(client, "post-SELL immediate")
    s["offset_from_sell"] = s["t"] - t_sell_fill
    print_state(s, baseline_t)
    post_sell_samples.append(s)

    # Poll every 500ms for 10 seconds, then every 5s for 30 more seconds
    next_poll_times = [0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 8.0, 12.0, 20.0, 30.0, 45.0]
    for t in next_poll_times:
        target_t = t_sell_fill + t
        wait = max(0, target_t - time.monotonic())
        if wait > 0:
            time.sleep(wait)
        s = snapshot(client, f"post-SELL t+{t:.1f}s")
        s["offset_from_sell"] = s["t"] - t_sell_fill
        print_state(s, baseline_t)
        post_sell_samples.append(s)

    # --- Analysis ---
    print("\n" + "=" * 110)
    print("  MARGIN RELEASE ANALYSIS")
    print("=" * 110)

    # When did initial_margin first hit $0?
    first_zero = None
    for s in post_sell_samples:
        if s["initial_margin"] == 0:
            first_zero = s
            break

    if first_zero:
        offset = first_zero["offset_from_sell"]
        print(f"  ✅ initial_margin dropped to $0.00 within {offset:.2f}s of SELL fill")
    else:
        last = post_sell_samples[-1]
        print(f"  ⚠️  initial_margin still ${last['initial_margin']:.2f} "
              f"after {last['offset_from_sell']:.2f}s — lag detected")

    # When did available_margin recover?
    baseline_avail = baseline["available_margin"]
    recovered = None
    for s in post_sell_samples:
        if s["available_margin"] >= baseline_avail - 1.0:  # within $1 of baseline (fees)
            recovered = s
            break

    if recovered:
        offset = recovered["offset_from_sell"]
        print(f"  ✅ available_margin recovered to near-baseline within {offset:.2f}s of SELL fill")
    else:
        last = post_sell_samples[-1]
        recovered_pct = last["available_margin"] / baseline_avail * 100 if baseline_avail else 0
        print(f"  ⚠️  available_margin at {recovered_pct:.1f}% of baseline after "
              f"{last['offset_from_sell']:.2f}s")

    print()
    print("  VERDICT:")
    if first_zero and first_zero["offset_from_sell"] < 2.0 and recovered and recovered["offset_from_sell"] < 2.0:
        print("  ✅ MARGIN RELEASE IS ~INSTANT. Strategy can rapidly flip positions without")
        print("     any settlement-lag concerns. Safe to proceed with live trading.")
    elif first_zero and first_zero["offset_from_sell"] < 30.0:
        print(f"  ⚠️  MARGIN RELEASE TAKES UP TO {first_zero['offset_from_sell']:.0f} SECONDS.")
        print(f"     At 30-min bar intervals the strategy still has plenty of time, but")
        print(f"     this is worth noting for high-frequency variants.")
    else:
        print("  ❌ MARGIN RELEASE IS SLOW OR INCOMPLETE. This breaks rapid position flipping.")
        print("     Investigate settlement mechanics before deploying live.")


if __name__ == "__main__":
    main()
