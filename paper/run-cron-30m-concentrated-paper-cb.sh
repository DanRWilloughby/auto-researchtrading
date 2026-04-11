#!/bin/bash
#
# Paper-Coinbase-Early cron wrapper — 30m-concentrated strategy.
#
# Instance: "paper-cb-early"
# Purpose: experimental — runs the strategy against Coinbase data at :02/:32
#   (2 min after bar close) to test whether earlier timing captures more edge
#   than the :14 live instance. ALWAYS dry-run; never places real orders.
#
# Comparison points:
#   paper-HL @ :15  → baseline (existing)
#   live @ :14      → same bar, slight early vs HL, real execution
#   paper-cb-early @ :02 → same bar, maximum early, simulated execution
#
# SAFETY: Hard-coded dry-run. Even if the gate is removed, this script has
# its own --instance=paper-cb-early which has nothing to do with real money.
# BUT for sanity, dry-run gates are still present.

export PATH="$HOME/.local/bin:$PATH"
export LIVE_TRADER_DRY_RUN=yes   # PAPER-CB EARLY ALWAYS DRY-RUN

cd ~/auto-researchtrading

uv run live/trader.py --once \
  --dry-run \
  --instance paper-cb-early \
  --strategy strategies/30m-concentrated/strategy.py \
  --interval 30m \
  >> live/logs/30m-concentrated-paper-cb-cron.log 2>&1
