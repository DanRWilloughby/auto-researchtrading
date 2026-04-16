"""A/B test: same strategy on same Coinbase data, two fill modes.

Mode A: fill at bar close (same as engine/prepare.py execute_delay=0, matches
        the prior 'cb-baseline' test that showed 73% win rate)
Mode B: fill at :14 open of next period (matches live cron firing reality)

Everything else identical. The delta between A and B is the 14-min post-bar
drift the strategy captures in simulation but can't capture live.
"""
from __future__ import annotations

import sys
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


@dataclass
class Pos:
    contracts: int = 0
    avg_entry: float = 0.0


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
    return dict(symbol=sig.symbol, side=side, contracts=delta_contracts,
                ref_price=fill_ref_price, fill_price=fill_price, notional=notional,
                fee=fee, realized_pnl=realized, slippage_cost=slip)


def run_mode(mode_label, fill_mode, thirty_min, one_min_idx, common_ts):
    """fill_mode: 'close' or 'open_plus_14'"""
    strategy = load_strategy()
    positions = {s: Pos() for s in SYMBOLS}
    trades = []

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

            if fill_mode == "close":
                # Fill at bar close — same price the signal was derived from.
                # Matches prior cb-baseline test with engine default execute_delay=0.
                ref_price = bar_data[sig.symbol].close
                actual_fill_ts = bar_end_ts
            else:
                # Fill at :14 open of next period — matches live cron firing reality.
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

            tr = execute(sig, ref_price, positions[sig.symbol], CONTRACT_SIZE[sig.symbol])
            if tr is None:
                continue
            tr["ts"] = actual_fill_ts
            trades.append(tr)

    return trades


def summarize(label, trades):
    if not trades:
        print(f"{label}: NO TRADES")
        return
    td = pd.DataFrame(trades)
    notional = td["notional"].sum()
    gross = td["realized_pnl"].sum()
    fees = td["fee"].sum()
    slip = td["slippage_cost"].sum()
    closes = td[td["realized_pnl"].abs() > 0.01]
    wins = (closes["realized_pnl"] > 0).sum() if not closes.empty else 0
    wr = 100 * wins / len(closes) if not closes.empty else 0.0

    print(f"\n=== {label} ===")
    print(f"Trades:              {len(td):>10,}")
    print(f"Notional:            ${notional:>13,.0f}")
    print(f"Gross realized P&L:  ${gross:>+13,.2f}")
    print(f"Fees:                ${fees:>13,.2f}")
    print(f"Slippage:            ${slip:>13,.2f}")
    print(f"Net:                 ${gross-fees-slip:>+13,.2f}")
    print(f"Gross bps (1-way):   {10000*gross/notional:>+7.2f}")
    print(f"Fee bps (1-way):     {-10000*fees/notional:>+7.2f}")
    print(f"Slippage bps (1-way):{-10000*slip/notional:>+7.2f}")
    print(f"Net bps (1-way):     {10000*(gross-fees-slip)/notional:>+7.2f}")
    print(f"Realizing trades:    {len(closes):>10,}")
    print(f"Win rate:            {wr:>10.1f}%  ({wins}/{len(closes)})")

    # Per-symbol
    print(f"\n  {'Sym':<5} {'Trades':>6} {'GrossBps':>10} {'NetBps':>9}")
    for s in SYMBOLS:
        ss = td[td["symbol"] == s]
        if ss.empty: continue
        n = ss["notional"].sum()
        gb = 10000 * ss["realized_pnl"].sum() / n
        nb = 10000 * (ss["realized_pnl"].sum() - ss["fee"].sum() - ss["slippage_cost"].sum()) / n
        print(f"  {s:<5} {len(ss):>6} {gb:>+9.2f} {nb:>+9.2f}")


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

    # Mode A: fill at bar close (matches prior cb-baseline)
    print("\nRunning Mode A (fill at bar close, same-bar execution — matches prior cb-baseline)...")
    trades_A = run_mode("A", "close", thirty_min, one_min_idx, common_ts)

    # Mode B: fill at :14 open (matches live reality)
    print("Running Mode B (fill at :14 open — matches live cron firing)...")
    trades_B = run_mode("B", "open_plus_14", thirty_min, one_min_idx, common_ts)

    summarize("Mode A — fill at bar close (prior cb-baseline-style)", trades_A)
    summarize("Mode B — fill at :14 open (live reality)", trades_B)

    # Delta
    if trades_A and trades_B:
        tdA = pd.DataFrame(trades_A)
        tdB = pd.DataFrame(trades_B)
        gA = 10000 * tdA["realized_pnl"].sum() / tdA["notional"].sum()
        gB = 10000 * tdB["realized_pnl"].sum() / tdB["notional"].sum()
        print(f"\n=== DELTA (A → B, the cost of 14-min fill delay) ===")
        print(f"Gross edge:    {gA:+.2f} bps → {gB:+.2f} bps   (Δ {gB-gA:+.2f} bps)")
        cA = tdA[tdA["realized_pnl"].abs() > 0.01]
        cB = tdB[tdB["realized_pnl"].abs() > 0.01]
        wrA = 100 * (cA["realized_pnl"] > 0).sum() / len(cA) if len(cA) else 0
        wrB = 100 * (cB["realized_pnl"] > 0).sum() / len(cB) if len(cB) else 0
        print(f"Win rate:      {wrA:.1f}% → {wrB:.1f}%          (Δ {wrB-wrA:+.1f}pp)")


if __name__ == "__main__":
    main()
