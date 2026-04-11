#!/bin/bash
#
# Live trading cron wrapper — 30m-concentrated strategy on Coinbase perps.
#
# Runs in parallel with run-cron-30m-concentrated.sh (paper trader).
# Paper trader remains completely untouched by this script.
#
# Both scripts run on the same VM, same Python env, same cron schedule.
# Only difference: paper trades simulated fills against Hyperliquid data,
# live trades real orders on Coinbase with the full risk manager.
#
# Cron entry:
#   15,45 * * * * /home/openclaw/auto-researchtrading/paper/run-cron-30m-concentrated-live.sh
#
# Flip between dry-run and live by editing the --live flag below:
#   --dry-run (default, no real orders, just logs what would have happened)
#   --live    (real orders via Coinbase API)
export PATH="$HOME/.local/bin:$PATH"
cd ~/auto-researchtrading

# DRY RUN MODE by default. Change to --live when ready for real orders.
uv run live/trader.py --once \
  --strategy strategies/30m-concentrated/strategy.py \
  --interval 30m \
  >> live/logs/30m-concentrated-cron.log 2>&1
