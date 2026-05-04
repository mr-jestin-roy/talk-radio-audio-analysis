"""
Hybrid retrieval with automatic backend detection.

Priority:
  1. Weaviate  — hybrid BM25 + vector + cross-encoder rerank  (production)
  2. ChromaDB  — vector search only, no rerank                (fallback/placeholder)

Call `backend()` to find out which is active.
"""

import os
from dataclasses import dataclass
from urllib.parse import urlparse

COLLECTION_NAME   = "Transcripts"
CHROMA_DIR        = os.environ.get("CHROMA_DIR", "chroma_db")
CHROMA_COLLECTION = "transcripts"
EMBED_MODEL_BGE  = "BAAI/bge-base-en-v1.5"    # used by Weaviate
EMBED_MODEL_MINI = "all-MiniLM-L6-v2"          # used by existing ChromaDB index
RERANK_MODEL     = "cross-encoder/ms-marco-MiniLM-L-6-v2"

WEAVIATE_URL     = os.environ.get("WEAVIATE_URL", "http://localhost:8080")
WEAVIATE_API_KEY = os.environ.get("WEAVIATE_API_KEY", "")


@dataclass
class SearchResult:
    chunk_id:     str
    date:         str
    year:         int
    month:        int
    hour:         int
    item_id:      str
    text:         str
    hybrid_score: float
    rerank_score: float | None = None


# ── Singleton models ──────────────────────────────────────────────────────────

_embedder_bge  = None
_embedder_mini = None
_reranker      = None


def _get_embedder_bge():
    global _embedder_bge
    if _embedder_bge is None:
        from sentence_transformers import SentenceTransformer
        _embedder_bge = SentenceTransformer(EMBED_MODEL_BGE, device="cpu")
    return _embedder_bge


def _get_embedder_mini():
    global _embedder_mini
    if _embedder_mini is None:
        from sentence_transformers import SentenceTransformer
        _embedder_mini = SentenceTransformer(EMBED_MODEL_MINI, device="cpu")
    return _embedder_mini


def _get_reranker():
    global _reranker
    if _reranker is None:
        from sentence_transformers import CrossEncoder
        _reranker = CrossEncoder(RERANK_MODEL)
    return _reranker


# ── Backend detection ─────────────────────────────────────────────────────────

def _weaviate_available() -> bool:
    """Check Weaviate is actually ready — not just something on the port."""
    try:
        import urllib.request, json
        parsed = urlparse(WEAVIATE_URL)
        host   = parsed.hostname or "localhost"
        port   = parsed.port or 8080
        resp   = urllib.request.urlopen(f"http://{host}:{port}/v1/.well-known/ready", timeout=2)
        # Must return 200 AND valid Weaviate JSON
        body = resp.read().decode()
        return resp.status == 200 and "weaviate" in body.lower()
    except Exception:
        return False


def _chromadb_available() -> bool:
    from pathlib import Path
    return Path(CHROMA_DIR).exists() and any(Path(CHROMA_DIR).iterdir())


def backend() -> str:
    """Returns 'weaviate', 'chromadb', or 'none'."""
    if _weaviate_available():
        return "weaviate"
    if _chromadb_available():
        return "chromadb"
    return "none"


# ── Weaviate connection ───────────────────────────────────────────────────────

def _weaviate_connect():
    import weaviate
    parsed = urlparse(WEAVIATE_URL)
    host   = parsed.hostname or "localhost"
    port   = parsed.port or 8080
    if WEAVIATE_API_KEY:
        return weaviate.connect_to_weaviate_cloud(
            cluster_url=WEAVIATE_URL,
            auth_credentials=weaviate.auth.AuthApiKey(WEAVIATE_API_KEY),
        )
    return weaviate.connect_to_local(host=host, port=port, grpc_port=50051)


# ── ChromaDB connection ───────────────────────────────────────────────────────

def _chroma_collection():
    import chromadb
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    return client.get_collection(name=CHROMA_COLLECTION)


# ── Weaviate search ───────────────────────────────────────────────────────────

