"""
One-time reconciliation: query Coinbase for actual fill details on every
live trade and overwrite the estimated prices/fees with real settled values.

Usage:
    uv run live/reconcile_trades.py [--write]

Without --write, prints what would change. With --write, updates the state file.
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from exchanges import CoinbaseClient


def reconcile(state_path: Path, client: CoinbaseClient, write: bool) -> None:
    with open(state_path) as f:
        state = json.load(f)

    trades = state.get("trade_log", [])
    updated = 0
    total_pnl_delta = 0.0
    total_fee_delta = 0.0

    for i, t in enumerate(trades):
        order_id = t.get("order_id")
        if not order_id or order_id in ("DRY_RUN", "PAPER_SIM"):
            continue

        old_price = t.get("fill_price", t.get("price", 0))
        old_fee = t.get("fee", t.get("fee_usd", 0))

        avg_price, total_fees, filled = client.get_order_fill_details(order_id)
        time.sleep(0.15)  # rate limit

        if avg_price <= 0:
            print(f"  [{i}] {t.get('time','?')[:19]} {t.get('symbol','?')} — could not fetch")
            continue

        price_delta = avg_price - old_price
        fee_delta = total_fees - old_fee

        if abs(price_delta) < 0.001 and abs(fee_delta) < 0.001:
            continue

        # Update trade record with actual values
        t["price"] = round(avg_price, 6)
        t["fill_price"] = round(avg_price, 6)
        t["fee"] = round(total_fees, 4)
        t["fee_usd"] = round(total_fees, 4)

        # Recompute notional from actual price
        contracts = abs(t.get("contracts", 0))
        symbol = t.get("symbol", "")
        cs = {"BTC": 0.01, "ETH": 0.1, "SOL": 5.0}.get(symbol, 1.0)
        actual_notional = contracts * cs * avg_price
        if t.get("contracts", 0) >= 0:
            t["size"] = round(actual_notional, 4)
            t["notional_usd"] = round(actual_notional, 4)
        else:
            t["size"] = round(-actual_notional, 4)
            t["notional_usd"] = round(actual_notional, 4)

        total_pnl_delta += price_delta
        total_fee_delta += fee_delta
        updated += 1

        print(f"  [{i:>2}] {t.get('time','')[:19]} {t.get('action',''):<14} {symbol:<4} "
              f"price ${old_price:.2f} → ${avg_price:.2f} ({price_delta:+.2f})  "
              f"fee ${old_fee:.2f} → ${total_fees:.2f} ({fee_delta:+.2f})")

    # Recompute P&L with corrected prices using position-tracking
    # (handles both long AND short round trips — not just uni-directional FIFO)
    print(f"\nReconciled {updated} trades. Recomputing P&L (bidirectional)...")
    # Track per-symbol: (signed_contracts, avg_entry_price, contract_size)
    positions: dict[str, list] = {}
    for t in trades:
        if t.get("dry_run"):
            t["pnl"] = 0.0
            continue
        symbol = t.get("symbol", "")
        contracts = t.get("contracts", 0)
        fill_price = t.get("fill_price", t.get("price", 0))
        cs = {"BTC": 0.01, "ETH": 0.1, "SOL": 5.0}.get(symbol, 1.0)

        pos = positions.get(symbol)
        current_contracts = pos[0] if pos else 0.0
        current_entry = pos[1] if pos else 0.0

        new_contracts = current_contracts + contracts
        pnl = 0.0

        # P&L occurs when position REDUCES or FLIPS direction
        if current_contracts != 0:
            same_direction = (current_contracts > 0 and contracts > 0) or \
                             (current_contracts < 0 and contracts < 0)
            if not same_direction:
                close_qty = min(abs(current_contracts), abs(contracts))
                if current_contracts > 0:
                    pnl = close_qty * cs * (fill_price - current_entry)
                else:
                    pnl = close_qty * cs * (current_entry - fill_price)

        # Update position
        if abs(new_contracts) < 1e-9:
            positions.pop(symbol, None)
        elif current_contracts == 0 or (current_contracts > 0) != (new_contracts > 0):
            # Fresh open or direction flipped — entry is the current fill
            positions[symbol] = [new_contracts, fill_price, cs]
        else:
            # Adding to existing position — weighted avg entry
            total_c = abs(current_contracts) + abs(contracts)
            if total_c > 0:
                avg_entry = (abs(current_contracts) * current_entry + abs(contracts) * fill_price) / total_c
            else:
                avg_entry = fill_price
            positions[symbol] = [new_contracts, avg_entry, cs]

        t["pnl"] = round(pnl, 4)

    # Summary
    total_realized = sum(t.get("pnl", 0) for t in trades)
    total_fees = sum(t.get("fee", 0) for t in trades if not t.get("dry_run"))
    print(f"Total realized P&L (FIFO): ${total_realized:+.2f}")
    print(f"Total fees: ${total_fees:.2f}")
    print(f"Net: ${total_realized - total_fees:+.2f}")

    if write:
        bak = state_path.with_suffix(state_path.suffix + ".pre-reconcile.bak")
        shutil.copy2(state_path, bak)
        with open(state_path, "w") as f:
            json.dump(state, f, indent=2, default=str)
        print(f"\n✓ Written to {state_path.name} (backup: {bak.name})")
    else:
        print("\n(dry run — rerun with --write to persist)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    client = CoinbaseClient(dry_run_default=True)
    state_path = PROJECT_ROOT / "live" / "state" / "30m-concentrated_live_state.json"

    print(f"Reconciling {state_path.name} against Coinbase order history...")
    print()
    reconcile(state_path, client, args.write)


if __name__ == "__main__":
    main()
