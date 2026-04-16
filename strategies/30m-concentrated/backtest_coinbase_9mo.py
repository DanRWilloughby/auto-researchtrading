"""
Coinbase-native 9-month backtest for 30m-concentrated.

Replays the live strategy against Coinbase 1-min candles for BTC/ETH/SOL,
matching live execution rules:

    - Signal from close of the fully-completed 30-min bar (ending at :00 / :30)
    - Fill at the OPEN of the :14 / :44 one-minute bar (matches live cron fire)
    - Integer-contract quantization (BTC 0.01, ETH 0.1, SOL 5.0 per contract)
    - Fee = notional × 3 bps + abs(contracts) × $0.15
    - Slippage = 3 bps adverse (BUY pays +3 bps, SELL receives −3 bps)
    - Fixed $10k capital (strategy sizing does NOT compound)
    - No risk manager, no cooldown, no circuit breaker, no funding

Output: per-trade log + aggregate bps breakdown (gross / fee / slippage / net)
by symbol and overall.
"""
from __future__ import annotations

import os
import sys
import json
import importlib.util
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "engine"))

from engine.prepare import BarData, Signal, PortfolioState  # noqa: E402

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SYMBOLS = ["BTC", "ETH", "SOL"]
CONTRACT_SIZE = {"BTC": 0.01, "ETH": 0.1, "SOL": 5.0}
DATA_DIR = Path("/tmp/trading-check-refresh")
STRATEGY_PATH = PROJECT_ROOT / "strategies" / "30m-concentrated" / "strategy.py"

INITIAL_CAPITAL = 10_000.0
FEE_BPS = 0.0003              # 3 bps of notional
FEE_PER_CONTRACT = 0.15       # $0.15 per contract
SLIPPAGE_BPS = 0.0003         # 3 bps adverse

LOOKBACK_BARS = 500           # history buffer passed to strategy
MIN_VOLUME_PER_BAR = 0        # include every bar regardless of volume

OUTPUT_DIR = Path(__file__).parent / "backtest_output"


# ---------------------------------------------------------------------------
# Data loading + aggregation
# ---------------------------------------------------------------------------

