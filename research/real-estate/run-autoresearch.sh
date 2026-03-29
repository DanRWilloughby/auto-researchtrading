#!/bin/bash
# Auto-Research: Real Estate Deal Sourcing — Paterson, NJ
# Runs daily via cron on the VM
#
# Cron entry (run at 7am ET daily):
#   0 11 * * * /home/openclaw/auto-researchtrading/research/real-estate/run-autoresearch.sh >> /home/openclaw/auto-researchtrading/research/real-estate/logs/cron.log 2>&1

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"

echo "========================================"
echo "Real Estate Auto-Research — $(date -u '+%Y-%m-%d %H:%M UTC')"
echo "========================================"

cd "$SCRIPT_DIR"

# Activate venv
if [ -d ".venv" ]; then
    source .venv/bin/activate
elif [ -d "../../.venv" ]; then
    source ../../.venv/bin/activate
fi

# Run the auto-research pipeline
python3 autoresearch.py 2>&1 | tee "$LOG_DIR/run-$(date -u '+%Y-%m-%d').log"

echo ""
echo "Done: $(date -u '+%Y-%m-%d %H:%M UTC')"
