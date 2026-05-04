# ──────────────────────────────────────────────────────────────────────────────
# Talk Radio Analysis Pipeline — production image for Azure Container Apps
#
# Data (transcripts/, chroma_db/, results/) is NOT baked in — mounted at
# runtime via Azure Files volumes for cheap persistent storage.
# ──────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/opt/hf_cache \
    SENTENCE_TRANSFORMERS_HOME=/opt/hf_cache \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

WORKDIR /app

# ── System dependencies ──────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

# ── Python deps (cached layer — invalidated only when requirements change) ───
COPY requirements.txt .
RUN pip install -r requirements.txt

# ── Pre-download embedding + reranker so the first request isn't slow ────────
RUN python -c "\
from sentence_transformers import SentenceTransformer, CrossEncoder; \
SentenceTransformer('BAAI/bge-base-en-v1.5'); \
SentenceTransformer('all-MiniLM-L6-v2'); \
CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"

# ── App code (small, changes often — last layer) ─────────────────────────────
COPY app.py analyze.py ingest.py retrieval.py ./
COPY prompts/ ./prompts/

# ── Mount points (filled by Azure Files volumes at runtime) ──────────────────
RUN mkdir -p /app/transcripts /app/chroma_db /app/results

EXPOSE 8501

# ── Health check used by Container Apps ──────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "app.py"]
