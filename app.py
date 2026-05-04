#!/usr/bin/env python3
"""
Rush Limbaugh Archive — Text Analysis Pipeline
Streamlit UI: Search · Single Episode · Batch Analysis · Time Series
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
import yaml
from openai import OpenAI

import retrieval

# ── Config ───────────────────────────────────────────────────────────────────

# Data directories — overridable via env (Azure mounts them at /app/data/…)
TRANSCRIPTS_DIR = Path(os.environ.get("TRANSCRIPTS_DIR", "transcripts"))
PROMPTS_DIR     = Path(os.environ.get("PROMPTS_DIR",     "prompts"))
RESULTS_DIR     = Path(os.environ.get("RESULTS_DIR",     "results"))
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

LLM_URL     = os.environ.get("LLM_URL", "")
LLM_MODEL   = os.environ.get("LLM_MODEL", "gemini-2.0-flash")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "dummy")

def _is_gemini(url: str) -> bool:
    """Gemini's OpenAI-compat endpoint doesn't support vllm-specific params."""
    return "googleapis.com" in url or "gemini" in url.lower()

DATE_RE = re.compile(r"rush-limbaugh-radio-show-(\d{4}-\d{2}-\d{2})")
HOUR_RE = re.compile(r"hour-(\d)")

st.set_page_config(
    page_title="Rush Limbaugh Archive",
    page_icon="📻",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
.pill-restrict  {background:#fde8e8;color:#c0392b;padding:2px 10px;border-radius:12px;font-size:13px;font-weight:600}
.pill-support   {background:#e8fde8;color:#1a7a3a;padding:2px 10px;border-radius:12px;font-size:13px;font-weight:600}
.pill-mixed     {background:#fff3dc;color:#a06000;padding:2px 10px;border-radius:12px;font-size:13px;font-weight:600}
.pill-neutral   {background:#eee;color:#555;padding:2px 10px;border-radius:12px;font-size:13px;font-weight:600}
.pill-oppose    {background:#fde8e8;color:#c0392b;padding:2px 10px;border-radius:12px;font-size:13px;font-weight:600}
.citation       {border-left:3px solid #ff4b4b;padding:4px 10px;margin:4px 0;font-style:italic;font-size:13px;color:#444;background:#fafafa}
.chunk-card     {border:1px solid #e0e0e0;border-radius:8px;padding:12px 16px;margin-bottom:10px;background:#fafafa}
.score-badge    {font-size:11px;color:#888;font-family:monospace}
</style>
""", unsafe_allow_html=True)


# ── Cached helpers ────────────────────────────────────────────────────────────

@st.cache_data(ttl=120)
def get_index_stats() -> dict:
    return retrieval.index_stats()


@st.cache_data(ttl=60)
def list_prompts() -> list[str]:
    PROMPTS_DIR.mkdir(exist_ok=True)
    return sorted(p.stem for p in PROMPTS_DIR.glob("*.yaml"))


@st.cache_data(ttl=30)
def list_results() -> list[str]:
    return sorted(str(p) for p in RESULTS_DIR.glob("*.csv"))


def load_prompt_cfg(name: str) -> dict:
    with open(PROMPTS_DIR / f"{name}.yaml") as f:
        return yaml.safe_load(f)


def save_prompt_cfg(name: str, cfg: dict):
    """Write edited prompt config back to its YAML file."""
    path = PROMPTS_DIR / f"{name}.yaml"
    with open(path, "w") as f:
        yaml.dump(cfg, f, allow_unicode=True, sort_keys=False, default_flow_style=False)


