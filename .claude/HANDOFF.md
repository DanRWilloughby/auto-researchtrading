# Session Handoff - 2026-03-27 (Afternoon)

## What We Did
- Ran split tests (val vs test) on 4 overnight auto-research strategies
- **MTF-Fusion confirmed as robust**: 89% OOS retention (33.34 → 29.83), deployed as paper trader
- Designed and built **daily-return scoring function** targeting 1% daily with DD < 10%
- Created 4 new aggressive strategy variants for $20K compounding goal:
  - 30m-highoctane (3.5x leverage, 8 coins) — overfit, 18% test DD
  - 15m-scalper (2.15x leverage, 15m bars) — interesting, better on test than val
  - 30m-voltarget (adaptive sizing) — safest, never >5.5% DD, scores 666-943
  - **30m-concentrated** (1.2x leverage, BTC/ETH/SOL, SOL-overweight) — **winner**
- Auto-research ran 250+ experiments across all 4 variants
- Ran regime tests: bear, bull, choppy, 13-month long-duration
- Deployed 30m-concentrated as paper trader (scaled BASE from 1.50→1.20 for OOS safety)
- Built **shadow execution tracker** in paper trader: tracks 4 slippage scenarios side by side
- Fixed return projector bug (hardcoded $1K trade size causing capital-dependent returns)
- Updated dashboard: dropdown strategy selector, greyed-out inactive strategies
- Deployed MTF-fusion + concentrated as paper traders on VM

## Paper Traders Running on VM (100.109.85.37)

| Strategy | Cron | Interval | Coins | Leverage | Started |
|---|---|---|---|---|---|
| 1h-8coin | :05 every hr | 1h | 8 coins | ~1x | Mar 22 |
| 30m-8coin | :05,:35 | 30m | 8 coins | ~1x | Mar 22 |
| 30m-mtf-fusion | :10,:40 | 30m | 8 coins | ~0.5x | Mar 27 AM |
| **30m-concentrated** | **:15,:45** | **30m** | **BTC/ETH/SOL** | **1.2x** | **Mar 27 PM** |

## 30m-Concentrated Key Metrics
- Val score: 4,228 (DD 5.7%), Test score: 3,167 (DD 8.6%)
- Position sizing: BTC 20%, ETH 30%, SOL 50% weights × 1.20 base
- At $20K: BTC $4.8K, ETH $7.2K, SOL $12K = $24K total (1.2x)
- Regime results: Bear 1516, Bull 1997, Choppy 1845 — positive everywhere
- 13-month train: DD 12% (over budget) — sizing intentionally reduced to 1.20

## Shadow Execution Tracker
paper_state.json now tracks `shadow_curves` and `shadow_cost_deltas` for:
- **Ideal**: 0 bps slip, 2 bps maker fee
- **Paper**: 1 bps slip, 5 bps taker fee (baseline)
- **Realistic**: 3 bps slip, 5 bps taker fee
- **Pessimistic**: 5 bps slip, 8 bps fee

## Dashboard
- URL: https://dashboard-green-nu-53.vercel.app
- Dropdown strategy selector (replaces horizontal scroll)
- Inactive strategies greyed out
- 30m CONCENTRATED and 30m MTF FUSION both selectable with experiments + paper trading

## Next Steps
- [ ] Monitor concentrated paper trader for 2-3 days — check daily returns vs shadow scenarios
- [ ] Build dashboard component to visualize shadow execution curves side by side
- [ ] If concentrated holds up live, consider deploying with real $20K on Hyperliquid
- [ ] Potentially blend concentrated + voltarget for adaptive sizing on the concentrated signal set
- [ ] Push overnight-lab dashboard code changes to git

## Quick Context
Major research session. Validated MTF-fusion (conservative, Sharpe 30+) and built a new high-return variant (30m-concentrated) targeting 1% daily on $20K with 1.2x leverage. Both deployed as paper traders with shadow execution tracking. The concentrated strategy works across all market regimes but needs live validation before real capital.
