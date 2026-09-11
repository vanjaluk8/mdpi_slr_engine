"""
screen.py — keyword + LLM screener for snowballed candidates.

Layer 1 — Hard exclusions: year < YEAR_CUTOFF (default 2017), or citation_count < MIN_CITATIONS (default 0 = disabled)
Layer 2 — Keyword scoring on title against four term sets (PEFT / LLM / systems / distributed):
            ≥2 sets matched                          → INCLUDE
            1 set matched (peft / systems / distributed) → REVIEW
            llm-only match                           → EXCLUDE (too broad)
            0 sets matched                           → EXCLUDE
Layer 3 — LLM triage (optional, --llm flag): sends each REVIEW row's title + venue
            to Claude and resolves it to INCLUDE, EXCLUDE, or UNCERTAIN.
            UNCERTAIN rows remain in the review queue for manual inspection.

Outputs (written to data/snowball_output/02_screening/):
  02_screened_<date>.csv     — full list with inclusion decisions
  03_review_queue_<date>.csv — REVIEW / UNCERTAIN rows for manual inspection
  04_included_<date>.csv     — papers that passed screening (your reading list)
  log_screening_<date>.json  — audit record (for PRISMA)

Usage (via the run.py dispatcher):
    python run.py screen                   # reads the latest 01_raw_* automatically
    python run.py screen --min-citations 3
    python run.py screen --llm
    python run.py screen --llm --model claude-haiku-4-5-20251001
Direct (lower-level):
    python -m slr_engine.screen --input data/snowball_output/01_retrieval/01_raw_YYYY-MM-DD.csv
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# ── Term sets ─────────────────────────────────────────────────────────────────

PEFT_TERMS = [
    "adapter", "adapters", "adapter module", "adapter modules",
    "lora", "low-rank adaptation", "low rank adaptation",
    "parameter-efficient fine-tuning", "parameter efficient fine tuning",
    "parameter-efficient tuning", "parameter efficient tuning",
    "prompt tuning", "prefix tuning", "peft",
    "ia3", "(IA)^3",
    "soft prompt", "soft prompts",
    "bitfit", "adapter-tuning", "adapter tuning",
]

LLM_TERMS = [
    "large language model", "large language models", "llm", "llms",
    "transformer", "transformers",
    "language model", "language models",
    "foundation model", "foundation models",
    "pre-trained model", "pre-trained language",
    "bert", "gpt", "t5", "roberta", "llama", "mistral", "falcon",
]

SYSTEMS_TERMS = [
    "serving system", "model serving", "inference server", "inference service",
    "inference system", "inference pipeline", "online serving",
    "multi-tenant", "multi tenant",
    "edge device", "edge devices", "edge computing", "edge inference",
    "adapter caching", "kv cache", "kv-cache",
    "scheduling", "multi-task inference", "task routing",
    "task-specific", "mixture of experts", "mixture-of-experts", "moe",
]

DISTRIBUTED_TERMS = [
    "peer-to-peer", "peer to peer", "p2p",
    "decentralized", "decentralised",
    "distributed inference", "distributed serving", "distributed learning",
    "collaborative inference", "collaborative serving", "collaborative learning",
    "federated learning", "federated fine-tuning", "federated adapter",
    "gossip", "overlay network", "distributed system",
]

ALL_TERM_SETS = [
    ("peft",        PEFT_TERMS),
    ("llm",         LLM_TERMS),
    ("systems",     SYSTEMS_TERMS),
    ("distributed", DISTRIBUTED_TERMS),
]

TERM_SET_VERSION = "1.0"  # bump when you change any term list


# ── Scoring helpers ───────────────────────────────────────────────────────────

def matched_sets(title: str) -> list[str]:
    """Return names of term sets that match anywhere in the title (case-insensitive)."""
    t = title.lower()
    return [name for name, terms in ALL_TERM_SETS if any(term.lower() in t for term in terms)]


def layer1_exclude(row: dict, year_cutoff: int, min_citations: int = 0) -> str | None:
    """Return an exclusion reason if the row fails hard filters, else None."""
    year_str = row.get("year", "").strip()
    if year_str:
        try:
            if int(year_str) < year_cutoff:
                return f"Year {year_str} < {year_cutoff}"
        except ValueError:
            pass
    if min_citations > 0:
        try:
            count = int(row.get("citation_count", 0) or 0)
            if count < min_citations:
                return f"citation_count {count} < {min_citations}"
        except (ValueError, TypeError):
            pass
    return None


def layer2_decision(title: str) -> tuple[str, str, str]:
    """
    Returns (inclusion, matched_sets_str, exclusion_reason).
    inclusion ∈ {"INCLUDE", "REVIEW", "EXCLUDE"}
    """
    hits = matched_sets(title)
    matched_str = "|".join(hits) if hits else ""

    if len(hits) >= 2:
        return "INCLUDE", matched_str, ""
    if len(hits) == 1:
        if hits[0] == "llm":
            return "EXCLUDE", matched_str, "LLM-only match (too broad)"
        return "REVIEW", matched_str, ""
    return "EXCLUDE", matched_str, "No keyword match in title"


# ── LLM screening (Layer 3) ───────────────────────────────────────────────────

LLM_SYSTEM_PROMPT = """\
You are a research assistant screening papers for a systematic literature review.

