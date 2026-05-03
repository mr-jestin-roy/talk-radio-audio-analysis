#!/usr/bin/env python3
import csv
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import requests

AUDIO_DIR = "audio"
TRANSCRIPTS_DIR = "transcripts"
DOWNLOAD_MANIFEST = "download_manifest.csv"
TRANSCRIBE_MANIFEST = "transcribe_manifest.csv"
WORKERS = 6

NGROK_URL = os.environ.get("NGROK_URL", "").rstrip("/")

manifest_lock = threading.Lock()

# Logging: write to both console and timestamped log file
log_file = f"transcribe_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(log_file),
    ],
)
log = logging.getLogger()


def init_manifest():
    if not Path(TRANSCRIBE_MANIFEST).exists():
        with open(TRANSCRIBE_MANIFEST, "w", newline="") as f:
            csv.writer(f).writerow(["item_id", "file", "status", "error"])


def load_manifest():
    manifest = {}
    if Path(TRANSCRIBE_MANIFEST).exists():
        with open(TRANSCRIBE_MANIFEST) as f:
            for row in csv.DictReader(f):
                manifest[row["file"]] = row
    return manifest


def update_manifest(item_id, filename, status, error=""):
    with manifest_lock:
        manifest = load_manifest()
        manifest[filename] = {"item_id": item_id, "file": filename, "status": status, "error": error}
        with open(TRANSCRIBE_MANIFEST, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["item_id", "file", "status", "error"])
            w.writeheader()
            for row in manifest.values():
                w.writerow(row)


def transcribe_file(mp3_path: Path, item_id: str, index: int, total: int):
    transcript_path = Path(TRANSCRIPTS_DIR) / item_id / (mp3_path.stem + "_transcript.txt")
    transcript_path.parent.mkdir(parents=True, exist_ok=True)

    # Skip if transcript already exists and is non-empty
    if transcript_path.exists() and transcript_path.stat().st_size > 0:
        update_manifest(item_id, mp3_path.name, "done")
        log.info(f"[{index}/{total}] skip {mp3_path.name} (transcript exists)")
        return True

    for attempt in range(1, 4):
        try:
            with open(mp3_path, "rb") as f:
                response = requests.post(
                    f"{NGROK_URL}/v1/audio/transcriptions",
                    headers={"ngrok-skip-browser-warning": "1"},
                    files={"file": (mp3_path.name, f, "audio/mpeg")},
                    data={"model": "distil-large-v3.5", "response_format": "json", "language": "en"},
                    timeout=300,
                )

            if response.status_code != 200:
                error = f"HTTP {response.status_code}: {response.text[:80]}"
                if attempt < 3:
                    log.info(f"[{index}/{total}] retry {attempt}/3 {mp3_path.name} — {error}")
                    time.sleep(5 * attempt)
                    continue
                update_manifest(item_id, mp3_path.name, "error", error=error)
                log.info(f"[{index}/{total}] ✗ {mp3_path.name} — {error}")
                return False

            transcript_path.write_text(response.json()["text"].strip())
            update_manifest(item_id, mp3_path.name, "done")
            log.info(f"[{index}/{total}] ✓ {mp3_path.name}")
            return True

        except (requests.Timeout, requests.ConnectionError, Exception) as e:
            if attempt < 3:
                log.info(f"[{index}/{total}] retry {attempt}/3 {mp3_path.name} — {e}")
                time.sleep(5 * attempt)
                continue
            update_manifest(item_id, mp3_path.name, "error", error=str(e)[:100])
            log.info(f"[{index}/{total}] ✗ {mp3_path.name} — {e}")
            return False


def load_downloaded_items():
    items = []
    if not Path(DOWNLOAD_MANIFEST).exists():
        return items
    with open(DOWNLOAD_MANIFEST) as f:
        for row in csv.DictReader(f):
            if row["status"] == "done":
                items.append(row["item_id"])
    return items


def main():
    if not NGROK_URL:
        log.error("ERROR: Set NGROK_URL environment variable first.")
        log.error("  export NGROK_URL=https://xxxx-xx-xx.ngrok-free.app")
        return

    log.info(f"Log file: {log_file}")
    log.info(f"Endpoint: {NGROK_URL}")

    os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)
    init_manifest()
    manifest = load_manifest()

    downloaded_items = load_downloaded_items()
    log.info(f"Downloaded items: {len(downloaded_items)}")

    all_files = []
    for item_id in downloaded_items:
        item_dir = Path(AUDIO_DIR) / item_id
        for mp3 in sorted(item_dir.glob("*.mp3")):
            transcript_path = Path(TRANSCRIPTS_DIR) / item_id / (mp3.stem + "_transcript.txt")
            already_done = (
                manifest.get(mp3.name, {}).get("status") == "done"
                or (transcript_path.exists() and transcript_path.stat().st_size > 0)
            )
            if not already_done:
                all_files.append((mp3, item_id))

    done_count = sum(1 for v in manifest.values() if v["status"] == "done")
    total_files = len(all_files) + done_count
    log.info(f"Total mp3 files: {total_files} | Already done: {done_count} | Remaining: {len(all_files)} | Workers: {WORKERS}\n")

    start = time.time()
    completed = done_count

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {
            executor.submit(transcribe_file, mp3, item_id, i + done_count + 1, total_files): mp3.name
            for i, (mp3, item_id) in enumerate(all_files)
        }
        for future in as_completed(futures):
            completed += 1
            if completed % 100 == 0:
                elapsed = time.time() - start
                rate = (completed - done_count) / elapsed
                remaining = total_files - completed
                eta_hours = (remaining / rate) / 3600 if rate > 0 else 0
                log.info(f"\n>>> Progress: {completed}/{total_files} | Rate: {rate:.1f} files/s | ETA: {eta_hours:.1f}h\n")

    done = sum(1 for v in load_manifest().values() if v["status"] == "done")
    errors = sum(1 for v in load_manifest().values() if v["status"] == "error")
    log.info(f"\n=== Finished: {done}/{total_files} done, {errors} errors ===")


if __name__ == "__main__":
    main()
