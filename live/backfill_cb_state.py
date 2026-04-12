"""
Back-fill per-trade pnl/fee and equity_curve on a CB trader state file.

Coinbase's fill API doesn't return per-trade realized P&L or per-trade fees;
it only exposes cumulative daily aggregates. This script walks the trade log,
computes FIFO realized P&L on every close, applies a Coinbase-modeled fee
(3 bps of notional + $0.15/contract), and rebuilds the equity_curve / HL-
compatible cash field so the dashboard shows the same level of detail as the
HL paper strategy.

For **CB Live** instances (any non-dry-run trade present), the script trusts
Coinbase's daily_price_pnl / daily_fees as authoritative totals and scales
the per-trade FIFO pnl to match. For **CB Paper** instances (all dry-run),
the Coinbase daily_* fields are ignored — they're polluted by the shared
live account's activity — and totals come from the FIFO computation.

Run with:
    python3 live/backfill_cb_state.py <state_file.json> [--write]
"""
import argparse
import json
import shutil
from collections import deque
from datetime import datetime, timezone
from pathlib import Path


COINBASE_BPS = 0.0003       # 3 basis points of notional
COINBASE_FIXED_PER_CONTRACT = 0.15  # $0.15 per contract


def _contract_size(symbol: str, sample_trade: dict) -> float:
    contracts = abs(float(sample_trade.get("contracts") or 0))
    price = float(sample_trade.get("fill_price") or 0)
    notional = abs(float(sample_trade.get("notional_usd") or 0))
    if contracts == 0 or price == 0:
        return {"BTC": 0.01, "ETH": 0.1, "SOL": 5.0}.get(symbol, 1.0)
    return notional / (contracts * price)


def _model_fee(trade: dict) -> float:
    notional = abs(float(trade.get("notional_usd") or 0))
    contracts = abs(float(trade.get("contracts") or 0))
    return notional * COINBASE_BPS + contracts * COINBASE_FIXED_PER_CONTRACT