def prompt_editor(cfg: dict, key_prefix: str) -> dict:
    """
    Inline prompt editor widget.  Returns the (possibly edited) prompt config.
    Changes are held in session_state until the user clicks Save.
    """
    prompt_name = cfg["prompt"]
    if not prompt_name:
        st.warning("Select a prompt in the sidebar first.")
        return {}

    # Load from YAML once per prompt selection
    ss_key = f"prompt_cfg_{prompt_name}"
    if ss_key not in st.session_state:
        st.session_state[ss_key] = load_prompt_cfg(prompt_name)
    pcfg = st.session_state[ss_key]

    with st.expander("✏️  Edit prompt", expanded=False):
        st.caption(f"Editing `prompts/{prompt_name}.yaml` — changes apply immediately to this run.")

        col_sys, col_prompt = st.columns(2)

        new_system = col_sys.text_area(
            "System message",
            value=pcfg.get("system", ""),
            height=140,
            key=f"{key_prefix}_system",
        )
        new_prompt = col_prompt.text_area(
            "User prompt  (use `{text}` where the transcript goes)",
            value=pcfg.get("prompt", ""),
            height=140,
            key=f"{key_prefix}_prompt",
        )

        # Show output_schema as editable YAML text
        schema_yaml = yaml.dump(
            pcfg.get("output_schema", {}),
            allow_unicode=True, sort_keys=False, default_flow_style=False,
        )
        new_schema_raw = st.text_area(
            "Output schema (YAML)",
            value=schema_yaml,
            height=160,
            key=f"{key_prefix}_schema",
            help="Defines the JSON fields the LLM must return. Edit enum values or add new fields here.",
        )

        sa, sb, _ = st.columns([1, 1, 3])
        if sa.button("💾  Save to YAML", key=f"{key_prefix}_save"):
            try:
                new_schema = yaml.safe_load(new_schema_raw) or {}
                pcfg["system"] = new_system
                pcfg["prompt"] = new_prompt
                pcfg["output_schema"] = new_schema
                st.session_state[ss_key] = pcfg
                save_prompt_cfg(prompt_name, pcfg)
                st.success(f"Saved to prompts/{prompt_name}.yaml")
            except yaml.YAMLError as e:
                st.error(f"Invalid YAML in schema: {e}")

        if sb.button("↩  Reset from file", key=f"{key_prefix}_reset"):
            if ss_key in st.session_state:
                del st.session_state[ss_key]
            st.rerun()

    # Return live (possibly unsaved) config
    try:
        live_schema = yaml.safe_load(
            st.session_state.get(f"{key_prefix}_schema", schema_yaml)
        ) or {}
    except Exception:
        live_schema = pcfg.get("output_schema", {})

    return {
        **pcfg,
        "system": st.session_state.get(f"{key_prefix}_system", pcfg.get("system", "")),
        "prompt": st.session_state.get(f"{key_prefix}_prompt", pcfg.get("prompt", "")),
        "output_schema": live_schema,
    }


@st.cache_data(ttl=600)
def corpus_dates() -> list[str]:
    """Return sorted list of all episode date strings found in the transcripts dir."""
    if not TRANSCRIPTS_DIR.exists():
        return []
    dates = []
    for d in TRANSCRIPTS_DIR.iterdir():
        m = DATE_RE.search(d.name)
        if m:
            dates.append(m.group(1))
    return sorted(dates)


def corpus_episode_count() -> int:
    return len(corpus_dates())


@st.cache_data(ttl=600)
def count_episodes_in_range(date_from: str, date_to: str) -> int:
    """Cached — recomputes only when date_from / date_to change."""
    return sum(1 for d in corpus_dates() if date_from <= d <= date_to)


# Common field-name aliases the LLM might use instead of the canonical names
_FIELD_ALIASES: dict[str, str] = {
    "quotes":        "citations",
    "quote":         "citations",
    "excerpts":      "citations",
    "excerpt":       "citations",
    "evidence":      "citations",
    "supporting_quotes": "citations",
    "subtopics":     "topics",
    "sub_topics":    "topics",
    "key_topics":    "topics",
    "tags":          "topics",
    "score":         "confidence",
    "certainty":     "confidence",
    "probability":   "confidence",
    "summary_text":  "summary",
    "analysis":      "summary",
    "label":         "stance",
    "classification": "stance",
}


def normalize_result(result: dict) -> dict:
    """Rename any alias keys to their canonical names."""
    out = {}
    for k, v in result.items():
        out[_FIELD_ALIASES.get(k, k)] = v
    return out


def stance_pill(stance: str) -> str:
    cls = f"pill-{stance.lower()}" if stance.lower() in ("restrict","support","mixed","neutral","oppose","negative","positive") else "pill-neutral"
    return f"<span class='{cls}'>{stance}</span>"


# ── Sidebar ───────────────────────────────────────────────────────────────────

