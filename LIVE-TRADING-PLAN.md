# Live Trading Plan — 30m-Concentrated

**Status:** Planning (not yet started)
**Capital:** $10K starting, 1.2x leverage
**Strategy:** 30m-concentrated (BTC/ETH/SOL) — **fully bidirectional (longs AND shorts)**
**Date:** 2026-03-31 (updated 2026-04-10)

---

## Strategy Requirements (constraints on platform choice)

The 30m-concentrated strategy is **fully bidirectional**:
- Goes **long** when bull votes >= 3 AND 1h trend is bullish
- Goes **short** when bear votes >= 3 AND 1h trend is bearish
- Can **flip directly** from long → short (and vice versa) without going flat first
- Uses **1.2x leverage** across 3 coins with per-coin weighting (BTC 20%, ETH 30%, SOL 50%)

This means **spot-only platforms cannot run this strategy faithfully**. You need perpetual futures or equivalent short-selling capability.

### Platform Comparison

| Platform | Shorts? | Leverage? | SOL? | API | Tax Reporting | Regulatory | Verdict |
|---|---|---|---|---|---|---|---|
| **Coinbase Perps** | Yes (perp-style futures) | Up to 10x | Yes | Yes (`coinbase-advanced-py`) | 1099 | CFTC-regulated DCM | **Best overall option** — regulated, all 3 coins, competitive fees |
| **Hyperliquid** | Yes (perps) | Up to 50x | Yes | Yes | DIY | Gray area (offshore DEX) | Best depth for BTC, but regulatory risk |
| **IBKR + CME Futures** | Yes (futures) | Yes (margin) | No | Excellent | 1099 + Section 1256 | Clean | No SOL, need contract rolling |
| **dYdX** | Yes (perps) | Up to 20x | Yes | Yes | DIY | Gray area (same as HL) | Alternative to Hyperliquid |
| Robinhood | No | No | Yes | Yes | 1099 | Clean | **Cannot run this strategy** (no shorts) |
| Alpaca | No | No | Yes | Yes | 1099 | Clean | **Cannot run this strategy** (no shorts) |

### Platform Deep Dive: Coinbase Perps (added 2026-04-10)

Coinbase launched CFTC-regulated perpetual-style futures (via Coinbase Financial Markets, a registered DCM) in July 2025. These are technically long-dated futures with 5-year expirations (Dec 2030) that functionally behave like perps — no monthly rollovers needed.

**Supported coins:** BTC, ETH, SOL, DOGE, AVAX, LINK, ADA, DOT, SUI, XRP, SHIB, BCH, LTC, HBAR, XLM (15 total — all 10 of our target coins covered)

**Fees (current promotional):** 0.00% maker / 0.03% taker — cheaper than Hyperliquid (0.01% / 0.035%). Standard tiered rates apply when promo expires (0.60%/0.40% at <$10K volume, scaling down).

**API:** Full programmatic trading via Advanced Trade API. REST endpoints for orders, positions, portfolio. WebSocket for level2, ticker, balance. JWT auth. Official Python SDK: `coinbase-advanced-py` with dedicated perps methods (`get_perps_portfolio_summary()`, `list_perps_positions()`, `market_order_buy()`, etc.).

**Funding rate:** Calculated hourly (not 8-hourly like Hyperliquid). Sampled every 3 min.

**Regulatory comparison:**

| Dimension | Coinbase | Hyperliquid |
|---|---|---|
| Regulator | CFTC-regulated DCM (since 2020) | None — offshore DEX |
| US legal status | Explicitly legal for US retail | Gray area — no US regulatory approval |
| Custody | Custodial (NASDAQ: COIN) | Non-custodial (smart contracts, anon team) |
| Tax reporting | 1099s issued | DIY |
| Consumer protections | CFTC oversight, segregated funds | None |
| Max leverage | 10x | 50x |
| Counterparty risk | Low (publicly traded company) | High (JELLY incident Mar 2025, $10.6M vault loss) |

