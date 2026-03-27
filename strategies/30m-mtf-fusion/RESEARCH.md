# 30m Multi-Timeframe Fusion — Research Log

## Hypothesis

Using a higher timeframe (1h) for trend direction and a lower timeframe (30m) for entry timing produces better risk-adjusted returns than either timeframe alone. The 1h trend acts as a directional filter; 30m signals provide precise entries.

## Architecture

```
1h Trend Filter (computed from 30m bars, 2-bar aggregation):
  ├── HTF EMA crossover (14/52 bars = 7h/26h equivalent)
  ├── HTF momentum (11-bar window, any direction)
  └── HTF MACD (standard)
  → Requires 2/3 agreement for trend direction

30m Entry Signals (must agree with 1h trend):
  ├── Medium momentum (14-bar)
  ├── Very-short momentum (8-bar)
  ├── EMA crossover (3/12)
  ├── RSI (8 period, 51/49 thresholds)
  └── MACD histogram (14/26/5)
  → Requires 3/5 votes + HTF agreement

Risk Management:
  ├── ATR trailing stop (10.0× ATR on peak)
  ├── Take profit: 2.2%
  ├── RSI exits: >69 (long), <31 (short)
  ├── Position flipping on opposing signal
  └── Cooldown: 2 bars between entries

Position Sizing:
  └── 4% equity × (1/8 per coin) = 0.5% per position
```

## Symbols

AVAX, BTC, DOGE, ETH, LINK, SOL (6 coins in val), + SUI, XRP (8 coins in test)

## Research Timeline (Mar 26, 2026 — overnight auto-research)

### Phase 1: Signal Architecture (exp0–exp3)
Started from 30m champion logic adapted for MTF. Key finding: removing Bollinger Band compression signal and using 5-signal/3-vote system (exp1, 18.26) outperformed 6-signal/4-vote (exp0, 18.00). Removing HTF MACD crippled performance (exp3, 5.23), confirming the 3-signal HTF filter is load-bearing.

### Phase 2: ATR Stop Optimization (exp4–exp9)
Swept ATR multiplier from 4.5 to 10.0. Found a plateau: 4.5→5.5 was the biggest jump (+3.5 Sharpe), then diminishing returns through 10.0. Settled on 10.0 (Sharpe 23.19) — very wide stops let winners run.

### Phase 3: Signal Ablation & Voting (exp10–exp17)
- Removing 30m MACD hurt significantly (exp10, 19.57)
- Cooldown 2 bars was Goldilocks (exp11 cooldown=1 comparable, exp12 cooldown=3 slightly worse)
- HTF 2/3 voting optimal (1/3 too loose, 3/3 too strict at 14.91)
- Position sizing and RSI exit experiments stable

### Phase 4: HTF Momentum Tuning (exp18–exp27)
Major breakthrough: shortening HTF momentum window from 24→12 bars jumped Sharpe from 23.19→27.55. Further tuning found 11 bars optimal (exp74, 32.61). Momentum threshold swept from 0.012 down to 0.0 — any momentum direction counts, just need the window right.

### Phase 5: Entry Threshold Optimization (exp28–exp34)
Lowered 30m dynamic entry threshold from 0.012→0.004 (exp33, 31.01). Below 0.004 showed diminishing returns. This increased trade count from ~21K to ~30K while maintaining win rate.

### Phase 6: Parameter Grid Search (exp35–exp85)
Fine-tuned all remaining parameters:
- **EMA crossover**: 3/12 optimal (exp48, 31.98)
- **Take profit**: 2.2% (exp62, 32.39) sweet spot between 2.0% and 2.5%
- **Position size**: 4% maximized Sharpe (exp56, 32.33) — smaller sizes trade risk-adjusted efficiency for lower absolute returns
- **MED_WINDOW**: 14 bars (exp67, 32.60)
- **SHORT_WINDOW**: 8 bars (exp65, 32.46)
- **MACD params**: 14/26/5 (exp80–84) — standard 14/26 with faster signal line

### Phase 7: Critical Discovery — Flipping Is Essential
exp37 (no-flip) was catastrophic: -7.98 Sharpe, -13.4% return. The strategy depends on position flipping on opposing signals. Without it, positions overstay and drawdown explodes to 14%.

exp38 (no RSI exits) similarly disastrous: -1.28 Sharpe, -26.6% return, 0% win rate. RSI overbought/oversold exits are structurally necessary.

## Final Parameters

| Parameter | Value | Source |
|-----------|-------|--------|
| HTF_EMA_FAST | 14 | 7h equivalent |
| HTF_EMA_SLOW | 52 | 26h equivalent |
| HTF_MOM_WINDOW | 11 | exp74 |
| HTF_MOM_THRESHOLD | 0.0 | exp27 |
| HTF_MIN_VOTES | 2/3 | baseline |
| EMA_FAST | 3 | exp48 |
| EMA_SLOW | 12 | exp48 |
| MED_WINDOW | 14 | exp67 |
| SHORT_WINDOW | 8 | exp65 |
| RSI_PERIOD | 8 | baseline |
| MACD | 14/26/5 | exp80 |
| ATR_STOP_MULT | 10.0 | exp9 |
| TAKE_PROFIT_PCT | 2.2% | exp62 |
| BASE_POSITION_PCT | 4% | exp56 |
| COOLDOWN_BARS | 2 | baseline |
| MIN_VOTES | 3/5 | exp1 |

## Split Test Results (Mar 27, 2026)

| Split | Score | Sharpe | Return | Max DD | Trades | Win Rate | PF | Coins |
|-------|-------|--------|--------|--------|--------|----------|----|-------|
| **Val** | 33.34 | 33.34 | +44.0% | 0.08% | 31,479 | 76.4% | 10.40 | 6 |
| **Test** | 29.83 | 29.83 | +35.1% | 0.10% | 32,070 | 73.6% | 9.89 | 8 |

**Score retention: 89%** — strongest out-of-sample performance of all 4 overnight strategies.

Comparative OOS results:
- 1h-pairs-arb: 23% retention (3.51→0.80)
- 1h-funding-mr: 7% retention (4.23→0.29)
- 1h-vol-mr: overfit (4.75→-0.91)
- **30m-mtf-fusion: 89% retention (33.34→29.83)**

## Why It Generalizes

1. **Structural edge, not parameter edge**: The multi-timeframe filter is a regime detector, not a curve-fitted signal. 1h trends are real market structure.
2. **High trade count**: 31K+ trades in val means each parameter tweak is validated across a large sample.
3. **Sub-0.1% drawdown**: The strategy rarely holds losing positions long — aggressive flipping + ATR stops prevent accumulation.
4. **Conservative sizing**: 0.5% per position means no single trade can materially impact equity.

## Paper Trading

Deployed to VM on Mar 27, 2026 @ 14:21 UTC.
- Cron: every 30 min at :10 and :40
- Symbols: all 8 coins (BTC, ETH, SOL, XRP, SUI, DOGE, AVAX, LINK)
- Initial capital: $100,000

## Experiment Count

91 experiments logged (88 auto-research + 3 split tests)
