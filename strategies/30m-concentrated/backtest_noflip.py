"""No-flip variant: any signal that would flip a position is converted to
a close-to-flat. Then the next bar, if the strategy still wants the opposite
side, it'll emit a fresh open-from-flat signal.

This isolates the clean-exit edge (60% WR in the baseline) from the flip tax.

Otherwise identical to backtest_coinbase_9mo.py:
- Signal from close of completed 30-min bar
- Fill at :14 open of next period (live reality)
- Integer-contract quantization
- 3 bps + $0.15/contract fees, 3 bps adverse slippage
- Fixed $10k capital
"""
from __future__ import annotations

import sys
import json
import importlib.util
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "engine"))

from engine.prepare import BarData, Signal, PortfolioState  # noqa: E402

SYMBOLS = ["BTC", "ETH", "SOL"]
CONTRACT_SIZE = {"BTC": 0.01, "ETH": 0.1, "SOL": 5.0}
DATA_DIR = Path("/tmp/trading-check-refresh")
STRATEGY_PATH = PROJECT_ROOT / "strategies" / "30m-concentrated" / "strategy.py"

INITIAL_CAPITAL = 10_000.0
FEE_BPS = 0.0003
FEE_PER_CONTRACT = 0.15
SLIPPAGE_BPS = 0.0003
LOOKBACK_BARS = 500

OUTPUT_DIR = Path(__file__).parent / "backtest_output_noflip"


@dataclass
class Pos:
    contracts: int = 0
    avg_entry: float = 0.0


