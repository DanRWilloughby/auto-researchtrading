# Intra-bar Stop-Loss / Take-Profit — Exploration Memo

**Date:** 2026-04-15
**Context:** Session discussion — the 30m-concentrated strategy can only react to price at XX:14/XX:44 crons. Between those times, positions are held passively. This memo explores whether adding an intra-bar stop-loss or take-profit could improve risk-adjusted returns.

---

## The problem

Current architecture:
- Strategy bar: 30 minutes
- Cron: fires at :14 and :44 (14 min after bar close)
- Position management: ONLY at cron time
- Between crons (30-min gap): pure passive MTM exposure

When price moves violently intra-bar, we can't react. Real examples today:
- 20:14: opened 3 longs (BTC+ETH+SOL) at high conviction (4/5, 5/5, 4/5 votes)
- Between 22:14 and 22:22: positions bounced around, giving back $30-50 of unrealized gains inside the bar before the cron could evaluate

Psychologically this is painful. Economically it's also real: if we had a mechanism to lock in gains when conditions reverse sharply, we'd reduce variance and potentially improve Sharpe.

---

## What already exists in the strategy (don't rebuild this)

`strategies/30m-concentrated/strategy.py` lines 173-213 already implements several exit rules that fire at each 30-min bar close:

| Rule | Param | Logic |
|---|---|---|
| **Trailing stop (ATR)** | `ATR_STOP_MULT = 8.0` | For long: exit if price < peak − 8×ATR. For short: exit if price > peak + 8×ATR. |
| **Take-profit (percent)** | `TAKE_PROFIT_PCT = 0.012` (1.2%) | Long: exit if price > entry × 1.012. Short: exit if price < entry × 0.988. |
| **RSI overbought/oversold** | `RSI_OVERBOUGHT = 69`, `RSI_OVERSOLD = 31` | Long: exit if RSI > 69. Short: exit if RSI < 31. |
| **Signal reversal** | `MIN_VOTES = 3` | Long → short flip if 3+ bearish votes and HTF confirms. |

**These only fire at bar-close evaluation (every 30 min).** The question is: should any of them (or a new mechanism) fire between bars as well?

---

## Three candidate designs

### Design A: Intra-bar fixed stop-loss (simplest)

A separate process running every 1-5 minutes that:
1. Reads `live_state.json` for current open positions + entry prices
2. Fetches current CB price
3. If loss exceeds threshold (e.g., 1.5% of entry), force-closes via market order
4. Writes a new trade record + alerts
5. Next XX:14 cron sees position is already flat

**Pros:**
- Caps tail losses — biggest wins for risk management
- Doesn't interfere with strategy signal logic (only acts on hard losses)
- Simple implementation: one new cron, ~50 lines of code

**Cons:**
- Adds complexity to the live trader (two things can now place orders)
- Race condition with the 30-min cron (both might try to close at once)
- Premature stops on normal retracements (8% ATR exit exists for a reason — gives trades room)
- Need careful threshold — too tight and we cut winners; too loose and no benefit

**Expected value:** modest positive if calibrated right. Tail-loss insurance mainly.

### Design B: Intra-bar trailing stop (tighter version of existing)

Similar to A but tracks peak price intra-bar and exits on N×ATR pullback:
- Every 1 min, update `peak_price[symbol]` if higher (for longs)
- If current price drops more than X×ATR from intra-bar peak, exit

**Pros:**
- Locks in gains — directly addresses the "$50 give-back in 5 min" feeling
- Psychologically comforting (you see the ceiling you captured)
- Natural fit with existing ATR trailing logic

**Cons:**
- Classic "stopped out on noise" problem — whipsaw markets kill this
- Requires persistent state between cron fires (where does peak_price live?)
- Existing code updates peak_price at bar close (every 30 min); intra-bar version needs parallel state

**Expected value:** likely negative on this momentum strategy. Trailing stops reward trending markets, which is exactly when you want to HOLD for the drift. Would need careful backtest before shipping.

### Design C: Intra-bar take-profit only (asymmetric)

A minute-level process that only fires for the **take-profit direction**:
- If current price > entry × 1.X% (parameter), close the position
- Does NOT fire for losses — leaves losing positions to the 30-min cron's normal exit logic

**Pros:**
- Locks in gains without risking premature stops on losers
- Simpler decision: one threshold, one direction
- Psychologically good (lock in wins, let losers run to signal-based exit)

**Cons:**
- Asymmetric — caps upside without protecting downside
- Could prematurely exit strong runs (if signal is right and price keeps going, we left $ on table)
- Interacts with existing 1.2% TAKE_PROFIT_PCT — need to decide: replace or supplement

**Expected value:** unclear. Depends entirely on whether the strategy's big winners are "quick spikes that retrace" vs "sustained trends." For momentum strategies, typically sustained trends — so intra-bar TP would HURT average winners.

---

## What the 9-month backtest tells us

From today's closes analysis, we already have data on when CLOSE signals fire and at what P&L. Some informative patterns:

