#!/usr/bin/env python3
"""
Async batch analysis engine — 4-8× faster than sequential via concurrent LLM calls.

Usage:
    python analyze.py \
        --prompt prompts/immigration_stance.yaml \
        --from 2005-01-01 --to 2020-12-31 \
        --llm-url https://xxxx.ngrok-free.app \
        --model Qwen/Qwen2.5-7B-Instruct \
        --output results/immigration_2005_2020.csv \
        --concurrency 6
"""

import argparse
import asyncio
import csv
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml
from openai import AsyncOpenAI

TRANSCRIPTS_DIR = Path("transcripts")
DATE_RE  = re.compile(r"rush-limbaugh-radio-show-(\d{4}-\d{2}-\d{2})")
HOUR_RE  = re.compile(r"hour-(\d)")

_FIELD_ALIASES: dict[str, str] = {
    "quotes":            "citations",
    "quote":             "citations",
    "excerpts":          "citations",
    "excerpt":           "citations",
    "evidence":          "citations",
    "supporting_quotes": "citations",
    "subtopics":         "topics",
    "sub_topics":        "topics",
    "key_topics":        "topics",
    "tags":              "topics",
    "score":             "confidence",
    "certainty":         "confidence",
    "probability":       "confidence",
    "summary_text":      "summary",
    "analysis":          "summary",
    "label":             "stance",
    "classification":    "stance",
}


def normalize_result(result: dict) -> dict:
    return {_FIELD_ALIASES.get(k, k): v for k, v in result.items()}


# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_date(item_id: str) -> str:
    m = DATE_RE.search(item_id)
    return m.group(1) if m else ""


def load_prompt(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def build_episodes(date_from: str, date_to: str) -> list[dict]:
    episodes = {}
    for tf in sorted(TRANSCRIPTS_DIR.rglob("*_transcript.txt")):
        item_id = tf.parent.name
        d       = parse_date(item_id)
        if not d or not (date_from <= d <= date_to):
            continue
        if item_id not in episodes:
            episodes[item_id] = {"item_id": item_id, "date": d, "hours": []}
        episodes[item_id]["hours"].append(tf)
    return sorted(episodes.values(), key=lambda e: e["date"])


def merge_episode(episode: dict) -> str:
    parts = []
    for tf in sorted(episode["hours"],
                     key=lambda p: HOUR_RE.search(p.stem).group(1) if HOUR_RE.search(p.stem) else "0"):
        t = tf.read_text(encoding="utf-8", errors="ignore").strip()
        if t:
            parts.append(t)
    return "\n\n".join(parts)


def load_checkpoint(output_path: Path) -> set[str]:
    cp = output_path.with_suffix(".checkpoint.json")
    return set(json.loads(cp.read_text())) if cp.exists() else set()


def save_checkpoint(output_path: Path, done: set[str]):
    output_path.with_suffix(".checkpoint.json").write_text(json.dumps(sorted(done)))


def write_progress(path: Path, data: dict):
    path.write_text(json.dumps(data))


# ── Async LLM call ────────────────────────────────────────────────────────────

async def call_llm(
    client: AsyncOpenAI,
    semaphore: asyncio.Semaphore,
    model: str,
    prompt_cfg: dict,
    transcript: str,
    retries: int = 3,
) -> dict:
    system     = prompt_cfg.get("system", "You are a political science researcher.")
    user_msg   = prompt_cfg["prompt"].replace("{text}", transcript[:12000])
    schema = prompt_cfg.get("output_schema")

    async with semaphore:
        for attempt in range(1, retries + 1):
            try:
                resp = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user",   "content": user_msg},
                    ],
                    temperature=0.0,
                    max_tokens=2048,
                    extra_body={"guided_json": schema} if schema else {},
                )
                raw = resp.choices[0].message.content.strip()
                # Strip Qwen3 <think>...</think> blocks
                raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
                # Strip markdown code fences
                raw = re.sub(r"^```(?:json)?\n?", "", raw)
                raw = re.sub(r"\n?```$", "", raw).strip()
                return normalize_result(json.loads(raw))

            except json.JSONDecodeError as e:
                if attempt == retries:
                    return {"error": f"JSON parse failed: {e}", "raw": raw[:200]}
                await asyncio.sleep(2 * attempt)

            except Exception as e:
                if attempt == retries:
                    return {"error": str(e)}
                await asyncio.sleep(5 * attempt)

    return {"error": "max retries exceeded"}


