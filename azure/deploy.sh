#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# Phases 4 + 5 — Build & push Docker image, deploy Weaviate + Streamlit
#                to Azure Container Apps.
#
# Pre-reqs:
#   1. azure/setup.sh       run (resources provisioned)
#   2. azure/upload_data.sh run (data on Azure Files)
#   3. Export these env vars:
#        export GITHUB_USER="mr-jestin-roy"
#        export GITHUB_PAT="ghp_xxxx"        # write:packages scope
#        export GEMINI_API_KEY="AIza..."      # https://aistudio.google.com/apikey
#
#   bash azure/deploy.sh
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AZ="$REPO_ROOT/.venv/bin/python3 -m azure.cli"
ENV_FILE="$REPO_ROOT/azure/.azure-env"

[[ -f "$ENV_FILE" ]] || { echo "Run azure/setup.sh first."; exit 1; }
# shellcheck disable=SC1090
source "$ENV_FILE"

: "${GITHUB_USER:?export GITHUB_USER=mr-jestin-roy}"
: "${GITHUB_PAT:?export GITHUB_PAT=ghp_xxx  (write:packages scope)}"
: "${GEMINI_API_KEY:?export GEMINI_API_KEY=AIza...  (https://aistudio.google.com/apikey)}"

cd "$REPO_ROOT"

IMAGE="ghcr.io/${GITHUB_USER,,}/talk-radio:latest"
WEAVIATE_IMAGE="cr.weaviate.io/semitechnologies/weaviate:1.28.0"
WEAVIATE_APP="talk-radio-weaviate"
STREAMLIT_APP="talk-radio-app"

# ── Phase 4: Build & push Streamlit image ────────────────────────────────────
echo "▶  Logging in to GitHub Container Registry..."
echo "$GITHUB_PAT" | docker login ghcr.io -u "$GITHUB_USER" --password-stdin

echo "▶  Building Docker image: $IMAGE"
docker build -t "$IMAGE" "$REPO_ROOT"

echo "▶  Pushing to ghcr.io..."
docker push "$IMAGE"

# ── Register Azure Files share with Container Apps environment ────────────────
echo "▶  Registering Azure Files share with Container Apps env..."
$AZ containerapp env storage set \
    --name "$CONTAINERAPP_ENV" \
    --resource-group "$RESOURCE_GROUP" \
    --storage-name talk-radio-files \
    --azure-file-account-name "$STORAGE_ACCOUNT" \
    --azure-file-account-key "$STORAGE_KEY" \
    --azure-file-share-name "$FILE_SHARE" \
    --access-mode ReadWrite \
    -o none

# ── Phase 5a: Deploy Weaviate Container App ───────────────────────────────────
# Weaviate runs as its own Container App inside the same environment.
# The Streamlit app connects to it via internal hostname.
# Cost: ~0.5 vCPU × 1GB RAM = ~$1-2/month on student credit.
echo "▶  Deploying Weaviate ($WEAVIATE_APP)..."

WEAVIATE_EXISTS=$($AZ containerapp show -n "$WEAVIATE_APP" -g "$RESOURCE_GROUP" \
    --query name -o tsv 2>/dev/null || echo "")

if [[ -z "$WEAVIATE_EXISTS" ]]; then
    $AZ containerapp create \
        --name "$WEAVIATE_APP" \
        --resource-group "$RESOURCE_GROUP" \
        --environment "$CONTAINERAPP_ENV" \
        --image "$WEAVIATE_IMAGE" \
        --target-port 8080 \
        --ingress internal \
        --transport http \
        --cpu 0.5 --memory 1.0Gi \
        --min-replicas 1 --max-replicas 1 \
        --env-vars \
            "QUERY_DEFAULTS_LIMIT=25" \
            "AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED=true" \
            "PERSISTENCE_DATA_PATH=/var/lib/weaviate" \
            "DEFAULT_VECTORIZER_MODULE=none" \
            "ENABLE_MODULES=" \
            "CLUSTER_HOSTNAME=node1" \
        -o none

    # Mount Azure Files volume for Weaviate persistence
    cat > /tmp/weaviate-patch.yaml <<YAML
properties:
  template:
    volumes:
      - name: weaviate-data
        storageType: AzureFile
        storageName: talk-radio-files
    containers:
      - name: $WEAVIATE_APP
        image: $WEAVIATE_IMAGE
        resources:
          cpu: 0.5
          memory: 1.0Gi
        volumeMounts:
          - volumeName: weaviate-data
            mountPath: /var/lib/weaviate
            subPath: weaviate
        env:
          - name: QUERY_DEFAULTS_LIMIT
            value: "25"
          - name: AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED
            value: "true"
          - name: PERSISTENCE_DATA_PATH
            value: /var/lib/weaviate
          - name: DEFAULT_VECTORIZER_MODULE
            value: none
          - name: ENABLE_MODULES
            value: ""
          - name: CLUSTER_HOSTNAME
            value: node1
