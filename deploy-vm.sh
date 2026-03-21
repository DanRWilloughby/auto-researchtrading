#!/usr/bin/env bash
#
# Deploy auto-researchtrading to VM in Docker container.
# Builds image, copies to VM, runs isolated backtest.
#
# Usage:
#   ./deploy-vm.sh build       # Build Docker image locally
#   ./deploy-vm.sh push        # Push image to VM
#   ./deploy-vm.sh download    # Download data on VM (needs network)
#   ./deploy-vm.sh run         # Run backtest on VM (no network)
#   ./deploy-vm.sh validate    # Validate data integrity on VM
#   ./deploy-vm.sh all         # Full pipeline: build → push → download → validate → run

set -euo pipefail

VM_HOST="openclaw@100.109.85.37"
VM_PROJECT_DIR="/home/openclaw/auto-researchtrading"
IMAGE_NAME="auto-research:hardened"
IMAGE_TAR="auto-research-hardened.tar"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

cmd_build() {
    log "Building Docker image..."
    docker build -t "$IMAGE_NAME" .
    log "Image built: $IMAGE_NAME"
}

cmd_push() {
    log "Saving image to tar..."
    docker save "$IMAGE_NAME" -o "$IMAGE_TAR"

    log "Copying image to VM..."
    scp "$IMAGE_TAR" "$VM_HOST:/tmp/$IMAGE_TAR"

    log "Loading image on VM..."
    ssh "$VM_HOST" "docker load -i /tmp/$IMAGE_TAR && rm /tmp/$IMAGE_TAR"

    log "Syncing project files..."
    ssh "$VM_HOST" "mkdir -p $VM_PROJECT_DIR/results"
    scp strategy.py "$VM_HOST:$VM_PROJECT_DIR/strategy.py"
    scp docker-compose.yml "$VM_HOST:$VM_PROJECT_DIR/docker-compose.yml"
    scp backtest_sandboxed.py "$VM_HOST:$VM_PROJECT_DIR/backtest_sandboxed.py"
    scp validate_data.py "$VM_HOST:$VM_PROJECT_DIR/validate_data.py"

    log "Image and files deployed to VM."
    rm -f "$IMAGE_TAR"
}

cmd_download() {
    log "Downloading data on VM (network enabled)..."
    ssh "$VM_HOST" "cd $VM_PROJECT_DIR && docker compose run --rm download"
    log "Data download complete."
}

cmd_validate() {
    log "Validating data integrity on VM..."
    ssh "$VM_HOST" "cd $VM_PROJECT_DIR && docker compose run --rm backtest validate_data.py --checksums"
    log "Validation complete."
}

cmd_run() {
    log "Running sandboxed backtest on VM..."
    ssh "$VM_HOST" "cd $VM_PROJECT_DIR && docker compose run --rm backtest backtest_sandboxed.py"
    log "Backtest complete."
}

cmd_all() {
    cmd_build
    cmd_push
    cmd_download
    cmd_validate
    cmd_run
}

# Route command
case "${1:-help}" in
    build)    cmd_build ;;
    push)     cmd_push ;;
    download) cmd_download ;;
    validate) cmd_validate ;;
    run)      cmd_run ;;
    all)      cmd_all ;;
    *)
        echo "Usage: $0 {build|push|download|validate|run|all}"
        echo ""
        echo "  build     Build Docker image locally"
        echo "  push      Push image + files to VM"
        echo "  download  Download market data on VM"
        echo "  validate  Validate data integrity"
        echo "  run       Run backtest (sandboxed, no network)"
        echo "  all       Full pipeline"
        exit 1
        ;;
esac
