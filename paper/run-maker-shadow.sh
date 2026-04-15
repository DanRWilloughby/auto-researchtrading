#!/bin/bash
# Maker Shadow Logger — aligned to live trader cron (:14 and :44) for
# consistent timing. Sleeps 90s at start so the live trader (which also
# fires at :14) completes its trade + writes to the trade log BEFORE the
# shadow tries to read it. Without this sleep the shadow could miss
# trades on runs where live trader takes >60s.
#
# Completely independent — if this crashes, live trading is unaffected.
sleep 90
cd /home/openclaw/auto-researchtrading
/home/openclaw/.local/bin/uv run live/maker_shadow.py >> live/logs/maker-shadow-cron.log 2>&1
