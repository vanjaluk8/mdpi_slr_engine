"""CSV persistence helpers."""
from __future__ import annotations

import csv
from pathlib import Path

from slr_engine.models import Paper

CSV_FIELDNAMES = [
    "ss_paper_id", "eid", "arxiv_id", "doi", "title", "authors",
    "year", "venue", "citation_count", "direction", "seed_key", "seed_group",
    "source_engine", "inclusion", "exclusion_reason", "notes", "abstract", "keywords",
]


def paper_to_csv_row(
    paper: Paper,
    direction: str,
    seed_key: str,
    seed_group: str,
) -> dict:
    """Flatten a Paper to a CSV row dict."""
    authors_str = "; ".join(paper.authors[:4])
    return {
        "ss_paper_id":      paper.paper_id,
        "eid":              paper.eid,
        "arxiv_id":         paper.arxiv_id,
        "doi":              paper.doi,
        "title":            paper.title,
        "authors":          authors_str,
        "year":             paper.year,
        "venue":            paper.venue,
        "citation_count":   paper.citation_count,
        "direction":        direction,
        "seed_key":         seed_key,
        "seed_group":       seed_group,
        "source_engine":    paper.source_engine,
        "inclusion":        "",
        "exclusion_reason": "",
        "notes":            "",
        "abstract":         "",
        "keywords":         "",
    }


def load_existing_keys(path: Path) -> tuple[set[str], set[str]]:
    """
    Returns (engine_keys, scopus_keys).

    engine_keys  — "dedup_key||engine" strings for every row already written.
                   Prevents re-adding the same (paper, engine) pair across runs.
                   Rows with no source_engine (legacy) are stored as plain keys
                   and block all engines.
    scopus_keys  — plain normalised DOIs for rows whose source_engine is "scopus".
                   Used in later passes to skip SS candidates already covered by
                   a Scopus entry (without removing any existing rows).
    """
    if not path.exists():
        return set(), set()
    engine_keys: set[str] = set()
    scopus_keys: set[str] = set()
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            engine = row.get("source_engine", "").strip()
            doi = row.get("doi", "").strip().lower()
            if doi.startswith("doi:"):
                doi = doi[4:]
            pid = row.get("ss_paper_id", "").strip()
            eid = row.get("eid", "").strip()
            for k in filter(None, [doi, pid, eid]):
                engine_keys.add(f"{k}||{engine}" if engine else k)
            if engine == "scopus" and doi:
                scopus_keys.add(doi)
    return engine_keys, scopus_keys


def append_candidates(rows: list[dict], path: Path) -> None:
    """Append candidate rows to the CSV, writing the header if the file is new."""
    write_header = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)
