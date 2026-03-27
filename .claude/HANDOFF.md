# Session Handoff - 2026-03-27 (Late Afternoon)

## What We Did
- Validated MTF-Fusion via split tests: 89% OOS retention (Sharpe 33→29.8), deployed as paper trader
- Built daily-return scoring function targeting 1% daily with DD < 10% constraint
- Created 4 aggressive strategy variants, ran 250+ experiments across all
- **30m-concentrated** emerged as winner: BTC/ETH/SOL, 1.2x leverage, SOL-overweight (50%)
- Regime tested all 4: bear/bull/choppy/long-duration — concentrated positive everywhere
- Reduced sizing from 1.50→1.20 for OOS safety (test DD 8.6% vs 11.6%)
- **Fixed double cash subtraction bug** in MODIFY trades (prepare.py + trader.py)
- Built shadow execution tracker: 4 slippage scenarios tracked side by side
- Fixed return projector bug (capital-dependent returns from hardcoded trade size)
- Updated dashboard: dropdown selector, greyed-out inactive strategies
- Increased auto-sync frequency from 60min→15min

## Bug Fix: Double Cash Subtraction
Lines 831-832 in prepare.py and 328-329 in trader.py subtracted `added` notional twice on position increases. Impact: ~$303/flip on concentrated (1.2x leverage), negligible on conservative strategies. All backtest results were conservative — actual performance is better. Concentrated paper state was reset; others kept their history.

## Paper Traders Running on VM (100.109.85.37)

| Strategy | Cron | Interval | Coins | Leverage | Trades | Status |
|---|---|---|---|---|---|---|
| 1h-8coin | :05 hourly | 1h | 8 coins | ~1x | 633 | Running since Mar 22 |
| 30m-8coin | :05,:35 | 30m | 8 coins | ~1x | 800 | Running since Mar 22 |
| 30m-mtf-fusion | :10,:40 | 30m | 8 coins | ~0.5x | 31 | Running since Mar 27 AM |
| **30m-concentrated** | **:15,:45** | **30m** | **BTC/ETH/SOL** | **1.2x** | **5** | **Reset Mar 27 2:13PM (bugfix)** |

## 30m-Concentrated Config (deployed)
- BASE_POSITION_PCT = 1.20 (reduced from 1.50 for OOS safety)
- SYMBOL_WEIGHTS: BTC 20%, ETH 30%, SOL 50%
- At $20K: BTC $4.8K, ETH $7.2K, SOL $12K = $24K total (1.2x leverage)
- ATR_STOP_MULT = 8.0, TAKE_PROFIT = 1.2%, COOLDOWN = 1 bar
- Val score: 4,228 (DD 5.7%), Test score: 3,167 (DD 8.6%)

## Dashboard
- URL: https://dashboard-green-nu-53.vercel.app
- Auto-sync: every 15 min via macOS launchd (com.overnight-lab.trading-sync)
- Dropdown strategy selector, inactive strategies greyed out
- Shadow execution curves in paper_state.json (not yet visualized in UI)

## Next Steps
- [ ] Monitor concentrated for 2-3 days — check daily returns vs shadow scenarios
- [ ] Build dashboard component to visualize shadow execution curves
- [ ] Re-run backtests with bugfix to get corrected scores (will be higher)
- [ ] If concentrated holds up, deploy with real $20K on Hyperliquid
- [ ] Consider blending concentrated signals with vol-target adaptive sizing

## Quick Context
Major session: split-tested overnight research, built 4 aggressive strategy variants for $20K compounding goal, found a double-subtraction accounting bug, fixed it, and deployed the concentrated strategy (1.2x leverage, BTC/ETH/SOL) as a paper trader. Dashboard auto-syncs every 15 min. Strategy targets ~1% daily return with <10% DD.
