#!/usr/bin/env python3
"""
Chunk transcripts → Weaviate (vector + BM25 hybrid index).

Weaviate replaces both ChromaDB (vector) and SQLite FTS5 (BM25) —
hybrid search is built-in.

Usage:
    python ingest.py                  # ingest all, resume if interrupted
    python ingest.py --reset          # wipe collection and re-ingest
    python ingest.py --status         # show index stats only
"""

import argparse
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import weaviate
import weaviate.classes as wvc
from sentence_transformers import SentenceTransformer

# ── Config ────────────────────────────────────────────────────────────────────

TRANSCRIPTS_DIR  = Path("transcripts")
COLLECTION_NAME  = "Transcripts"
EMBED_MODEL      = "BAAI/bge-base-en-v1.5"   # better than MiniLM for English news
CHUNK_SIZE       = 1800   # chars (~450 tokens)
CHUNK_OVERLAP    = 200
BATCH_SIZE       = 200    # Weaviate batch insert size

WEAVIATE_URL     = os.environ.get("WEAVIATE_URL", "http://localhost:8080")
WEAVIATE_API_KEY = os.environ.get("WEAVIATE_API_KEY", "")

DATE_RE = re.compile(r"rush-limbaugh-radio-show-(\d{4})-(\d{2})-(\d{2})")
HOUR_RE = re.compile(r"hour-(\d)")


# ── Weaviate connection ───────────────────────────────────────────────────────

def connect() -> weaviate.WeaviateClient:
    parsed = urlparse(WEAVIATE_URL)
    host   = parsed.hostname or "localhost"
    port   = parsed.port or 8080

    if WEAVIATE_API_KEY:
        return weaviate.connect_to_weaviate_cloud(
            cluster_url=WEAVIATE_URL,
            auth_credentials=weaviate.auth.AuthApiKey(WEAVIATE_API_KEY),
        )
    return weaviate.connect_to_local(host=host, port=port, grpc_port=50051)


# ── Collection schema ─────────────────────────────────────────────────────────

def ensure_collection(client: weaviate.WeaviateClient, reset: bool = False):
    if reset and client.collections.exists(COLLECTION_NAME):
        client.collections.delete(COLLECTION_NAME)
        print(f"Collection '{COLLECTION_NAME}' deleted.")

    if not client.collections.exists(COLLECTION_NAME):
        client.collections.create(
            name=COLLECTION_NAME,
            # We supply our own vectors — no external vectorizer needed
            vector_config=wvc.config.Configure.Vectors.self_provided(),
            # BM25 tokeniser for hybrid search
            inverted_index_config=wvc.config.Configure.inverted_index(
                bm25_b=0.75,
                bm25_k1=1.2,
            ),
            properties=[
                wvc.config.Property(name="text",        data_type=wvc.config.DataType.TEXT),
                wvc.config.Property(name="chunk_id",    data_type=wvc.config.DataType.TEXT),
                wvc.config.Property(name="item_id",     data_type=wvc.config.DataType.TEXT),
                wvc.config.Property(name="date",        data_type=wvc.config.DataType.TEXT),
                wvc.config.Property(name="year",        data_type=wvc.config.DataType.INT),
                wvc.config.Property(name="month",       data_type=wvc.config.DataType.INT),
                wvc.config.Property(name="hour",        data_type=wvc.config.DataType.INT),
                wvc.config.Property(name="chunk_index", data_type=wvc.config.DataType.INT),
            ],
        )
        print(f"Collection '{COLLECTION_NAME}' created.")
    else:
        print(f"Collection '{COLLECTION_NAME}' exists — resuming.")


# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_date(item_id: str) -> tuple[str, int, int]:
    m = DATE_RE.search(item_id)
    if not m:
        return "", 0, 0
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}", int(m.group(1)), int(m.group(2))


def parse_hour(filename: str) -> int:
    m = HOUR_RE.search(filename)
    return int(m.group(1)) if m else 0


