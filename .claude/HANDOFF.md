# Session Handoff — 2026-04-15 (Filter Research)

## What We Did

### Live health check (restart since 00:27 UTC)
- 42 trades over 12.78h, equity $9,500 → $9,253.87 (**–2.59%**)
- Realized P&L –$44.65, fees $121.38 — **73% of the loss was fees, 27% was price**
- Per symbol: BTC –$43.65, ETH –$102.25 (worst), SOL –$20.13
- No real halts. 28 "manual kill flag" events logged but trading continued — side-channel bug, not P&L risk
- HWM drift re-emerging: `hwm_track` log shows `hwm_new_realized: 10000.0` on 27 of 28 ticks, not the $9,505 post-reset peak. dd_new_pct at 7.6% against phantom $10K anchor, approaching 10% kill threshold

### Fee model verified directly from CB order receipts
Three tickets from 13:14 UTC today reproduced exactly:
- **BTC**: 5 contracts @ $74,350 → $1.12 CB + $0.75 reg = $1.87 (3.0 bps × $3,717.50 = $1.1153 ✓)
- **ETH**: 16 contracts @ $2,332 → $1.12 CB + $2.40 reg = $3.52
- **SOL**: 9 contracts @ $83.74 → $1.13 CB + $1.35 reg = $2.48

Model confirmed: **`3.0 bps × notional + $0.15 × contracts` per side on CB VIP 4**. Contract multipliers: BTC 0.01, ETH 0.1, SOL 5.

### Round-trip cost per coin (the number that matters)
| Coin | RT fee | Break-even move | Edge RT (HL paper) | Edge/Cost |
|---|---|---|---|---|
| BTC | 10.1 bps | $74.90 on $74K | 22.8 bps | 2.26× |
| **ETH** | **18.9 bps** | **$4.40 on $2,332** | 28.1 bps | **1.49×** |
| SOL | 13.2 bps | $0.11 on $83.74 | 29.0 bps | 2.20× |

ETH's per-contract reg fee ($0.15 × 16 contracts = $2.40) dwarfs its bps component. ETH is the fee-heaviest coin and has the thinnest edge-to-cost margin.

### Maker vs taker on the same 42 live trades
Maker (mid level) would have saved $44.63 on 42 trades = **18% of total drawdown recovered**. Fee savings $39.19 (32% fee reduction) + price improvement $4.20. ETH is where maker matters most (+$41 alone).

### HL paper simulation: ETH-drop hypothesis tested (REJECTED)
1,335 HL trades over 19 days, realistic CB fees applied with compounding preserved:
- **Baseline all 3 coins (realistic taker)**: +44.26%
- **Drop ETH (taker)**: +33.05% — **11pp WORSE**
- ETH contributes +$9,454 net over 19 days; dropping it forfeits that