### Decision: Coinbase Perps (primary), Hyperliquid (secondary/data)

**Coinbase Perps** is the primary execution venue. It supports all 3 coins, offers competitive fees, eliminates regulatory risk, and handles tax reporting. The 10x leverage cap is irrelevant — strategy uses 1.2x.

**Hyperliquid** remains valuable as a data source (candle + funding rate history for backtesting) and as a secondary venue for multi-exchange execution at scale.

**Optional future addition:** Run a 2-coin variant (BTC/ETH only) on IBKR via CME Micro Futures for Section 1256 tax treatment (60% long-term / 40% short-term). Evaluate after 30+ days of live results.

### Exchange Refactoring Required

The codebase currently has Hyperliquid API calls hardcoded in two files:
- `engine/prepare.py` (~200 lines) — candle + funding rate downloads
- `paper/trader.py` (~50 lines) — live price fetching

Refactor into an exchange abstraction layer:

```
ExchangeClient (ABC)
├── fetch_candles(symbol, interval, start, end) → DataFrame
├── fetch_funding(symbol, start, end) → DataFrame
├── place_order(symbol, side, size, order_type) → OrderResult
├── get_positions() → dict
├── get_portfolio() → PortfolioState
│
├── HyperliquidClient   (keep for backtesting data + secondary execution)
└── CoinbaseClient      (new, primary for live + paper trading)
```

Strategy files, backtest engine, and portfolio management are already exchange-agnostic — no changes needed there.

---

## Dan's Pre-Work (before our build session)

### Coinbase (Primary)
- [ ] Upgrade to Coinbase Advanced account (if not already)
- [ ] Complete perps eligibility assessment (suitability screening in-app)
- [ ] Generate API keys for Advanced Trade (JWT-based)
- [ ] Allocate $10K to perps portfolio via `allocate_portfolio()` or UI
- [ ] Verify BTC, ETH, SOL perp markets are accessible from your state

### Hyperliquid (Secondary / Data Source)
- [ ] Get a hardware wallet (Ledger Nano X or S Plus) if you don't have one
- [ ] Create a **dedicated trading wallet** (new MetaMask/Rabby) — used ONLY for this
- [ ] Optional: Fund with small amount ($500) for secondary execution testing
- [ ] Create a Hyperliquid **API sub-wallet** (trade-only, no withdraw permissions)

### General
- [ ] Sign up for Koinly (free tier is fine to start)
- [ ] Optional: 30-min consult with crypto tax accountant re: perp futures treatment
- [ ] Set up Telegram bot for alerts (BotFather → create bot → save token)

## Build Together (estimated ~6 hours)

### Phase 1: Exchange Abstraction + Coinbase Client (2 hours)
- Build `ExchangeClient` ABC and `CoinbaseClient` implementation using `coinbase-advanced-py`
- Implement: `fetch_candles()`, `fetch_funding()`, `place_order()`, `get_positions()`, `get_portfolio()`
- Keep `HyperliquidClient` for historical data and optional secondary execution
- Update `paper/trader.py` to use `CoinbaseClient` for live price data
- Handle Coinbase-specific: hourly funding (vs 8h on HL), product IDs, contract sizing

### Phase 2: Safety & Risk Management Layer (2 hours)
- Build `RiskManager` module (see Safety Architecture section below)
- Implement circuit breakers, flash crash guard, kill switch
- Wire into trading loop — all signals pass through RiskManager before execution
- Add Telegram alert dispatch for all safety events

### Phase 3: Live Order Execution (1 hour)
- Swap simulated fills for real Coinbase perps orders
- Limit orders with max slippage deviation
- Order confirmation and retry logic
- Handle long/short/flip order flow
- Fill verification: confirm position matches expected state after each trade
- Run paper and live side by side for tracking error comparison

