"""
resolve_venues.py — Two-pass preprint cleanup for G-group CSVs.

Pass 1 — Venue resolution:
  For each paper whose Journal field is "ArXiv" (or similar), query the
  Semantic Scholar API to find the real published venue (ICML, NeurIPS, ICLR,
  ACL, …) and overwrite the Journal field in-place.

Pass 2 — Citation threshold filter (optional):
  Remove papers that are *still* preprint-only after pass 1 and whose citation
  count falls below --threshold (default 10).  Removed rows are written to a
  separate *_removed.csv sidecar so nothing is lost.

Usage:
    python -m slr_engine.resolve_venues                # dry-run, threshold 10
    python -m slr_engine.resolve_venues --apply        # write changes to CSVs
    python -m slr_engine.resolve_venues --apply --threshold 5
    python -m slr_engine.resolve_venues --apply --no-filter   # venue fix only, no removal
    python -m slr_engine.resolve_venues --apply --skip-resolve  # threshold filter only
"""
from __future__ import annotations

import argparse
import csv
import re
import time
from collections import defaultdict
from pathlib import Path

import requests

# ── Config ────────────────────────────────────────────────────────────────────
SS_PAPER_URL  = "https://api.semanticscholar.org/graph/v1/paper/{id}"
SS_FIELDS     = "publicationVenue,venue,externalIds,citationCount"
SLEEP_S       = 1.2      # polite delay between API calls
BATCH_LOG     = 20       # print progress every N papers
DEFAULT_THRESHOLD = 10   # drop preprints with fewer than this many citations

# Venue strings that still count as "preprint" after resolution
_PREPRINT_MARKERS = {"arxiv", "biorxiv", "medrxiv", "ssrn", "researchsquare",
                     "preprint", "techrxiv"}


def _is_preprint(venue: str) -> bool:
    v = venue.strip().lower()
    return not v or any(m in v for m in _PREPRINT_MARKERS)


def _extract_ss_id(row: dict) -> str | None:
    """
    Return the best Semantic Scholar paper identifier for this row.
    Priority: SS hash from URL > arXiv ID from DOI/URL > plain DOI.
    """
    url = row.get("URL", "").strip()
    doi = row.get("DOI", "").strip()

    # 1. SS paper hash (40-char hex in semanticscholar.org URLs)
    m = re.search(r"semanticscholar\.org/paper/([0-9a-f]{40})", url)
    if m:
        return m.group(1)

    # 2. arXiv ID from DOI like 10.48550/arXiv.2106.09685
    m = re.search(r"arxiv\.(\d{4}\.\d{4,5})", doi, re.I)
    if m:
        return f"ARXIV:{m.group(1)}"

    # 3. arXiv ID from URL like https://arxiv.org/abs/2106.09685
    m = re.search(r"arxiv\.org/abs/(\d{4}\.\d{4,5})", url, re.I)
    if m:
        return f"ARXIV:{m.group(1)}"

    # 4. Any other DOI
    if doi and not doi.lower().startswith("10.48550"):   # skip arXiv DOIs
        return f"DOI:{doi}"

    return None


def _query_ss(ss_id: str, session: requests.Session, ssl_verify: bool = True) -> dict | None:
    """Query SS paper endpoint; return JSON or None on error."""
    url = SS_PAPER_URL.format(id=ss_id)
    try:
        resp = session.get(url, params={"fields": SS_FIELDS},
                           timeout=15, verify=ssl_verify)
        if resp.status_code == 404:
            return None
        if resp.status_code == 429:
            print("    [rate-limit] waiting 60 s …")
            time.sleep(60)
            resp = session.get(url, params={"fields": SS_FIELDS},
                               timeout=15, verify=ssl_verify)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        print(f"    [warn] {ss_id}: {exc}")
        return None


def _best_venue(data: dict) -> str | None:
    """Extract the best venue string from an SS API response."""
    # publicationVenue is most reliable
    pv = data.get("publicationVenue") or {}
    name = pv.get("name", "").strip()
    if name and not _is_preprint(name):
        return name

    # fall back to venue string
    v = (data.get("venue") or "").strip()
    if v and not _is_preprint(v):
        return v

    return None


# ── Core logic ────────────────────────────────────────────────────────────────