def sidebar() -> dict:
    st.sidebar.markdown("## 📻 Rush Limbaugh Archive\n**Text Analysis Pipeline**")
    st.sidebar.divider()

    # Corpus stats
    stats     = get_index_stats()
    all_dates = corpus_dates()
    n_ep      = len(all_dates)
    n_chunks  = stats["total"]
    yr_min    = all_dates[0][:4]  if all_dates else "?"
    yr_max    = all_dates[-1][:4] if all_dates else "?"

    st.sidebar.markdown("**CORPUS**")
    st.sidebar.markdown(f"""
| | |
|---|---|
| Episodes | **{n_ep:,}** |
| Hours    | **{n_ep*3:,}** |
| Chunks   | **{n_chunks:,}** |
| Range    | **'{yr_min[2:]} – '{yr_max[2:]}** |
""")
    # Backend badge
    b = stats.get("backend", "none")
    if b == "weaviate":
        st.sidebar.success("● Weaviate  (hybrid search)")
    elif b == "chromadb":
        st.sidebar.warning("● ChromaDB  (vector only — placeholder)")
    else:
        st.sidebar.error("● No index found — run ingest.py")
    st.sidebar.divider()

    # Prompt picker + creator
    st.sidebar.markdown("**PROMPT CONFIG**")
    prompts = list_prompts()
    selected_prompt = st.sidebar.selectbox("", prompts or ["(none)"], label_visibility="collapsed")
    if prompts:
        st.sidebar.caption(f"prompts/{selected_prompt}.yaml")

    with st.sidebar.expander("➕  Create new prompt config"):
        new_name = st.text_input(
            "File name (no spaces)",
            placeholder="gun_control_stance",
            key="new_prompt_name",
        )
        new_desc = st.text_input(
            "Description",
            placeholder="Classify Rush's stance on gun control",
            key="new_prompt_desc",
        )
        new_topic = st.text_input(
            "Topic phrase (used in prompt text)",
            placeholder="gun control policy",
            key="new_prompt_topic",
        )
        st.caption(
            "Stance labels — one per line, format:  `label = definition`\n"
            "e.g.  `oppose = opposes gun control or stricter measures`"
        )
        default_labels = (
            "oppose = opposes or criticises this policy\n"
            "support = supports or endorses this policy\n"
            "mixed = expresses both opposing and supporting views\n"
            "neutral = topic mentioned but no clear stance\n"
            "absent = topic not meaningfully discussed"
        )
        new_labels_raw = st.text_area(
            "Stance labels",
            value=default_labels,
            height=150,
            key="new_prompt_labels",
            label_visibility="collapsed",
        )

        if st.button("💾  Create YAML", key="new_prompt_create", type="primary"):
            # ── validation ─────────────────────────────────────────
            slug = re.sub(r"[^\w]", "_", new_name.strip().lower())
            if not slug:
                st.error("Enter a file name.")
            elif not new_topic.strip():
                st.error("Enter a topic phrase.")
            else:
                dest = PROMPTS_DIR / f"{slug}.yaml"
                if dest.exists():
                    st.error(f"`{slug}.yaml` already exists — choose a different name.")
                else:
                    # Parse stance lines
                    stance_items: list[tuple[str,str]] = []
                    for line in new_labels_raw.strip().splitlines():
                        if "=" in line:
                            lbl, defn = line.split("=", 1)
                            stance_items.append((lbl.strip(), defn.strip()))

                    if not stance_items:
                        st.error("Add at least one stance label.")
                    else:
                        enum_values = [s[0] for s in stance_items]
                        enum_str    = " | ".join(enum_values)
                        label_lines = "\n".join(
                            f'  - "{lbl}" = {defn}' for lbl, defn in stance_items
                        )
                        user_prompt = (
                            f"Analyse the following Rush Limbaugh transcript and classify "
                            f"his stance on {new_topic.strip()}.\n\n"
                            f"Stance labels:\n{label_lines}\n\n"
                            f"Return ONLY the following JSON object — "
                            f"use EXACTLY these field names, no others:\n\n"
                            f"{{\n"
                            f'  "stance": "<{enum_str}>",\n'
                            f'  "confidence": <number between 0.0 and 1.0>,\n'
                            f'  "summary": "<2-3 sentence qualitative summary>",\n'
                            f'  "citations": [\n'
                            f'    "<verbatim quote from transcript, max 120 chars>"\n'
                            f'  ],\n'
                            f'  "topics": ["<sub-topic>", "<sub-topic>"]\n'
                            f"}}\n\n"
                            f"Rules:\n"
                            f"- citations must be exact word-for-word quotes from the transcript\n"
                            f"- topics = specific sub-topics or policy aspects mentioned\n"
                            f"- output nothing except the JSON object\n"
                            f"/no_think\n\n"
                            f"Transcript:\n{{text}}"
                        )
                        cfg_out = {
                            "name":        slug,
                            "description": new_desc.strip() or f"Classify Rush's stance on {new_topic.strip()}",
                            "system": (
                                "You are a political science researcher coding talk radio "
                                "transcripts for a study on media influence and public opinion. "
                                "Your task is to analyse transcripts and return structured JSON. "
                                "Be precise and evidence-based. "
                                "Citations must be verbatim quotes from the provided text."
                            ),
                            "prompt": user_prompt,
                            "output_fields": ["stance", "confidence", "summary", "citations", "topics"],
                            "output_schema": {
                                "type": "object",
                                "required": ["stance", "confidence", "summary", "citations", "topics"],
                                "properties": {
                                    "stance":     {"type": "string", "enum": enum_values},
                                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                                    "summary":    {"type": "string"},
                                    "citations":  {"type": "array",  "items": {"type": "string"}},
                                    "topics":     {"type": "array",  "items": {"type": "string"}},
                                },
                            },
                        }
                        save_prompt_cfg(slug, cfg_out)
                        list_prompts.clear()   # bust cache so new file appears
                        st.success(f"Created `prompts/{slug}.yaml` — select it above ↑")
    st.sidebar.divider()

    # LLM backend
    st.sidebar.markdown("**LLM BACKEND**")
    llm_url = st.sidebar.text_input(
        "LLM_URL",
        value=LLM_URL,
        placeholder="https://xxxx.ngrok-free.app",
        label_visibility="collapsed",
    )
    model = st.sidebar.text_input("Model", value=LLM_MODEL)
    st.sidebar.divider()

    # Date range — defaults driven by actual corpus
    all_dates   = corpus_dates()
    corpus_min  = date.fromisoformat(all_dates[0])  if all_dates else date(2004, 1,  1)
    corpus_max  = date.fromisoformat(all_dates[-1]) if all_dates else date(2021, 12, 31)

    st.sidebar.markdown("**DATE RANGE**")
    date_from = st.sidebar.date_input("From", value=corpus_min,
                                       min_value=corpus_min, max_value=corpus_max)
    date_to   = st.sidebar.date_input("To",   value=corpus_max,
                                       min_value=corpus_min, max_value=corpus_max)

    # Footer
    st.sidebar.divider()
    st.sidebar.markdown(
        "<div style='font-size:10px;color:#aaa;line-height:1.7;text-align:center'>"
        "© 2025 SoDa Labs, Monash Business School<br>"
        "All rights reserved.<br>"
        "Rush Limbaugh™ is a registered trademark<br>"
        "of Premiere Networks, Inc.<br>"
        "This tool is for academic research purposes only."
        "</div>",
        unsafe_allow_html=True,
    )

    return {
        "prompt":    selected_prompt if prompts else None,
        "llm_url":   llm_url,
        "model":     model,
        "date_from": str(date_from),
        "date_to":   str(date_to),
        "year_from": date_from.year,
        "year_to":   date_to.year,
    }