The review topic is: **Decentralized adapter-based LLM systems** — specifically
peer-to-peer multi-task NLP inference using PEFT adapters (LoRA, bottleneck adapters,
prefix tuning, etc.) over a shared frozen transformer backbone. The three core themes
are: (1) parameter-efficient fine-tuning / adapters, (2) distributed / P2P / federated
systems, (3) large language model serving and inference.

You will be given a paper title and venue. Based only on that information, decide
whether the paper is relevant to this review.

Reply with a JSON object and nothing else:
{"decision": "INCLUDE" | "EXCLUDE" | "UNCERTAIN", "reason": "<one sentence>"}

Rules:
- INCLUDE  — clearly relevant to at least one of the three themes and likely useful
- EXCLUDE  — clearly off-topic (unrelated domain, pure CV/audio/RL with no NLP angle, etc.)
- UNCERTAIN — genuinely ambiguous from title alone; needs abstract or full text
"""

def _llm_classify_batch(
    rows: list[dict],
    api_key: str,
    model: str,
    base_url: str | None = None,
    delay: float = 0.3,
) -> list[dict]:
    """
    Send each row's title + venue to Claude and return the rows with updated
    inclusion / exclusion_reason fields.
    Rows already resolved (INCLUDE/EXCLUDE) are passed through untouched.
    """
    try:
        import anthropic
    except ImportError:
        print("ERROR: anthropic package not installed. Run: pip install anthropic", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key, base_url=base_url)
    total = len(rows)

    for i, row in enumerate(rows, 1):
        if row.get("inclusion") in ("INCLUDE", "EXCLUDE"):
            continue  # already resolved by keyword layer

        title = row.get("title", "").strip()
        venue = row.get("venue", "").strip()
        user_msg = f'Title: "{title}"\nVenue: "{venue}"' if venue else f'Title: "{title}"'

        try:
            response = client.messages.create(
                model=model,
                max_tokens=80,
                system=LLM_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
            raw = response.content[0].text.strip()
            # strip markdown code fences if model wraps in ```json ... ```
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            parsed = json.loads(raw)
            decision = parsed.get("decision", "UNCERTAIN").upper()
            reason   = parsed.get("reason", "")
            if decision not in ("INCLUDE", "EXCLUDE", "UNCERTAIN"):
                decision = "UNCERTAIN"
                reason = f"Unexpected model response: {raw[:120]}"
        except Exception as exc:
            decision = "UNCERTAIN"
            reason = f"LLM error: {exc}"

        row["inclusion"] = decision
        row["exclusion_reason"] = reason if decision != "INCLUDE" else ""
        row["matched_sets"] = row.get("matched_sets", "") + "|llm" if row.get("matched_sets") else "llm"

        if i % 50 == 0 or i == total:
            print(f"  LLM screening: {i}/{total} done  "
                  f"(INCLUDE {sum(1 for r in rows if r.get('inclusion')=='INCLUDE')}, "
                  f"EXCLUDE {sum(1 for r in rows if r.get('inclusion')=='EXCLUDE')}, "
                  f"UNCERTAIN {sum(1 for r in rows if r.get('inclusion')=='UNCERTAIN')})",
                  flush=True)

        time.sleep(delay)

    return rows


# ── File fingerprint ──────────────────────────────────────────────────────────

def _sha256_hex(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            data = f.read(chunk)
            if not data:
                break
            h.update(data)
    return h.hexdigest()


# ── Core screening function ───────────────────────────────────────────────────

def screen(
    input_path: Path,
    year_cutoff: int = 2021,
    min_citations: int = 0,
    llm: bool = False,
    llm_model: str | None = None,
) -> Path:
    """
    Screen candidates CSV and write outputs + audit log.
    Returns path to the full screened output CSV.
    The input file is NEVER modified.

    If llm=True, rows left as REVIEW after keyword scoring are sent to Claude
    for classification. Rows Claude cannot resolve become UNCERTAIN and stay
    in the review queue for manual inspection.
    """
    rows: list[dict] = []
    with input_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    input_sha256 = _sha256_hex(input_path)

    # Ensure output columns exist
    for col in ("inclusion", "exclusion_reason", "matched_sets"):
        if col not in fieldnames:
            fieldnames.append(col)

    # Per-exclusion-reason counter for the audit log
    exclusion_reasons: Counter[str] = Counter()
    decisions: Counter[str] = Counter()
    per_group: dict[str, Counter] = {}
    per_direction: dict[str, Counter] = {}

    for row in rows:
        group = row.get("seed_group", "unknown")
        direction = row.get("direction", "unknown")
        per_group.setdefault(group, Counter())
        per_direction.setdefault(direction, Counter())

        # Skip rows that already have a decision (idempotent re-runs)
        if row.get("inclusion", "").strip() in ("INCLUDE", "EXCLUDE", "REVIEW"):
            decisions["ALREADY_SET"] += 1
            continue

        # Layer 1 — hard exclusions
        hard_reason = layer1_exclude(row, year_cutoff, min_citations)
        if hard_reason:
            row["inclusion"] = "EXCLUDE"
            row["exclusion_reason"] = hard_reason
            row["matched_sets"] = ""
            decisions["EXCLUDE"] += 1
            exclusion_reasons[hard_reason.split(" < ")[0]] += 1  # group by type
            per_group[group]["EXCLUDE"] += 1
            per_direction[direction]["EXCLUDE"] += 1
            continue

        # Layer 2 — keyword scoring
        title = row.get("title", "").strip()
        decision, matched, reason = layer2_decision(title)
        row["inclusion"] = decision
        row["exclusion_reason"] = reason
        row["matched_sets"] = matched
        decisions[decision] += 1
        if reason:
            exclusion_reasons[reason] += 1
        per_group[group][decision] += 1
        per_direction[direction][decision] += 1

    # ── Layer 3: LLM triage of REVIEW rows ────────────────────────────────────
    llm_stats: dict = {}
    if llm:
        from slr_engine.config import ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL, LLM_SCREEN_MODEL
        api_key = ANTHROPIC_API_KEY
        if llm_model is None:
            llm_model = LLM_SCREEN_MODEL
        if not api_key:
            print("ERROR: ANTHROPIC_API_KEY not set. Add it to app/.env or export it.", file=sys.stderr)
            sys.exit(1)

        review_rows_before = [r for r in rows if r.get("inclusion") == "REVIEW"]
        n_review = len(review_rows_before)
        print(f"\nLayer 3 — LLM triage: {n_review} REVIEW rows → {llm_model}")

        _llm_classify_batch(review_rows_before, api_key=api_key, model=llm_model, base_url=ANTHROPIC_BASE_URL)

        # Recount decisions after LLM pass
        llm_include  = sum(1 for r in review_rows_before if r.get("inclusion") == "INCLUDE")
        llm_exclude  = sum(1 for r in review_rows_before if r.get("inclusion") == "EXCLUDE")
        llm_uncertain = sum(1 for r in review_rows_before if r.get("inclusion") == "UNCERTAIN")
        decisions["INCLUDE"] += llm_include
        decisions["EXCLUDE"] += llm_exclude
        decisions["REVIEW"]  -= (llm_include + llm_exclude)  # resolved rows removed from REVIEW
        llm_stats = {
            "model": llm_model,
            "rows_sent": n_review,
            "resolved_include": llm_include,
            "resolved_exclude": llm_exclude,
            "uncertain_remaining": llm_uncertain,
        }
        print(f"  → INCLUDE {llm_include}  EXCLUDE {llm_exclude}  UNCERTAIN {llm_uncertain}")

    # ── Derive date suffix from input filename (e.g. 01_raw_2026-04-03 → 2026-04-03) ──
    # Works for any 01_raw_<date> filename; falls back to today if pattern not found.
    import re as _re
    _date_match = _re.search(r"\d{4}-\d{2}-\d{2}", input_path.stem)
    _date_suffix = _date_match.group(0) if _date_match else datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # ── Write screened CSV (input is NOT touched) ──────────────────────────────
    # Screening outputs live in the 02_screening/ stage folder regardless of where
    # the raw input was read from.
    from slr_engine.config import SCREENING_DIR
    out_path = SCREENING_DIR / f"02_screened_{_date_suffix}.csv"
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    # ── Write INCLUDE list ─────────────────────────────────────────────────────
    include_path = SCREENING_DIR / f"04_included_{_date_suffix}.csv"
    include_rows = [r for r in rows if r.get("inclusion") == "INCLUDE"]
    with include_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(include_rows)

    # ── Write REVIEW / UNCERTAIN queue ────────────────────────────────────────
    review_path = SCREENING_DIR / f"03_review_queue_{_date_suffix}.csv"
    review_rows = [r for r in rows if r.get("inclusion") in ("REVIEW", "UNCERTAIN")]
    if review_rows:
        with review_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(review_rows)

    # ── Write screening audit log ──────────────────────────────────────────────
    log_path = SCREENING_DIR / f"log_screening_{_date_suffix}.json"

    audit = {
        "screening_run": {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "tool": "slr_engine.screen",
            "term_set_version": TERM_SET_VERSION,
            "year_cutoff": year_cutoff,
            "min_citations": min_citations,
        },
        "input": {
            "file": input_path.name,
            "sha256": input_sha256,
            "total_records": len(rows),
            "note": "Original file was NOT modified. Decisions written to screened_candidates file.",
        },
        "outputs": {
            "screened_csv": out_path.name,
            "included_csv": include_path.name,
            "review_queue_csv": review_path.name if review_rows else None,
            "audit_log": log_path.name,
        },
        "decisions": {
            "INCLUDE": decisions["INCLUDE"],
            "REVIEW":  decisions["REVIEW"],
            "EXCLUDE": decisions["EXCLUDE"],
            "already_set_skipped": decisions.get("ALREADY_SET", 0),
        },
        "exclusion_breakdown": dict(exclusion_reasons),
        "by_seed_group": {g: dict(c) for g, c in sorted(per_group.items())},
        "by_direction":  {d: dict(c) for d, c in sorted(per_direction.items())},
        "term_sets": {
            name: terms for name, terms in ALL_TERM_SETS
        },
        "screening_rules": [
            "Layer 1: Exclude if year < year_cutoff or citation_count < min_citations",
            "Layer 2: Score title against 4 term sets (peft, llm, systems, distributed)",
            "  >= 2 sets matched → INCLUDE",
            "  1 set matched (non-llm) → REVIEW",
            "  llm-only match → EXCLUDE (precision too low)",
            "  0 sets matched → EXCLUDE",
            "Layer 3 (optional): LLM triage of REVIEW rows via Claude API",
            "  INCLUDE / EXCLUDE resolved; UNCERTAIN rows remain for manual inspection",
        ],
        "llm_screening": llm_stats if llm_stats else "not run",
    }

    with log_path.open("w", encoding="utf-8") as f:
        json.dump(audit, f, indent=2)

    # ── Console summary ────────────────────────────────────────────────────────
    n_uncertain = sum(1 for r in rows if r.get("inclusion") == "UNCERTAIN")
    total_processed = decisions["INCLUDE"] + decisions["REVIEW"] + decisions["EXCLUDE"]
    print(f"\nScreening complete — {total_processed} rows processed")
    print(f"  INCLUDE      : {decisions['INCLUDE']:>5}  → {include_path.name}")
    if n_uncertain:
        print(f"  UNCERTAIN    : {n_uncertain:>5}  ┐")
    if review_rows:
        print(f"  REVIEW       : {len(review_rows) - n_uncertain:>5}  ┘ → {review_path.name}")
    else:
        print(f"  REVIEW queue : {decisions['REVIEW']:>5}  → {review_path.name}")
    print(f"  EXCLUDE      : {decisions['EXCLUDE']:>5}")
    if decisions.get("ALREADY_SET"):
        print(f"  Already set  : {decisions['ALREADY_SET']:>5}  (skipped, not reprocessed)")
    print(f"\nAudit log   : {log_path.name}")
    print(f"Full output : {out_path.name}")

    return out_path


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    from slr_engine.config import CANDIDATES_FILE, RETRIEVAL_DIR

    parser = argparse.ArgumentParser(description="Keyword + LLM screener for snowballed candidates")
    parser.add_argument(
        "--input", "-i",
        type=Path,
        default=None,
        help="Path to candidates CSV (default: today's candidates file)",
    )
    parser.add_argument(
        "--year-cutoff",
        type=int,
        default=2021,
        help="Exclude papers published before this year (default: 2021)",
    )
    parser.add_argument(
        "--min-citations",
        type=int,
        default=0,
        help="Exclude papers with fewer citations than this threshold (default: 0 = disabled)",
    )
    parser.add_argument(
        "--llm",
        action="store_true",
        help="Run LLM triage (Layer 3) on REVIEW rows via Claude API",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Claude model for LLM triage (default: LLM_SCREEN_MODEL from .env)",
    )
    args = parser.parse_args()

    input_path = args.input or CANDIDATES_FILE
    if not input_path.exists():
        files = sorted(RETRIEVAL_DIR.glob("01_raw_*.csv"), reverse=True)
        if not files:
            print(f"No raw candidates file (01_raw_*.csv) found in {RETRIEVAL_DIR}", file=sys.stderr)
            sys.exit(1)
        input_path = files[0]
        print(f"Using most recent candidates file: {input_path.name}")

    screen(input_path, year_cutoff=args.year_cutoff, min_citations=args.min_citations, llm=args.llm, llm_model=args.model)


if __name__ == "__main__":
    main()
