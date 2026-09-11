"""
abstract_review.py — interactive abstract screener + Zotero RIS exporter.

Usage
-----
    # Interactive review (reads 07_filtered_<date>.csv)
    python -m slr_engine.abstract_review

    # With LLM suggestions (Sonnet by default)
    python -m slr_engine.abstract_review --llm
    python -m slr_engine.abstract_review --llm --model claude-haiku-4-5-20251001

    # Specify input explicitly
    python -m slr_engine.abstract_review --input data/snowball_output/04_enrich/07_filtered_2026-04-10.csv --llm

    # Skip review; just re-export RIS from an already-reviewed file
    python -m slr_engine.abstract_review --input data/snowball_output/05_review/08_abstract_reviewed_2026-04-10.csv --export-ris

Keymap during review
--------------------
    k / Enter  — KEEP
    s          — SKIP
    d          — DEFER  (come back later)
    b          — go Back one paper (undo last decision)
    q          — quit and save progress

LLM suggestions
---------------
    When --llm is active, the AI suggestion (KEEP/SKIP/DEFER + one-line reason) is shown
    above the keymap. The next paper's suggestion is pre-fetched in the background while
    you read the current abstract, so there is usually no waiting. Suggestions are cached
    in .abstract_suggestions_<date>.json and reused on resume.

Output files (same directory as input)
---------------------------------------
    08_abstract_reviewed_<date>.csv   — original columns + abstract_decision (KEEP/SKIP/DEFER)
    08_zotero_ready_<date>.ris        — RIS file of KEEP papers, ready for Zotero import
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import textwrap
import threading
import tty
import termios
from concurrent.futures import ThreadPoolExecutor, Future
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── ANSI colours ─────────────────────────────────────────────────────────────

_GREEN  = "\033[32m"
_RED    = "\033[31m"
_YELLOW = "\033[33m"
_CYAN   = "\033[36m"
_DIM    = "\033[2m"
_BOLD   = "\033[1m"
_RESET  = "\033[0m"

_REC_COLOUR = {"KEEP": _GREEN, "SKIP": _RED, "DEFER": _YELLOW}


# ── Terminal helpers ──────────────────────────────────────────────────────────

def _getch() -> str:
    """Read a single keypress without requiring Enter."""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return ch


def _clear() -> None:
    os.system("clear")


def _wrap(text: str, width: int = 88, indent: str = "  ") -> str:
    if not text:
        return f"{indent}(no abstract)"
    return "\n".join(
        textwrap.fill(line, width=width, initial_indent=indent, subsequent_indent=indent)
        for line in text.splitlines()
    )


# ── Display ───────────────────────────────────────────────────────────────────

def _show_paper(
    row: dict,
    index: int,
    total: int,
    kept: int,
    skipped: int,
    deferred: int,
    suggestion: dict | None,
    suggestion_loading: bool,
) -> None:
    _clear()

    tier = row.get("tier", "")
    notes = row.get("notes", "")
    relevance_score = ""
    if notes:
        m = re.search(r"relevance_score=([\d.]+)", notes)
        if m:
            relevance_score = f"  rel={m.group(1)}"

    print(f"─── {index}/{total}  {_GREEN}kept={kept}{_RESET}  skipped={skipped}  deferred={deferred} ───")
    print()
    print(f"  {_BOLD}Title  :{_RESET} {row.get('title', '')}")
    print(f"  Authors: {_DIM}{row.get('authors', '')}{_RESET}")
    print(f"  Year   : {row.get('year', '')}   Venue: {row.get('venue', '')}   "
          f"Citations: {row.get('citation_count', '')}   Tier: {tier}{relevance_score}")
    print(f"  DOI    : {_DIM}{row.get('doi', '') or row.get('arxiv_id', '') or '–'}{_RESET}")
    group = row.get("seed_group", "") or row.get("direction", "")
    if group:
        print(f"  Group  : {group}")

    print()
    print(f"  {_BOLD}Abstract{_RESET}")
    print("  " + "─" * 70)
    print(_wrap(row.get("abstract", "")))
    print()

    keywords = row.get("keywords", "")
    if keywords:
        kw_short = keywords[:200] + ("…" if len(keywords) > 200 else "")
        print(f"  {_DIM}Keywords: {kw_short}{_RESET}")
        print()

    # ── AI suggestion panel ───────────────────────────────────────────────────
    if suggestion_loading:
        print(f"  {_DIM}╭─ AI suggestion ──────────────────────────────────────────────────────╮{_RESET}")
        print(f"  {_DIM}│  fetching…{_RESET}")
        print(f"  {_DIM}╰──────────────────────────────────────────────────────────────────────╯{_RESET}")
        print()
    elif suggestion:
        rec   = suggestion.get("recommendation", "DEFER")
        summ  = suggestion.get("summary", "")
        relev = suggestion.get("relevance", "")
        col   = _REC_COLOUR.get(rec, _YELLOW)
        model_tag = suggestion.get("_model", "")
        header = f"AI suggestion{f' · {model_tag}' if model_tag else ''}"

        print(f"  {_CYAN}╭─ {header} {'─' * max(0, 60 - len(header))}╮{_RESET}")
        print(f"  {_CYAN}│{_RESET}  Suggest  : {col}{_BOLD}{rec}{_RESET}")
        if summ:
            for line in textwrap.wrap(summ, width=66):
                print(f"  {_CYAN}│{_RESET}  Summary  : {line}")
        if relev:
            for line in textwrap.wrap(relev, width=66):
                print(f"  {_CYAN}│{_RESET}  Relevance: {line}")
        print(f"  {_CYAN}╰──────────────────────────────────────────────────────────────────────╯{_RESET}")
        print()

    # Highlight the AI-suggested key in the keymap
    rec = (suggestion or {}).get("recommendation", "")
    def _key(letter: str, label: str) -> str:
        if suggestion and rec == label:
            return f"{_BOLD}{_GREEN}[{letter}] {label}←{_RESET}"
        return f"[{letter}] {label}"

    print(f"  {_key('k', 'KEEP')}   {_key('s', 'SKIP')}   {_key('d', 'DEFER')}   [b] Back   [q] Quit")


# ── CSV I/O ───────────────────────────────────────────────────────────────────

def _load(path: Path) -> tuple[list[dict], list[str]]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    return rows, fieldnames


def _save(rows: list[dict], fieldnames: list[str], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# ── Suggestion cache ──────────────────────────────────────────────────────────

def _cache_key(row: dict) -> str:
    return row.get("doi") or row.get("arxiv_id") or row.get("ss_paper_id") or row.get("title", "")[:120]


def _load_cache(cache_path: Path) -> dict[str, Any]:
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_cache(cache: dict, cache_path: Path) -> None:
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


# ── LLM suggestion ────────────────────────────────────────────────────────────

_SUGGEST_SYSTEM = """\
You are a research assistant helping screen papers for a systematic literature review (SLR).

