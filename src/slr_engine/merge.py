"""
merge.py — combine 00_prevalidated + 04_included → 05_merged.

Usage:
    python -m slr_engine.merge                                    # uses today's files
    python -m slr_engine.merge --date 2026-04-10
    python -m slr_engine.merge --prevalidated path/to/00_*.csv \
                        --included path/to/04_*.csv
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from slr_engine.storage import CSV_FIELDNAMES


# ── Dedup helpers (mirrors corpus_loader; kept local to avoid circular import) ──

def _dedup_keys(row: dict) -> list[str]:
    keys: list[str] = []
    doi = (row.get("doi", "") or "").strip().lower()
    if doi.startswith("doi:"):
        doi = doi[4:]
    arxiv = (row.get("arxiv_id", "") or "").strip()
    pid   = (row.get("ss_paper_id", "") or "").strip()
    if doi:
        keys.append(doi)
    if arxiv:
        keys.append(f"arxiv:{arxiv.lower()}")
    if pid:
        keys.append(f"ss:{pid}")
    return keys


# ── Core merge function ────────────────────────────────────────────────────────

def merge(
    prevalidated_path: Path,
    included_path: Path,
    output_path: Path,
) -> tuple[int, int]:
    """
    Merge 00_prevalidated + 04_included → 05_merged.

    Dedup strategy:
      - All prevalidated rows are written first.
      - Snowball rows (04_included) are added only if their DOI / arXiv ID /
        SS paper_id don't already appear in the prevalidated set.

    Returns (n_prevalidated, n_new_from_snowball).
    """
    prevalidated: list[dict] = []
    seen: set[str] = set()

    # Load prevalidated
    if prevalidated_path.exists():
        with prevalidated_path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                prevalidated.append(row)
                seen.update(k for k in _dedup_keys(row) if k)
    else:
        print(f"  [merge] WARNING: {prevalidated_path.name} not found — "
              f"merging snowball inclusions only.")

    # Load snowball inclusions, skip duplicates
    new_rows: list[dict] = []
    if included_path.exists():
        with included_path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                row_keys = [k for k in _dedup_keys(row) if k]
                if row_keys and any(k in seen for k in row_keys):
                    continue          # already in prevalidated corpus
                new_rows.append(row)
                seen.update(row_keys)
    else:
        print(f"  [merge] WARNING: {included_path.name} not found — "
              f"no snowball rows to add.")

    # Write merged file
    all_rows = prevalidated + new_rows
    output_path.parent.mkdir(exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)

    return len(prevalidated), len(new_rows)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    from slr_engine.config import IMPORT_DIR, SCREENING_DIR, MERGE_DIR

    parser = argparse.ArgumentParser(
        description="Merge 00_prevalidated + 04_included → 05_merged"
    )
    parser.add_argument("--date", default=None,
                        help="Date suffix, e.g. 2026-04-10 (default: most recent files)")
    parser.add_argument("--prevalidated", type=Path, default=None,
                        help="Path to 00_prevalidated_*.csv")
    parser.add_argument("--included", type=Path, default=None,
                        help="Path to 04_included_*.csv")
    parser.add_argument("--output", "-o", type=Path, default=None,
                        help="Output path (default: 05_merged_<date>.csv)")
    args = parser.parse_args()

    def _find(base: Path, prefix: str) -> Path:
        if args.date:
            p = base / f"{prefix}_{args.date}.csv"
            if p.exists():
                return p
            raise FileNotFoundError(f"Not found: {p}")
        files = sorted(base.glob(f"{prefix}_*.csv"), reverse=True)
        if not files:
            raise FileNotFoundError(f"No {prefix}_*.csv in {base}")
        return files[0]

    try:
        pre_path = args.prevalidated or _find(IMPORT_DIR, "00_prevalidated")
        inc_path = args.included     or _find(SCREENING_DIR, "04_included")
    except FileNotFoundError as e:
        import sys
        print(f"Error: {e}", file=sys.stderr)
        raise SystemExit(1)

    import re
    dm = re.search(r"\d{4}-\d{2}-\d{2}", pre_path.stem)
    date_str = dm.group(0) if dm else "merged"
    out_path = args.output or (MERGE_DIR / f"05_merged_{date_str}.csv")

    n_pre, n_new = merge(pre_path, inc_path, out_path)
    print(f"Merge complete → {out_path.name}")
    print(f"  Pre-validated rows : {n_pre}")
    print(f"  New snowball rows  : {n_new}")
    print(f"  Total              : {n_pre + n_new}")


if __name__ == "__main__":
    main()