- **Total 9-month P&L was positive** even though most bars within a trade see some adverse drift before the close
- **ATR-stop exits (mid < peak - 8×ATR)** only fired on ~15% of closes
- **TP exits (price > entry × 1.012)** fired on ~30% of closes — the 1.2% target is meaningful
- **RSI exits** fired on ~10%
- **Signal-reversal exits** fired on ~45%

If we added intra-bar TP at 1.0% or 0.8%, we'd lock in SOME of those that would have hit 1.2% at bar close. But also miss the ones that went from +0.9% → +1.5% during the same bar.

**Empirical test needed**: take the 9-month backtest trade log, for each open position, walk forward minute-by-minute and check "when did price first cross +1.0% / +1.2% / +1.5%?". Compare that MTM-time to the strategy's actual close time.

If the bar's high happens early in the bar (first 10 min), intra-bar TP wins. If near close, it doesn't help. This is directly testable with the 9 months of 1-min CB data we already have.

---

## The "intra-bar volatility is painful" caveat

A lot of the motivation here is **mental-accounting**. You see the dashboard show unrealized P&L swing $50 in 5 min and it feels bad.

But:
- **Unrealized P&L will always oscillate** intra-bar. Adding intra-bar stops doesn't fix that — it just changes when they get locked in.
- If you backtest the strategy with intra-bar stops added and Sharpe doesn't improve, **the feeling doesn't justify the cost.**
- The right cognitive framing: **each trade is a 30-min decision**, don't watch the dashboard between crons. Let the strategy's exit logic work.

That said, if tail-loss insurance (Design A with a wide threshold, e.g., 2%+ adverse move) catches a flash-crash once or twice a year, it could pay for itself many times over.

---

## Recommended next steps

### Step 1 — before building anything

**Backtest the three designs on the 9-month CB dataset:**
1. Reconstruct each open trade's minute-by-minute price path using the 1-min CB candles we already have
2. For each position, measure: did an intra-bar rule trigger before the strategy's actual close? If so, what was the P&L at intra-bar trigger vs actual close?
3. Aggregate across all trades: net P&L under each design vs baseline

This gives us a straight before/after comparison with real data. About 2-4 hours of analysis work.

### Step 2 — decide based on data

- If **Design A (wide-stop tail-loss insurance) shows positive EV**: ship it. Simple, cheap, defensive.
- If **Design B (trailing) or Design C (intra-bar TP) shows positive EV**: deeper work on implementation.
- If none show positive EV: document the finding and let the 30-min cron own all exit decisions. Psychological anxiety is not a reason to add code.

### Step 3 — implementation (if warranted)

- New cron `live/intra_bar_monitor.py` running every 1-2 minutes
- Reads state, fetches price, checks configured rule
- Uses the SAME `CoinbaseClient` and risk manager as the main trader
- Writes to the same trade log (with `trigger: "intra_bar_stop"` flag)
- Telegram alert on trigger

### Step 4 — live validation

- Start in dry-run mode for 1-2 weeks (logs hypothetical triggers, doesn't execute)
- Compare hypothetical intra-bar triggers to actual cron-time outcomes
- If data consistent with backtest, activate

---

## Interaction with the current maker-shadow work

The maker shadow research (separately documented in `2026-04-15_maker_shadow_research.md`) focuses on HOW we execute trades (market vs limit). The SL/TP exploration focuses on WHEN we exit. They're orthogonal — changes to one don't conflict with the other.

If both lines of research produce positive results, the combined system would look like:
- Entry: pure taker at :14 cron (decided: hybrid doesn't help on opens)
- Exit: hybrid maker-then-taker at :14/:44 cron (if shadow validates closes hypothesis)
- **Intra-bar stop** (if SL/TP backtest shows value): fires between crons for risk events

---

## Open questions

1. **What threshold for intra-bar tail-loss stop?** Proposal: 1.5% adverse move from entry. Backtest sweep from 1.0% to 3.0%.
2. **How often should the intra-bar monitor fire?** Every 1 minute is ideal but expensive (1440 API calls/day). Every 5 min is a reasonable compromise.
3. **Should intra-bar exits be taker or hybrid maker?** Probably taker for a stop (speed matters during a flash crash). Hybrid for a TP (no urgency).
4. **Interaction with kill switches?** If intra-bar stop fires, does that count toward daily-loss limit, 24h DD, etc.? Need risk-manager integration.

---

## Summary

**The intra-bar volatility frustration is real, but the solution isn't automatic.** The 30-min cron is a design constraint that tends to work on average for this strategy, but leaves money on the table in sharp reversals.

Before building anything, we should backtest the three designs on the 9-month dataset + 1-min candles we already have. 2-4 hours of work would tell us whether any of them actually improves risk-adjusted returns.

If backtest shows positive EV → ship the simplest version (Design A, wide-stop insurance) first.
If backtest shows no edge → document, move on, accept the 30-min rhythm.

---

**Status:** Proposal only. No code committed. Revisit after the maker-shadow 2-4 week data collection is complete so we don't compound complexity.