# ── Async main ────────────────────────────────────────────────────────────────

async def run(args):
    prompt_cfg   = load_prompt(args.prompt)
    output_path  = Path(args.output)
    progress_path = output_path.with_suffix(".progress.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    episodes  = build_episodes(args.date_from, args.date_to)
    if not episodes:
        print("No episodes found in range.")
        sys.exit(1)

    done_dates = set() if args.no_resume else load_checkpoint(output_path)
    pending    = [e for e in episodes if e["date"] not in done_dates]

    print(f"Episodes: {len(episodes):,}  |  Done: {len(done_dates):,}  |  Remaining: {len(pending):,}")
    print(f"Concurrency: {args.concurrency}  |  Model: {args.model}")

    output_fields = prompt_cfg.get("output_fields", [])
    base_fields   = ["date", "item_id", "error"]
    all_fields    = base_fields + [f for f in output_fields if f not in base_fields]

    # Open CSV (append for resume)
    file_exists = output_path.exists() and output_path.stat().st_size > 0
    csv_file    = open(output_path, "a", newline="", encoding="utf-8")
    writer      = csv.DictWriter(csv_file, fieldnames=all_fields, extrasaction="ignore")
    if not file_exists:
        writer.writeheader()

    client    = AsyncOpenAI(
        base_url=f"{args.llm_url.rstrip('/')}/v1",
        api_key=os.environ.get("LLM_API_KEY", "dummy"),
    )
    semaphore  = asyncio.Semaphore(args.concurrency)
    write_lock = asyncio.Lock()

    start     = time.time()
    completed = len(done_dates)
    errors    = 0
    processed = 0  # episodes processed in this run

    write_progress(progress_path, {
        "status": "running", "total": len(episodes),
        "completed": completed, "errors": errors,
        "started": datetime.now().isoformat(),
        "prompt": Path(args.prompt).stem, "last_date": "", "last_result": {},
    })

    async def process_episode(episode: dict):
        nonlocal completed, errors, processed

        transcript = merge_episode(episode)
        result     = await call_llm(client, semaphore, args.model, prompt_cfg, transcript)

        row = {
            "date":    episode["date"],
            "item_id": episode["item_id"],
            "error":   result.get("error", ""),
        }
        for field in output_fields:
            val = result.get(field, "")
            if isinstance(val, list):
                val = " | ".join(str(v) for v in val)
            row[field] = val

        async with write_lock:
            writer.writerow(row)
            csv_file.flush()
            done_dates.add(episode["date"])
            save_checkpoint(output_path, done_dates)

            if result.get("error"):
                errors += 1
            completed += 1
            processed += 1

            elapsed   = time.time() - start
            rate      = processed / max(elapsed, 1)
            remaining = len(pending) - processed
            eta_sec   = int(remaining / rate) if rate > 0 else 0

            write_progress(progress_path, {
                "status": "running", "total": len(episodes),
                "completed": completed, "errors": errors,
                "elapsed_sec": int(elapsed), "eta_sec": eta_sec,
                "prompt": Path(args.prompt).stem,
                "last_date": episode["date"],
                "last_result": {
                    k: (v[:120] if isinstance(v, str) else v)
                    for k, v in result.items() if k != "error"
                },
            })
            print(f"  [{completed}/{len(episodes)}] {episode['date']}  errors:{errors}", end="\r")

    # Run all episodes concurrently (bounded by semaphore)
    await asyncio.gather(*[process_episode(ep) for ep in pending])

    csv_file.close()
    write_progress(progress_path, {
        "status": "done", "total": len(episodes),
        "completed": completed, "errors": errors,
        "output": str(output_path),
    })
    print(f"\n{'='*55}")
    print(f"  Done: {completed:,} episodes  |  Errors: {errors}  |  Time: {int(time.time()-start)}s")
    print(f"  Output: {output_path}")
    print(f"{'='*55}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt",      required=True)
    parser.add_argument("--from",        dest="date_from", required=True)
    parser.add_argument("--to",          dest="date_to",   required=True)
    parser.add_argument("--llm-url",     required=True)
    parser.add_argument("--model",       default="Qwen/Qwen2.5-7B-Instruct")
    parser.add_argument("--output",      required=True)
    parser.add_argument("--concurrency", type=int, default=6,
                        help="Parallel LLM requests (default 6)")
    parser.add_argument("--no-resume",   action="store_true",
                        help="Ignore checkpoint, restart from scratch")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