# ── Tab 1: Semantic Search ────────────────────────────────────────────────────

def tab_search(cfg: dict):
    st.markdown("### Search")
    st.caption("Hybrid BM25 + vector search across all transcripts, re-ranked by cross-encoder.")

    query     = st.text_input("", placeholder="e.g. What did Rush say about Obamacare?",
                               label_visibility="collapsed")
    c1, c2, c3 = st.columns([2, 2, 1])
    n_results = c1.slider("Results", 3, 20, 8)
    alpha     = c2.slider("BM25 ← blend → Vector", 0.0, 1.0, 0.5, step=0.1,
                           help="0 = pure keyword, 1 = pure semantic")
    run       = c3.button("Search", type="primary", use_container_width=True)

    # Run search and store result in session state so tab switches don't clear it
    if run and query.strip():
        with st.spinner("Searching + re-ranking…"):
            results = retrieval.hybrid_search(
                query=query,
                n_results=n_results,
                alpha=alpha,
                year_from=cfg["year_from"],
                year_to=cfg["year_to"],
                rerank=True,
            )
        st.session_state["search_results"] = results
        st.session_state["search_query"]   = query

    results = st.session_state.get("search_results")
    query   = st.session_state.get("search_query", query)

    if not results:
        return

    st.markdown(f"**{len(results)} results** for *{query}*")
    st.divider()

    for r in results:
        c1, c2 = st.columns([5, 1])
        c1.markdown(f"<span style='font-size:12px;color:#888'>{r.date} · Hour {r.hour}</span>",
                    unsafe_allow_html=True)
        score_str = f"rerank: {r.rerank_score:.3f}" if r.rerank_score else f"hybrid: {r.hybrid_score:.3f}"
        c2.markdown(f"<span class='score-badge'>{score_str}</span>", unsafe_allow_html=True)
        st.markdown(f"<div class='chunk-card'>{r.text[:600]}{'…' if len(r.text)>600 else ''}</div>",
                    unsafe_allow_html=True)


