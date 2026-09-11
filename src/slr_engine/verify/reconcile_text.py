#!/usr/bin/env python3
"""reconcile_text.py — validate the PRISMA numbers written in the manuscript prose.

The manuscript (.tex) states the PRISMA funnel and final-list numbers as
hardcoded prose (e.g. `\\textbf{972 unique records}`, `352-paper`,
`\\textbf{502}-record merged pool`, `1,150 raw records`). Those can silently
drift from the pipeline data. This tool:

  * reads one or more .tex files,
  * for EACH canonical funnel / final-list / route fact, finds the number in the
    prose near its identifying context phrase — so it disambiguates numbers that
    recur with different meanings (e.g. final tiers 48/42/33 vs the enrichment
    pool tiers 90/141/233, or abstract SKIP 165 vs the appendix's 338),
  * compares it to the audited value in verify_common.EXPECTED (plus the
    audited 70/53 route split), and
  * prints a per-fact table: IN TEXT vs CANONICAL -> OK / DRIFT / not located.

It exits non-zero if ANY located value has drifted. Facts whose canonical value
is simply NOT stated in the prose are reported as "not located" (informational,
not a failure) so you can see at a glance what the text omits.

Usage:
    python3 src/slr_engine/verify/reconcile_text.py              # auto-detect manuscript
    python3 src/slr_engine/verify/reconcile_text.py --tex ../mdpi_paper_slr/sections/03_methodology.tex
    python3 src/slr_engine/verify/reconcile_text.py --dir ../mdpi_paper_slr/sections
    python3 src/slr_engine/verify/reconcile_text.py --all                    # also list every integer in
                                                                             #   the text that matches no
                                                                             #   canonical fact

The `--all` pass additionally lists every integer found in the text that is NOT
matched by any canonical fact (years, percentages, counts of other things, and
explained alternative aggregations such as the appendix's 338 SKIP). Use it to
spot anything that might be a drift masquerading as an unrelated quantity.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

# Reuse the audited funnel expectations + the repo-root resolver.
from slr_engine.verify.verify_common import EXPECTED, repo_root

# ── Canonical facts: (key, label, canon, anchor-regex with group(1)=the number) ──
# Text is normalised first (`\textbf{972}` -> `972`), so anchors match plain prose.
# Narrow context phrases separate numbers that recur with different meanings.
_NUM = r"(\d[\d,]*)"
_FACTS = [
    # ── Funnel ────────────────────────────────────────────────────────────────────
    ("raw_retrieved",      "Raw snowball candidates",     EXPECTED["raw_retrieved"],
     _NUM + r"\s+raw\s+(?:records|candidates)"),
    ("screening_pool",     "Screening pool",              EXPECTED["screening_pool"],
     _NUM + r"\s+unique\s+records"),
    ("dedup_removed",      "Duplicates removed",          EXPECTED["dedup_removed"],
     _NUM + r"\s+duplicates"),
    ("title_include",      "Title INCLUDE",               EXPECTED["title_include"],
     _NUM + r"\s+(?:passed|screened-in)"),
    ("title_uncertain",    "Title UNCERTAIN",             EXPECTED["title_uncertain"],
     _NUM + r"\s+UNCERTAIN\s+(?:records?|title)"),
    ("prevalidated_corpus","Pre-validated corpus",        EXPECTED["prevalidated_corpus"],
     _NUM + r"-paper\s+pre-validated"),
    ("merged_corpus",      "Merged corpus",               EXPECTED["merged_corpus"],
     _NUM + r"-record\s+merged\s+pool"),
    ("enriched_retained",  "Enriched retained",           EXPECTED["enriched_retained"],
     r"retained\s+" + _NUM + r"\s+records"),
    ("abstract_pool",      "Abstract-review pool",        EXPECTED["abstract_pool"],
     r"abstract-review\s+pool[^\d]{0,12}?" + _NUM),
    ("abstract_keep",      "Abstract KEEP",               EXPECTED["abstract_keep"],
     _NUM + r"\s+were\s+classified\s+KEEP"),
    # `were classified SKIP` appears TWICE: 173 (but re-flagged DEFER) and 165
    # (final). Target the final 165 via "the remaining"; the 173 is abstract_defer.
    ("abstract_skip",      "Abstract SKIP (final)",       EXPECTED["abstract_skip"],
     r"the\s+remaining\s+" + _NUM + r"\s+were\s+classified\s+SKIP"),
    ("abstract_defer",     "Abstract DEFER (re-flagged)", EXPECTED["abstract_defer"],
     _NUM + r"\s+were\s+classified\s+SKIP\s+but\s+re-flagged\s+DEFER"),
    ("fulltext_queue",     "Full-text review queue",      EXPECTED["fulltext_queue"],
     _NUM + r"\s+records\s*\(\s*214\s+KEEP"),
    ("extract_prelim",     "Extraction preliminary",      EXPECTED["extract_preliminary"],
     _NUM + r"-record\s+extraction\s+subset"),
    ("extract_final",      "Data extraction (final)",     EXPECTED["extract_final"],
     r"total\s+is\s+therefore\s+" + _NUM),
    ("final_list",         "Final reading list (rows)",   EXPECTED["final_list"],
     _NUM + r"-paper\s+final\s+reading\s+list"),
    ("final_distinct",     "Distinct papers",             EXPECTED["final_distinct"],
     _NUM + r"\s+distinct\s+papers"),
    # ── Final tiers (final-stage; distinct from the 464-pool tiers 90/141/233) ────
    ("final_tier1",        "Final tier 1",                EXPECTED["final_tier1"],
     _NUM + r"\s*Tier-1"),
    ("final_tier2",        "Final tier 2",                EXPECTED["final_tier2"],
     _NUM + r"\s*Tier-2"),
    ("final_tier3",        "Final tier 3",                EXPECTED["final_tier3"],
     _NUM + r"\s*Tier-3"),
    # ── Never stated in prose — kept so the report shows they are ABSENT, not wrong ─
    ("final_core",         "Final corpus core",           EXPECTED["final_core"],
     r"(?!)"),   # 89 (flag as "not located": the manuscript does not state it)
    ("final_background",   "Final corpus background",     EXPECTED["final_background"],
     r"(?!)"),   # 34 (flag as "not located")
    # ── LLM / screening internals (appendix) ─────────────────────────────────────
    ("llm_sent",           "LLM triage queue",            EXPECTED["llm_sent"],
     _NUM + r"-record\s+triage\s+queue"),
    ("llm_include",        "LLM promoted to INCLUDE",     EXPECTED["llm_include"],
     r"triage\s+queue\s+into\s+" + _NUM + r"\s+inclusion"),
    # ── Entry route (audited editorial split) ────────────────────────────────────
    ("route_fulltext",     "Route full-text",             70,
     _NUM + r"\s*\(\s*56"),
    ("route_abstract",     "Route abstract-2nd-pass",     53,
     _NUM + r"\s+of\s+the\s+123\s+final"),
]


def _norm_tex(text: str) -> str:
    """Normalise LaTeX number wrappers so `\\textbf{972}` reads as `972`."""
    # Unwrap single-argument commands (e.g. \textbf{972} -> 972). The inner braces
    # have no nested braces in these wrappers, so a non-greedy [^{}]* is safe.
    return re.sub(r"\\[a-zA-Z]+\{([^{}]*)\}", r"\1", text)


def _to_int(s: str | None) -> int | None:
    if s is None:
        return None
    s = s.strip().replace(",", "")
    return int(s) if s.isdigit() else None


def reconcile(paths: list[Path], show_all: bool) -> int:
    text = ""
    for p in paths:
        text += p.read_text(encoding="utf-8", errors="replace") + "\n"
    norm = _norm_tex(text)

    rows = []  # (label, canon, found, ok)
    failures: list[str] = []
    not_located: list[str] = []

    for _key, label, canon, anchor in _FACTS:
        m = re.search(anchor, norm)
        found = _to_int(m.group(1)) if (m and m.lastindex) else None
        if found is None:
            rows.append((label, canon, None, False))
            not_located.append(label)
        elif found == canon:
            rows.append((label, canon, found, True))
        else:
            rows.append((label, canon, found, False))
            failures.append(f"{label}: prose says {found}, canonical {canon}")

    # ── Print the table ──────────────────────────────────────────────────────────
    print("=" * 78)
    print("  RECONCILE - PRISMA numbers in manuscript prose vs. pipeline data")
    print("  Sources: " + ", ".join(str(p.name) for p in paths))
    print("=" * 78)
    print(f"{'Fact':<38}{'Canonical':>10}{'In text':>10}  Verdict")
    print("-" * 78)
    for label, canon, found, ok in rows:
        verdict = "OK" if found == canon else ("not located" if found is None else "DRIFT")
        fs = str(found) if found is not None else "-"
        print(f"{label:<38}{canon:>10}{fs:>10}  {verdict}")
    print("-" * 78)

    # Facts that are simply absent from the prose (not a drift, but worth seeing).
    if not_located:
        print("  not located (canonical value not stated anywhere in this prose):")
        print("    " + ", ".join(not_located))

    # ── --all: integers in the text that match no canonical value ────────────────
    if show_all:
        canon_vals = set(int(v) for v in EXPECTED.values()) | {70, 53}
        nums = Counter()
        for raw in re.findall(r"(?<![\w-])\d[\d,]{1,}(?![\w-])", norm):
            v = _to_int(raw)
            if v and v not in canon_vals:
                nums[v] += 1
        print("\n  --all: integers in the text not equal to any canonical funnel")
        print("  value (years, percentages, dates, or explained alternatives such")
        print("  as the appendix 338 SKIP). Review that these are NOT meant to be")
        print("  funnel counts:\n")
        for v, n in nums.most_common():
            print(f"    {v:>7}  x{n}")

    n_ok = sum(1 for (_label, canon, found, ok) in rows if found is not None and ok)
    n_drift = len(failures)
    n_nl = len(not_located)
    print(f"\n  OK: {n_ok}   DRIFT: {n_drift}   not located (absent): {n_nl}")
    if failures:
        print("  FAIL (prose has drifted from the data):")
        for f in failures:
            print("    " + f)
    else:
        print("  EVERY LOCATED PRISMA NUMBER IN THE PROSE MATCHES THE DATA.")
    print("=" * 78)
    return 1 if failures else 0


def _candidate_paths() -> list[Path]:
    given: list[Path] = []
    if args.tex:
        given.append(Path(args.tex))
    if args.dir:
        given += sorted(Path(args.dir).glob("*.tex"))
    if not given:
        for cand in (repo_root().parent / "mdpi_paper_slr",      # sibling paper repo (flattened)
                     repo_root().parent / "papers_code" / "writing" / "mdpi_paper"):
            if (cand / "sections").exists():
                given = sorted((cand / "sections").glob("*.tex"))
                break
    if not given:
        given = sorted(repo_root().glob("**/*.tex"))
    return given


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Reconcile PRISMA numbers in .tex prose")
    ap.add_argument("--tex", help="path to a single .tex file")
    ap.add_argument("--dir", help="directory of .tex files to scan (all *.tex inside)")
    ap.add_argument("--all", action="store_true",
                    help="also list every non-canonical integer found in the text")
    args = ap.parse_args()

    paths = _candidate_paths()
    if not paths:
        print("No .tex files found. Pass --tex /path/main.tex or --dir /sections")
        sys.exit(2)
    sys.exit(reconcile(paths, args.all))
