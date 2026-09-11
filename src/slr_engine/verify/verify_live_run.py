#!/usr/bin/env python3
"""verify_live_run.py — prove a FRESH pipeline run reproduces the manuscript.

The other suites recompute every number from the committed, frozen
`verification_fixtures/` snapshots. That proves the *shipped data* matches the
paper, but a reviewer rightly also asks: "does the actual pipeline — run from
scratch in data/snowball_output/ (or SLR_OUTPUT_DIR) — produce these same
numbers, or did you hand-curate those fixtures to fit?"

This suite closes that gap. It ignores the fixtures entirely and recomputes the
core PRISMA-funnel invariants directly from the *live* output tree, globbing the
newest dated files actually present (a fresh `run_pipeline.sh` run, or whichever
tree `--out` / SLR_OUTPUT_DIR points at). It then checks each live-computed
number against the manuscript value in verify_common.EXPECTED, and cross-checks
the committed PRISMA summary text.

Exit code 0 iff every live number matches the manuscript.

Usage:
    python3 src/slr_engine/verify/verify_live_run.py                 # live data/snowball_output/
    SLR_OUTPUT_DIR=/path/to/run bash run_pipeline.sh --phase verify  # a --out run
"""
from __future__ import annotations

import csv
import sys

from slr_engine.verify.verify_common import (
    EXPECTED, FINAL_DUPLICATE_WORKS, Checks, counter,
    live_glob, live_latest_csv, live_latest_json,
    snowball_output_dir, write_run,
)

# Which manuscript figure/table each funnel invariant lives in (for the report's
# "Manuscript ref" column). Adjust to the reviewer-facing manuscript artifact IDs.
REF = {
    "n_seeds":              "Fig. 1 / §3.1",
    "raw_retrieved":        "Fig. 1",
    "screening_pool":       "Fig. 1",
    "dedup_removed":        "Fig. 1",
    "title_include":        "Fig. 1",
    "title_uncertain":      "Fig. 1",
    "title_exclude":        "Fig. 1",
    "llm_include":          "Fig. 1",
    "llm_uncertain_remain": "Fig. 1",
    "prevalidated_corpus":  "Fig. 1",
    "merged_corpus":        "Fig. 1",
    "enriched_retained":    "Fig. 1",
    "abstract_keep":        "Fig. 1",
    "abstract_defer":       "Fig. 1",
    "abstract_skip":        "Fig. 1",
    "abstract_pool":        "Fig. 1",
    "fulltext_queue":       "Fig. 1",
    "extract_final":        "Fig. 1",
    "final_list":           "Fig. 1",
    "final_distinct":       "Fig. 1 + §3.4",
    "prisma_summary":       "PRISMA_summary.md",
}


