"""
Multi-Level Maker Shadow — simulates HYBRID limit orders at 4 spread positions.

Runs on its own cron AFTER the live trader. Zero impact on live trading.

Tests 4 maker price levels per trade:
  - bid/ask (most passive, best price, lowest fill rate)
  - bid + 25% spread / ask - 25% spread
  - mid-spread (50/50)
  - ask - 1 tick / bid + 1 tick (most aggressive maker, highest fill rate)

Hybrid execution model (2026-04-14): if the maker order doesn't fill within
max_wait (30m), the sim falls back to TAKER at the live actual_fill_price
with taker fee. This mirrors the production Phase 2 pilot behavior. Prior
versions skipped missed signals entirely, which systematically dropped
OPEN_LONG and CLOSE actions in trending markets and made the shadow data
an overly pessimistic proxy for hybrid performance.

Trade actions are tagged `_MAKER_FILL` or `_TAKER_FALLBACK`; fill_mode
field on each trade enables per-mode analysis.

Each level maintains its own simulated portfolio and equity curve,
outputting separate paper_state.json files for the dashboard.
"""
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from exchanges import CoinbaseClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [maker-shadow] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("maker-shadow")

LOGS_DIR = PROJECT_ROOT / "live" / "logs"
STATE_DIR = PROJECT_ROOT / "live" / "state"

PRODUCT_IDS = {
    "BTC": "BIP-20DEC30-CDE",
    "ETH": "ETP-20DEC30-CDE",
    "SOL": "SLP-20DEC30-CDE",
}

CONTRACT_SIZES = {
    "BTC": 0.01,
    "ETH": 0.1,
    "SOL": 5.0,
}

PRICE_INCREMENTS = {
    "BTC": 5.0,
    "ETH": 0.5,
    "SOL": 0.01,
}

INITIAL_EQUITY = 10000.0
FEE_PER_CONTRACT = 0.15
TAKER_BPS = 0.0003

# Price levels to test — fraction of spread from passive side toward aggressive
# 0.0 = at bid/ask (most passive), 1.0 = crossing the spread (taker)
PRICE_LEVELS = [
    {"id": "passive", "label": "At Bid/Ask", "spread_frac": 0.0},
    {"id": "quarter", "label": "25% Into Spread", "spread_frac": 0.25},
    {"id": "mid", "label": "Mid-Spread", "spread_frac": 0.50},
    {"id": "aggressive", "label": "Near Cross", "spread_frac": 0.90},
]


def state_file(level_id):
    return STATE_DIR / f"maker_shadow_{level_id}_state.json"


def paper_state_file(level_id):
    return STATE_DIR / f"maker_shadow_{level_id}_paper_state.json"


def load_shadow_state(level_id):
    sf = state_file(level_id)
    if sf.exists():
        with open(sf) as f:
            return json.load(f)
    now_ms = int(time.time() * 1000)
    return {
        "level_id": level_id,
        "last_processed_ts": 0,
        "pending_maker_orders": [],
        "stats": {
            "total_trades_shadowed": 0,
            "maker_would_have_filled": 0,
            "maker_would_have_missed": 0,
            "maker_pending": 0,
            "total_taker_fees_paid": 0.0,
            "total_maker_fees_hypothetical": 0.0,
        },
        "sim": {
            "initial_equity": INITIAL_EQUITY,
            "cash": INITIAL_EQUITY,
            "positions": {},
            "entry_prices": {},
            "realized_pnl": 0.0,
            "total_fees": 0.0,
            "equity_curve": [{"ts": now_ms, "equity": INITIAL_EQUITY}],
            "trade_log": [],
            "peak_equity": INITIAL_EQUITY,
        },
    }


def save_shadow_state(st):
    sf = state_file(st["level_id"])
    sf.parent.mkdir(parents=True, exist_ok=True)
    with open(sf, "w") as f:
        json.dump(st, f, indent=2, default=str)