SLR topic: Decentralized adapter-based LLM systems — peer-to-peer multi-task NLP inference
using PEFT adapters (LoRA, bottleneck adapters, prefix/prompt tuning, etc.) over a shared
frozen transformer backbone.

Three core themes (relevant if it covers ≥1 substantially):
  1. PEFT / adapters — LoRA, adapter modules, prefix/prompt tuning, adapter composition,
     AdapterFusion, modular fine-tuning, parameter-efficient transfer learning
  2. Distributed / P2P / federated systems — collaborative inference, gossip protocols,
     federated learning with adapters, decentralised training, overlay networks
  3. LLM serving & multi-task inference — multi-tenant serving, task routing, mixture-of-experts,
     adapter caching/scheduling, edge inference, model serving systems

Respond with a JSON object and nothing else:
{
  "recommendation": "KEEP" | "SKIP" | "DEFER",
  "summary": "<one sentence: what this paper contributes>",
  "relevance": "<one sentence: why it is or is not relevant to the SLR>"
}

KEEP  — clearly covers ≥1 core theme and will likely be cited
SKIP  — off-topic (pure CV/audio/RL/unrelated NLP, no systems or adapter angle)
DEFER — genuinely ambiguous from abstract alone; flag for closer reading
"""


def _fetch_suggestion(row: dict, client: Any, model: str) -> dict:
    """Call the LLM and return a suggestion dict. Never raises — returns error dict on failure."""
    title    = row.get("title", "").strip()
    abstract = (row.get("abstract") or "").strip()
    venue    = row.get("venue", "").strip()

    user_msg = f'Title: "{title}"\nVenue: "{venue}"\n\nAbstract:\n{abstract}' if abstract \
               else f'Title: "{title}"\nVenue: "{venue}"'

    try:
        response = client.messages.create(
            model=model,
            max_tokens=200,
            system=_SUGGEST_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw)
        rec = parsed.get("recommendation", "DEFER").upper()
        if rec not in ("KEEP", "SKIP", "DEFER"):
            rec = "DEFER"
        return {
            "recommendation": rec,
            "summary":    parsed.get("summary", ""),
            "relevance":  parsed.get("relevance", ""),
            "_model":     model.split("-")[1] if "-" in model else model,  # e.g. "sonnet"
        }
    except Exception as exc:
        return {
            "recommendation": "DEFER",
            "summary":   "",
            "relevance": f"(suggestion error: {exc})",
            "_model":    model.split("-")[1] if "-" in model else model,
        }


def _get_or_fetch(row: dict, client: Any, model: str, cache: dict, cache_path: Path) -> dict:
    key = _cache_key(row)
    if key and key in cache:
        return cache[key]
    result = _fetch_suggestion(row, client, model)
    if key:
        cache[key] = result
        _save_cache(cache, cache_path)
    return result


# ── RIS export ────────────────────────────────────────────────────────────────

_RIS_TYPE_MAP = {
    "arxiv": "JOUR",
    "conference": "CONF",
    "proceedings": "CONF",
}


def _ris_type(row: dict) -> str:
    venue = (row.get("venue") or "").lower()
    for kw, ty in _RIS_TYPE_MAP.items():
        if kw in venue:
            return ty
    return "JOUR"


def export_ris(rows: list[dict], out_path: Path) -> int:
    """Write KEEP rows to a .ris file. Returns count of exported papers."""
    keep_rows = [r for r in rows if r.get("abstract_decision") == "KEEP"]
    if not keep_rows:
        print("No KEEP papers to export.")
        return 0

    lines: list[str] = []
    for row in keep_rows:
        ty = _ris_type(row)
        lines.append(f"TY  - {ty}")

        title = row.get("title", "").strip()
        if title:
            lines.append(f"TI  - {title}")

        authors_raw = row.get("authors", "").strip()
        if authors_raw:
            parts = [re.sub(r"^and\s+", "", a.strip(), flags=re.IGNORECASE)
                     for a in re.split(r",\s*|\;\s*", authors_raw) if a.strip()]
            for author in parts:
                lines.append(f"AU  - {author}")

        year = row.get("year", "").strip()
        if year:
            lines.append(f"PY  - {year}")

        venue = row.get("venue", "").strip()
        if venue:
            lines.append(f"{'BT' if ty == 'CONF' else 'JO'}  - {venue}")

        doi = row.get("doi", "").strip()
        if doi:
            lines.append(f"DO  - {doi}")

        arxiv = row.get("arxiv_id", "").strip()
        if arxiv and not doi:
            lines.append(f"UR  - https://arxiv.org/abs/{arxiv}")

        abstract = row.get("abstract", "").strip()
        if abstract:
            lines.append(f"AB  - {abstract}")

        keywords = row.get("keywords", "").strip()
        if keywords:
            for kw in re.split(r";|,", keywords):
                kw = kw.strip()
                if kw:
                    lines.append(f"KW  - {kw}")

        tier = row.get("tier", "").strip()
        notes_parts = []
        if tier:
            notes_parts.append(f"Tier {tier}")
        group = (row.get("seed_group") or row.get("direction", "")).strip()
        if group:
            notes_parts.append(f"Group: {group}")
        user_notes = row.get("notes", "").strip()
        if user_notes:
            notes_parts.append(user_notes)
        if notes_parts:
            lines.append(f"N1  - {' | '.join(notes_parts)}")

        lines.append("ER  - ")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return len(keep_rows)


# ── Interactive review loop ───────────────────────────────────────────────────

def review(input_path: Path, llm_client: Any = None, llm_model: str = "") -> Path:
    """
    Run interactive abstract review.
    Returns path to the output reviewed CSV.
    """
    rows, fieldnames = _load(input_path)

    if "abstract_decision" not in fieldnames:
        fieldnames.append("abstract_decision")
    for row in rows:
        row.setdefault("abstract_decision", "")

    m = re.search(r"\d{4}-\d{2}-\d{2}", input_path.stem)
    date_suffix = m.group(0) if m else datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Review outputs (08_*) always go to the 05_review/ stage folder.
    from slr_engine.config import REVIEW_DIR
    out_csv      = REVIEW_DIR / f"08_abstract_reviewed_{date_suffix}.csv"
    out_ris      = REVIEW_DIR / f"08_zotero_ready_{date_suffix}.ris"
    cache_path   = REVIEW_DIR / f".abstract_suggestions_{date_suffix}.json"

    # Resume: load decisions from a previous session
    if out_csv.exists():
        existing, _ = _load(out_csv)
        existing_map = {
            r.get("doi") or r.get("arxiv_id") or r.get("title"): r.get("abstract_decision", "")
            for r in existing
        }
        for row in rows:
            key = row.get("doi") or row.get("arxiv_id") or row.get("title")
            if not row["abstract_decision"] and existing_map.get(key):
                row["abstract_decision"] = existing_map[key]
        already_done = sum(1 for r in rows if r["abstract_decision"])
        if already_done:
            print(f"Resuming — {already_done}/{len(rows)} already reviewed.")

    # LLM setup
    cache: dict = _load_cache(cache_path) if llm_client else {}
    executor    = ThreadPoolExecutor(max_workers=1) if llm_client else None
    prefetch: dict[int, Future] = {}  # row_index → Future[suggestion dict]

    def _undecided_indices() -> list[int]:
        return [idx for idx, r in enumerate(rows) if not r["abstract_decision"]]

    def _start_prefetch(idx: int) -> None:
        """Submit a background fetch for rows[idx] if not already cached/submitted."""
        if not executor:
            return
        key = _cache_key(rows[idx])
        if key and key in cache:
            return  # already cached
        if idx not in prefetch or prefetch[idx].done():
            prefetch[idx] = executor.submit(
                _get_or_fetch, rows[idx], llm_client, llm_model, cache, cache_path
            )

    def _get_suggestion(idx: int) -> dict | None:
        """Return suggestion for rows[idx], waiting if still loading."""
        if not llm_client:
            return None
        key = _cache_key(rows[idx])
        if key and key in cache:
            return cache[key]
        if idx in prefetch:
            return prefetch[idx].result()  # wait for background fetch
        # Synchronous fallback (shouldn't normally happen)
        return _get_or_fetch(rows[idx], llm_client, llm_model, cache, cache_path)

    def _suggestion_loading(idx: int) -> bool:
        if not llm_client:
            return False
        key = _cache_key(rows[idx])
        if key and key in cache:
            return False
        return idx in prefetch and not prefetch[idx].done()

    total   = len(rows)
    history: list[int] = []

    # Kick off the first suggestion fetch immediately
    undecided = _undecided_indices()
    if llm_client and undecided:
        _start_prefetch(undecided[0])

    try:
        i = 0
        while i < total:
            row = rows[i]

            if row["abstract_decision"] and i not in history:
                i += 1
                continue

            kept      = sum(1 for r in rows if r["abstract_decision"] == "KEEP")
            skipped   = sum(1 for r in rows if r["abstract_decision"] == "SKIP")
            deferred  = sum(1 for r in rows if r["abstract_decision"] == "DEFER")
            remaining = sum(1 for r in rows if not r["abstract_decision"])

            loading    = _suggestion_loading(i)
            suggestion = None if loading else _get_suggestion(i)

            _show_paper(row, total - remaining + 1, total, kept, skipped, deferred,
                        suggestion, loading)

            # If still loading, re-render once it's done (so user sees suggestion before deciding)
            if loading:
                suggestion = _get_suggestion(i)  # waits for background thread
                _show_paper(row, total - remaining + 1, total, kept, skipped, deferred,
                            suggestion, False)

            ch = _getch()

            if ch in ("k", "\r", "\n", ""):
                row["abstract_decision"] = "KEEP"
                history.append(i)
                _save(rows, fieldnames, out_csv)
                i += 1
            elif ch == "s":
                row["abstract_decision"] = "SKIP"
                history.append(i)
                _save(rows, fieldnames, out_csv)
                i += 1
            elif ch == "d":
                row["abstract_decision"] = "DEFER"
                history.append(i)
                _save(rows, fieldnames, out_csv)
                i += 1
            elif ch == "b":
                if history:
                    prev = history.pop()
                    rows[prev]["abstract_decision"] = ""
                    _save(rows, fieldnames, out_csv)
                    i = prev
            elif ch in ("q", "\x03"):
                _clear()
                print(f"Saved progress → {out_csv.name}")
                break

            # Pre-fetch suggestion for the next undecided paper
            if llm_client:
                remaining_idx = [idx for idx in range(i, total) if not rows[idx]["abstract_decision"]]
                if remaining_idx:
                    _start_prefetch(remaining_idx[0])

    except KeyboardInterrupt:
        pass
    finally:
        _save(rows, fieldnames, out_csv)
        if executor:
            executor.shutdown(wait=False)

    # Summary
    kept      = sum(1 for r in rows if r["abstract_decision"] == "KEEP")
    skipped   = sum(1 for r in rows if r["abstract_decision"] == "SKIP")
    deferred  = sum(1 for r in rows if r["abstract_decision"] == "DEFER")
    undecided = sum(1 for r in rows if not r["abstract_decision"])

    _clear()
    print("Review session complete")
    print(f"  KEEP      : {kept}")
    print(f"  SKIP      : {skipped}")
    print(f"  DEFER     : {deferred}")
    print(f"  Undecided : {undecided}")
    print(f"  Reviewed  : {out_csv.name}")

    if kept:
        n = export_ris(rows, out_ris)
        print(f"  Zotero    : {out_ris.name}  ({n} papers)")
        print()
        print(f"  Import into Zotero: File → Import → {out_ris.name}")

    return out_csv


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    from slr_engine.config import ENRICH_DIR, REVIEW_DIR, ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL

    parser = argparse.ArgumentParser(description="Interactive abstract screener + Zotero RIS exporter")
    parser.add_argument("--input", "-i", type=Path, default=None,
                        help="Path to filtered CSV (default: latest 07_filtered_*.csv)")
    parser.add_argument("--llm", action="store_true",
                        help="Show AI suggestions for each abstract (requires ANTHROPIC_API_KEY)")
    parser.add_argument("--model", default="claude-sonnet-4-6",
                        help="Claude model for suggestions (default: claude-sonnet-4-6)")
    parser.add_argument("--export-ris", action="store_true",
                        help="Skip review; re-export RIS from existing decisions")
    args = parser.parse_args()

    if args.input:
        input_path = args.input
    else:
        candidates = sorted(ENRICH_DIR.glob("07_filtered_*.csv"), reverse=True)
        if not candidates:
            candidates = sorted(REVIEW_DIR.glob("08_abstract_reviewed_*.csv"), reverse=True)
        if not candidates:
            print(f"No 07_filtered_*.csv found in {ENRICH_DIR}", file=sys.stderr)
            sys.exit(1)
        input_path = candidates[0]
        print(f"Using: {input_path.name}")

    if not input_path.exists():
        print(f"File not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    if args.export_ris:
        rows, _ = _load(input_path)
        m = re.search(r"\d{4}-\d{2}-\d{2}", input_path.stem)
        date_suffix = m.group(0) if m else datetime.now(timezone.utc).strftime("%Y-%m-%d")
        out_ris = input_path.parent / f"08_zotero_ready_{date_suffix}.ris"
        n = export_ris(rows, out_ris)
        print(f"Exported {n} KEEP papers → {out_ris}")
        return

    llm_client = None
    if args.llm:
        try:
            import anthropic
        except ImportError:
            print("ERROR: anthropic package not installed. Run: pip install anthropic", file=sys.stderr)
            sys.exit(1)
        if not ANTHROPIC_API_KEY:
            print("ERROR: ANTHROPIC_API_KEY not set. Add it to .env or export it.", file=sys.stderr)
            sys.exit(1)
        llm_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, base_url=ANTHROPIC_BASE_URL)
        print(f"AI suggestions enabled  ({args.model})")

    review(input_path, llm_client=llm_client, llm_model=args.model)


if __name__ == "__main__":
    main()