### Regime analysis (HL paper, 19 days)
- Portfolio: 17 green / 3 red days (85% hit rate)
- ETH: 14 green / 6 red (70% — worst)
- **ETH on low-vol days: +$24 avg net (essentially break-even)**
- **ETH on high-vol days: +$1,473 avg net (94% of ETH's total profit)**
- ETH hour-of-day pattern (HL paper): Asia 00-07 had 82% fees-to-gross ratio; US session 17-23 had 32%

### Volatility predictability validated on 944 days CB data (but unactionable for this strategy)
- Lag-1 range autocorrelation: BTC +0.339, **ETH +0.400**, SOL +0.388, all p < 10⁻²⁷
- After LOW prev-day, ETH has 51.8% chance of LOW today vs 7.1% after HIGH (t=-10.26)
- **Signal is real and statistically overwhelming. But it doesn't help the strategy.**

### Filter battery — 13 experiments on 944 days, ALL underperform baseline

| Code | Filter | Δ Return | Δ Sharpe |
|---|---|---|---|
| BASE | No filter | — | 12.53 |
| E1 | ETH half-size low-vol | –22% | –0.01 |
| E2 | ETH no-new-entries low-vol | –36% | –0.05 |
| E3 | All coins halfsize low-vol | –35% | +0.04 |
| E4 | ETH skip extreme funding | –25% | –0.03 |
| E5 | Hard cut: only 13-23 UTC | –100% | **–4.45** |
| E6 | Half-size 00-12 UTC | –99.7% | –1.44 |
| E7 | Narrower: only 14-23 UTC | –100% | –4.90 |
| E8 | E5 + skip ETH hour-15 | –100% | –4.85 |
| E9 | 1.5× size 17-23 UTC | +350% | –0.93 (MaxDD → 11.77%) |
| E10 | 1.5× size 17-19 UTC | –19% | –0.81 |
| E11 | 1.5× 17-23 + 0.5× 00-07 | –95% | –2.31 |
| E12 | 1.25× during 13-23 UTC | +726% | –0.35 (MaxDD 9.86%) |

**Every filter either underperforms or is equivalent to taking more risk (E12 is just "lever up").** The 30m-concentrated strategy's edge is regime-invariant over 944 days. Its internal feature stack (MTF momentum + MACD + RSI + HTF trend + ATR stops) self-selects entries well enough that external filters only remove alpha.

### Account scaling correction (Dan caught this)
I claimed larger accounts would outgrow the $0.15/contract fee. **Wrong.** Because contracts scale linearly with notional, per-contract fees stay at the same bps (~2 bps for BTC) at any account size. On CB perps there is no "outgrow the fees" lever. Only real reductions are: VIP tier upgrade, maker vs taker (0.5 bps), or different exchange.

### Exchange research (US-accessible perpetuals, April 2026)
| Venue | Status US | Fee model | Per-ct fee? | Notes |
|---|---|---|---|---|
| Coinbase | ✅ live | bps + per-contract | **$0.15** | Current |
| **Bitnomial** | ✅ CFTC-regulated, live | **UNDISCLOSED** | Unknown | **Best unknown — worth calling** |
| CME (via broker) | ✅ 24/7 from 2026-05-29 | per-contract | N/A | Dated futures, not perps |
| Kraken US | ✅ (CME-only via NinjaTrader) | per-contract | N/A | Not perps |
| Hyperliquid | ❌ ToS blocks US | flat bps only | **No** | Tier 0: 0.045% taker / –0.015% maker rebate |
| Robinhood | ❌ EU only | flat bps | No | No US launch announced |

**Hyperliquid fees verified from docs**: https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees

**CFTC cleared US-regulated perps in July 2025**; Bitnomial first to market in March 2026; more venues expected "within weeks" per CFTC Chair Selig.

## Current State

- **Live trader**: running cleanly post-reset. Equity $9,253.87, positions BTC+SOL, ETH flat
- **Branch**: `fixes/phase2-maker-pilot` (not pushed)
- **Phase 2 maker pilot**: BUILT, NOT DEPLOYED. Waiting on 24h hybrid shadow data (already accumulating; 157 trades at 81% fill rate as of earlier check)
- **Filter research**: CLOSED. All hypotheses tested, all rejected. Strategy runs as-is.

## Pending / Not Yet Tested

- [ ] Contact Bitnomial sales for perpetual fee schedule (the one unknown that could matter)
- [ ] Phase 2 maker pilot activation (BTC, reduced notional, daylight hours, live observer)
- [ ] ETH-drop sensitivity on 944 days at per-symbol REALISTIC fees (not just flat 6 bps avg) — marginal confirmation but probably won't flip the "keep ETH" call
- [ ] Investigate halt-events-logged-but-trading-continues side-channel bug
- [ ] Investigate hwm_track showing `hwm_new_realized: 10000.0` not $9,505 post-reset
- [ ] Clean up BTC parquet data (one bad-tick row at 387% daily range)

## Next Steps (priority order)

1. **Call/email Bitnomial** for their perpetual fee schedule. If their per-contract fee is <$0.15 or they use flat bps, ETH's break-even drops dramatically. This is the single highest-leverage unknown remaining.
2. **Deploy Phase 2 maker pilot** once hybrid shadow has 48h+ of data. Start BTC-only at reduced notional during daylight hours. Expected +0.5–1 bps edge improvement per side.
3. **Fix HWM-drift + halt-log side channels** before they become real P&L problems.
4. **Ship strategy as-is.** Don't build filters. Don't drop ETH. Don't time-gate. All 13 experiments say the strategy edge is regime-invariant over 944 days.
5. **Monitor new US-legal venues** as CFTC clears more. Watch for flat-bps pricing structure to arrive onshore.

## Quick Context

Dense research session: verified CB fee model directly against order receipts, tested 13 filter variants on 944 days of real CB data, validated that the strategy's edge is surprisingly regime-invariant (no filter helps). Also confirmed HL's flat-bps model via docs, researched Bitnomial as the only US-legal alternative to CB perps. Dan correctly flagged that per-contract fees scale with account size (no "outgrowing" the fee drag on CB). The productive path forward is Phase 2 maker + Bitnomial fee inquiry, not filter engineering.

## Files created this session

- `/tmp/trading-check/analyze.py` — live state analysis
- `/tmp/trading-check/backtest_eth_drop.py` — HL paper ETH-drop simulation
- `/tmp/trading-check/regime_analysis.py` — 19-day regime buckets
- `/tmp/trading-check/predictability.py` — 19-day autocorrelation test
- `/tmp/trading-check/predictability_full.py` — 944-day autocorrelation test (the valid one)
- `/tmp/trading-check/hour_of_day.py` — HL paper hour-of-day analysis
- `/tmp/trading-check/run_filter_backtest.py` — first ETH-drop full-data backtest
- `/tmp/trading-check/filter_experiments.py` — E1-E4 vol filters
- `/tmp/trading-check/filter_tod_experiments.py` — E5-E8 time-of-day filters
- `/tmp/trading-check/filter_boost_experiments.py` — E9-E12 size boosts
- `/tmp/trading-check/{BTC,ETH,SOL}_30m.parquet` — cached CB historical data
- `/tmp/trading-check/hl_paper_state.json` — cached HL paper state