# ── Tab 2: Single-Episode Analysis ───────────────────────────────────────────

def tab_single(cfg: dict):
    st.markdown("### Single-Episode Analysis")
    st.caption("Test your prompt on one episode — verify results before running at scale.")

    if not cfg["prompt"]:
        st.warning("Select a prompt in the sidebar.")
        return
    if not cfg["llm_url"]:
        st.warning("Enter your LLM_URL in the sidebar.")
        return

    # Live prompt editor
    prompt_cfg = prompt_editor(cfg, key_prefix="single")
    if not prompt_cfg:
        return

    st.divider()

    all_dates = corpus_dates()
    ep_default = date.fromisoformat(all_dates[len(all_dates)//2]) if all_dates else date(2010, 4, 23)
    ep_date = st.date_input("Episode date", value=ep_default,
                             min_value=date.fromisoformat(all_dates[0])  if all_dates else date(2004,1,1),
                             max_value=date.fromisoformat(all_dates[-1]) if all_dates else date(2021,12,31))
    run = st.button("▶  Analyse episode", type="primary")

    if run:
        date_str = str(ep_date)
        item_id  = f"rush-limbaugh-radio-show-{date_str}"
        ep_dir   = TRANSCRIPTS_DIR / item_id

        if not ep_dir.exists():
            st.error(f"No transcript found for {date_str}.")
            st.session_state.pop("single_result", None)
        else:
            parts     = [tf.read_text(encoding="utf-8", errors="ignore").strip()
                         for tf in sorted(ep_dir.glob("*_transcript.txt"))]
            full_text = "\n\n".join(parts)
            system    = prompt_cfg.get("system", "You are a political science researcher.")
            user_msg  = prompt_cfg["prompt"].replace("{text}", full_text[:12000])
            schema    = prompt_cfg.get("output_schema")

            # Debug panel
            with st.expander("🔍  Debug — what gets sent to the LLM", expanded=False):
                st.markdown("**Schema passed as guided_json:**")
                st.json(schema if schema else "(none)")
                st.markdown("**First 800 chars of user message:**")
                st.code(user_msg[:800], language="text")

            with st.spinner("Calling LLM…"):
                try:
                    gemini = _is_gemini(cfg["llm_url"])
                    base_url = (
                        cfg["llm_url"].rstrip("/")          # Gemini: full URL already
                        if gemini
                        else f"{cfg['llm_url'].rstrip('/')}/v1"
                    )
                    client = OpenAI(base_url=base_url, api_key=LLM_API_KEY)
                    resp   = client.chat.completions.create(
                        model=cfg["model"],
                        messages=[{"role":"system","content":system},
                                  {"role":"user","content":user_msg}],
                        temperature=0.0,
                        max_tokens=2048,
                        # Gemini: use json_schema response_format (enforces fields like guided_json)
                        # vllm:   use guided_json via extra_body
                        **({"response_format": {
                                "type": "json_schema",
                                "json_schema": {
                                    "name": "analysis_result",
                                    "strict": True,
                                    "schema": schema,
                                },
                            }} if gemini and schema
                           else {"response_format": {"type": "json_object"}} if gemini
                           else {"extra_body": {"guided_json": schema} if schema else {}}),
                    )
                    raw = resp.choices[0].message.content.strip()
                    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
                    raw = re.sub(r"^```(?:json)?\n?", "", raw)
                    raw = re.sub(r"\n?```$", "", raw).strip()
                    if not raw:
                        st.error("LLM returned empty response after stripping think tokens.")
                    else:
                        result = normalize_result(json.loads(raw))
                        st.session_state["single_result"] = {
                            "date": date_str, "hours": len(parts),
                            "chars": len(full_text), "result": result,
                        }
                except json.JSONDecodeError:
                    st.error("LLM response was not valid JSON.")
                    st.code(raw[:1000], language="text")
                except Exception as e:
                    st.error(f"LLM call failed: {e}")

    # Display last result — survives tab switches
    saved = st.session_state.get("single_result")
    if not saved:
        return

    result   = saved["result"]
    date_str = saved["date"]
    st.info(f"**{date_str}** · {saved['hours']} hours · {saved['chars']:,} chars")
    st.success("Done")
    st.divider()

    stance = result.get("stance", "?")
    conf   = result.get("confidence", "?")
    topics = result.get("topics", [])
    c1, c2, c3 = st.columns(3)
    c1.markdown(f"**Stance**<br>{stance_pill(stance)}", unsafe_allow_html=True)
    c2.metric("Confidence", f"{conf:.2f}" if isinstance(conf, float) else conf)
    c3.markdown(f"**Topics**<br>{', '.join(topics) if topics else '—'}", unsafe_allow_html=True)

    st.markdown("**Summary**")
    st.write(result.get("summary", "—"))

    citations = result.get("citations", [])
    if citations:
        st.markdown("**Citations**")
        for c in citations:
            st.markdown(f"<div class='citation'>{c}</div>", unsafe_allow_html=True)

    st.divider()
    st.markdown("**Raw JSON**")
    st.json(result)


# ── Tab 3: Batch Analysis ─────────────────────────────────────────────────────

def tab_batch(cfg: dict):
    st.markdown("### Batch Analysis")
    st.caption("Run LLM analysis across a date range and export to CSV.")

    if not cfg["prompt"]:
        st.warning("Select a prompt in the sidebar.")
        return
    if not cfg["llm_url"]:
        st.warning("Enter your LLM_URL in the sidebar.")
        return

    # Prompt editor — changes must be saved to YAML before batch starts
    prompt_cfg = prompt_editor(cfg, key_prefix="batch")
    if not prompt_cfg:
        return

    st.info("💡 Edit the prompt above, click **Save to YAML**, then start the batch run below.")
    st.divider()

    # Token estimate: transcript capped at 12,000 chars ÷ 4 chars/token ≈ 3,000 input
    # + ~300 output tokens ≈ 3,300 total per episode.
    # Cost reference: GPT-4o-mini rate ($0.15/1M input + $0.60/1M output) as a
    # commercial-API benchmark — your local vllm run costs $0.
    TOKENS_PER_EP   = 3_300
    INPUT_COST_PER_M  = 0.15   # GPT-4o-mini input  $/1M tokens
    OUTPUT_COST_PER_M = 0.60   # GPT-4o-mini output $/1M tokens
    INPUT_TOKENS  = 3_000
    OUTPUT_TOKENS =   300
    n_ep = count_episodes_in_range(cfg["date_from"], cfg["date_to"])
    est_tokens = n_ep * TOKENS_PER_EP
    est_cost   = n_ep * (INPUT_TOKENS * INPUT_COST_PER_M + OUTPUT_TOKENS * OUTPUT_COST_PER_M) / 1_000_000

    c1,c2,c3,c4 = st.columns(4)
    c1.metric("Episodes matched", f"{n_ep:,}")
    c2.metric("Est. tokens", f"{est_tokens/1_000_000:.1f}M",
              help="~3,000 input + ~300 output tokens per episode (12,000-char transcript cap)")
    c3.metric("Est. cost if using GPT-4o-mini", f"${est_cost:.2f}",
              help="GPT-4o-mini: $0.15/1M input + $0.60/1M output. Your local vllm run is free.")
    c4.metric("Your cost (local vllm)", "$0.00")

    st.divider()

    output_path   = RESULTS_DIR / f"{cfg['prompt']}_{cfg['date_from']}_{cfg['date_to']}.csv"
    progress_path = output_path.with_suffix(".progress.json")

    st.markdown(f"**Output:** `{output_path}`")

    if "batch_proc" not in st.session_state:
        st.session_state.batch_proc = None

    r1,r2,r3 = st.columns([2,1,2])
    proc    = st.session_state.batch_proc
    running = proc is not None and proc.poll() is None

    with r1:
        if st.button("▶  Run Batch Analysis", type="primary",
                     use_container_width=True, disabled=running):
            cmd = [
                sys.executable, "analyze.py",
                "--prompt",   str(PROMPTS_DIR / f"{cfg['prompt']}.yaml"),
                "--from",     cfg["date_from"],
                "--to",       cfg["date_to"],
                "--llm-url",  cfg["llm_url"],
                "--model",    cfg["model"],
                "--output",   str(output_path),
            ]
            st.session_state.batch_proc = subprocess.Popen(cmd)
            st.rerun()

    with r2:
        if st.button("⏸  Pause", disabled=not running, use_container_width=True):
            st.session_state.batch_proc.terminate()
            st.session_state.batch_proc = None
            st.rerun()

    with r3:
        if output_path.exists() and output_path.stat().st_size > 0:
            df_exp = pd.read_csv(output_path)
            st.download_button("⬇  Export CSV", df_exp.to_csv(index=False),
                               file_name=output_path.name, mime="text/csv",
                               use_container_width=True)
        else:
            st.button("⬇  Export CSV", disabled=True, use_container_width=True)

    st.divider()

    if progress_path.exists():
        prog      = json.loads(progress_path.read_text())
        total     = prog.get("total", 1)
        completed = prog.get("completed", 0)
        errors    = prog.get("errors", 0)
        status    = prog.get("status","unknown")
        eta_sec   = prog.get("eta_sec", 0)
        last_date = prog.get("last_date","")
        pct       = completed / max(total,1)
        eta_str   = f"~{eta_sec//60} min remaining" if eta_sec>60 else f"{eta_sec}s"
        badge     = "● Running" if running else ("✓ Done" if status=="done" else "⏸ Paused")

        st.markdown(f"**Progress · {output_path.name}** &nbsp; {badge} &nbsp; {completed:,}/{total:,} episodes")
        st.progress(pct, text=f"{pct*100:.0f}%  ·  {eta_str}  ·  checkpoint: {last_date} (resumable)")

        m1,m2,m3 = st.columns(3)
        m1.metric("Completed", f"{completed:,}")
        m2.metric("Errors", errors)
        m3.metric("Remaining", f"{max(total-completed,0):,}")

        if output_path.exists() and output_path.stat().st_size > 0:
            st.markdown("**Live output preview**")
            df_live  = pd.read_csv(output_path)
            df_show  = df_live.tail(20).copy()

            def color_stance(val):
                return {
                    "restrict":  "background-color:#fde8e8;color:#c0392b",
                    "support":   "background-color:#e8fde8;color:#1a7a3a",
                    "mixed":     "background-color:#fff3dc;color:#a06000",
                    "oppose":    "background-color:#fde8e8;color:#c0392b",
                    "negative":  "background-color:#fde8e8;color:#c0392b",
                    "positive":  "background-color:#e8fde8;color:#1a7a3a",
                }.get(str(val).lower(), "")

            stance_col = "stance" if "stance" in df_show.columns else None
            if stance_col:
                st.dataframe(df_show.style.map(color_stance, subset=[stance_col]),
                             use_container_width=True, hide_index=True)
            else:
                st.dataframe(df_show, use_container_width=True, hide_index=True)

        if running:
            time.sleep(3)
            st.rerun()
    elif running:
        st.info("Starting…")
        time.sleep(2)
        st.rerun()
    else:
        st.info("Configure the sidebar and click **Run Batch Analysis** to start.")


# ── Tab 4: Time-Series Explorer ───────────────────────────────────────────────

def tab_timeseries():
    st.markdown("### Time-Series Explorer")
    st.caption("Visualise how stances and topics shift over time.")

    csv_files = list_results()
    if not csv_files:
        st.info("No results yet — run a batch analysis first.")
        return

    selected = st.selectbox("Results file", csv_files)
    df       = pd.read_csv(selected, parse_dates=["date"])
    st.markdown(f"**{len(df):,} episodes** · columns: {', '.join(df.columns.tolist())}")
    st.divider()

    c1,c2 = st.columns(2)

    if "stance" in df.columns:
        df["year_month"] = df["date"].dt.to_period("M").astype(str)
        sc = df.groupby(["year_month","stance"]).size().reset_index(name="count")
        fig = px.bar(sc, x="year_month", y="count", color="stance",
                     color_discrete_map={
                         "restrict":"#e74c3c","support":"#2ecc71",
                         "mixed":"#f39c12","neutral":"#95a5a6",
                         "oppose":"#e74c3c","negative":"#e74c3c","positive":"#2ecc71",
                     },
                     title="Stance distribution over time",
                     labels={"year_month":"Month","count":"Episodes"})
        fig.update_layout(xaxis_tickangle=-45, height=380)
        c1.plotly_chart(fig, use_container_width=True)

    if "confidence" in df.columns:
        df["year"] = df["date"].dt.year
        ct = df.groupby("year")["confidence"].mean().reset_index()
        fig2 = px.line(ct, x="year", y="confidence", markers=True,
                       title="Average confidence by year",
                       labels={"year":"Year","confidence":"Avg. confidence"})
        fig2.update_layout(height=380, yaxis_range=[0,1])
        c2.plotly_chart(fig2, use_container_width=True)

    # Key phrases — bar chart + word cloud side by side
    phrase_col = next((c for c in ("key_phrases","citations","topics") if c in df.columns), None)
    if phrase_col:
        st.divider()
        st.markdown("**Key phrases**")
        yr_min = int(df["date"].dt.year.min())
        yr_max = int(df["date"].dt.year.max())
        if yr_min < yr_max:
            yr_range = st.slider("Filter by year", yr_min, yr_max, (yr_min, yr_max))
        else:
            st.caption(f"Year: {yr_min}")
            yr_range = (yr_min, yr_max)
        df_yr = df[(df["date"].dt.year >= yr_range[0]) & (df["date"].dt.year <= yr_range[1])]

        all_phrases = []
        for val in df_yr[phrase_col].dropna():
            all_phrases.extend([p.strip().strip('"') for p in str(val).split("|") if p.strip()])

        phrase_counts = pd.Series(all_phrases).value_counts().head(40).reset_index()
        phrase_counts.columns = ["phrase","count"]

        bc, wc = st.columns(2)

        fig3 = px.bar(phrase_counts.head(20), x="count", y="phrase",
                      orientation="h", title="Top 20 key phrases", height=500,
                      color="count", color_continuous_scale="Reds")
        fig3.update_layout(yaxis={"categoryorder":"total ascending"}, coloraxis_showscale=False)
        bc.plotly_chart(fig3, use_container_width=True)

        freq_dict = dict(zip(phrase_counts["phrase"], phrase_counts["count"]))
        if freq_dict:
            import io
            import matplotlib.pyplot as plt
            from wordcloud import WordCloud
            wcloud = WordCloud(width=700, height=500, background_color="white",
                               colormap="Reds", max_words=80,
                               prefer_horizontal=0.8).generate_from_frequencies(freq_dict)
            fig_wc, ax = plt.subplots(figsize=(7,5))
            ax.imshow(wcloud, interpolation="bilinear")
            ax.axis("off")
            fig_wc.tight_layout(pad=0)
            buf = io.BytesIO()
            fig_wc.savefig(buf, format="png", dpi=150, bbox_inches="tight")
            buf.seek(0)
            plt.close(fig_wc)
            wc.markdown("**Word cloud**")
            wc.image(buf, use_container_width=True)

    st.divider()
    st.dataframe(df.sort_values("date", ascending=False).head(100),
                 use_container_width=True, hide_index=True)
    st.download_button("⬇  Download full CSV", df.to_csv(index=False),
                       file_name=Path(selected).name, mime="text/csv")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    cfg = sidebar()

    # ── Persistent attribution banner (above all tabs) ────────────────────────
    st.markdown("""
<div style="
    background:#f8f8f8;
    border-left:4px solid #e0e0e0;
    border-radius:6px;
    padding:10px 18px;
    margin-bottom:16px;
    display:flex;
    justify-content:space-between;
    align-items:center;
    flex-wrap:wrap;
    gap:8px;
">
  <div>
    <span style="font-size:13px;font-weight:600;color:#333">📻 Rush Limbaugh Archive — Text Analysis Pipeline</span><br>
    <span style="font-size:12px;color:#666">
      Co-authored by&nbsp;
      <b>Prof. Paul Anton Raschky</b> (Monash University)&nbsp;&amp;&nbsp;
      <b>Prof. Ashani Amarasinghe</b> (University of Sydney)
    </span>
  </div>
  <div style="text-align:right">
    <span style="font-size:12px;color:#888">
      Research Assistant: <b>Jestin Roy</b><br>
      SoDa Labs, Monash Business School
    </span>
  </div>
</div>
""", unsafe_allow_html=True)

    tab1,tab2,tab3,tab4 = st.tabs(["Search","Single episode","Batch analysis","Time series"])
    with tab1: tab_search(cfg)
    with tab2: tab_single(cfg)
    with tab3: tab_batch(cfg)
    with tab4: tab_timeseries()


if __name__ == "__main__":
    main()