def chunk_text(text: str) -> list[str]:
    chunks, start = [], 0
    while start < len(text):
        chunks.append(text[start:start + CHUNK_SIZE].strip())
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return [c for c in chunks if len(c) > 100]


def existing_chunk_ids(collection) -> set[str]:
    """Fetch all chunk_ids already in Weaviate (for resume support)."""
    done = set()
    cursor = None
    while True:
        result = collection.query.fetch_objects(
            limit=1000,
            after=cursor,
            return_properties=["chunk_id"],
        )
        if not result.objects:
            break
        for obj in result.objects:
            done.add(obj.properties["chunk_id"])
        cursor = result.objects[-1].uuid
    return done


# ── Main ingest ───────────────────────────────────────────────────────────────

def ingest(reset: bool = False):
    print(f"Loading embedding model: {EMBED_MODEL}")
    model = SentenceTransformer(EMBED_MODEL, device="cpu")

    print(f"Connecting to Weaviate at {WEAVIATE_URL}")
    client = connect()
    ensure_collection(client, reset=reset)
    collection = client.collections.get(COLLECTION_NAME)

    print("Scanning existing chunks (for resume)…")
    done_ids = existing_chunk_ids(collection)
    print(f"  Already indexed: {len(done_ids):,} chunks")

    transcript_files = sorted(TRANSCRIPTS_DIR.rglob("*_transcript.txt"))
    print(f"  Transcript files: {len(transcript_files):,}\n")

    new_chunks = skipped = 0
    buffer_props   = []
    buffer_vectors = []

    def flush():
        nonlocal new_chunks
        if not buffer_props:
            return
        vectors = model.encode(
            [p["text"] for p in buffer_props],
            batch_size=64,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        with collection.batch.dynamic() as batch:
            for props, vec in zip(buffer_props, vectors):
                batch.add_object(
                    properties=props,
                    vector=vec.tolist(),
                    uuid=weaviate.util.generate_uuid5(props["chunk_id"]),
                )
        new_chunks += len(buffer_props)
        buffer_props.clear()
        buffer_vectors.clear()
        print(f"  Indexed: {new_chunks:,} new  |  Skipped: {skipped:,}", end="\r")

    for tf in transcript_files:
        item_id          = tf.parent.name
        date_str, yr, mo = parse_date(item_id)
        hour             = parse_hour(tf.stem)
        text             = tf.read_text(encoding="utf-8", errors="ignore").strip()
        if not text:
            continue

        for ci, chunk in enumerate(chunk_text(text)):
            cid = f"{item_id}__h{hour}__c{ci}"
            if cid in done_ids:
                skipped += 1
                continue

            buffer_props.append({
                "text":        chunk,
                "chunk_id":    cid,
                "item_id":     item_id,
                "date":        date_str,
                "year":        yr,
                "month":       mo,
                "hour":        hour,
                "chunk_index": ci,
            })

            if len(buffer_props) >= BATCH_SIZE:
                flush()

    flush()
    total = collection.aggregate.over_all(total_count=True).total_count
    print(f"\n{'─'*50}")
    print(f"  New chunks indexed : {new_chunks:,}")
    print(f"  Skipped (existing) : {skipped:,}")
    print(f"  Total in Weaviate  : {total:,}")
    print(f"{'─'*50}")
    client.close()


def status():
    client = connect()
    try:
        col   = client.collections.get(COLLECTION_NAME)
        total = col.aggregate.over_all(total_count=True).total_count
        print(f"Weaviate collection '{COLLECTION_NAME}': {total:,} chunks")
        sample = col.query.fetch_objects(limit=1, return_properties=["date", "year"]).objects
        if sample:
            print(f"Sample: {sample[0].properties}")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset",  action="store_true", help="Wipe collection and re-ingest")
    parser.add_argument("--status", action="store_true", help="Show index stats")
    args = parser.parse_args()

    if args.status:
        status()
    else:
        ingest(reset=args.reset)
