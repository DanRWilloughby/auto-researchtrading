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
MAKER_BPS = 0.00025  # CB VIP 4: 2.5 bps maker. Previously omitted — treated as 0.

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
            # ADDED 2026-04-15: split by action_type (open vs close).
            # 9-month backtest found hybrid maker is strongly +EV on closes
            # and marginally -EV on opens. Tracking these separately here
            # validates the split on real live fills before we'd deploy
            # any closes-only hybrid. See by_action['open']['fills'] etc.
            "by_action": {
                "open":  {"shadowed": 0, "fills": 0, "misses": 0, "pending": 0},
                "close": {"shadowed": 0, "fills": 0, "misses": 0, "pending": 0},
                "unknown": {"shadowed": 0, "fills": 0, "misses": 0, "pending": 0},
            },
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

        # FIX 2026-04-15: reduced from 1800 (30 min) to 240 (4 min) to match
        # the hybrid maker-then-taker spec being tested in Phase 2. The 30-min
        # window produced optimistic fill rates (~80%) that don't represent
        # what a realistic production hybrid would capture; 4 min is the
        # actual timeout we'd use before falling back to taker.
        max_wait = 240

        # Age threshold: need 30 seconds of observable 1-min candle data past
        # placed_ts before we can check. Cron runs at XX:16 for trades at
        # XX:14, so by the time check runs, we have ~2 min of candle history
        # after placed_ts — plenty for a 4-min window.
        if age_seconds < 30:
            still_pending.append(entry)
            continue

        try:
            # FIX 2026-04-15: previously used 30m candles which inflated fill
            # rates by checking price action that occurred BEFORE placed_ts.
            # A 30m candle has a bar-open timestamp; the old filter
            # `candle_ts_sec < placed_ts - 60` checked the bar-open but the
            # candle covers [bar_open, bar_open+1800). So when placed_ts
            # landed mid-bar, the candle's pre-placed price range counted
            # toward "fill", which is impossible — orders can only fill from
            # placed_ts forward.
            #
            # Switched to 1m candles. Each candle is a 60s window, so the
            # temporal leakage is bounded at 1 minute (acceptable — prices
            # within a single minute are usually stable enough that the edge
            # case of "fill happened in the first second, before we placed"
            # is negligible). Filter now strictly excludes candles whose
            # bar-open is before placed_ts.
            start_ms = int(placed_ts * 1000)
            # Only look at candles up to max_wait seconds after placed_ts.
            # Previously fetched up to now(), which meant we could count a
            # fill that happened 30 min after place even with max_wait=240.
            end_ms = int((placed_ts + max_wait) * 1000)
            candles = client.fetch_candles(symbol, "1m", start_ms, end_ms)

            if candles is not None and len(candles) > 0:
                filled = False
                fill_candle_ts = None
                window_end_sec = placed_ts + max_wait
                for _, row in candles.iterrows():
                    candle_ts_sec = row["timestamp"] / 1000
                    # Strict: candle's bar-open must be at-or-after placed_ts
                    # AND within the max_wait timeout window.
                    if candle_ts_sec < placed_ts:
                        continue
                    if candle_ts_sec > window_end_sec:
                        break
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

        # ADDED 2026-04-15: classify open vs close.
        # Uses target_pos from live trade log: if 0 after the fill, this trade
        # closed an existing position; if non-zero, it opened or modified one.
        # 9-month CB backtest showed closes are net-positive for hybrid maker
        # (+$805K across 9 mo) while opens are mixed-to-negative (-$425K). The
        # shadow needs to track these separately so we can validate the split
        # empirically on live data before activating any closes-only hybrid.
        target_pos = trade.get("target_pos", None)
        if target_pos is None:
            action_type = "unknown"
        elif abs(target_pos) < 1e-6:
            action_type = "close"
        else:
            action_type = "open"

        taker_fee = notional * TAKER_BPS + contracts * FEE_PER_CONTRACT
        # FIX 2026-04-15: previously `maker_fee = contracts * FEE_PER_CONTRACT`,
        # which omitted the 2.5 bps base rate and inflated reported maker savings
        # ~6x vs reality. CB VIP 4 maker = 2.5 bps × notional + $0.15/contract.
        # Real per-side savings vs taker = 0.5 bps × notional (~$0.19 on $3.7K trade).
        maker_fee = notional * MAKER_BPS + contracts * FEE_PER_CONTRACT

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
                "action_type": action_type,  # 'open' | 'close' | 'unknown' — see classification above
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
                # FIX 2026-04-15: placed_ts should be the actual live trade
                # time (when the maker would have been placed in production),
                # NOT when the shadow cron happens to run. The shadow cron
                # runs ~2 min after live cron, so using time.time() here
                # systematically under-measures fills that happen in the
                # first 2 minutes.
                "placed_ts": trade_ts / 1000.0,  # ms → sec
                "maker_filled": None,
            }

            st["pending_maker_orders"].append(shadow_entry)
            st["stats"]["total_trades_shadowed"] += 1
            st["stats"]["total_taker_fees_paid"] += taker_fee
            st["stats"]["total_maker_fees_hypothetical"] += maker_fee
            # ADDED 2026-04-15: per-action-type shadow count
            act_bucket = st["stats"].setdefault("by_action", {}).setdefault(
                action_type, {"shadowed": 0, "fills": 0, "misses": 0, "pending": 0})
            act_bucket["shadowed"] += 1

        log_shadow_entry({
            "type": "shadow_multi",
            "ts": time.time(),
            "trade_ts": trade_ts,
            "symbol": symbol,
            "side": side,
            "action_type": action_type,  # ADDED 2026-04-15
            "target_pos": target_pos,    # raw source for classification audit
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
            # Fallback taker pays full taker fee at the actual fill price.
            # Note: see parallel fix at line ~469 — maker_fee now correctly
            # includes the 2.5 bps base rate (not zero bps as before).
            taker_fee = notional * TAKER_BPS + abs(contracts) * FEE_PER_CONTRACT
            trade_ts = entry.get("trade_ts", int(time.time() * 1000))

            # ADDED 2026-04-15: per-action-type fill/miss tracking
            entry_action = entry.get("action_type", "unknown")
            act_bucket = st["stats"].setdefault("by_action", {}).setdefault(
                entry_action, {"shadowed": 0, "fills": 0, "misses": 0, "pending": 0})

            if entry.get("maker_filled"):
                st["stats"]["maker_would_have_filled"] += 1
                act_bucket["fills"] += 1
                te = apply_trade_to_sim(sim, symbol, side, contracts,
                                        maker_price, maker_fee, trade_ts)
                te["action"] = te["action"].replace("_MAKER_SIM", "_MAKER_FILL")
                te["fill_mode"] = "maker"
            else:
                # Hybrid fallback: maker order didn't fill in time →
                # take at the live actual-fill price with taker fee.
                # This is what the production hybrid execution path does.
                st["stats"]["maker_would_have_missed"] += 1
                act_bucket["misses"] += 1
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

    # ADDED 2026-04-15: per-action-type split (open vs close).
    # This is what we need to validate the closes-only hybrid policy
    # before considering any production activation.
    logger.info("")
    logger.info("=== Per-action split (validates the 9-mo backtest finding) ===")
    logger.info("%-12s | %-8s | %6s | %6s | %6s | %7s", "Level", "Action", "Shadow", "Fills", "Misses", "Fill %")
    logger.info("-" * 65)
    for level in PRICE_LEVELS:
        lid = level["id"]; st = states[lid]
        by_act = st["stats"].get("by_action", {})
        for act in ("open", "close", "unknown"):
            d = by_act.get(act, {})
            shadowed = d.get("shadowed", 0); fills = d.get("fills", 0); misses = d.get("misses", 0)
            if shadowed == 0 and fills == 0 and misses == 0:
                continue
            resolved = fills + misses
            fp = fills / resolved * 100 if resolved else 0
            logger.info("%-12s | %-8s | %6d | %6d | %6d | %6.1f%%",
                        lid, act, shadowed, fills, misses, fp)

    logger.info("=== Multi-Level Maker Shadow complete ===")


if __name__ == "__main__":
    main()