YAML
    $AZ containerapp update \
        --name "$WEAVIATE_APP" \
        --resource-group "$RESOURCE_GROUP" \
        --yaml /tmp/weaviate-patch.yaml \
        -o none
    rm /tmp/weaviate-patch.yaml
    echo "   Weaviate deployed (internal only)"
else
    echo "   Weaviate already exists — skipping"
fi

# Get internal Weaviate URL (only reachable within the Container Apps env)
WEAVIATE_FQDN=$($AZ containerapp show \
    -n "$WEAVIATE_APP" -g "$RESOURCE_GROUP" \
    --query "properties.configuration.ingress.fqdn" -o tsv)
WEAVIATE_INTERNAL_URL="http://${WEAVIATE_FQDN}"

# ── Phase 5b: Restore Weaviate data from Azure Files ─────────────────────────
echo ""
echo "  IMPORTANT: First-time Weaviate restore"
echo "  The Weaviate volume is empty on first deploy. Run this ONCE to restore:"
echo "  The startup script will untar weaviate_data.tar.gz on boot."
echo "  (See azure/restore_weaviate.sh for manual restore instructions)"
echo ""

# ── Phase 5c: Deploy Streamlit Container App ──────────────────────────────────
echo "▶  Deploying Streamlit app ($STREAMLIT_APP)..."

STREAMLIT_EXISTS=$($AZ containerapp show -n "$STREAMLIT_APP" -g "$RESOURCE_GROUP" \
    --query name -o tsv 2>/dev/null || echo "")

COMMON_ENV_VARS=(
    "WEAVIATE_URL=$WEAVIATE_INTERNAL_URL"
    "LLM_URL=https://generativelanguage.googleapis.com/v1beta/openai"
    "LLM_MODEL=gemini-2.0-flash"
    "LLM_API_KEY=secretref:gemini-api-key"
    "TRANSCRIPTS_DIR=/app/data/transcripts"
    "RESULTS_DIR=/app/data/results"
)

if [[ -z "$STREAMLIT_EXISTS" ]]; then
    $AZ containerapp create \
        --name "$STREAMLIT_APP" \
        --resource-group "$RESOURCE_GROUP" \
        --environment "$CONTAINERAPP_ENV" \
        --image "$IMAGE" \
        --target-port 8501 \
        --ingress external \
        --transport auto \
        --cpu 0.5 --memory 1.0Gi \
        --min-replicas 0 --max-replicas 1 \
        --secrets "gemini-api-key=$GEMINI_API_KEY" \
        --env-vars "${COMMON_ENV_VARS[@]}" \
        -o none

    # Mount Azure Files for transcripts + results
    cat > /tmp/streamlit-patch.yaml <<YAML
properties:
  template:
    volumes:
      - name: app-data
        storageType: AzureFile
        storageName: talk-radio-files
    containers:
      - name: $STREAMLIT_APP
        image: $IMAGE
        resources:
          cpu: 0.5
          memory: 1.0Gi
        volumeMounts:
          - volumeName: app-data
            mountPath: /app/data
        env:
          - name: WEAVIATE_URL
            value: "$WEAVIATE_INTERNAL_URL"
          - name: LLM_URL
            value: "https://generativelanguage.googleapis.com/v1beta/openai"
          - name: LLM_MODEL
            value: "gemini-2.0-flash"
          - name: LLM_API_KEY
            secretRef: gemini-api-key
          - name: TRANSCRIPTS_DIR
            value: /app/data/transcripts
          - name: RESULTS_DIR
            value: /app/data/results
YAML
    $AZ containerapp update \
        --name "$STREAMLIT_APP" \
        --resource-group "$RESOURCE_GROUP" \
        --yaml /tmp/streamlit-patch.yaml \
        -o none
    rm /tmp/streamlit-patch.yaml
else
    echo "   Updating existing Streamlit app with new image..."
    $AZ containerapp update \
        --name "$STREAMLIT_APP" \
        --resource-group "$RESOURCE_GROUP" \
        --image "$IMAGE" \
        -o none
fi

# ── Done ─────────────────────────────────────────────────────────────────────
APP_URL=$($AZ containerapp show -n "$STREAMLIT_APP" -g "$RESOURCE_GROUP" \
    --query "properties.configuration.ingress.fqdn" -o tsv)

echo
echo "======================================================"
echo "  Deploy complete!"
echo ""
echo "  Streamlit app : https://$APP_URL"
echo "  Weaviate      : $WEAVIATE_INTERNAL_URL (internal only)"
echo ""
echo "  Tail logs:"
echo "  .venv/bin/python3 -m azure.cli containerapp logs show \\"
echo "    -n $STREAMLIT_APP -g $RESOURCE_GROUP --follow"
echo "======================================================"
