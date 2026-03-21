#!/usr/bin/env bash
#
# Autonomous experiment loop — hardened version.
#
# The AI modifies strategy.py on the HOST, then each backtest
# runs inside a fresh Docker container with:
#   - No network access
#   - Read-only filesystem
#   - Memory/CPU limits
#   - Hard timeout
#
# The AI never executes inside the container it's modifying.
#
# Usage:
#   ./run_loop.sh           # run locally with Docker
#   ./run_loop.sh --vm      # run on VM via SSH
#   ./run_loop.sh --bare    # run without Docker (not recommended)

set -euo pipefail

IMAGE_NAME="auto-research:hardened"
VM_HOST="openclaw@100.109.85.37"
VM_DIR="/home/openclaw/auto-researchtrading"
RESULTS_FILE="results.tsv"
MAX_TIMEOUT=180  # container-level kill after 3 min

MODE="${1:---local}"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

# Initialize results file
if [ ! -f "$RESULTS_FILE" ]; then
    echo -e "commit\tscore\tsharpe\tmax_dd\tstatus\tdescription" > "$RESULTS_FILE"
fi

run_backtest_docker() {
    # Run backtest in fresh container, capture output
    timeout "$MAX_TIMEOUT" docker run --rm \
        --network=none \
        --read-only \
        --tmpfs /tmp:size=512m \
        --memory=4g \
        --cpus=2 \
        --security-opt=no-new-privileges:true \
        --cap-drop=ALL \
        -v "$(pwd)/strategy.py:/app/strategy.py:ro" \
        -v "${HOME}/.cache/autotrader/data:/app/data:ro" \
        "$IMAGE_NAME" \
        backtest_sandboxed.py 2>&1
}

run_backtest_vm() {
    # Copy strategy to VM, run in container there
    scp -q strategy.py "$VM_HOST:$VM_DIR/strategy.py"
    ssh "$VM_HOST" "cd $VM_DIR && timeout $MAX_TIMEOUT docker run --rm \
        --network=none \
        --read-only \
        --tmpfs /tmp:size=512m \
        --memory=4g \
        --cpus=2 \
        --security-opt=no-new-privileges:true \
        --cap-drop=ALL \
        -v '$VM_DIR/strategy.py:/app/strategy.py:ro' \
        -v '\${HOME}/.cache/autotrader/data:/app/data:ro' \
        '$IMAGE_NAME' \
        backtest_sandboxed.py 2>&1"
}

run_backtest_bare() {
    # Run directly — not recommended
    echo "WARNING: Running without container isolation."
    python backtest_sandboxed.py 2>&1
}

# Select runner
case "$MODE" in
    --local) RUNNER=run_backtest_docker ;;
    --vm)    RUNNER=run_backtest_vm ;;
    --bare)  RUNNER=run_backtest_bare ;;
    *)
        echo "Usage: $0 [--local|--vm|--bare]"
        exit 1
        ;;
esac

log "Starting experiment loop (mode: $MODE)"
log "Each backtest runs in isolated container. Strategy modified on host only."
log "Press Ctrl+C to stop."
echo ""

# The actual loop is driven by the AI agent (Claude/GPT).
# This script provides the infrastructure:
#   1. AI modifies strategy.py
#   2. AI calls: ./run_loop.sh --local (or --vm)
#   3. This script runs the backtest in a container
#   4. AI reads output, decides whether to keep or revert
#
# For fully autonomous operation, the AI should:
#   1. Read current strategy.py
#   2. Modify it with an experiment
#   3. git commit
#   4. Run this script
#   5. Parse score from output
#   6. If improved: keep. If worse: git reset --hard HEAD~1
#   7. Loop

OUTPUT=$($RUNNER)
echo "$OUTPUT"

# Extract score for easy parsing
SCORE=$(echo "$OUTPUT" | grep "^score:" | awk '{print $2}' || echo "FAILED")
log "Score: $SCORE"
