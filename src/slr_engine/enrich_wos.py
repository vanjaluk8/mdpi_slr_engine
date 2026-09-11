"""
enrich_wos.py — Augment a candidates/screened CSV with WoS UIDs and times-cited.

For each row that has a DOI, looks the paper up in the Web of Science Starter API
and fills in two new columns:
  wos_uid         — WoS accession number (e.g. WOS:000267144200002)
  wos_times_cited — times-cited count from WoS

Rows that already have a wos_uid are skipped (idempotent re-runs).
Rows without a DOI are skipped and left blank.
The input file is NEVER modified; output is written alongside it.

Usage:
    python -m slr_engine.enrich_wos --input data/snowball_output/01_retrieval/01_raw_2026-04-09.csv
    python -m slr_engine.enrich_wos --input data/snowball_output/02_screening/04_included_2026-04-09.csv
    python -m slr_engine.enrich_wos --input data/snowball_output/01_retrieval/01_raw_2026-04-09.csv --wos-key YOUR_KEY
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

from slr_engine.config import SLEEP_S, WOS_API_KEY
from slr_engine.engines.wos import lookup

WOS_COLUMNS = ["wos_uid", "wos_times_cited"]


def enrich(input_path: Path, api_key: str) -> Path:
    """
    Read input_path, add WoS columns, write enriched CSV next to the input.
    Returns the path of the written file.
    """
    rows: list[dict] = []
    with input_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    # Add new columns if missing
    for col in WOS_COLUMNS:
        if col not in fieldnames:
            fieldnames.append(col)

    total   = len(rows)
    skipped = 0
    found   = 0
    missed  = 0

    for i, row in enumerate(rows, 1):
        # Idempotent: skip rows already enriched
        if row.get("wos_uid", "").strip():
            skipped += 1
            continue

        doi = row.get("doi", "").strip()
        if not doi:
            row.setdefault("wos_uid", "")
            row.setdefault("wos_times_cited", "")
            missed += 1
            continue

        result = lookup(doi, api_key)
        row["wos_uid"]         = result["wos_uid"]
        row["wos_times_cited"] = result["wos_times_cited"]

        if result["wos_uid"]:
            found += 1
        else:
            missed += 1

        if i % 50 == 0 or i == total:
            print(f"  WoS enrich: {i}/{total}  found={found}  missed={missed}  skipped={skipped}",
                  flush=True)

        time.sleep(SLEEP_S)

    out_path = input_path.parent / (input_path.stem + "_wos.csv")
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWoS enrichment complete.")
    print(f"  Rows total   : {total}")
    print(f"  WoS matched  : {found}")
    print(f"  No DOI/miss  : {missed}")
    print(f"  Already had  : {skipped}")
    print(f"  Output       : {out_path}")
    return out_path


def main() -> None:
    from slr_engine.config import CANDIDATES_FILE, OUTPUT_DIR

    parser = argparse.ArgumentParser(description="Enrich candidates CSV with WoS UIDs and times-cited")
    parser.add_argument(
        "--input", "-i",
        type=Path,
        default=None,
        help="CSV to enrich (default: today's raw candidates file)",
    )
    parser.add_argument(
        "--wos-key",
        default="",
        help="WoS Starter API key (overrides WOS_API_KEY env var)",
    )
    args = parser.parse_args()

    api_key = args.wos_key or WOS_API_KEY
    if not api_key:
        print("ERROR: WOS_API_KEY not set. Add it to app/.env or pass --wos-key.", file=sys.stderr)
        sys.exit(1)

    input_path = args.input or CANDIDATES_FILE
    if not input_path.exists():
        files = sorted(OUTPUT_DIR.glob("01_raw_*.csv"), reverse=True)
        if not files:
            print(f"No raw candidates file found in {OUTPUT_DIR}", file=sys.stderr)
            sys.exit(1)
        input_path = files[0]
        print(f"Using most recent candidates file: {input_path.name}")

    enrich(input_path, api_key)


if __name__ == "__main__":
    main()