### Phase 4: VM Security + Monitoring (1 hour)
- Create dedicated `trader` user on VM (separate from `openclaw`)
- Store Coinbase API keys in `/etc/secrets/` with `0600` permissions
- UFW firewall — outbound only to Coinbase + Hyperliquid API endpoints
- Fail2ban on SSH (defense in depth on top of Tailscale)
- Daily encrypted backup of trade state to Dan's Mac
- Configure trade logging: timestamp, pair, side, size, price, fee, realized PnL per trade
- Daily CSV export for backup
- Connect Koinly (CSV import or API)
- Add live tab to Vercel dashboard: real equity vs paper equity, fill quality, fee actuals

## Validation Protocol

1. **48-hour minimum size test** — run live at $100 positions alongside paper
2. Compare: slippage per trade, fill rate, fee actuals vs estimates
3. Verify shorts execute correctly (open, close, and flip scenarios)
4. If tracking error < 1% of paper performance → scale to full $10K
5. If tracking error > 1% → diagnose and fix before scaling

## Architecture

```
[Coinbase Advanced Account]       ← CFTC-regulated, perp-style futures
        ↓ API keys (JWT)
[VM: trader.py --live]            ← cron every 30min, CoinbaseClient
        ├─ RiskManager             ← circuit breaker, flash crash guard, kill switch
        ├─ FlashCrashGuard thread  ← WebSocket price monitor between bars
        ├─ AlertDispatch           ← Telegram notifications
        └─ Watchdog (systemd)      ← auto-restart if trader goes silent

Optional secondary:
[Hyperliquid L1 Account]          ← secondary venue for multi-exchange execution at scale
        ↓ API sub-wallet
[VM: same trader.py]              ← ExchangeClient abstraction routes to both venues
```

## Safety & Risk Management Architecture

### Overview

The `RiskManager` module wraps the entire trading loop. Every signal passes through it before execution. The flash crash guard runs on a separate thread, monitoring prices between bars.

