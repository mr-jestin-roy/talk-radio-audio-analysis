#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# Phase 3 — Upload transcripts + Weaviate index to Azure Files.
# Run ONCE (or again to sync after re-indexing).
#
#   bash azure/upload_data.sh
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AZ="$REPO_ROOT/.venv/bin/python3 -m azure.cli"
ENV_FILE="$REPO_ROOT/azure/.azure-env"

[[ -f "$ENV_FILE" ]] || { echo "Run azure/setup.sh first."; exit 1; }
# shellcheck disable=SC1090
source "$ENV_FILE"

cd "$REPO_ROOT"

WEAVIATE_VOLUME="radioshowpipeline_weaviate_data"
WEAVIATE_BACKUP_DIR="$REPO_ROOT/azure/weaviate_backup"

# ── Check Weaviate is running ─────────────────────────────────────────────────
echo "▶  Checking Weaviate is running..."
curl -fsS http://localhost:8080/v1/.well-known/ready >/dev/null || {
    echo "Weaviate is not running. Start it first:"
    echo "  docker compose up -d weaviate"
    exit 1
}

# ── 1. Weaviate backup via API → tar → Azure Files ───────────────────────────
echo "▶  Backing up Weaviate index (~2 GB) via Docker volume copy..."
mkdir -p "$WEAVIATE_BACKUP_DIR"

# Copy from Docker volume into a local tar (no sudo needed)
docker run --rm \
    -v "$WEAVIATE_VOLUME":/source:ro \
    -v "$WEAVIATE_BACKUP_DIR":/backup \
    alpine sh -c "cd /source && tar czf /backup/weaviate_data.tar.gz ."

echo "   Backup size: $(du -sh "$WEAVIATE_BACKUP_DIR/weaviate_data.tar.gz" | cut -f1)"

echo "▶  Uploading Weaviate backup to Azure Files..."
$AZ storage file upload \
    --account-name "$STORAGE_ACCOUNT" \
    --account-key  "$STORAGE_KEY" \
    --share-name   "$FILE_SHARE" \
    --source       "$WEAVIATE_BACKUP_DIR/weaviate_data.tar.gz" \
    --path         "weaviate/weaviate_data.tar.gz" \
    -o none

# ── 2. Transcripts → Azure Files ─────────────────────────────────────────────
[[ -d transcripts ]] || { echo "transcripts/ not found in $REPO_ROOT"; exit 1; }

echo "▶  Uploading transcripts/ (~298 MB, ~9,157 files)..."
echo "   This takes 10-20 min on first run (8 parallel connections)..."
$AZ storage file upload-batch \
    --account-name "$STORAGE_ACCOUNT" \
    --account-key  "$STORAGE_KEY" \
    --destination  "$FILE_SHARE/transcripts" \
    --source       ./transcripts \
    --max-connections 8

# ── 3. Create empty results/ dir ─────────────────────────────────────────────
echo "▶  Creating results/ directory on Azure Files..."
$AZ storage directory create \
    --account-name "$STORAGE_ACCOUNT" \
    --account-key  "$STORAGE_KEY" \
    --share-name   "$FILE_SHARE" \
    --name         results \
    -o none 2>/dev/null || true

echo
echo "======================================================"
echo "  Phase 3 complete — data is on Azure Files"
echo "  Weaviate index : $FILE_SHARE/weaviate/weaviate_data.tar.gz"
echo "  Transcripts    : $FILE_SHARE/transcripts/ (9,157 files)"
echo ""
echo "  Next: bash azure/deploy.sh"
echo "======================================================"
