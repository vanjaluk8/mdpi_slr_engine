"""
Merge 10_data_extraction_2026-05-02.csv with MANUAL_SCOPUS_export_1105.csv
and MANUAL_WOS_export_1105.txt into 11_data_extraction_2026-05-12.csv.

Rules:
- Keep the structure of the base extraction CSV
- Add a 'source' column: 'WOS', 'SCOPUS', or '' for original records
- Deduplicate on DOI (preferred) then normalised title
"""

import csv
import re

# Extraction tables + manual vendor exports live in 06_extraction/.
from slr_engine.config import EXTRACTION_DIR

BASE = EXTRACTION_DIR

BASE_CSV  = BASE / "10_data_extraction_2026-05-02.csv"
SCOPUS    = BASE / "11_MANUAL_SCOPUS_export_1105.csv"
WOS_TXT   = BASE / "11_MANUAL_WOS_export_1105.txt"
OUT_CSV   = BASE / "11_data_extraction_2026-05-12.csv"

BASE_COLS = [
    "paper_key", "arxiv_id", "doi", "title", "year", "venue",
    "citation_count", "tier", "review_source", "corpus",
    "contribution_type", "method_name", "peft_technique",
    "distribution_mechanism", "thesis_sections", "contribution_codes",
    "datasets", "metrics", "key_finding", "notes_raw", "source",
]


def norm_doi(doi: str) -> str:
    return doi.strip().lower().lstrip("https://doi.org/").lstrip("http://dx.doi.org/")


def norm_title(title: str) -> str:
    return re.sub(r"\s+", " ", title.lower().strip())


def empty_row() -> dict:
    return {c: "" for c in BASE_COLS}



def main() -> None:
    # ── 1. Load base file ────────────────────────────────────────────────────

    records = []       # list of dicts (BASE_COLS + source)
    seen_doi   = {}    # norm_doi  → index in records
    seen_title = {}    # norm_title → index in records

    with open(BASE_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rec = empty_row()
            for k in BASE_COLS:
                if k != "source":
                    rec[k] = row.get(k, "")
            rec["source"] = ""
            idx = len(records)
            records.append(rec)
            if rec["doi"]:
                seen_doi[norm_doi(rec["doi"])] = idx
            if rec["title"]:
                seen_title[norm_title(rec["title"])] = idx

    print(f"Base records loaded: {len(records)}")


    # ── helpers ──────────────────────────────────────────────────────────────────

    def is_duplicate(doi: str, title: str):
        """Return index of existing record if duplicate, else None."""
        if doi:
            nd = norm_doi(doi)
            if nd in seen_doi:
                return seen_doi[nd]
        if title:
            nt = norm_title(title)
            if nt in seen_title:
                return seen_title[nt]
        return None


    def register(rec: dict):
        idx = len(records)
        records.append(rec)
        if rec["doi"]:
            seen_doi[norm_doi(rec["doi"])] = idx
        if rec["title"]:
            seen_title[norm_title(rec["title"])] = idx


    # ── 2. Load Scopus ───────────────────────────────────────────────────────────

    scopus_new = 0
    scopus_dup = 0

    with open(SCOPUS, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            title = row.get("Title", "").strip()
            doi   = row.get("DOI", "").strip()
            dup   = is_duplicate(doi, title)
            if dup is not None:
                scopus_dup += 1
                # Tag existing record if it has no source yet
                if not records[dup]["source"]:
                    records[dup]["source"] = "SCOPUS"
                continue

            rec = empty_row()
            rec["title"]          = title
            rec["doi"]            = doi
            rec["year"]           = row.get("Year", "").strip()
            rec["venue"]          = row.get("Source title", "").strip()
            rec["citation_count"] = row.get("Cited by", "").strip()
            rec["paper_key"]      = doi or row.get("EID", "").strip()
            rec["source"]         = "SCOPUS"
            register(rec)
            scopus_new += 1

    print(f"Scopus  — new: {scopus_new}  duplicates: {scopus_dup}")


    # ── 3. Load WoS ─────────────────────────────────────────────────────────────

    wos_new = 0
    wos_dup = 0

    with open(WOS_TXT, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            title = row.get("TI", "").strip()
            doi   = row.get("DI", "").strip()
            dup   = is_duplicate(doi, title)
            if dup is not None:
                wos_dup += 1
                if not records[dup]["source"]:
                    records[dup]["source"] = "WOS"
                continue

            rec = empty_row()
            rec["title"]          = title
            rec["doi"]            = doi
            rec["year"]           = row.get("PY", "").strip()
            rec["venue"]          = row.get("SO", "").strip()
            rec["citation_count"] = row.get("TC", "").strip()
            rec["paper_key"]      = doi or row.get("UT", "").strip()
            rec["source"]         = "WOS"
            register(rec)
            wos_new += 1

    print(f"WoS     — new: {wos_new}  duplicates: {wos_dup}")


    # ── 4. Write output ──────────────────────────────────────────────────────────

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=BASE_COLS)
        writer.writeheader()
        writer.writerows(records)

    print(f"\nOutput: {OUT_CSV}")
    print(f"Total records: {len(records)}  (base: {len(records) - scopus_new - wos_new}  +Scopus: {scopus_new}  +WoS: {wos_new})")


if __name__ == "__main__":
    main()
