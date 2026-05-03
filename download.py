#!/usr/bin/env python3
import csv
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ITEMS_FILE = "items.txt"
MANIFEST_FILE = "download_manifest.csv"
AUDIO_DIR = "audio"
WORKERS = 3

manifest_lock = threading.Lock()

def init_manifest():
    if not Path(MANIFEST_FILE).exists():
        with open(MANIFEST_FILE, "w", newline="") as f:
            csv.writer(f).writerow(["item_id", "status", "mp3_count", "error"])

def load_manifest():
    manifest = {}
    if Path(MANIFEST_FILE).exists():
        with open(MANIFEST_FILE) as f:
            for row in csv.DictReader(f):
                manifest[row["item_id"]] = row
    return manifest

def update_manifest(item_id, status, mp3_count=0, error=""):
    with manifest_lock:
        manifest = load_manifest()
        manifest[item_id] = {"item_id": item_id, "status": status, "mp3_count": mp3_count, "error": error}
        with open(MANIFEST_FILE, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["item_id", "status", "mp3_count", "error"])
            w.writeheader()
            for row in manifest.values():
                w.writerow(row)

def download_item(item_id, index, total):
    item_dir = Path(AUDIO_DIR) / item_id
    try:
        cmd = [".venv/bin/ia", "download", item_id, "-g", "*.mp3", "--destdir", AUDIO_DIR]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        if result.returncode != 0:
            error = (result.stderr or result.stdout).split("\n")[0][:100]
            update_manifest(item_id, "error", error=error)
            print(f"[{index}/{total}] ✗ {item_id} — {error}")
            return False

        mp3_count = len(list(item_dir.glob("*.mp3")))
        if mp3_count == 0:
            update_manifest(item_id, "error", error="no mp3 files found")
            print(f"[{index}/{total}] ✗ {item_id} — no mp3s found")
            return False

        update_manifest(item_id, "done", mp3_count=mp3_count)
        print(f"[{index}/{total}] ✓ {item_id} — {mp3_count} mp3(s)")
        return True
    except subprocess.TimeoutExpired:
        update_manifest(item_id, "error", error="timeout")
        print(f"[{index}/{total}] ✗ {item_id} — timeout")
        return False
    except Exception as e:
        update_manifest(item_id, "error", error=str(e)[:100])
        print(f"[{index}/{total}] ✗ {item_id} — {e}")
        return False

def main():
    os.makedirs(AUDIO_DIR, exist_ok=True)
    init_manifest()
    manifest = load_manifest()

    with open(ITEMS_FILE) as f:
        items = [line.strip() for line in f if line.strip()]

    pending = [(i, item_id) for i, item_id in enumerate(items, 1)
               if not (item_id in manifest and manifest[item_id]["status"] == "done")]

    done_count = len(items) - len(pending)
    print(f"Total: {len(items)} | Already done: {done_count} | Remaining: {len(pending)} | Workers: {WORKERS}\n")

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {executor.submit(download_item, item_id, i, len(items)): item_id
                   for i, item_id in pending}
        completed = done_count
        for future in as_completed(futures):
            completed += 1
            if completed % 50 == 0:
                done = sum(1 for m in load_manifest().values() if m["status"] == "done")
                print(f"\n>>> Progress: {done}/{len(items)} done\n")

    done = sum(1 for m in load_manifest().values() if m["status"] == "done")
    errors = sum(1 for m in load_manifest().values() if m["status"] == "error")
    print(f"\n=== Finished: {done}/{len(items)} done, {errors} errors ===")

if __name__ == "__main__":
    main()