```
┌──────────────────────────────────────────────────────────────┐
│  RiskManager                                                 │
│                                                              │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ CircuitBreaker                                          │ │
│  │  ├─ max_dd_24h: 5%          → flatten all + halt        │ │
│  │  ├─ max_dd_session: 3%      → flatten all + halt        │ │
│  │  ├─ max_loss_daily: $500    → flatten all + halt        │ │
│  │  └─ manual_kill: file flag  → flatten all + halt        │ │
│  └─────────────────────────────────────────────────────────┘ │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ FlashCrashGuard (separate thread, runs between bars)    │ │
│  │  ├─ WebSocket price feed (Coinbase + Hyperliquid)       │ │
│  │  ├─ Check every 5 seconds against thresholds            │ │
│  │  ├─ Per-position: |move| > 3% from entry → emergency   │ │
│  │  ├─ Portfolio-wide: unrealized PnL < -2% → emergency   │ │
│  │  └─ Emergency = market close all + alert + halt         │ │
│  └─────────────────────────────────────────────────────────┘ │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ PositionLimits                                          │ │
│  │  ├─ max_leverage: 1.2x (NOT the current 20x)           │ │
│  │  ├─ max_notional_per_coin: configurable per coin        │ │
│  │  ├─ max_concentration: 50% of equity per coin           │ │
│  │  └─ max_open_positions: 3                               │ │
│  └─────────────────────────────────────────────────────────┘ │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ DataGuard                                               │ │
│  │  ├─ Stale candle check: latest ts within 2x interval    │ │
│  │  ├─ Price sanity: reject if price deviates > 10% from   │ │
│  │  │  last known bar (likely bad data, not real move)      │ │
│  │  ├─ Funding rate alert: > 0.5% per 8h → warn           │ │
│  │  └─ Volume anomaly: < 20% of 7d avg → reduce sizing    │ │
│  └─────────────────────────────────────────────────────────┘ │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ OrderSafety                                             │ │
│  │  ├─ Max slippage: reject fill if > X bps from expected  │ │
│  │  ├─ Order timeout: cancel unfilled limits after 30s     │ │
│  │  ├─ Fill verification: confirm position matches intent  │ │
│  │  └─ Retry with backoff: 3 attempts, then alert + skip   │ │
│  └─────────────────────────────────────────────────────────┘ │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ CorrelationGuard                                        │ │
│  │  ├─ Detect correlated drawdown across BTC/ETH/SOL       │ │
│  │  ├─ If all 3 positions losing simultaneously > 1.5%     │ │
│  │  │  → reduce exposure by 50% (not full flatten)         │ │
│  │  └─ Re-expand on next signal if conditions normalize    │ │
│  └─────────────────────────────────────────────────────────┘ │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ AlertDispatch                                           │ │
│  │  ├─ Telegram bot (primary)                              │ │
│  │  ├─ Log file (always)                                   │ │
│  │  └─ Kill switch state file (/etc/trader/kill.flag)      │ │
│  │                                                         │ │
│  │  Alert types:                                           │ │
│  │  ├─ TRADE: every entry/exit with size, price, PnL       │ │
│  │  ├─ DAILY: equity, DD, positions, funding costs         │ │
│  │  ├─ WARNING: stale data, high funding, volume anomaly   │ │
│  │  ├─ CIRCUIT_BREAKER: DD threshold hit, all flattened    │ │
│  │  ├─ FLASH_CRASH: emergency exit triggered               │ │
│  │  ├─ KILL_SWITCH: manual halt activated                  │ │
│  │  └─ HEARTBEAT: every 6h confirming bot is alive         │ │
│  └─────────────────────────────────────────────────────────┘ │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ Watchdog (separate process)                             │ │
│  │  ├─ Monitors trader process health                      │ │
│  │  ├─ If no tick logged in > 2 intervals → restart + alert│ │
│  │  ├─ Heartbeat file updated each tick                    │ │
│  │  └─ Runs as systemd service                             │ │
│  └─────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

### The Flash Crash Problem (Why This Matters)

Current 30m bar interval means worst case 29 minutes of blindness between bars. The FlashCrashGuard solves this with a separate WebSocket thread:

| Scenario | Without inter-bar monitor | With FlashCrashGuard |
|---|---|---|
| SOL drops 12% in 5 min | Stop evaluates 25 min later, loss ~12% | Emergency exit at -3%, loss capped |
| BTC flash wick -8%, recovers | Stop may trigger on bar close at wrong price | Real-time evaluation, exits on breach |
| Exchange data gap | Keeps stale positions, no awareness | Detects missing data, alerts, holds |
| Correlated crash (all 3 coins) | Each position hits stop independently (3x loss) | Portfolio-level guard fires first, cuts exposure faster |

### Kill Switch Mechanisms

Three ways to halt trading immediately:

1. **File-based kill switch:** Touch `/etc/trader/kill.flag` → trader detects on next tick (or WebSocket check), flattens all, halts. Useful for manual intervention via SSH.
2. **Telegram command:** Reply `/kill` to bot → bot creates kill flag, same behavior. For remote intervention from phone.
3. **Automatic:** Circuit breaker or flash crash guard triggers → automatic flatten + halt + alert.

To resume after a kill: delete the flag file and restart the trader process. Requires deliberate human action.

### Configuration (all thresholds in one config file)

```yaml
# risk_config.yaml
circuit_breaker:
  max_dd_24h_pct: 5.0          # halt if 5% drawdown in rolling 24h
  max_dd_session_pct: 3.0      # halt if 3% drawdown since bot start
  max_daily_loss_usd: 500      # halt if $500 lost in calendar day

flash_crash_guard:
  enabled: true
  check_interval_sec: 5        # poll price every 5 seconds
  per_position_threshold_pct: 3.0   # exit if position moves 3% against
  portfolio_threshold_pct: 2.0      # exit all if portfolio unrealized PnL < -2%
  websocket_sources:
    - coinbase                  # primary
    - hyperliquid               # secondary / cross-check