def resolve_and_filter(
    papers_repo: Path,
    threshold: int = DEFAULT_THRESHOLD,
    apply: bool = False,
    do_resolve: bool = True,
    do_filter: bool = True,
    ssl_verify: bool = True,
) -> None:
    csv_files = sorted(papers_repo.glob("G[1-6]_*.csv"))
    if not csv_files:
        print(f"No G*.csv files found in {papers_repo}")
        return

    session = requests.Session()
    session.headers.update({"User-Agent": "SLR-venue-resolver/1.0"})

    # Aggregate stats across all files
    stats = defaultdict(int)

    for csv_path in csv_files:
        gid = re.match(r"(G\d)", csv_path.stem).group(1)
        print(f"\n{'─'*60}")
        print(f"  {gid}  {csv_path.name}")
        print(f"{'─'*60}")

        with csv_path.open(newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            fieldnames = reader.fieldnames
            rows = list(reader)

        # Normalise the BOM-stripped key name for downstream use
        cite_key_col = next(
            (k for k in (fieldnames or []) if k.lstrip("\ufeff") == "Cite Key"),
            "Cite Key",
        )

        kept, removed_rows = [], []
        resolved_count = 0

        # ── Pass 1: venue resolution ──────────────────────────────────────────
        if do_resolve:
            arxiv_rows = [r for r in rows if _is_preprint(r.get("Journal", ""))]
            print(f"  Preprints to resolve : {len(arxiv_rows)}/{len(rows)}")

            for i, row in enumerate(arxiv_rows, 1):
                ss_id = _extract_ss_id(row)
                if not ss_id:
                    if i % BATCH_LOG == 0:
                        print(f"    … {i}/{len(arxiv_rows)}")
                    time.sleep(SLEEP_S)
                    continue

                time.sleep(SLEEP_S)
                data = _query_ss(ss_id, session, ssl_verify)
                if not data:
                    continue

                venue = _best_venue(data)
                if venue:
                    old = row["Journal"]
                    row["Journal"] = venue
                    # Also refresh citation count if SS returned a fresher value
                    ss_cites = data.get("citationCount")
                    if ss_cites is not None:
                        row["Citation Count"] = str(ss_cites)
                    resolved_count += 1
                    print(f"    ✓ {row.get(cite_key_col,'?'):12s}  {old!r:18s} → {venue!r}")

                if i % BATCH_LOG == 0:
                    print(f"    … {i}/{len(arxiv_rows)}")

            print(f"  Venues resolved : {resolved_count}")
            stats["resolved"] += resolved_count
        else:
            print("  [skip] venue resolution")

        # ── Pass 2: citation threshold filter ─────────────────────────────────
        removed_in_file = 0
        if do_filter:
            for row in rows:
                still_preprint = _is_preprint(row.get("Journal", ""))
                try:
                    cites = float(row.get("Citation Count", "0") or 0)
                except ValueError:
                    cites = 0.0
                if still_preprint and cites < threshold:
                    removed_rows.append(row)
                    removed_in_file += 1
                else:
                    kept.append(row)
            print(f"  Removed (preprint + < {threshold} cites) : {removed_in_file}")
            stats["removed"] += removed_in_file
        else:
            kept = rows
            print("  [skip] citation threshold filter")

        stats["total"] += len(rows)

        # ── Write ─────────────────────────────────────────────────────────────
        if apply:
            with csv_path.open("w", newline="", encoding="utf-8-sig") as fh:
                writer = csv.DictWriter(fh, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(kept)

            if removed_rows:
                sidecar = csv_path.with_name(csv_path.stem + "_removed.csv")
                with sidecar.open("w", newline="", encoding="utf-8-sig") as fh:
                    writer = csv.DictWriter(fh, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(removed_rows)
                print(f"  Removed rows saved → {sidecar.name}")

            print(f"  ✓ Written: {len(kept)} rows")
        else:
            print(f"  [dry-run] Would keep {len(kept)}, remove {removed_in_file}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  Summary")
    print(f"{'='*60}")
    print(f"  Total papers processed : {stats['total']}")
    print(f"  Venues resolved        : {stats['resolved']}")
    if do_filter:
        remaining = stats["total"] - stats["removed"]
        print(f"  Papers removed         : {stats['removed']} (preprint + < {threshold} cites)")
        print(f"  Papers remaining       : {remaining}")
    if not apply:
        print("\n  This was a DRY RUN — pass --apply to write changes.")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    here      = Path(__file__).resolve().parent   # app/

    try:
        from slr_engine.config import SSL_VERIFY, PAPERS_REPO
    except ImportError:
        SSL_VERIFY = True
        PAPERS_REPO = here.parent / "data" / "inputs"
    papers_repo = PAPERS_REPO

    parser = argparse.ArgumentParser(
        description="Resolve arXiv venue labels and filter low-citation preprints"
    )
    parser.add_argument("--apply",         action="store_true",
                        help="Write changes to CSVs (default: dry-run)")
    parser.add_argument("--threshold",     type=int, default=DEFAULT_THRESHOLD,
                        help=f"Min citations for preprint-only papers (default: {DEFAULT_THRESHOLD})")
    parser.add_argument("--no-filter",     action="store_true",
                        help="Skip citation threshold filter (venue resolution only)")
    parser.add_argument("--skip-resolve",  action="store_true",
                        help="Skip venue resolution (citation filter only)")
    parser.add_argument("--papers-repo",   type=Path, default=papers_repo,
                        help=f"Path to papers_repo directory (default: {papers_repo})")
    args = parser.parse_args()

    resolve_and_filter(
        papers_repo=args.papers_repo,
        threshold=args.threshold,
        apply=args.apply,
        do_resolve=not args.skip_resolve,
        do_filter=not args.no_filter,
        ssl_verify=SSL_VERIFY,
    )


if __name__ == "__main__":
    main()