def save_paper_state(st, client):
    sim = st["sim"]
    level_id = st["level_id"]
    positions = sim.get("positions", {})
    entry_prices = sim.get("entry_prices", {})

    unrealized = 0.0
    pos_notionals = {}
    for sym, contracts in positions.items():
        if abs(contracts) < 1e-9:
            continue
        cs = CONTRACT_SIZES.get(sym, 1.0)
        entry = entry_prices.get(sym, 0)
        try:
            mark = client.fetch_current_price(sym)
        except Exception:
            mark = entry
        if contracts > 0:
            unrealized += contracts * cs * (mark - entry)
        else:
            unrealized += abs(contracts) * cs * (entry - mark)
        pos_notionals[sym] = round(contracts * cs * mark, 2)

    equity = sim["cash"] + sim["realized_pnl"] - sim["total_fees"] + unrealized

    now_ms = int(time.time() * 1000)
    sim["equity_curve"].append({"ts": now_ms, "equity": round(equity, 4)})
    if equity > sim.get("peak_equity", INITIAL_EQUITY):
        sim["peak_equity"] = round(equity, 4)

    level_info = next((l for l in PRICE_LEVELS if l["id"] == level_id), {})
    paper = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "interval": "30m",
        "initial_equity": INITIAL_EQUITY,
        "cash": round(sim["cash"] + sim["realized_pnl"] - sim["total_fees"] - sum(abs(v) for v in pos_notionals.values()), 2),
        "positions": pos_notionals,
        "entry_prices": {sym: round(entry_prices.get(sym, 0), 6) for sym in pos_notionals},
        "equity_curve": sim["equity_curve"],
        "trade_log": sim["trade_log"],
        "last_bar_ts": {},
        "history_buffers": {},
        "peak_equity": sim.get("peak_equity", INITIAL_EQUITY),
        "strategy_state": {},
        "shadow_stats": st["stats"],
        "price_level": level_info,
    }

    psf = paper_state_file(level_id)
    with open(psf, "w") as f:
        json.dump(paper, f, indent=2, default=str)


def apply_trade_to_sim(sim, symbol, side, contracts, fill_price, fee, ts):
    cs = CONTRACT_SIZES.get(symbol, 1.0)
    signed_contracts = contracts if side == "BUY" else -contracts
    notional = abs(contracts) * cs * fill_price

    current_pos = sim["positions"].get(symbol, 0)
    current_entry = sim["entry_prices"].get(symbol, 0)

    pnl = 0.0
    if current_pos != 0:
        if (current_pos > 0 and signed_contracts < 0) or \
           (current_pos < 0 and signed_contracts > 0):
            close_qty = min(abs(current_pos), abs(contracts))
            if current_pos > 0:
                pnl = close_qty * cs * (fill_price - current_entry)
            else:
                pnl = close_qty * cs * (current_entry - fill_price)

    new_pos = current_pos + signed_contracts
    if abs(new_pos) < 1e-9:
        sim["positions"].pop(symbol, None)
        sim["entry_prices"].pop(symbol, None)
    else:
        sim["positions"][symbol] = new_pos
        sim["entry_prices"][symbol] = fill_price

    sim["realized_pnl"] += pnl
    sim["total_fees"] += fee

    if abs(new_pos) < 1e-9 and abs(current_pos) > 0:
        action = "CLOSE"
    elif abs(current_pos) < 1e-9:
        action = "OPEN_LONG" if signed_contracts > 0 else "OPEN_SHORT"
    else:
        action = "MODIFY"

    trade_entry = {
        "ts": ts,
        "time": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "action": f"{action}_MAKER_SIM",
        "price": round(fill_price, 6),
        "size": round(signed_contracts * cs * fill_price, 4),
        "target_pos": round((new_pos * cs * fill_price) if new_pos != 0 else 0, 2),
        "pnl": round(pnl, 4),
        "fee": round(fee, 4),
        "contracts": signed_contracts,
        "notional_usd": round(notional, 4),
        "fill_price": round(fill_price, 6),
        "fee_usd": round(fee, 4),
        "order_id": "MAKER_SIM",
        "dry_run": True,
    }
    sim["trade_log"].append(trade_entry)
    return trade_entry


