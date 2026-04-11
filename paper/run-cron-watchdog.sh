#!/bin/bash
#
# Watchdog cron wrapper.
#
# Runs every 5 minutes and checks that live trading instances are ticking
# on schedule. Dispatches Telegram alerts if a trader is silent longer than
# expected.
#
# Recommended cron entry:
#   */5 * * * * /home/openclaw/auto-researchtrading/paper/run-cron-watchdog.sh
#
# The watchdog is idempotent — running it more often just produces more
# health checks. It does NOT restart anything; it only alerts.

export PATH="$HOME/.local/bin:$PATH"
cd ~/auto-researchtrading

uv run live/watchdog.py >> live/logs/watchdog.log 2>&1