position_limits:
  max_leverage: 1.2
  max_notional_per_coin:
    BTC: 5000                   # at $10K capital
    ETH: 5000
    SOL: 5000
  max_concentration_pct: 50     # no single coin > 50% of equity
  max_open_positions: 3

data_guard:
  stale_candle_max_age_intervals: 2   # alert if candle is 2+ intervals old
  price_deviation_pct: 10.0           # reject if price jumps 10% from last bar
  funding_rate_alert_threshold: 0.005 # 0.5% per 8h
  volume_anomaly_threshold: 0.2       # alert if volume < 20% of 7d average

order_safety:
  max_slippage_bps: 10          # reject fill if > 10 bps from expected
  order_timeout_sec: 30         # cancel unfilled limits after 30s
  max_retries: 3
  retry_backoff_sec: [2, 5, 10]

alerts:
  telegram_bot_token: "${TELEGRAM_BOT_TOKEN}"
  telegram_chat_id: "${TELEGRAM_CHAT_ID}"
  heartbeat_interval_hours: 6
  daily_summary_hour_utc: 14    # 10am ET

watchdog:
  max_silent_intervals: 2       # restart if no tick in 2 intervals (1 hour)
  heartbeat_file: /etc/trader/heartbeat
  kill_flag_file: /etc/trader/kill.flag
