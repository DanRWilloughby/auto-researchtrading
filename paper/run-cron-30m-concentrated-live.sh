#!/bin/bash
#
# Live trading cron wrapper — 30m-concentrated strategy on Coinbase perps.
#
# Instance: "live"
# Purpose: real-money trading (currently dry-run during validation)
#
# Runs at :14/:44 — 14 min after bar close, 1 min before paper-HL at :15.
# Same bar decision as paper-HL, but Coinbase execution with full risk mgr.
#
# SAFETY: Double dry-run gate
#   1. LIVE_TRADER_DRY_RUN env var set to "yes"
#   2. --dry-run flag explicitly passed
# Both must be removed to actually trade.
#
# To switch to LIVE trading:
#   1. Remove "LIVE_TRADER_DRY_RUN=yes" line
#   2. Remove "--dry-run" flag
#   3. Commit the change
#   4. Watch first tick via Telegram alerts

export PATH="$HOME/.local/bin:$PATH"
export LIVE_TRADER_DRY_RUN=yes   # DRY-RUN GATE 1 (env var)

cd ~/auto-researchtrading

uv run live/trader.py --once \
  --dry-run \
  --instance live \
  --strategy strategies/30m-concentrated/strategy.py \
  --interval 30m \
  >> live/logs/30m-concentrated-live-cron.log 2>&1