def load_1m(symbol: str) -> pd.DataFrame:
    path = DATA_DIR / f"{symbol}_1m_9mo.parquet"
    df = pd.read_parquet(path)
    df = df.sort_values("timestamp").drop_duplicates(subset="timestamp").reset_index(drop=True)
    df["dt"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df


def aggregate_to_30m(df1m: pd.DataFrame) -> pd.DataFrame:
    """Aggregate 1-min OHLCV to 30-min bars. Bars are labeled by their START ts
    (i.e. the bar starting at 00:00 ends at 00:30)."""
    g = df1m.set_index("dt").resample("30min", label="left", closed="left")
    agg = pd.DataFrame({
        "timestamp": (g["timestamp"].first()).astype("Int64"),
        "open": g["open"].first(),
        "high": g["high"].max(),
        "low": g["low"].min(),
        "close": g["close"].last(),
        "volume": g["volume"].sum(),
    })
    agg = agg.dropna(subset=["close"]).reset_index()
    agg["timestamp"] = agg["timestamp"].astype(int)
    agg["funding_rate"] = 0.0
    return agg


# ---------------------------------------------------------------------------
# Strategy loading
# ---------------------------------------------------------------------------

def load_strategy():
    spec = importlib.util.spec_from_file_location("strategy_module", STRATEGY_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Strategy()


# ---------------------------------------------------------------------------
# Execution mechanics
# ---------------------------------------------------------------------------

def compute_fee(notional: float, contracts: float) -> float:
    return abs(notional) * FEE_BPS + abs(contracts) * FEE_PER_CONTRACT


def apply_slippage(reference_price: float, side: str) -> float:
    """BUY pays +3 bps, SELL receives −3 bps."""
    if side == "BUY":
        return reference_price * (1 + SLIPPAGE_BPS)
    return reference_price * (1 - SLIPPAGE_BPS)


def notional_to_contracts(notional_usd: float, price: float, contract_size: float) -> int:
    """Integer contracts. Rounds toward zero (conservative — never over-shoots target)."""
    if price <= 0 or contract_size <= 0:
        return 0
    raw = notional_usd / (price * contract_size)
    return int(np.trunc(raw))


# ---------------------------------------------------------------------------
# Trade execution against one signal
# ---------------------------------------------------------------------------

@dataclass
class Position:
    contracts: int = 0          # signed integer contracts
    avg_entry: float = 0.0      # USD per coin


@dataclass
class TradeRecord:
    ts: int
    bar_end_dt: str
    fill_dt: str
    symbol: str
    side: str                   # BUY or SELL
    contracts: int              # signed
    ref_price: float            # :14/:44 open BEFORE slippage
    fill_price: float           # after slippage
    notional: float             # abs notional of this fill
    fee: float
    realized_pnl: float
    slippage_cost: float        # $ cost of the 3bps slippage vs ref_price
    pre_pos_contracts: int
    post_pos_contracts: int
    target_notional: float


def execute_signal(
    sig: Signal,
    fill_ref_price: float,
    pos: Position,
    bar_end_ts_ms: int,
    fill_minute_ts_ms: int,
) -> TradeRecord | None:
    """Apply a signal by placing a market order at fill_ref_price.

    Returns a TradeRecord if a trade happened, else None.
    """
    cs = CONTRACT_SIZE[sig.symbol]

    # Current position in USD notional (signed) using fill_ref_price for valuation
    current_notional = pos.contracts * cs * fill_ref_price

    # Desired delta in USD
    delta_usd = sig.target_position - current_notional

    # Convert delta to integer contracts at the reference price
    delta_contracts_raw = delta_usd / (fill_ref_price * cs)
    delta_contracts = int(np.trunc(delta_contracts_raw))
    if delta_contracts == 0:
        return None

    side = "BUY" if delta_contracts > 0 else "SELL"
    fill_price = apply_slippage(fill_ref_price, side)

    pre_contracts = pos.contracts
    pre_entry = pos.avg_entry

    # Realized P&L if reducing/closing/flipping
    realized = 0.0
    if pre_contracts != 0 and ((pre_contracts > 0) != (delta_contracts > 0)):
        close_qty = min(abs(pre_contracts), abs(delta_contracts))
        if pre_contracts > 0:  # closing a long
            realized = close_qty * cs * (fill_price - pre_entry)
        else:                   # closing a short
            realized = close_qty * cs * (pre_entry - fill_price)

    # Update position
    new_contracts = pre_contracts + delta_contracts
    if new_contracts == 0:
        pos.contracts = 0
        pos.avg_entry = 0.0
    elif (pre_contracts == 0) or (pre_contracts > 0) == (delta_contracts > 0):
        # opening or adding same side → weighted avg entry
        if abs(new_contracts) > 0:
            pos.avg_entry = (abs(pre_contracts) * pre_entry + abs(delta_contracts) * fill_price) / abs(new_contracts)
        pos.contracts = new_contracts
    else:
        # flipping (order size > current abs): remaining contracts open on the OTHER side at fill_price
        if abs(delta_contracts) > abs(pre_contracts):
            pos.contracts = new_contracts
            pos.avg_entry = fill_price
        else:
            # partial close, same sign retained
            pos.contracts = new_contracts
            # pos.avg_entry unchanged

    notional = abs(delta_contracts) * cs * fill_price
    fee = compute_fee(notional, delta_contracts)
    slip_cost = abs(delta_contracts) * cs * abs(fill_price - fill_ref_price)

    return TradeRecord(
        ts=fill_minute_ts_ms,
        bar_end_dt=datetime.fromtimestamp(bar_end_ts_ms / 1000, tz=timezone.utc).isoformat(),
        fill_dt=datetime.fromtimestamp(fill_minute_ts_ms / 1000, tz=timezone.utc).isoformat(),
        symbol=sig.symbol,
        side=side,
        contracts=delta_contracts,
        ref_price=fill_ref_price,
        fill_price=fill_price,
        notional=notional,
        fee=fee,
        realized_pnl=realized,
        slippage_cost=slip_cost,
        pre_pos_contracts=pre_contracts,
        post_pos_contracts=pos.contracts,
        target_notional=sig.target_position,
    )


# ---------------------------------------------------------------------------
# Main backtest loop
# ---------------------------------------------------------------------------

def run_backtest():
    # Load data
    print("Loading 1-min Coinbase parquets...")
    one_min = {sym: load_1m(sym) for sym in SYMBOLS}
    for sym in SYMBOLS:
        df = one_min[sym]
        print(f"  {sym}: {len(df):>7,} 1-min bars, {df['dt'].iloc[0]} → {df['dt'].iloc[-1]}")

    print("Aggregating to 30-min bars...")
    thirty_min = {sym: aggregate_to_30m(one_min[sym]) for sym in SYMBOLS}
    for sym in SYMBOLS:
        print(f"  {sym}: {len(thirty_min[sym]):>6,} 30-min bars")

    # Build a :14/:44 lookup: ts_ms of the bar ending → ts_ms + 14min → 1-min bar with that start
    # Quick index for 1-min bars by ts_ms
    one_min_idx = {
        sym: one_min[sym].set_index("timestamp")[["open", "dt"]]
        for sym in SYMBOLS
    }

    # Unified 30-min bar timeline: intersection of all symbols
    ts_sets = [set(thirty_min[sym]["timestamp"]) for sym in SYMBOLS]
    common_ts = sorted(set.intersection(*ts_sets))
    print(f"Common 30-min bar timestamps across all 3 symbols: {len(common_ts):,}")

    # Pre-index per-symbol 30m frames for fast slicing
    for sym in SYMBOLS:
        thirty_min[sym] = thirty_min[sym].set_index("timestamp", drop=False).sort_index()

    # Load strategy
    print("Loading strategy...")
    strategy = load_strategy()

    # State
    positions: dict[str, Position] = {sym: Position() for sym in SYMBOLS}
    trades: list[TradeRecord] = []
    cash = INITIAL_CAPITAL
    realized_total = 0.0
    fees_total = 0.0
    slippage_total = 0.0
    equity_curve = []   # list of (ts_ms, equity)

    # --- Bar loop ---
    skipped_no_fill_bar = 0
    warmup_needed = LOOKBACK_BARS

    print(f"Running strategy on {len(common_ts):,} bars...")
    for i, bar_start_ts in enumerate(common_ts):
        if i < warmup_needed:
            continue

        bar_end_ts = bar_start_ts + 30 * 60 * 1000  # bar that STARTED at bar_start ENDS 30min later

        # Build BarData for each symbol using history UP TO AND INCLUDING this bar
        # (the bar with start = bar_start_ts just closed)
        bar_data = {}
        for sym in SYMBOLS:
            df30 = thirty_min[sym]
            # Get rows up to and including this bar_start_ts; take last LOOKBACK_BARS
            history = df30.loc[:bar_start_ts].tail(LOOKBACK_BARS)
            if len(history) < LOOKBACK_BARS // 5:   # require some warmup
                continue
            latest = history.iloc[-1]
            bar_data[sym] = BarData(
                symbol=sym,
                timestamp=int(latest["timestamp"]),
                open=float(latest["open"]),
                high=float(latest["high"]),
                low=float(latest["low"]),
                close=float(latest["close"]),
                volume=float(latest["volume"]),
                funding_rate=0.0,
                history=history,
            )

        if not bar_data:
            continue

        # Fixed capital — strategy sizing stays anchored to $10k throughout
        current_equity_usd_positions = 0.0
        current_entries = {}
        current_positions_notional = {}
        for sym in SYMBOLS:
            if positions[sym].contracts == 0:
                continue
            # Mark position value at this bar's close for portfolio context
            ref_close = float(thirty_min[sym].loc[bar_start_ts]["close"])
            current_positions_notional[sym] = positions[sym].contracts * CONTRACT_SIZE[sym] * ref_close
            current_entries[sym] = positions[sym].avg_entry
        portfolio = PortfolioState(
            cash=cash,
            positions=current_positions_notional,
            entry_prices=current_entries,
            equity=INITIAL_CAPITAL,   # FIXED — sizing does not compound
            timestamp=bar_end_ts,
        )

        # Run strategy
        try:
            signals = strategy.on_bar(bar_data, portfolio)
        except Exception as e:
            print(f"  strategy error at bar {datetime.fromtimestamp(bar_start_ts/1000, tz=timezone.utc)}: {e}")
            continue

        if not signals:
            # Record equity snapshot anyway
            unrealized = 0.0
            for sym in SYMBOLS:
                if positions[sym].contracts == 0:
                    continue
                ref_close = float(thirty_min[sym].loc[bar_start_ts]["close"])
                cs = CONTRACT_SIZE[sym]
                if positions[sym].contracts > 0:
                    unrealized += positions[sym].contracts * cs * (ref_close - positions[sym].avg_entry)
                else:
                    unrealized += positions[sym].contracts * cs * (ref_close - positions[sym].avg_entry)
            equity_curve.append((bar_end_ts, INITIAL_CAPITAL + realized_total - fees_total + unrealized))
            continue

        # Determine fill minute (bar_end + 14 minutes) — use its OPEN as reference price
        fill_minute_ts_ms = bar_end_ts + 14 * 60 * 1000

        # Execute signals
        for sig in signals:
            if sig.symbol not in SYMBOLS:
                continue
            om_idx = one_min_idx[sig.symbol]
            # Find the 1-min bar starting at exactly fill_minute_ts_ms; if missing, try +60s, +120s up to +5min
            fill_ref_price = None
            actual_fill_ts = None
            for offset_min in range(0, 6):
                probe = fill_minute_ts_ms + offset_min * 60 * 1000
                if probe in om_idx.index:
                    fill_ref_price = float(om_idx.loc[probe, "open"])
                    actual_fill_ts = probe
                    break
            if fill_ref_price is None:
                skipped_no_fill_bar += 1
                continue

            trade = execute_signal(sig, fill_ref_price, positions[sig.symbol],
                                    bar_end_ts, actual_fill_ts)
            if trade is None:
                continue

            cash -= trade.side == "BUY" and trade.notional or -trade.notional
            cash -= trade.fee
            realized_total += trade.realized_pnl
            fees_total += trade.fee
            slippage_total += trade.slippage_cost
            trades.append(trade)

        # Equity snapshot
        unrealized = 0.0
        for sym in SYMBOLS:
            if positions[sym].contracts == 0:
                continue
            ref_close = float(thirty_min[sym].loc[bar_start_ts]["close"])
            cs = CONTRACT_SIZE[sym]
            if positions[sym].contracts > 0:
                unrealized += positions[sym].contracts * cs * (ref_close - positions[sym].avg_entry)
            else:
                unrealized += positions[sym].contracts * cs * (ref_close - positions[sym].avg_entry)
        equity_curve.append((bar_end_ts, INITIAL_CAPITAL + realized_total - fees_total + unrealized))

    # -------------------- Summary --------------------
    print()
    print("=" * 72)
    print(f"Backtest complete: {len(trades):,} trades")
    print(f"Skipped signals due to missing fill bar: {skipped_no_fill_bar}")
    print()

    OUTPUT_DIR.mkdir(exist_ok=True)
    with open(OUTPUT_DIR / "trades.jsonl", "w") as f:
        for t in trades:
            f.write(json.dumps(t.__dict__) + "\n")
    ec_df = pd.DataFrame(equity_curve, columns=["ts_ms", "equity"])
    ec_df.to_csv(OUTPUT_DIR / "equity_curve.csv", index=False)

    if not trades:
        print("No trades executed.")
        return

    td = pd.DataFrame([t.__dict__ for t in trades])

    # Aggregate bps (of notional traded)
    total_notional = td["notional"].sum()
    gross_pnl = td["realized_pnl"].sum()
    gross_bps = 10_000 * gross_pnl / total_notional if total_notional else 0
    fee_bps = 10_000 * td["fee"].sum() / total_notional if total_notional else 0
    slip_bps = 10_000 * td["slippage_cost"].sum() / total_notional if total_notional else 0
    net_bps = gross_bps - fee_bps - slip_bps

    print(f"{'Metric':<22}{'Value':>16}")
    print("-" * 40)
    print(f"{'Total notional':<22}{'$' + format(total_notional, ',.0f'):>16}")
    print(f"{'Gross realized P&L':<22}{'$' + format(gross_pnl, ',.2f'):>16}")
    print(f"{'Fees paid':<22}{'$' + format(td['fee'].sum(), ',.2f'):>16}")
    print(f"{'Slippage cost':<22}{'$' + format(td['slippage_cost'].sum(), ',.2f'):>16}")
    net_pnl = gross_pnl - td['fee'].sum() - td['slippage_cost'].sum()
    print(f"{'Net P&L':<22}{'$' + format(net_pnl, ',.2f'):>16}")
    print()
    print(f"{'Gross bps (1-way)':<22}{f'{gross_bps:+.2f}':>16}")
    print(f"{'Fee bps (1-way)':<22}{f'-{fee_bps:.2f}':>16}")
    print(f"{'Slippage bps (1-way)':<22}{f'-{slip_bps:.2f}':>16}")
    print(f"{'Net bps (1-way)':<22}{f'{net_bps:+.2f}':>16}")
    print()

    # Per-symbol
    print(f"{'Symbol':<8}{'Trades':>8}{'Notional':>14}{'Gross bps':>12}{'Fee bps':>10}{'Slip bps':>10}{'Net bps':>10}")
    print("-" * 72)
    for sym in SYMBOLS:
        ss = td[td["symbol"] == sym]
        if ss.empty: continue
        n = ss["notional"].sum()
        gb = 10_000 * ss["realized_pnl"].sum() / n if n else 0
        fb = 10_000 * ss["fee"].sum() / n if n else 0
        sb = 10_000 * ss["slippage_cost"].sum() / n if n else 0
        nb = gb - fb - sb
        print(f"{sym:<8}{len(ss):>8}{'$' + format(n, ',.0f'):>14}{gb:>+12.2f}{-fb:>+10.2f}{-sb:>+10.2f}{nb:>+10.2f}")

    # Per-action (direction / event)
    print()
    td["is_realizing"] = td["realized_pnl"].abs() > 0.01
    closes = td[td["is_realizing"]]
    opens = td[~td["is_realizing"]]
    print(f"Realizing trades (closes/flips): {len(closes):>5}  notional ${closes['notional'].sum():,.0f}  realized ${closes['realized_pnl'].sum():+,.2f}")
    print(f"Pure open trades:                 {len(opens):>5}  notional ${opens['notional'].sum():,.0f}")

    # Win rate (count of realizing trades with positive realized_pnl)
    if not closes.empty:
        wins = (closes["realized_pnl"] > 0).sum()
        win_rate = 100 * wins / len(closes)
        print(f"Win rate on realizing trades: {win_rate:.1f}% ({wins}/{len(closes)})")

    # Time range / samples
    first_dt = td["fill_dt"].iloc[0]
    last_dt = td["fill_dt"].iloc[-1]
    print()
    print(f"Time range: {first_dt} → {last_dt}")
    print()
    print(f"Output: {OUTPUT_DIR}/trades.jsonl and equity_curve.csv")


if __name__ == "__main__":
    run_backtest()
