#!/bin/bash
#
# Live trading cron wrapper — 30m-concentrated strategy on Coinbase perps.
#
# Runs in parallel with run-cron-30m-concentrated.sh (paper trader).
# Paper trader remains completely untouched by this script.
#
# SAFETY: Double dry-run gate
#   1. LIVE_TRADER_DRY_RUN env var set to "yes"
#   2. --dry-run flag explicitly passed
# Both must be removed to actually trade.
#
# To switch to LIVE trading:
#   1. Remove both "LIVE_TRADER_DRY_RUN=yes" and "--dry-run"
#   2. Commit the change (this script is tracked)
#   3. Watch the first tick carefully via Telegram alerts
#
# Cron entry:
#   16,46 * * * * /home/openclaw/auto-researchtrading/paper/run-cron-30m-concentrated-live.sh

export PATH="$HOME/.local/bin:$PATH"
export LIVE_TRADER_DRY_RUN=yes   # DRY-RUN GATE 1 (env var)

cd ~/auto-researchtrading

uv run live/trader.py --once \
  --dry-run \
  --strategy strategies/30m-concentrated/strategy.py \
  --interval 30m \
  >> live/logs/30m-concentrated-cron.log 2>&1