def compute_maker_price(side, best_bid, best_ask, spread_frac, symbol):
    """Compute limit price at a given fraction into the spread."""
    spread = best_ask - best_bid
    tick = PRICE_INCREMENTS.get(symbol, 0.01)

    if side == "BUY":
        # Move from bid toward ask
        raw = best_bid + spread * spread_frac
        # Round down to tick (stay on bid side)
        price = round(raw // tick * tick, 6)
        # Don't cross the ask
        return min(price, best_ask - tick)
    else:
        # Move from ask toward bid
        raw = best_ask - spread * spread_frac
        # Round up to tick (stay on ask side)
        price = round((-(-raw // tick)) * tick, 6)
        # Don't cross the bid
        return max(price, best_bid + tick)


def fetch_order_book(client, symbol, depth=10):
    try:
        resp = client._client.get_product_book(PRODUCT_IDS[symbol], limit=depth)
        rd = resp if isinstance(resp, dict) else (
            vars(resp) if hasattr(resp, "__dict__") else {}
        )

        pricebook = rd.get("pricebook", "")
        if isinstance(pricebook, str):
            import ast
            try:
                pricebook = ast.literal_eval(pricebook)
            except Exception:
                pricebook = {}
        elif hasattr(pricebook, "__dict__"):
            pricebook = vars(pricebook)

        if not isinstance(pricebook, dict):
            pricebook = {}

        def parse_levels(levels):
            result = []
            for level in levels:
                if hasattr(level, "__dict__"):
                    level = vars(level)
                if isinstance(level, dict):
                    result.append({
                        "price": float(level.get("price", 0)),
                        "size": float(level.get("size", 0)),
                    })
            return result

        bids = parse_levels(pricebook.get("bids", []))
        asks = parse_levels(pricebook.get("asks", []))

        best_bid = bids[0]["price"] if bids else 0.0
        best_ask = asks[0]["price"] if asks else 0.0
        spread = best_ask - best_bid if best_bid > 0 and best_ask > 0 else 0.0
        spread_bps = (spread / ((best_bid + best_ask) / 2) * 10000) if spread > 0 else 0.0

        bid_depth = sum(l["size"] for l in bids)
        ask_depth = sum(l["size"] for l in asks)

        return {
            "symbol": symbol,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread": round(spread, 6),
            "spread_bps": round(spread_bps, 4),
            "bid_depth_contracts": bid_depth,
            "ask_depth_contracts": ask_depth,
            "ts": time.time(),
        }
    except Exception as e:
        logger.error("Order book fetch failed for %s: %s", symbol, e)
        return {"symbol": symbol, "error": str(e), "ts": time.time()}


def get_new_live_trades(last_ts):
    new_trades = []
    for day_offset in [1, 0]:
        d = datetime.now(timezone.utc) - timedelta(days=day_offset)
        date_str = d.strftime("%Y-%m-%d")
        log_file = LOGS_DIR / f"trades_{date_str}.jsonl"
        if not log_file.exists():
            continue
        with open(log_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    trade = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if trade.get("dry_run", True):
                    continue
                if trade.get("ts", 0) <= last_ts:
                    continue
                new_trades.append(trade)
    return new_trades


def check_pending_fills(client, pending):
    still_pending = []
    resolved = []

    for entry in pending:
        symbol = entry["symbol"]
        limit_price = entry["maker_limit_price"]
        side = entry["side"]
        placed_ts = entry["placed_ts"]
        age_seconds = time.time() - placed_ts

        if age_seconds < 60:
            still_pending.append(entry)
            continue

        max_wait = 1800

        try:
            start_ms = int(placed_ts * 1000) - 1800000
            end_ms = int(time.time() * 1000)
            candles = client.fetch_candles(symbol, "30m", start_ms, end_ms)

            if candles is not None and len(candles) > 0:
                filled = False
                fill_candle_ts = None
                for _, row in candles.iterrows():
                    candle_ts_sec = row["timestamp"] / 1000
                    if candle_ts_sec < placed_ts - 60:
                        continue
                    if side == "BUY":
                        if row["low"] <= limit_price:
                            filled = True
                            fill_candle_ts = row["timestamp"]
                            break
                    else:
                        if row["high"] >= limit_price:
                            filled = True
                            fill_candle_ts = row["timestamp"]
                            break

                entry["resolved"] = True
                entry["resolved_ts"] = time.time()

                if filled:
                    entry["maker_filled"] = True
                    entry["fill_candle_ts"] = fill_candle_ts
                    resolved.append(entry)
                elif age_seconds >= max_wait:
                    entry["maker_filled"] = False
                    entry["missed_reason"] = "price_never_reached"
                    resolved.append(entry)
                else:
                    still_pending.append(entry)
            else:
                if age_seconds >= max_wait:
                    entry["resolved"] = True
                    entry["maker_filled"] = False
                    entry["missed_reason"] = "no_candle_data"
                    resolved.append(entry)
                else:
                    still_pending.append(entry)

        except Exception as e:
            logger.warning("Fill check failed for %s: %s", symbol, e)
            if age_seconds >= max_wait:
                entry["resolved"] = True
                entry["maker_filled"] = False
                entry["missed_reason"] = f"error: {e}"
                resolved.append(entry)
            else:
                still_pending.append(entry)

    return still_pending, resolved


def log_shadow_entry(entry):
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = LOGS_DIR / f"maker_shadow_{date_str}.jsonl"
    with open(log_path, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def main():
    logger.info("=== Multi-Level Maker Shadow starting ===")
    client = CoinbaseClient(dry_run_default=True)

    # Load state for each price level
    states = {}
    for level in PRICE_LEVELS:
        states[level["id"]] = load_shadow_state(level["id"])

    # Use the minimum last_processed_ts across all levels
    min_last_ts = min(s["last_processed_ts"] for s in states.values())

    # 1. Snapshot order books
    books = {}
    for symbol in ["BTC", "ETH", "SOL"]:
        books[symbol] = fetch_order_book(client, symbol)
        if "error" not in books[symbol]:
            logger.info(
                "%s book: bid=$%s / ask=$%s spread=%.1f bps",
                symbol, books[symbol]["best_bid"],
                books[symbol]["best_ask"], books[symbol]["spread_bps"],
            )

    # 2. Find new live trades
    new_trades = get_new_live_trades(min_last_ts)
    logger.info("Found %d new live trades", len(new_trades))

    # 3. For each new trade, create shadow entries at each price level
    for trade in new_trades:
        symbol = trade["symbol"]
        book = books.get(symbol, {})
        if "error" in book or book.get("best_bid", 0) == 0:
            continue

        side = trade["action"].replace("_LIVE", "").replace("_DRYRUN", "")
        if side not in ("BUY", "SELL"):
            continue

        actual_fill = trade.get("fill_price", trade.get("price", 0))
        contracts = abs(trade.get("contracts", 0))
        notional = abs(trade.get("notional_usd", 0))
        trade_ts = trade.get("ts", 0)

        taker_fee = notional * TAKER_BPS + contracts * FEE_PER_CONTRACT
        maker_fee = contracts * FEE_PER_CONTRACT

        for level in PRICE_LEVELS:
            lid = level["id"]
            st = states[lid]

            if trade_ts <= st["last_processed_ts"]:
                continue

            maker_price = compute_maker_price(
                side, book["best_bid"], book["best_ask"],
                level["spread_frac"], symbol
            )

            shadow_entry = {
                "type": "shadow_comparison",
                "level_id": lid,
                "ts": time.time(),
                "trade_ts": trade_ts,
                "symbol": symbol,
                "side": side,
                "contracts": contracts,
                "notional_usd": notional,
                "actual_fill_price": actual_fill,
                "maker_limit_price": maker_price,
                "maker_fee": round(maker_fee, 4),
                "book_best_bid": book["best_bid"],
                "book_best_ask": book["best_ask"],
                "book_spread_bps": book["spread_bps"],
                "spread_frac": level["spread_frac"],
                "placed_ts": time.time(),
                "maker_filled": None,
            }

            st["pending_maker_orders"].append(shadow_entry)
            st["stats"]["total_trades_shadowed"] += 1
            st["stats"]["total_taker_fees_paid"] += taker_fee
            st["stats"]["total_maker_fees_hypothetical"] += maker_fee

        log_shadow_entry({
            "type": "shadow_multi",
            "ts": time.time(),
            "trade_ts": trade_ts,
            "symbol": symbol,
            "side": side,
            "contracts": contracts,
            "actual_fill": actual_fill,
            "book_bid": book["best_bid"],
            "book_ask": book["best_ask"],
            "levels": {
                l["id"]: compute_maker_price(
                    side, book["best_bid"], book["best_ask"],
                    l["spread_frac"], symbol
                ) for l in PRICE_LEVELS
            },
        })

    # Update last_processed_ts for all levels
    if new_trades:
        max_ts = max(t.get("ts", 0) for t in new_trades)
        for st in states.values():
            st["last_processed_ts"] = max(st["last_processed_ts"], max_ts)

    # 4. Resolve pending fills for each level
    for level in PRICE_LEVELS:
        lid = level["id"]
        st = states[lid]
        sim = st["sim"]

        pending = st.get("pending_maker_orders", [])
        still_pending, resolved = check_pending_fills(client, pending)

        for entry in resolved:
            symbol = entry["symbol"]
            side = entry["side"]
            contracts = int(entry["contracts"])
            maker_price = entry["maker_limit_price"]
            maker_fee = entry["maker_fee"]
            actual_fill = entry.get("actual_fill_price", maker_price)
            notional = entry.get("notional_usd") or \
                abs(contracts) * CONTRACT_SIZES.get(symbol, 1.0) * actual_fill
            taker_fee = notional * TAKER_BPS + abs(contracts) * FEE_PER_CONTRACT
            trade_ts = entry.get("trade_ts", int(time.time() * 1000))

            if entry.get("maker_filled"):
                st["stats"]["maker_would_have_filled"] += 1
                te = apply_trade_to_sim(sim, symbol, side, contracts,
                                        maker_price, maker_fee, trade_ts)
                te["action"] = te["action"].replace("_MAKER_SIM", "_MAKER_FILL")
                te["fill_mode"] = "maker"
            else:
                # Hybrid fallback: maker order didn't fill in time →
                # take at the live actual-fill price with taker fee.
                # This is what the production hybrid execution path does.
                st["stats"]["maker_would_have_missed"] += 1
                st["stats"].setdefault("taker_fallback_count", 0)
                st["stats"]["taker_fallback_count"] += 1
                te = apply_trade_to_sim(sim, symbol, side, contracts,
                                        actual_fill, taker_fee, trade_ts)
                te["action"] = te["action"].replace("_MAKER_SIM", "_TAKER_FALLBACK")
                te["fill_mode"] = "taker_fallback"

        st["pending_maker_orders"] = still_pending
        st["stats"]["maker_pending"] = len(still_pending)

    # 5. Save state and paper state for each level
    for level in PRICE_LEVELS:
        lid = level["id"]
        st = states[lid]
        save_paper_state(st, client)
        save_shadow_state(st)

    # 6. Summary
    logger.info("=== Summary ===")
    logger.info("%-12s | %6s | %6s | %6s | %7s | %10s", "Level", "Shadow", "Filled", "Missed", "Fill %", "Sim Equity")
    logger.info("-" * 65)
    for level in PRICE_LEVELS:
        lid = level["id"]
        st = states[lid]
        stats = st["stats"]
        filled = stats["maker_would_have_filled"]
        missed = stats["maker_would_have_missed"]
        total_resolved = filled + missed
        fill_pct = filled / total_resolved * 100 if total_resolved > 0 else 0
        ec = st["sim"]["equity_curve"]
        equity = ec[-1]["equity"] if ec else INITIAL_EQUITY
        logger.info("%-12s | %6d | %6d | %6d | %6.1f%% | $%9.2f",
                    lid, stats["total_trades_shadowed"], filled, missed, fill_pct, equity)

    logger.info("=== Multi-Level Maker Shadow complete ===")


if __name__ == "__main__":
    main()