def backfill(state: dict) -> dict:
    trades = sorted(state.get("trade_log", []), key=lambda t: t.get("ts", 0))
    if not trades:
        return state

    initial_equity = float(state.get("initial_equity") or 10000.0)

    # --- 1. Detect instance type ---
    # A CB Live instance has at least one non-dry-run fill. A CB Paper instance
    # is all dry-run. Coinbase's daily_* is only authoritative for live.
    has_live_fills = any(not t.get("dry_run", False) for t in trades)

    # --- 2. Target-based walk ---
    # For every trade row, decide whether it actually changes position state.
    # Live instances: only LIVE fills change state (DRYRUN rows are non-events).
    # Paper instances: a BUY is state-changing only if we're currently flat in
    # that symbol; a duplicate BUY while already long is a no-op (the paper
    # trader logged the order because its skip logic misreads the shared
    # Coinbase account). SELLs close the current position in full.
    open_lots: dict[str, tuple[float, float, float]] = {}  # sym -> (contracts, entry, cs)
    cum_realized_fifo = 0.0
    cum_fees_model = 0.0
    enriched_trades = []

    for t in trades:
        ts = t.get("ts")
        symbol = t.get("symbol")
        contracts = float(t.get("contracts") or 0)
        fill_price = float(t.get("fill_price") or 0)
        notional_usd = float(t.get("notional_usd") or 0)
        action = t.get("action", "")
        is_dry_run = bool(t.get("dry_run", False))

        cs = _contract_size(symbol, t)

        # --- State-change gate ---
        if has_live_fills:
            # Only real fills move state; DRYRUN rows are recorded but inert.
            participates = not is_dry_run
        else:
            # Paper: BUY only if currently flat, SELL only if currently long.
            existing = open_lots.get(symbol)
            if contracts > 0:
                participates = existing is None
            elif contracts < 0:
                participates = existing is not None
            else:
                participates = False

        fee = _model_fee(t) if participates else 0.0
        pnl = 0.0

        if participates:
            if contracts > 0:
                # OPEN_LONG — we're going from flat to long.
                open_lots[symbol] = (contracts, fill_price, cs)
            elif contracts < 0:
                # CLOSE — realize P&L against the existing lot, go to flat.
                existing = open_lots.pop(symbol, None)
                if existing is not None:
                    oc, oe, ocs = existing
                    close_contracts = min(oc, abs(contracts))
                    pnl = close_contracts * ocs * (fill_price - oe)

            cum_realized_fifo += pnl
            cum_fees_model += fee

        # Derive target_pos (current notional after this trade)
        current_lot = open_lots.get(symbol)
        target_notional = (
            current_lot[0] * current_lot[2] * current_lot[1] if current_lot else 0.0
        )

        enriched_trades.append({
            "ts": ts,
            "time": t.get("time"),
            "symbol": symbol,
            "action": action,
            "price": round(fill_price, 6),
            "size": round(notional_usd if contracts >= 0 else -abs(notional_usd), 4),
            "target_pos": round(target_notional, 2),
            "pnl": round(pnl, 4),
            "fee": round(fee, 4),
            "contracts": contracts,
            "notional_usd": notional_usd,
            "fill_price": fill_price,
            "fee_usd": t.get("fee_usd", 0.0),
            "order_id": t.get("order_id"),
            "dry_run": is_dry_run,
        })

    # --- 3. Choose authoritative totals based on instance type ---
    if has_live_fills:
        # Trust Coinbase for live instances — its daily_* is the real account
        # P&L (slippage, funding, settlement). Scale per-trade pnl/fee so the
        # trade-log column sums match the account-level totals exactly.
        auth_cum_realized = float(state.get("daily_price_pnl") or cum_realized_fifo)
        auth_cum_fees = float(state.get("daily_fees") or cum_fees_model)
        if abs(cum_realized_fifo) > 1e-6:
            pnl_scale = auth_cum_realized / cum_realized_fifo
            for et in enriched_trades:
                if et["pnl"] != 0:
                    et["pnl"] = round(et["pnl"] * pnl_scale, 4)
        if abs(cum_fees_model) > 1e-6:
            fee_scale = auth_cum_fees / cum_fees_model
            for et in enriched_trades:
                if et["fee"] != 0:
                    et["fee"] = round(et["fee"] * fee_scale, 4)
    else:
        # Pure paper instance — Coinbase daily_* reflects the sibling live
        # account and must be ignored. Totals come from our FIFO walk.
        auth_cum_realized = cum_realized_fifo
        auth_cum_fees = cum_fees_model

    # --- 4. Rebuild equity_curve: one point per trade + a current live point ---
    equity_curve_rebuilt = []
    started_ts = None
    orig_curve = state.get("equity_curve") or []
    if orig_curve:
        started_ts = orig_curve[0].get("ts")
    if started_ts:
        equity_curve_rebuilt.append({"ts": started_ts, "equity": round(initial_equity, 4)})

    running_real = 0.0
    running_fees = 0.0
    for et in enriched_trades:
        running_real += float(et["pnl"])
        running_fees += float(et["fee"])
        eq = initial_equity + running_real - running_fees
        equity_curve_rebuilt.append({"ts": et["ts"], "equity": round(eq, 4)})

    # --- 5. Positions / entry_prices for this instance ---
    # Live instances: Coinbase's state["positions"] is ground truth.
    # Paper instances: Coinbase's state reflects the sibling live account, so
    # we reconstruct from the FIFO open lots.
    if has_live_fills:
        final_positions = dict(state.get("positions") or {})
        final_entry_prices = dict(state.get("entry_prices") or {})
    else:
        final_positions = {}
        final_entry_prices = {}
        for sym, (oc, oe, ocs) in open_lots.items():
            if oc <= 1e-12:
                continue
            final_positions[sym] = round(oc * ocs * oe, 4)
            final_entry_prices[sym] = round(oe, 6)

    total_exposure = sum(abs(float(v) or 0) for v in final_positions.values())

    # --- 6. Final "current" point reflecting live unrealized ---
    # Implied unrealized = (original last equity) - (accounting equity without unrealized)
    last_orig_equity = orig_curve[-1].get("equity") if orig_curve else initial_equity
    accounting_equity = initial_equity + auth_cum_realized - auth_cum_fees
    implied_unrealized = last_orig_equity - accounting_equity
    if abs(implied_unrealized) > 500:
        implied_unrealized = 0.0

    live_equity = accounting_equity + implied_unrealized
    last_ts = orig_curve[-1].get("ts") if orig_curve else int(
        datetime.now(timezone.utc).timestamp() * 1000
    )
    if enriched_trades and last_ts > enriched_trades[-1]["ts"]:
        equity_curve_rebuilt.append({"ts": last_ts, "equity": round(live_equity, 4)})

    # --- 7. HL-semantic virtual cash ---
    virtual_cash = initial_equity + auth_cum_realized - auth_cum_fees - total_exposure

    # --- 8. Peak equity is the max seen over the rebuilt curve + live point ---
    peak_equity = max((p["equity"] for p in equity_curve_rebuilt), default=initial_equity)

    # --- 9. Write results ---
    state["trade_log"] = enriched_trades
    state["equity_curve"] = equity_curve_rebuilt
    state["positions"] = final_positions
    state["entry_prices"] = final_entry_prices
    state["cash"] = round(virtual_cash, 4)
    state["peak_equity"] = round(peak_equity, 4)
    # Display totals (cumulative since instance start = prior + today).
    state["cumulative_realized_pnl_total"] = round(auth_cum_realized, 4)
    state["cumulative_fees_total"] = round(auth_cum_fees, 4)

    # Rollover bookkeeping for trader.py: this instance started today so
    # prior_days_* = 0, and the current authoritative totals are "today's".
    state["prior_days_realized_pnl"] = 0.0
    state["prior_days_fees"] = 0.0
    state["last_daily_realized_snapshot"] = round(auth_cum_realized, 4)
    state["last_daily_fees_snapshot"] = round(auth_cum_fees, 4)
    state["current_utc_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Rebuild sim_open_lots from the final target positions so trader.py's
    # paper-mode FIFO resumes from the correct state.
    sim_lots = {}
    for sym, notional in final_positions.items():
        entry = final_entry_prices.get(sym)
        if not entry:
            continue
        # Derive contracts from notional at entry: contracts = notional/(cs×entry)
        cs = {"BTC": 0.01, "ETH": 0.1, "SOL": 5.0}.get(sym, 1.0)
        contracts = notional / (cs * entry) if entry else 0.0
        if contracts > 1e-12:
            sim_lots[sym] = [round(contracts, 8), round(entry, 6), cs]
    state["sim_open_lots"] = sim_lots

    # For paper instances, clear the misleading "borrowed" daily_* fields.
    if not has_live_fills:
        state["daily_price_pnl"] = round(auth_cum_realized, 4)
        state["daily_fees"] = round(auth_cum_fees, 4)
        state["daily_total_pnl"] = round(auth_cum_realized - auth_cum_fees, 4)

    return state


def _report(path: Path, before: dict, after: dict, trades_sample):
    print(f"=== {path.name} ===")
    for k in sorted(set(list(before.keys()) + list(after.keys()))):
        b = before.get(k, "—")
        a = after.get(k, "—")
        arrow = " → " if b != a else " = "
        print(f"  {k:32} {b}{arrow}{a}")
    print()
    print("Sample enriched trades (first / mid / last):")
    for i in [0, len(trades_sample) // 2, -1]:
        t = trades_sample[i]
        print(f"  [{i:>3}] {t['time'][:19]} {t['action']:<12} {t['symbol']:<4} "
              f"price=${t['price']:<10.2f} size=${t['size']:<10.2f} "
              f"pnl=${t['pnl']:+8.2f} fee=${t['fee']:6.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("state_file")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    path = Path(args.state_file)
    with open(path) as f:
        state = json.load(f)

    before = {
        "num_trades": len(state.get("trade_log", [])),
        "trades_with_pnl": sum(1 for t in state.get("trade_log", []) if t.get("pnl")),
        "trades_with_fee": sum(1 for t in state.get("trade_log", []) if t.get("fee")),
        "equity_curve_points": len(state.get("equity_curve", [])),
        "cash": state.get("cash"),
        "peak_equity": state.get("peak_equity"),
    }

    new_state = backfill(state)

    after = {
        "num_trades": len(new_state.get("trade_log", [])),
        "trades_with_pnl": sum(1 for t in new_state.get("trade_log", []) if t.get("pnl")),
        "trades_with_fee": sum(1 for t in new_state.get("trade_log", []) if t.get("fee")),
        "equity_curve_points": len(new_state.get("equity_curve", [])),
        "cash": new_state.get("cash"),
        "peak_equity": new_state.get("peak_equity"),
        "cumulative_realized_pnl_total": new_state.get("cumulative_realized_pnl_total"),
        "cumulative_fees_total": new_state.get("cumulative_fees_total"),
    }

    _report(path, before, after, new_state["trade_log"])

    if args.write:
        bak = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, bak)
        with open(path, "w") as f:
            json.dump(new_state, f, indent=2, default=str)
        print(f"\n✓ Wrote {path} (backup at {bak.name})")
    else:
        print("\n(dry run — rerun with --write to persist)")


if __name__ == "__main__":
    main()
