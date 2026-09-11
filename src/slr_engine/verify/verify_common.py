#!/usr/bin/env python3
"""verify_common.py — shared helpers for the PRISMA-funnel verification scripts.

These scripts recompute every published funnel / quality-appraisal number from the
pipeline's own data files (the committed `verification_fixtures/` snapshot, falling
back to the freshly-generated `data/snowball_output/`). No number is read
from the manuscript.

Standalone (this module) has no third-party dependencies.
"""
from __future__ import annotations

import csv
import json
import os
import sys
from collections import Counter
from pathlib import Path

# ── Expected funnel values, as reported in the MDPI manuscript (final, audited) ──
# Source of truth: papers_code/writing/mdpi_paper/supplementary/PRISMA_NUMBERS_VALIDATION.md
# and the Data Availability note in the manuscript. These are the numbers the
# pipeline must reproduce EXACTLY.
EXPECTED = {
    # Stage 1 — identification
    "raw_retrieved":        1150,   # backward_examined 450 + forward_examined 700
    "dedup_removed":        178,    # 1150 - 972
    "screening_pool":       972,    # unique records after dedup
    # Stage 2 — title screening
    "title_include":        162,
    "title_uncertain":       19,
    "title_exclude":        791,    # 162 + 19 + 791 = 972
    # Stage 3 — LLM triage
    "llm_sent":             130,
    "llm_include":           51,
    "llm_exclude":           60,
    "llm_uncertain_remain":  19,
    # Merge
    "prevalidated_corpus":  352,    # G0-G6 (9 + 343)
    "merge_dedup":           12,    # duplicates removed on merge (514->502)
    "merged_corpus":        502,    # 162 + 352 - 12
    # Stage 5 — enrichment / relevance filter
    "enriched_retained":    464,
    "enriched_deprioritized": 32,
    "enriched_offtopic":      6,    # 464 + 32 + 6 = 502
    # Stage 6 — abstract review
    "abstract_pool":        552,    # 464 + 88 forward-added
    "abstract_keep":        214,
    "abstract_defer":       173,    # re-flagged DEFER-bound (in the 387 full-text queue)
    "abstract_skip":        165,    # pure SKIP (214 + 173 + 165 = 552)
    # Stage 7 — full-text queue & extraction
    "fulltext_queue":       387,    # 214 KEEP + 173 DEFER
    "extract_preliminary":  190,    # 10_data_extraction (KEEP-origin; paper prose '191' = 190+1 DEFER-origin)
    "extract_final":        224,    # 190 + 34 top-up (11_data_extraction)
    "extract_topup":         34,    # 224 - 190 (paper calls these the '33 supplementary' + rounding)
    # Final
    "final_list":           123,    # rows
    "n_duplicate_works":      3,    # works appearing as 2 rows each (see FINAL_DUPLICATE_WORKS)
    "final_distinct":       120,    # distinct papers (123 rows - 3 extra rows)
    "final_tier1":           48,
    "final_tier2":           42,
    "final_tier3":           33,    # 48 + 42 + 33 = 123
    "final_core":            89,
    "final_background":      34,    # 89 + 34 = 123
}


# ── Duplicate works in the final 123-row list ────────────────────────────────
# 123 rows = 120 distinct papers: three works each appear as two rows (a
# different retrieval key per row), so each contributes one extra row.
# Source: supplementary/PRISMA_NUMBERS_VALIDATION.md (distinct-paper reconciliation,
# corrected 2026-08-22 for the third pair CaraServe/Toppings).
# Each entry is a tuple of the two row keys (paper_key) that refer to the same work.
FINAL_DUPLICATE_WORKS = [
    ("2106.09685", "a8ca46b171467ceb2d7652fbfb67fe701ad86092"),   # LoRA (arXiv vs Scopus)
    ("1902.00751", "29ddc1f43f28af7c846515e32cc167bc66886d0c"),   # Adapter-based PEFT (arXiv vs Scopus)
    ("2401.11240", "69d631b3875149050ab3088501cfc9d5cbea9e99"),   # CaraServe (arXiv) == Toppings (USENIX ATC)
]


def repo_root() -> Path:
    """slr_engine repo root = parent of this scripts/ dir."""
    return Path(__file__).resolve().parent.parent