```

## Risk Guardrails

- **Max capital at risk:** $10K (hard cap, never add more without deliberate decision)
- **Circuit breaker:** 5% equity drawdown in 24h → flatten all, halt, Telegram alert
- **Flash crash guard:** WebSocket inter-bar monitoring, emergency exit if position moves > 3% against
- **Kill switch:** File-based + Telegram command + automatic triggers
- **Correlation guard:** If all 3 coins losing simultaneously > 1.5%, reduce exposure 50%
- **Coinbase API keys:** Scoped to trading only (no withdrawal permissions)
- **Leverage:** 1.2x only — code-enforced, config-driven, NOT the old 20x default
- **Position limits:** Max notional per coin, max concentration, max 3 open positions
- **Data validation:** Stale candle detection, price sanity bounds, funding rate alerts
- **Order safety:** Max slippage, timeout, fill verification, retry logic
- **Watchdog:** Separate process, auto-restart if trader goes silent, Telegram alert
- **Daily review:** Check live vs paper tracking error every morning

## Capital Deployment & Market Capacity (researched 2026-04-10)

How much capital can this strategy absorb before market impact erodes the edge?

### Order Book Depth (BTC+ETH+SOL perps, ±1% of mid-price, aggregate across venues)

| Coin | ±0.1% Depth | ±0.5% Depth | ±1.0% Depth | 24h Volume |
|---|---|---|---|---|
| BTC | ~$85-110M | ~$240-310M | ~$530-660M | ~$35B |
| ETH | ~$65-85M | ~$160-210M | ~$340-490M | ~$29B |
| SOL | ~$10-16M | ~$42-55M | ~$80-105M | ~$7B |

### Per-Exchange Capacity (BTC+ETH+SOL combined, market orders)

| Exchange | Max Capital (market orders) | Notes |
|---|---|---|
| **Binance** | $5-10M | Deepest books by far, 40% of global volume |
| **OKX** | $2-5M | Strong ETH depth |
| **Coinbase** | $1-3M | Growing, regulatory advantage |
| **Hyperliquid** | $500K-2M | Good BTC/ETH top-of-book, thin SOL |
| **Bybit** | $2-4M | |

### Scaling Tiers

| Capital | Impact | Execution Approach |
|---|---|---|
| **$10K** (starting) | Zero (<0.1 bps) | Single exchange, market orders fine |
| **$100K** | Negligible (<0.5 bps) | Single exchange, market orders fine |
| **$500K** | Minimal (<1 bps) | Single exchange, limit orders preferred |
| **$2M** | Low (1-2 bps) | Single exchange, limit orders |
| **$5-10M** | Manageable (2-5 bps) | Split across 2-3 exchanges |
| **$10-20M** | Noticeable (5-10 bps) | 3-4 exchanges, TWAP execution needed |
| **$20-50M** | Significant | BTC+ETH only (drop SOL), algorithmic execution |
| **$50M+** | Edge likely deteriorating | OTC desks required |

**Binding constraint:** SOL is the thinnest market. At $5M+ capital, SOL becomes the bottleneck. If SOL is dropped or its weight reduced, the strategy can absorb significantly more capital.

**The 1% rule:** You can trade ~1% of a coin's 30-minute volume without meaningful impact. At 30-min intervals: BTC ~$7.3M, ETH ~$6.0M, SOL ~$1.5M per trade.

### Multi-Exchange Execution (future, at scale)

When capital exceeds single-exchange capacity, the exchange abstraction layer supports splitting orders:

```
OrderRouter
├─ Assess depth on each connected exchange (real-time book snapshots)
├─ Split order proportional to available depth
├─ Execute in parallel across venues
├─ Reconcile fills and update portfolio state
└─ Log venue-level execution quality for analysis
```

This is not needed at $10K. Build it when scaling past $500K.

## Operations Log

### 2026-04-11: Live deployment + first 24h of 3-instance comparison

**19:08 UTC — CB Live and CB Paper go live.** All three instances (HL Paper, CB Live, CB Paper) running the same 30m-concentrated strategy at staggered timings (:15/:45, :14/:44, :02/:32). CB Live at $10K real money on Coinbase perps. CB Paper at $10K simulated. HL Paper already running 15 days on Hyperliquid at ~$150K equity.

**19:08–20:32 UTC — Startup phase.** CB Live's cold start burned the 19:00 bar as DRYRUN during initialization, causing it to enter one bar behind HL Paper. All three eventually synchronized on the 20:00 bar — same direction (long BTC/ETH/SOL), same decision. CB Live and HL Paper are 1 minute apart on the same bar (:14 vs :15). CB Paper fires 12 minutes earlier (:02/:32) as the "early bird" timing test.

**First round trip results (same strategy, different timing):**
- HL Paper: +$457.61 realized (exited at 19:45 near local peak) — NOTE: on a different bar than CB due to startup offset, not a clean comparison
- CB Paper: +$1.96 realized (exited at 20:02, 12 min earlier than CB Live)
- CB Live: −$50.90 realized (exited at 20:14, caught the trough)
- First signal of timing hypothesis: CB Paper's 12-min-earlier exit avoided the worst of the dip

**23:14 UTC — Strategy flips BTC short.** Both HL Paper (23:15) and CB Live (23:14) flip BTC to short on the same 23:00 bar — 1 minute apart, confirming steady-state synchronization. HL also flips SOL; CB Live does not (indicator threshold difference between Hyperliquid and Coinbase data feeds). CB Paper misses this bar entirely because the 23:02 tick couldn't get the 23:00 bar data from Coinbase (data availability lag).

**23:14–03:32 UTC — CB Paper maintenance downtime (7 hours).** Multiple dashboard and trader.py fixes required to resolve:
1. **Position tracking bug**: `_resync_state_post_orders` wrote pre-order positions (state showed `positions:{}` after a BUY). Fixed by querying Coinbase AFTER orders land.
2. **Equity stuck at $10K**: Coinbase's `total_usd_balance` CFM field was missing/stale. Fixed by computing equity from `initial + daily_realized_pnl + unrealized`.
3. **CB Paper shared-account contamination**: Paper instance was reading CB Live's positions from the shared Coinbase account, generating confused signals. Fixed by adding independent `sim_open_lots` tracker and `_execute_paper_sim()` that bypasses `place_market_order` entirely.
4. **Midnight rollover double-count**: Assumed Coinbase's `daily_realized_pnl` resets at UTC midnight. It does NOT — it's inception-to-date and includes fees. Rollover logic added yesterday's total to a field that already contained it, doubling realized losses ($9,770 shown vs $9,890 actual). Fixed by reading `daily_realized_pnl` directly as total P&L.
5. **Trade history missing P&L/fees**: Coinbase fill API doesn't return per-trade pnl or fees. Fixed with backfill script (FIFO matching + fee cost model) and ongoing enrichment in `_enrich_trade_fields`.
6. **Paper sim accumulation bug**: `place_market_order(dry_run=True)` returned the full order size (not the delta from sim state), so every tick added to positions instead of maintaining target. Fixed by `_execute_paper_sim()` which computes deltas against `sim_open_lots`.

During this 7-hour window, CB Live made 26 trades (actively cycling), while CB Paper sat frozen holding long BTC/ETH/SOL from 20:32 entries. Market dropped 2-3% (BTC 73420→71770, ETH 2300→2220, SOL 85.36→82.60).

**03:32 UTC — CB Paper resumes.** First tick with new paper-sim code. Paper sim was still holding longs from 20:32. Strategy signals "go short." Closes longs at a **−$341.36 realized loss** (the accumulated 7 hours of holding through a 2-3% drop). Opens shorts. This single event is ~95% of the performance gap between CB Live and CB Paper.

**Performance at +19 hours (14:09 UTC Apr 12):**

| Instance | Equity | Return | Trades | Status |
|---|---|---|---|---|
| CB Live | $10,134 (Coinbase cash) | +1.34% | 58 | Flat, profitable, running clean |
| CB Paper | $9,661 | −3.39% | 23 | Short BTC/ETH/SOL, sim working correctly |
| HL Paper | ~$154,843 | +2.93% since 19:08 | active | Flat, most active trader |

**The −4.7% gap between CB Live and CB Paper is NOT a strategy divergence.** It's entirely attributable to the 7-hour maintenance window where CB Paper was frozen holding longs during a drop. CB Live navigated this by actively trading (flipping short at 23:14, cycling through 26 trades). Since CB Paper resumed at 03:32, the new sim code is running correctly — making independent decisions from its own `sim_open_lots`, skipping when already at target, computing proper FIFO P&L on position changes.

**Key infrastructure learnings:**
- Coinbase's `daily_realized_pnl` is inception-to-date (not daily-resetting) and INCLUDES fees. Don't build rollover logic around it.
- Coinbase's `total_usd_balance` is unreliable during pending spot↔futures transfers. Use `available_margin + unrealized` for equity.
- Paper sim instances sharing a Coinbase account MUST track their own position state independently. Reading from `client.get_positions()` returns the sibling live account's positions.
- Dashboard equity formula (`cash + positions + unrealized`) requires `cash` to be HL-semantic (initial + realized − fees − exposure). Raw Coinbase `available_margin` breaks this formula.
- **Use `available_margin + unrealized` for equity** (not `initial + daily_realized + unrealized`). The `available_margin` is actual Coinbase cash and doesn't lag. Add 5-second delay before querying post-order to let fills settle.

### 2026-04-12: Dashboard P&L consistency + projection model overhaul

**Equity card fix:** Switched from position-based computation (`cash + positions + unrealized`) to `equity_curve[-1]` as source of truth for the main equity metric card, drawdown, and all summary displays.

**Equity source fix:** Switched trader.py from `initial + daily_realized_pnl + unrealized` to `available_margin + unrealized`. The `daily_realized_pnl` field lags behind settled cash by $20-100. `available_margin` IS the actual cash. Added 5-second post-order settlement delay before querying.

**Hourly P&L chart fix:** Was computing per-hour P&L from trade-log FIFO sums (broken, double-counted fees). Switched to equity_curve deltas grouped by hour — same source as everything else.

**Trade reconciliation:** Added `get_order_fill_details()` to CoinbaseClient. After each live order, trader.py now queries Coinbase for the ACTUAL `average_filled_price` and `total_fees` (with 0.5s settlement delay) and records those instead of the pre-order estimate. Reconciled all 60 existing trades against Coinbase — found fill prices off by $1-$200+ per trade.

**Trade history summary bar:** Added Gross P&L / Fees / Net breakdown. Gross and Net derived from equity_curve (Coinbase ground truth), fees from sum of per-order Coinbase-settled amounts.

**CB Paper independent sim:** Paper instance now has its own `_execute_paper_sim()` that computes position deltas from `sim_open_lots` and fetches live prices for fills. No longer calls `place_market_order(dry_run=True)` which was causing position accumulation. Strategy portfolio built from sim state, not shared Coinbase account.

**Return projection model overhaul:** For live strategies, transaction costs (slippage, spread, fees, funding, impact) are already baked into the observed NET returns. The projection now starts from NET daily return and applies ONLY forward-looking structural assumptions (alpha decay 15%/yr, adverse regime 25%, execution miss 3%, operational risk 3%). Transaction cost sliders are available but disabled by default for live data to prevent double-counting. For paper strategies or what-if modeling, they can be re-enabled.

**Fee structure confirmed from live data:**
- Per-contract regulatory fee ($0.15) = ~4.2 bps weighted avg (BTC 2.0, SOL 3.5, ETH 6.5 bps). Does NOT dilute with scale — contracts scale proportionally with notional.
- At $250K+ volume (~$234M/month), qualifies for Coinbase Tier 5: 0.5 bps maker / 2.0 bps taker.
- Switching to limit orders at $500K+ drops total per-trade cost from ~9.7 bps to ~4.5 bps (51% reduction).

---

## Known Risks

1. **OOS overfitting:** Backtest test split blew up at current config (11.6% max DD, score -999). Paper trading has not yet seen a real stress event.
2. **Fee drag:** Shadow curves show pessimistic fee scenario nearly zeros out returns. Real slippage will be somewhere between paper and pessimistic. Coinbase promotional fees (0.00%/0.03%) are favorable but may expire.
3. **Execution gap:** 30-minute cron interval means you can't react to flash crashes/pumps between bars. Stops only fire on next bar. **Mitigated by FlashCrashGuard** (inter-bar WebSocket monitoring).
4. **Short squeeze risk:** Strategy shorts during bearish signals. A sudden trend reversal (e.g., surprise Fed announcement, ETF approval) could gap against short positions before the next bar check. **Mitigated by FlashCrashGuard + CorrelationGuard.**
5. **Coinbase perps maturity:** US perps launched July 2025 — relatively new product. WebSocket perps support may be less mature than spot. Test thoroughly in paper before live.
6. **Coinbase "perpetual-style" contracts:** These actually expire in 5 years (Dec 2030), though they're functionally identical to true perps. No rollover needed for practical purposes but technically not perpetual.
7. **Custodial risk:** Coinbase holds funds (unlike self-custody on Hyperliquid). Mitigated by Coinbase being publicly traded (NASDAQ: COIN) with CFTC oversight and segregated funds.

## Future Scaling Path

### Phase A: Prove the edge ($10K, Coinbase only)
- Run live at $10K for 30+ days
- Compare live vs paper tracking error
- Validate fill quality, fee actuals, funding costs
- Target: replicate >60% of paper performance after costs

### Phase B: Scale on Coinbase ($10K → $100K)
- If Phase A tracking error < 1%, increase capital gradually
- $10K → $25K → $50K → $100K over 60 days
- No execution changes needed — single exchange is fine at this scale
- Add limit orders if market order slippage exceeds 2 bps

### Phase C: Multi-exchange ($100K → $2M)
- Build exchange abstraction + CoinbaseClient + additional exchange clients
- Add Binance or OKX as secondary venue (if US access viable via VPN or international entity)
- Implement order splitting proportional to book depth
- Consider IBKR CME Micro Futures for BTC/ETH with Section 1256 tax treatment

### Phase D: Algorithmic execution ($2M+)
- TWAP execution over 2-5 minutes per trade
- Smart order routing across 3-4 venues
- SOL becomes the bottleneck — consider reducing weight or dropping
- At this scale, execution quality IS the alpha — invest in infra accordingly