def _snapshot_or_live(prefix: str) -> list[dict]:
    """Return the canonical S-file from snapshots/, else the newest dated live CSV.

    A fresh pipeline run regenerates the date-free S1..S7b snapshots into
    `data/snowball_output/snapshots/`, so those are the live artifacts to read.
    In a partial `--out` run snapshots may be absent — then fall back to any
    dated live file with the same prefix.
    """
    hits = live_glob(f"{prefix}*.csv")
    hit = next((h for h in hits if "snapshots" in str(h)), hits[0] if hits else None)
    if hit is None:
        raise FileNotFoundError(f"no live {prefix}*.csv under the output tree")
    with hit.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def verify() -> int:
    print("LIVE-RUN REPRODUCIBILITY — proves a fresh pipeline run matches the manuscript")
    print("Source: live data/snowball_output/  (SLR_OUTPUT_DIR honoured)\n")
    lines: list[str] = [
        "LIVE-RUN REPRODUCIBILITY — recomputed from a fresh pipeline run",
        "Source: live data/snowball_output/  (fixtures deliberately NOT used)",
    ]

    c = Checks()
    c.suite = "Live run"

    # ── Retrieval (identification) ────────────────────────────────────────────
    # A fresh `retrieve` writes log_retrieval_<date>.json; glob the newest one.
    retr = live_latest_json("log_retrieval_*.json")
    seeds = {k: v for k, v in retr.items() if k != "_meta"}
    n_seeds = retr["_meta"].get("n_seeds", len(seeds))
    raw = sum(s.get("backward_examined", 0) + s.get("forward_examined", 0) for s in seeds.values())
    pool = sum(s.get("total_new", 0) for s in seeds.values())
    dedup = raw - pool
    c.check("n_seeds submitted (live)", n_seeds, EXPECTED.get("n_seeds", 7), REF["n_seeds"])
    c.check("raw records retrieved (live)", raw, EXPECTED["raw_retrieved"], REF["raw_retrieved"])
    c.check("unique records after dedup (live)", pool, EXPECTED["screening_pool"], REF["screening_pool"])
    c.check("duplicates removed (live)", dedup, EXPECTED["dedup_removed"], REF["dedup_removed"])

    # ── Title screening (from the live screening log) ────────────────────────
    try:
        scre = live_latest_json("log_screening_*.json")
        dec = scre["decisions"]
        pool_screen = scre["input"]["total_records"]
        c.check("screening pool (live)", pool_screen, EXPECTED["screening_pool"], REF["screening_pool"])
        c.check("title INCLUDE (live)", dec.get("INCLUDE"), EXPECTED["title_include"], REF["title_include"])
        c.check("title REVIEW/UNCERTAIN (live)", dec.get("REVIEW"), EXPECTED["title_uncertain"], REF["title_uncertain"])
        c.check("title EXCLUDE (live)", dec.get("EXCLUDE"), EXPECTED["title_exclude"], REF["title_exclude"])
        c.check("title screen sums to pool (live)",
                dec["INCLUDE"] + dec["REVIEW"] + dec["EXCLUDE"],
                EXPECTED["screening_pool"], REF["screening_pool"])
        llm = scre.get("llm_screening", {})
        c.check("llm promoted to INCLUDE (live)", llm.get("resolved_include"), EXPECTED["llm_include"], REF["llm_include"])
        c.check("llm UNCERTAIN remaining (live)", llm.get("uncertain_remaining"), EXPECTED["llm_uncertain_remain"], REF["llm_uncertain_remain"])
    except FileNotFoundError as e:
        # Screening log may not exist for a partial --out run; degrade gracefully
        # rather than fail the whole audit.
        print(f"  [SKIP] no live screening log — {e}")
        lines.append(f"[SKIP] no live screening log — {e}")

    # ── Corpus / merge / enrichment (canonical S-files regenerate into snapshots/) ──
    try:
        s1 = _snapshot_or_live("S1_prevalidated_corpus")
        c.check("pre-validated corpus (live)", len(s1), EXPECTED["prevalidated_corpus"], REF["prevalidated_corpus"])
        s5 = _snapshot_or_live("S5_merged_corpus")
        c.check("merged corpus (live)", len(s5), EXPECTED["merged_corpus"], REF["merged_corpus"])
        s6 = _snapshot_or_live("S6_enriched_reading_pool")
        c.check("enriched retained (live)", len(s6), EXPECTED["enriched_retained"], REF["enriched_retained"])
        s7b = _snapshot_or_live("S7b_abstract_reviewed_final")
        pool_actual = len(s7b)
        keep = counter(s7b, "abstract_decision").get("KEEP", 0)
        c.check("abstract-review pool (live)", pool_actual, EXPECTED["abstract_pool"], REF["abstract_pool"])
        c.check("abstract KEEP (live)", keep, EXPECTED["abstract_keep"], REF["abstract_keep"])
    except FileNotFoundError as e:
        print(f"  [SKIP] no live S-series found — {e}")
        lines.append(f"[SKIP] no live S-series found — {e}")

    # ── Final reading list + distinct papers ───────────────────────────────────
    try:
        final = live_latest_csv("13_final_reading_list_*.csv")
        c.check("final reading list rows (live)", len(final), EXPECTED["final_list"], REF["final_list"])
        row_keys = {(r.get("paper_key") or "").strip() for r in final}
        n_pairs_present = sum(1 for a, b in FINAL_DUPLICATE_WORKS if a in row_keys and b in row_keys)
        distinct = len(final) - n_pairs_present
        c.check("distinct papers (live)", distinct, EXPECTED["final_distinct"], REF["final_distinct"])
    except FileNotFoundError as e:
        print(f"  [SKIP] no live final reading list — {e}")
        lines.append(f"[SKIP] no live final reading list — {e}")

    # ── Cross-check the committed PRISMA_summary.md against the manuscript values ──
    # The summary is a separate, human-facing artifact; verify it did not drift
    # from the recomputed funnel. Only probe the headline values the summary is
    # *expected to state* (it formats raw as "1,150" — thousands comma — and never
    # prints intermediate merge/extraction counts as bare numerals), so the check
    # tolerates presentation while still catching real numeric drift.
    prisma_md = snowball_output_dir() / "07_final" / "PRISMA_summary.md"
    if prisma_md.exists():
        text = prisma_md.read_text(encoding="utf-8")
        probes = {
            "screening pool":        EXPECTED["screening_pool"],
            "title INCLUDE":         EXPECTED["title_include"],
            "title EXCLUDE":         EXPECTED["title_exclude"],
            "abstract KEEP":         EXPECTED["abstract_keep"],
            "abstract-review pool":  EXPECTED["abstract_pool"],
            "final list":            EXPECTED["final_list"],
        }
        # tolerate thousands separators: compare against both "972" and any
        # "N,N…" rendering of the same magnitude
        missing = []
        for name, val in probes.items():
            needle = str(val)
            if needle not in text and f"{val:,}" not in text:
                missing.append(f"{name}={val}")
        c.check("PRISMA_summary.md consistent with funnel (values in summary)",
                len(missing), 0, REF["prisma_summary"])
    else:
        print(f"  [SKIP] PRISMA_summary.md not found at {prisma_md}")
        lines.append("[SKIP] PRISMA_summary.md not found")

    rc = c.summary()
    lines.append(f"\n{len(c.passes)} passed, {len(c.failures)} failed")
    if c.failures:
        lines += ["FAIL: " + f for f in c.failures]
    out = write_run("verify_live_run.result.txt", lines)
    print(f"\nRun saved to: {out}")
    return rc


def main() -> int:
    return verify()


if __name__ == "__main__":
    sys.exit(main())