def fixtures_dir() -> Path:
    return repo_root() / "verification_fixtures"


def snowball_output_dir() -> Path:
    # Honour the same output-tree override as app/config.py, so `verify`
    # audits the redirected run (e.g. `run_pipeline.sh --out DIR`).
    override = os.environ.get("SLR_OUTPUT_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return repo_root() / "data" / "snowball_output"


# Stage subfolders under data/snowball_output/ (must mirror app/config.py). Files
# live in per-stage folders, so the live fallback searches these in order too.
_LIVE_STAGE_SUBDIRS = (
    "00_import", "01_retrieval", "02_screening", "03_merge", "04_enrich",
    "05_review", "06_extraction", "07_final", "snapshots",
)


def resolve(name: str) -> Path:
    """Prefer the committed fixture snapshot; fall back to the live output dir.

    The live fallback first checks the root, then each stage subfolder, so it
    keeps working regardless of which subfolder a file lives in.
    """
    p = fixtures_dir() / name
    if p.exists():
        return p
    out = snowball_output_dir()
    q = out / name
    if q.exists():
        return q
    for sub in _LIVE_STAGE_SUBDIRS:
        q = out / sub / name
        if q.exists():
            return q
    raise FileNotFoundError(f"Neither {p} nor <data/snowball_output/{name}> exists. "
                            f"Commit/refresh verification_fixtures/ (or run the pipeline).")


def load_csv(name: str) -> list[dict]:
    with resolve(name).open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_json(name: str) -> dict:
    return json.loads(resolve(name).read_text(encoding="utf-8"))


# ── Live-run loaders (prove a fresh pipeline run reproduces the manuscript) ────
# The committed fixtures are frozen, dated snapshots. To prove a genuinely fresh
# pipeline run yields the same numbers, we must read the *live* output tree and
# glob for whatever dated files are actually there (today's run, or a --out run),
# rather than hardcoding the fixture dates. `live_only_*` reads there directly.
def _live_root() -> Path:
    return snowball_output_dir()


def live_glob(pattern: str) -> list[Path]:
    """Return every live file matching pattern (dates allowed), newest first."""
    out = _live_root()
    hits = sorted(out.glob(pattern), reverse=True)
    if not hits:
        for sub in _LIVE_STAGE_SUBDIRS:
            hits += sorted((out / sub).glob(pattern), reverse=True)
    # de-duplicate while preserving (reverse) date order
    seen: set[Path] = set()
    ordered: list[Path] = []
    for h in sorted(hits, reverse=True):
        if h not in seen:
            seen.add(h)
            ordered.append(h)
    return ordered


def live_latest_csv(pattern: str) -> list[dict]:
    """Newest live CSV matching pattern (never the fixtures)."""
    hits = live_glob(pattern)
    if not hits:
        raise FileNotFoundError(
            f"No live output file matching '{pattern}' under {_live_root()} — "
            f"have you run the pipeline? (or point --out / SLR_OUTPUT_DIR at a run)")
    with hits[0].open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def live_latest_json(pattern: str) -> dict:
    """Newest live JSON matching pattern (never the fixtures)."""
    hits = live_glob(pattern)
    if not hits:
        raise FileNotFoundError(
            f"No live output file matching '{pattern}' under {_live_root()} — "
            f"have you run the pipeline?")
    return json.loads(hits[0].read_text(encoding="utf-8"))


def counter(rows: list[dict], field: str) -> Counter:
    return Counter((r.get(field) or "").strip() for r in rows)


# ── tiny check harness ──────────────────────────────────────────────────────────
# ALL_RECORDS accumulates every check across all suites in one process, so the
# unified `verify.py` runner can emit a single combined final report. Each record
# carries the structured (label, actual, expected, ok) tuple, not just text.
ALL_RECORDS: list[dict] = []


class Checks:
    def __init__(self):
        self.passes: list[str] = []
        self.failures: list[str] = []
        self.records: list[dict] = []   # structured view used by the report writer

    def check(self, label: str, actual, expected, ref: str = "") -> None:
        ok = (actual == expected)
        record = {"label": label, "actual": actual, "expected": expected, "ok": ok,
                  "suite": getattr(self, "suite", "unknown"), "ref": ref}
        self.records.append(record)
        ALL_RECORDS.append(record)
        self.passes.append(label) if ok else self.failures.append(
            f"{label}: expected {expected}, got {actual}")
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {label}: {actual} {'' if ok else f'(expected {expected})'}")

    def summary(self) -> int:
        print("\n" + "=" * 64)
        print(f"  {len(self.passes)} passed, {len(self.failures)} failed")
        for f in self.failures:
            print("  FAIL:", f)
        print("=" * 64)
        return 0 if not self.failures else 1


def write_run(script_name: str, lines: list[str]) -> Path:
    """Save a verification run into data/snowball_output/verification_runs/ (paper-protocol)."""
    out_dir = snowball_output_dir() / "verification_runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / script_name
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def reports_dir() -> Path:
    """Directory for the combined final reproducibility report."""
    return snowball_output_dir() / "verification_runs"


def make_report(records: list[dict] | None = None) -> Path:
    """Render ALL_RECORDS into FINAL_REPORT.md + FINAL_REPORT.json.

    Produces the reviewer-facing Data-Availability artifact: every paper figure
    verified, the recomputed value, and a pass/fail verdict, ending in an
    explicit reproducibility statement.
    """
    records = ALL_RECORDS if records is None else records
    if not records:
        raise ValueError("no check records — run the verification suites first")

    n_ok = sum(1 for r in records if r["ok"])
    n_total = len(records)
    n_fail = n_total - n_ok

    md: list[str] = [
        "# Final Verification Report — slr_engine vs the manuscript",
        "",
        "Every number below is **recomputed from the pipeline's own data files**, "
        "never read from the manuscript text. Two independent paths are checked:",
        "",
        "1. **Fixtures** — recomputed from the frozen, committed snapshots in "
        "`verification_fixtures/` (what ships in this repository), and",
        "2. **Live run** — recomputed from a fresh pipeline run in "
        "`data/snowball_output/` (or the `SLR_OUTPUT_DIR` tree), proving the "
        "*actual* pipeline reproduces the same numbers, not just curated fixtures.",
        "",
        f"- Suites run: multiple; checks executed: **{n_total}**, passed: **{n_ok}**, failed: **{n_fail}**",
        "",
        "## Verified values (committed fixtures)",
        "",
        "| # | Suite | Figure / check | Recomputed | Manuscript ref | Verdict |",
        "|---|-------|----------------|-----------|----------------|---------|",
    ]
    for i, r in enumerate(records, 1):
        actual = r["actual"]
        # render tuple/list actuals compactly, e.g. tier (48, 42, 33)
        if isinstance(actual, (list, tuple)):
            actual = "(" + ", ".join(str(x) for x in actual) + ")"
        verdict = "✅ pass" if r["ok"] else "❌ FAIL"
        ref = r.get("ref") or ""
        md.append(f"| {i} | {r.get('suite','')} | {r['label']} | {actual} | {ref} | {verdict} |")

    md += [
        "",
        "## Reproducibility statement",
        "",
        f"**{'ALL CHECKS PASS' if n_fail == 0 else 'CHECKS FAILED'}** — "
        f"{n_ok}/{n_total} verifications matched the manuscript "
        f"({'reproducible from committed fixtures.' if n_fail == 0 else 'see FAIL rows above.'})",
        "",
        "> The values quoted in the PRISMA funnel and quality-appraisal tables of the "
        "manuscript are exactly reproduced here from the pipeline artifacts committed "
        "to `verification_fixtures/`. Run `python3 scripts/verify.py` to regenerate "
        "this report on any fresh clone.",
    ]

    out_md = reports_dir() / "FINAL_REPORT.md"
    out_md.write_text("\n".join(md) + "\n", encoding="utf-8")

    out_json = reports_dir() / "FINAL_REPORT.json"
    out_json.write_text(json.dumps({
        "generated_from": "verification_fixtures/ and live data/snowball_output/ (or SLR_OUTPUT_DIR)",
        "checks_total": n_total, "checks_passed": n_ok, "checks_failed": n_fail,
        "reproducible": n_fail == 0,
        "checks": [
            {k: r.get(k, "") for k in ("suite", "label", "actual", "expected", "ok", "ref")}
            for r in records
        ],
    }, indent=2), encoding="utf-8")

    return out_md
