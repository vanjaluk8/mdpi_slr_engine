"""
build_reading_list.py — Produce the final SLR reading list CSV.

Merges 11_data_extraction with any new include=yes records from
12_arxiv_for_manual_search, deduplicates by paper_key, and outputs
13_final_reading_list_<date>.csv sorted for writing.

Usage:
    python build_reading_list.py
"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

# Reads extraction tables from 06_extraction/ and writes the final list to 07_final/.
from slr_engine.config import EXTRACTION_DIR, FINAL_DIR

# Resolved lazily in main() so importing this module (from the CLI) is
# side-effect free — the glob would otherwise raise on an empty output tree.
TODAY = date.today().isoformat()

# ── Column order for the reading list ─────────────────────────────────────────
OUTPUT_COLS = [
    "read_status",          # empty — fill while reading
    "tier",
    "corpus",
    "contribution_type",
    "thesis_sections",
    "title",
    "authors",
    "year",
    "venue",
    "citation_count",
    "peft_technique",
    "distribution_mechanism",
    "contribution_codes",
    "key_finding",
    "doi",
    "arxiv_id",
    "source",
    "paper_key",
]

# ── Sort key ──────────────────────────────────────────────────────────────────
_CORPUS_ORDER = {"core": 0, "background": 1, "": 2}
_CONTRIBS_ORDER = [
    "adapter-serving", "P2P-DL", "P2P-FL", "distributed-PEFT", "distributed-inference",
    "federated-PEFT", "federated-PEFT+routing",
    "MoE-adapter-routing", "adapter-routing", "adapter-composition",
    "PEFT-method", "survey", "foundational-P2P", "MoE-routing", "gossip-learning",
]


def _sort_key(row: dict) -> tuple:
    corpus = _CORPUS_ORDER.get((row.get("corpus") or "").lower().strip(), 2)
    try:
        tier = int(row.get("tier") or 3)
    except ValueError:
        tier = 3
    ct = row.get("contribution_type") or ""
    try:
        ct_rank = _CONTRIBS_ORDER.index(ct)
    except ValueError:
        ct_rank = len(_CONTRIBS_ORDER)
    try:
        year = -int(row.get("year") or 0)   # descending year
    except ValueError:
        year = 0
    return (corpus, tier, ct_rank, year)


def _is_preprint(r: dict) -> bool:
    venue = (r.get("venue") or "").strip().lower()
    doi   = (r.get("doi")   or "").strip()
    return (
        "arxiv" in venue
        or venue in ("", "corr", "preprint")
        or doi.startswith("10.48550")
        or (not venue and not doi and bool((r.get("arxiv_id") or "").strip()))
    )


def _include(r: dict, min_citations_t3_bg: int = 50) -> bool:
    """Inclusion rule targeting ~120 papers with minimal arXiv preprints.

    T1  — all (core and background)
    T2  — all published; core preprints included; background preprints excluded
    T3  — core published only; background published only if citation_count ≥ min
    Incomplete (tier unknown / G_SNOW_F) — excluded
    """
    tier   = (r.get("tier")   or "").strip()
    corpus = (r.get("corpus") or "").strip()
    pp     = _is_preprint(r)

    if tier not in ("1", "2", "3") or not corpus:
        return False                        # incomplete record

    if tier == "1":
        return True

    if tier == "2":
        if not pp:
            return True                     # any published T2
        return corpus == "core"             # core preprint T2 only

    # tier == "3"
    if pp:
        return False                        # no T3 preprints
    if corpus == "core":
        return True                         # core T3 published
    try:
        cit = int(r.get("citation_count") or 0)
    except ValueError:
        cit = 0
    return cit >= min_citations_t3_bg       # background T3 published ≥ 50 cit


def _url(row: dict) -> str:
    arxiv = (row.get("arxiv_id") or "").strip()
    doi   = (row.get("doi") or "").strip()
    if arxiv and arxiv.replace(".", "").isdigit():
        return f"https://arxiv.org/abs/{arxiv}"
    if doi:
        return f"https://doi.org/{doi}"
    return ""


def load_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main() -> None:
    extraction_candidates = sorted(EXTRACTION_DIR.glob("11_data_extraction_*.csv"), reverse=True)
    if not extraction_candidates:
        print("ERROR: no 11_data_extraction_*.csv found — supply restricted data "
              "(SLR_DATA_ROOT). See docs/restricted-data.md.")
        return
    EXTRACTION_CSV = extraction_candidates[0]
    ARXIV_CSV = EXTRACTION_DIR / "12_arxiv_for_manual_search.csv"
    OUT_CSV = FINAL_DIR / f"13_final_reading_list_{TODAY}.csv"

    # ── Load extraction file (ground truth) ───────────────────────────────────
    rows11 = load_csv(EXTRACTION_CSV)
    seen_keys: set[str] = set()
    merged: list[dict] = []

    for r in rows11:
        key = (r.get("paper_key") or "").strip()
        if key and key in seen_keys:
            continue  # skip true duplicates
        if key:
            seen_keys.add(key)
        merged.append(r)

    print(f"Loaded {len(merged)} records from {EXTRACTION_CSV.name}")

    # ── Load arxiv file — add only NEW include=yes records ────────────────────
    if ARXIV_CSV.exists():
        rows12 = load_csv(ARXIV_CSV)
        new_added = 0
        for r in rows12:
            if (r.get("include") or "").strip().lower() != "yes":
                continue
            key = (r.get("paper_key") or "").strip()
            if key in seen_keys:
                continue
            seen_keys.add(key)
            merged.append(r)
            new_added += 1
        print(f"Added {new_added} new record(s) from {ARXIV_CSV.name}")

    # ── Sort ──────────────────────────────────────────────────────────────────
    merged.sort(key=_sort_key)

    # ── Filter ────────────────────────────────────────────────────────────────
    # Apply inclusion rule: ~120 papers, arXiv minimised
    non_empty = [r for r in merged if _include(r)]

    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLS, extrasaction="ignore")
        writer.writeheader()
        for i, r in enumerate(non_empty, 1):
            out = {col: (r.get(col) or "") for col in OUTPUT_COLS}
            out["read_status"] = ""  # always blank
            writer.writerow(out)

    print(f"\nWritten {len(non_empty)} records → {OUT_CSV.name}")
    print("  Corpus breakdown:")
    from collections import Counter
    corpus_ct = Counter((r.get("corpus") or "unknown") for r in non_empty)
    for k, v in sorted(corpus_ct.items()):
        print(f"    {k}: {v}")
    print("  Tier breakdown:")
    tier_ct = Counter((r.get("tier") or "?") for r in non_empty)
    for k, v in sorted(tier_ct.items()):
        print(f"    Tier {k}: {v}")
    print("  Contribution types:")
    ct_c = Counter((r.get("contribution_type") or "unknown") for r in non_empty)
    for k, v in sorted(ct_c.items(), key=lambda x: -x[1]):
        print(f"    {k}: {v}")


if __name__ == "__main__":
    main()