def load_1m(symbol):
    df = pd.read_parquet(DATA_DIR / f"{symbol}_1m_9mo.parquet")
    df = df.sort_values("timestamp").drop_duplicates(subset="timestamp").reset_index(drop=True)
    df["dt"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df


def aggregate_to_30m(df1m):
    g = df1m.set_index("dt").resample("30min", label="left", closed="left")
    agg = pd.DataFrame({
        "timestamp": g["timestamp"].first().astype("Int64"),
        "open": g["open"].first(),
        "high": g["high"].max(),
        "low": g["low"].min(),
        "close": g["close"].last(),
        "volume": g["volume"].sum(),
    }).dropna(subset=["close"]).reset_index()
    agg["timestamp"] = agg["timestamp"].astype(int)
    agg["funding_rate"] = 0.0
    return agg


def load_strategy():
    spec = importlib.util.spec_from_file_location("strategy_module", STRATEGY_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Strategy()


def compute_fee(notional, contracts):
    return abs(notional) * FEE_BPS + abs(contracts) * FEE_PER_CONTRACT


def execute(sig, fill_ref_price, pos, cs):
    current_notional = pos.contracts * cs * fill_ref_price
    delta_usd = sig.target_position - current_notional
    delta_contracts = int(np.trunc(delta_usd / (fill_ref_price * cs)))
    if delta_contracts == 0:
        return None
    side = "BUY" if delta_contracts > 0 else "SELL"
    fill_price = fill_ref_price * (1 + SLIPPAGE_BPS) if side == "BUY" else fill_ref_price * (1 - SLIPPAGE_BPS)

    pre_c, pre_entry = pos.contracts, pos.avg_entry
    realized = 0.0
    if pre_c != 0 and ((pre_c > 0) != (delta_contracts > 0)):
        close_qty = min(abs(pre_c), abs(delta_contracts))
        realized = close_qty * cs * ((fill_price - pre_entry) if pre_c > 0 else (pre_entry - fill_price))

    new_c = pre_c + delta_contracts
    if new_c == 0:
        pos.contracts, pos.avg_entry = 0, 0.0
    elif (pre_c == 0) or (pre_c > 0) == (delta_contracts > 0):
        pos.avg_entry = (abs(pre_c) * pre_entry + abs(delta_contracts) * fill_price) / abs(new_c)
        pos.contracts = new_c
    else:
        if abs(delta_contracts) > abs(pre_c):
            pos.contracts, pos.avg_entry = new_c, fill_price
        else:
            pos.contracts = new_c

    notional = abs(delta_contracts) * cs * fill_price
    fee = compute_fee(notional, delta_contracts)
    slip = abs(delta_contracts) * cs * abs(fill_price - fill_ref_price)
    return dict(
        symbol=sig.symbol, side=side, contracts=delta_contracts,
        ref_price=fill_ref_price, fill_price=fill_price, notional=notional,
        fee=fee, realized_pnl=realized, slippage_cost=slip,
        pre_pos=pre_c, post_pos=pos.contracts,
        target_notional=sig.target_position,
    )


def rewrite_signal_no_flip(sig, pos_contracts, cs, fill_ref_price):
    """If a signal would flip the position sign, rewrite it to target=0 (flatten only)."""
    if pos_contracts == 0:
        return sig  # no flip possible from flat
    # Convert sig.target_position (USD) to target contracts at fill_ref_price
    target_contracts = int(np.trunc(sig.target_position / (fill_ref_price * cs)))
    if target_contracts == 0:
        return sig  # already a full-close signal
    if (pos_contracts > 0) != (target_contracts > 0):
        # Sign flip requested — rewrite to target=0 (flatten only)
        return Signal(symbol=sig.symbol, target_position=0.0, order_type=sig.order_type, metadata=sig.metadata)
    return sig


def main():
    print("Loading 1-min Coinbase parquets...")
    one_min = {s: load_1m(s) for s in SYMBOLS}
    print("Aggregating to 30-min bars...")
    thirty_min = {s: aggregate_to_30m(one_min[s]) for s in SYMBOLS}
    one_min_idx = {s: one_min[s].set_index("timestamp")[["open"]] for s in SYMBOLS}
    for s in SYMBOLS:
        thirty_min[s] = thirty_min[s].set_index("timestamp", drop=False).sort_index()
    common_ts = sorted(set.intersection(*[set(thirty_min[s].index) for s in SYMBOLS]))
    print(f"Common bars: {len(common_ts):,}")

    strategy = load_strategy()
    positions = {s: Pos() for s in SYMBOLS}
    trades = []
    flip_signals_rewritten = 0

    print("Running no-flip backtest...")
    for i, bar_start_ts in enumerate(common_ts):
        if i < LOOKBACK_BARS:
            continue
        bar_end_ts = bar_start_ts + 30 * 60 * 1000

        bar_data = {}
        for sym in SYMBOLS:
            df30 = thirty_min[sym]
            history = df30.loc[:bar_start_ts].tail(LOOKBACK_BARS)
            if len(history) < LOOKBACK_BARS // 5:
                continue
            latest = history.iloc[-1]
            bar_data[sym] = BarData(
                symbol=sym, timestamp=int(latest["timestamp"]),
                open=float(latest["open"]), high=float(latest["high"]),
                low=float(latest["low"]), close=float(latest["close"]),
                volume=float(latest["volume"]), funding_rate=0.0, history=history,
            )
        if not bar_data:
            continue

        current_notionals, current_entries = {}, {}
        for sym in SYMBOLS:
            if positions[sym].contracts != 0:
                ref_close = float(thirty_min[sym].loc[bar_start_ts]["close"])
                current_notionals[sym] = positions[sym].contracts * CONTRACT_SIZE[sym] * ref_close
                current_entries[sym] = positions[sym].avg_entry
        portfolio = PortfolioState(cash=INITIAL_CAPITAL, positions=current_notionals,
                                    entry_prices=current_entries, equity=INITIAL_CAPITAL,
                                    timestamp=bar_end_ts)

        try:
            signals = strategy.on_bar(bar_data, portfolio)
        except Exception:
            continue
        if not signals:
            continue

        for sig in signals:
            if sig.symbol not in SYMBOLS:
                continue
            fill_minute_ts = bar_end_ts + 14 * 60 * 1000
            om = one_min_idx[sig.symbol]
            ref_price = None
            actual_fill_ts = None
            for offset in range(0, 6):
                probe = fill_minute_ts + offset * 60 * 1000
                if probe in om.index:
                    ref_price = float(om.loc[probe, "open"])
                    actual_fill_ts = probe
                    break
            if ref_price is None:
                continue

            # No-flip rewrite
            original_target = sig.target_position
            sig_rewritten = rewrite_signal_no_flip(sig, positions[sig.symbol].contracts,
                                                    CONTRACT_SIZE[sig.symbol], ref_price)
            if sig_rewritten is not sig:
                flip_signals_rewritten += 1

            tr = execute(sig_rewritten, ref_price, positions[sig.symbol], CONTRACT_SIZE[sig.symbol])
            if tr is None:
                continue
            tr["ts"] = actual_fill_ts
            tr["bar_end_dt"] = datetime.fromtimestamp(bar_end_ts/1000, tz=timezone.utc).isoformat()
            tr["fill_dt"] = datetime.fromtimestamp(actual_fill_ts/1000, tz=timezone.utc).isoformat()
            tr["original_target_notional"] = original_target
            tr["was_flip_rewritten"] = sig_rewritten is not sig
            trades.append(tr)

    # ---------- Summary ----------
    print()
    print(f"Total signals rewritten from flip → flatten: {flip_signals_rewritten}")
    print(f"Total trades executed: {len(trades):,}")

    OUTPUT_DIR.mkdir(exist_ok=True)
    with open(OUTPUT_DIR / "trades.jsonl", "w") as f:
        for t in trades:
            f.write(json.dumps(t) + "\n")

    td = pd.DataFrame(trades)
    if td.empty:
        print("No trades executed.")
        return

    total_notional = td["notional"].sum()
    gross_pnl = td["realized_pnl"].sum()
    fees = td["fee"].sum()
    slip = td["slippage_cost"].sum()
    net = gross_pnl - fees - slip

    def bps(x):
        return 10_000 * x / total_notional if total_notional else 0

    print()
    print("=" * 60)
    print(f"No-flip variant — 9-month Coinbase backtest")
    print(f"{'Total notional':<24}${total_notional:>14,.0f}")
    print(f"{'Gross realized P&L':<24}${gross_pnl:>+14,.2f}   {bps(gross_pnl):+7.2f} bps")
    print(f"{'Fees':<24}${fees:>14,.2f}   {-bps(fees):+7.2f} bps")
    print(f"{'Slippage':<24}${slip:>14,.2f}   {-bps(slip):+7.2f} bps")
    print(f"{'Net P&L':<24}${net:>+14,.2f}   {bps(net):+7.2f} bps")
    print()

    # Win rate segments
    realizing = td[td["realized_pnl"].abs() > 0.01]
    full_close = td[(td["post_pos"] == 0) & (td["realized_pnl"].abs() > 0.01)]
    if not realizing.empty:
        wr_all = 100 * (realizing["realized_pnl"] > 0).sum() / len(realizing)
        print(f"Win rate (all realizing): {wr_all:.1f}% ({(realizing['realized_pnl']>0).sum()}/{len(realizing)})")
    if not full_close.empty:
        wr_close = 100 * (full_close["realized_pnl"] > 0).sum() / len(full_close)
        print(f"Win rate (full closes):   {wr_close:.1f}% ({(full_close['realized_pnl']>0).sum()}/{len(full_close)})")

    # Per-symbol
    print()
    print(f"{'Sym':<5} {'Trades':>8} {'Notional':>14} {'Gross bps':>12} {'Net bps':>10}")
    for s in SYMBOLS:
        ss = td[td["symbol"] == s]
        if ss.empty: continue
        n = ss["notional"].sum()
        gb = 10000 * ss["realized_pnl"].sum() / n if n else 0
        nb = 10000 * (ss["realized_pnl"].sum() - ss["fee"].sum() - ss["slippage_cost"].sum()) / n if n else 0
        print(f"{s:<5} {len(ss):>8} ${n:>12,.0f} {gb:>+11.2f} {nb:>+9.2f}")

    # Comparison to baseline with flips
    print()
    print("=== Comparison vs baseline (with flips) ===")
    print(f"{'':<28}{'With flips':>14}{'No flips':>14}")
    print(f"{'Gross bps':<28}{-2.39:>+13.2f}{bps(gross_pnl):>+13.2f}")
    print(f"{'Net bps':<28}{-11.65:>+13.2f}{bps(net):>+13.2f}")


if __name__ == "__main__":
    main()