def _weaviate_search(
    query: str,
    n_results: int,
    alpha: float,
    year_from: int | None,
    year_to: int | None,
    rerank: bool,
) -> list[SearchResult]:
    import weaviate.classes.query as wq

    embedder  = _get_embedder_bge()
    query_vec = embedder.encode(query, normalize_embeddings=True).tolist()

    client = _weaviate_connect()
    try:
        col     = client.collections.get(COLLECTION_NAME)
        filters = []
        if year_from:
            filters.append(wq.Filter.by_property("year").greater_or_equal(year_from))
        if year_to:
            filters.append(wq.Filter.by_property("year").less_or_equal(year_to))
        filt    = filters[0] & filters[1] if len(filters) == 2 else (filters[0] if filters else None)
        fetch_n = n_results * 3 if rerank else n_results

        response = col.query.hybrid(
            query=query,
            vector=query_vec,
            alpha=alpha,
            limit=fetch_n,
            filters=filt,
            return_metadata=wq.MetadataQuery(score=True),
            return_properties=["text","chunk_id","item_id","date","year","month","hour"],
        )
    finally:
        client.close()

    results = [
        SearchResult(
            chunk_id     = o.properties["chunk_id"],
            date         = o.properties.get("date", ""),
            year         = o.properties.get("year", 0),
            month        = o.properties.get("month", 0),
            hour         = o.properties.get("hour", 0),
            item_id      = o.properties.get("item_id", ""),
            text         = o.properties["text"],
            hybrid_score = o.metadata.score if o.metadata else 0.0,
        )
        for o in response.objects
    ]

    if rerank and results:
        reranker = _get_reranker()
        scores   = reranker.predict([(query, r.text) for r in results])
        for r, s in zip(results, scores):
            r.rerank_score = float(s)
        results.sort(key=lambda r: r.rerank_score, reverse=True)

    return results[:n_results]


# ── ChromaDB search (fallback) ────────────────────────────────────────────────

def _chromadb_search(
    query: str,
    n_results: int,
    year_from: int | None,
    year_to: int | None,
) -> list[SearchResult]:
    embedder = _get_embedder_mini()
    col      = _chroma_collection()

    where = None
    conditions = []
    if year_from:
        conditions.append({"year": {"$gte": year_from}})
    if year_to:
        conditions.append({"year": {"$lte": year_to}})
    if len(conditions) == 2:
        where = {"$and": conditions}
    elif len(conditions) == 1:
        where = conditions[0]

    res = col.query(
        query_texts=[query],
        n_results=n_results,
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    results = []
    for doc, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0]):
        results.append(SearchResult(
            chunk_id     = f"{meta.get('item_id','')}__h{meta.get('hour',0)}__c{meta.get('chunk_index',0)}",
            date         = meta.get("date", ""),
            year         = meta.get("year", 0),
            month        = meta.get("month", 0),
            hour         = meta.get("hour", 0),
            item_id      = meta.get("item_id", ""),
            text         = doc,
            hybrid_score = round(1 - dist, 3),
        ))
    return results


# ── Public API ────────────────────────────────────────────────────────────────

def hybrid_search(
    query: str,
    n_results: int = 10,
    alpha: float = 0.5,
    year_from: int | None = None,
    year_to: int | None = None,
    rerank: bool = True,
    candidate_multiplier: int = 3,
) -> list[SearchResult]:
    b = backend()
    if b == "weaviate":
        return _weaviate_search(query, n_results, alpha, year_from, year_to, rerank)
    elif b == "chromadb":
        # ChromaDB: vector-only, no BM25, no rerank
        return _chromadb_search(query, n_results, year_from, year_to)
    else:
        return []


def index_stats() -> dict:
    b = backend()
    try:
        if b == "weaviate":
            client = _weaviate_connect()
            col    = client.collections.get(COLLECTION_NAME)
            total  = col.aggregate.over_all(total_count=True).total_count
            sample = col.query.fetch_objects(limit=5000, return_properties=["year"]).objects
            years  = [o.properties["year"] for o in sample if o.properties.get("year")]
            client.close()
            return {"total": total, "year_min": min(years) if years else 2004,
                    "year_max": max(years) if years else 2021, "backend": "weaviate"}

        elif b == "chromadb":
            col   = _chroma_collection()
            total = col.count()
            # Sample 200 to get year range — fetching all 179K at once is too slow
            sample = col.get(limit=200, include=["metadatas"])["metadatas"]
            years  = [m["year"] for m in sample if m.get("year")]
            return {"total": total, "year_min": min(years) if years else 2005,
                    "year_max": max(years) if years else 2021, "backend": "chromadb"}

    except Exception:
        pass
    return {"total": 0, "year_min": 2004, "year_max": 2021, "backend": "none"}
