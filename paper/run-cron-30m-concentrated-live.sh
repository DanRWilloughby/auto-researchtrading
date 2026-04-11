#!/bin/bash
#
# 🔴 LIVE TRADING cron wrapper — 30m-concentrated strategy on Coinbase perps.
#
# Instance: "live"
# Status: LIVE — places REAL orders on Coinbase with real money.
#
# Runs at :14/:44 — 14 min after bar close, 1 min before paper-HL at :15.
# Same bar decision as paper-HL, but Coinbase execution with full risk mgr.
#
# To return to dry-run mode:
#   1. Add "export LIVE_TRADER_DRY_RUN=yes" before the uv run line
#   2. Add "--dry-run" flag to the uv run command
# OR: create state/kill.flag to halt all trading immediately.

export PATH="$HOME/.local/bin:$PATH"
cd ~/auto-researchtrading

uv run live/trader.py --once \
  --instance live \
  --strategy strategies/30m-concentrated/strategy.py \
  --interval 30m \
  >> live/logs/30m-concentrated-live-cron.log 2>&1
