"""
corpus_loader.py — import and deduplicate the pre-validated paper corpus.

Sources:
  G0_seed_papers.md      — foundational seed papers (markdown, hand-annotated)
  G1_*.csv … G6_*.csv   — group papers discovered via undermind.ai queries (CSV)

Public API:
  load_g0_seeds(g0_path)         → list[dict]  seed dicts for snowballing
  load_prevalidated_keys(repo)   → set[str]    DOI/arXiv keys to pre-register
  build_prevalidated(g0, gxs, out) → int       write 00_prevalidated_*.csv, return row count
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

from slr_engine.storage import CSV_FIELDNAMES


# ── Deduplication helpers ──────────────────────────────────────────────────────

def _norm_doi(doi: str) -> str:
    doi = doi.strip().lower()
    if doi.startswith("doi:"):
        doi = doi[4:]
    return doi


def _dedup_keys(row: dict) -> list[str]:
    """Return all non-empty canonical keys for a row (used to detect duplicates)."""
    keys: list[str] = []
    doi = _norm_doi(row.get("doi", ""))
    arxiv = (row.get("arxiv_id", "") or "").strip()
    pid = (row.get("ss_paper_id", "") or "").strip()
    if doi:
        keys.append(doi)
    if arxiv:
        keys.append(f"arxiv:{arxiv.lower()}")
    if pid:
        keys.append(f"ss:{pid}")
    return keys


# ── G0 markdown parser ─────────────────────────────────────────────────────────

def _extract_arxiv_id(urls_str: str) -> str:
    m = re.search(r'arxiv\.org/abs/([\d.]+v?\d*)', urls_str, re.I)
    return m.group(1).split("v")[0] if m else ""  # strip version suffix


def _extract_acl_doi(urls_str: str) -> str:
    m = re.search(r'aclanthology\.org/([^/\s?#]+)', urls_str, re.I)
    if m:
        slug = m.group(1).rstrip("/")
        return f"10.18653/v1/{slug}"
    return ""


def _extract_doi_from_url(url: str) -> str:
    m = re.search(r'doi\.org/(10\.[^\s]+)', url, re.I)
    return m.group(1).rstrip(").,") if m else ""


def _extract_ss_id(urls_str: str) -> str:
    m = re.search(r'semanticscholar\.org/paper/([a-f0-9]{20,})', urls_str, re.I)
    return m.group(1) if m else ""


def _parse_g0_entry(raw: str) -> dict | None:
    """Parse one markdown paper block into a row dict."""
    lines = [l.strip() for l in raw.strip().split("\n") if l.strip()]
    if not lines:
        return None

    # First line: "x] **Title** — Authors, Year"  (leading "- [" already stripped)
    first = lines[0]
    # Remove checkbox + opening bold marker
    content = re.sub(r'^[x ]?\]\s*\*\*', '', first, flags=re.I).strip()

    # Split on " — " to separate title from authors/year
    if " — " not in content:
        return None
    title_part, rest = content.split(" — ", 1)
    title = title_part.rstrip("*").strip()

    # Year: first 4-digit sequence (handles "2022/2023")
    year_m = re.search(r'(\d{4})', rest)
    year = year_m.group(1) if year_m else ""
    authors = rest[: year_m.start()].strip().rstrip(",").strip() if year_m else rest.strip()

    venue, urls_str = "", ""
    for line in lines[1:]:
        line = line.lstrip("-").strip()
        if line.startswith("**Venue**:"):
            venue = line[len("**Venue**:"):].strip()
        elif line.startswith("**URL**:"):
            urls_str = line[len("**URL**:"):].strip()

    arxiv_id = _extract_arxiv_id(urls_str)
    doi = _extract_acl_doi(urls_str) or _extract_doi_from_url(urls_str)
    ss_pid = _extract_ss_id(urls_str)

    # Seed ID for snowballing API lookup
    if arxiv_id:
        seed_id = f"arXiv:{arxiv_id}"
    elif doi:
        seed_id = doi
    else:
        seed_id = ""  # thesis or URL without resolvable ID

    # Derive a short key: first token of authors string is the first author's last name
    # e.g. "Houlsby et al." → "Houlsby";  "Šajina" → "Šajina"
    last_name = authors.split()[0] if authors else ""
    seed_key = f"{last_name} {year}" if last_name and year else title[:30]

    return {
        "_seed_id":       seed_id,      # internal; used by load_g0_seeds()
        "ss_paper_id":    ss_pid,
        "eid":            "",
        "arxiv_id":       arxiv_id,
        "doi":            doi,
        "title":          title,
        "authors":        authors,
        "year":           year,
        "venue":          venue,
        "citation_count": "",
        "direction":      "SEED",
        "seed_key":       seed_key,
        "seed_group":     "G0",
        "source_engine":  "seed",
        "inclusion":      "INCLUDE",
        "exclusion_reason": "",
        "notes":          "",
        "abstract":       "",
        "keywords":       "",
    }


def parse_g0_markdown(g0_path: Path) -> list[dict]:
    """Return all G0 row dicts (pipeline schema + '_seed_id' key)."""
    text = g0_path.read_text(encoding="utf-8")
    # Split on paper entry markers; keep everything after the first "- ["
    blocks = re.split(r'\n- \[', text)
    rows = []
    for block in blocks[1:]:          # skip file header
        row = _parse_g0_entry(block)
        if row:
            rows.append(row)
    return rows


def load_g0_seeds(g0_path: Path) -> list[dict]:
    """
    Return seed dicts for snowballing (format expected by core.run_snowball).
    Skips papers with no resolvable API ID (e.g., local thesis).
    """
    seeds = []
    for row in parse_g0_markdown(g0_path):
        sid = row["_seed_id"]
        if not sid:
            continue
        seeds.append({
            "id":    sid,
            "group": "G0",
            "key":   row["seed_key"],
            "title": row["title"],
        })
    return seeds


# ── G1-G6 CSV parser ───────────────────────────────────────────────────────────

def _parse_gx_row(csv_row: dict, group: str) -> dict:
    """Convert one G1-G6 CSV row to pipeline schema."""
    raw_doi = (csv_row.get("DOI") or "").strip()

    # Normalise arXiv DOI alias → plain arXiv ID
    arxiv_id = ""
    if re.match(r'10\.48550/arXiv\.', raw_doi, re.I):
        arxiv_id = raw_doi[len("10.48550/arXiv."):]

    # Extract SS paper_id from URL when DOI is absent
    url = (csv_row.get("URL") or "").strip()
    ss_pid = _extract_ss_id(url)

    # Venue: journal + volume (skip bare "abs/XXXXX" arXiv volume strings)
    journal = (csv_row.get("Journal") or "").strip()
    volume  = (csv_row.get("Volume")  or "").strip()
    venue   = f"{journal} {volume}".strip() if volume and not volume.startswith("abs/") else journal

    # Citation count: cast to int-string, tolerating floats
    raw_cc = (csv_row.get("Citation Count") or "").strip()
    try:
        citation_count = str(int(float(raw_cc)))
    except (ValueError, TypeError):
        citation_count = ""

    rel_score = (csv_row.get("Relevance Score") or "").strip()
    notes = f"relevance_score={rel_score}" if rel_score else ""

    return {
        "_seed_id":       "",           # G1-G6 papers are not used as snowball seeds
        "ss_paper_id":    ss_pid,
        "eid":            "",
        "arxiv_id":       arxiv_id,
        "doi":            raw_doi,
        "title":          (csv_row.get("Title")   or "").strip(),
        "authors":        (csv_row.get("Authors") or "").strip(),
        "year":           (csv_row.get("Year")    or "").strip(),
        "venue":          venue,
        "citation_count": citation_count,
        "direction":      "PREVALIDATED",
        "seed_key":       (csv_row.get("Cite Key") or "").strip(),
        "seed_group":     group,
        "source_engine":  "undermind",
        "inclusion":      "INCLUDE",
        "exclusion_reason": "",
        "notes":          notes,
        "abstract":       (csv_row.get("Abstract") or "").strip(),
        "keywords":       "",
    }


def parse_gx_csv(path: Path, group: str) -> list[dict]:
    """Parse one G1-G6 CSV file → list of pipeline-schema row dicts."""
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        for csv_row in csv.DictReader(f):
            row = _parse_gx_row(csv_row, group)
            if row["title"]:
                rows.append(row)
    return rows


# ── Corpus builder ─────────────────────────────────────────────────────────────

def build_prevalidated(
    g0_path: Path,
    gx_paths: list[tuple[str, Path]],
    output_path: Path,
) -> int:
    """
    Merge G0 + G1-G6 papers, deduplicate, write 00_prevalidated_*.csv.

    Dedup priority: G0 > G1 > G2 > … > G6.
    When a paper appears in multiple groups:
      - First occurrence keeps its seed_group / direction.
      - Abstract and citation_count are filled from whichever has them.

    Returns total number of rows written.
    """
    seen: dict[str, int] = {}    # dedup_key → index in `merged`
    merged: list[dict]   = []

    def _add(row: dict) -> None:
        keys = _dedup_keys(row)
        # Check for an existing entry
        existing_idx = next((seen[k] for k in keys if k in seen), None)
        if existing_idx is not None:
            existing = merged[existing_idx]
            # Fill missing abstract / citation_count from later group
            if not existing["abstract"] and row["abstract"]:
                existing["abstract"] = row["abstract"]
            if not existing["citation_count"] and row["citation_count"]:
                existing["citation_count"] = row["citation_count"]
            # Record the additional group in notes
            other_group = row["seed_group"]
            if other_group not in existing.get("notes", ""):
                existing["notes"] = "; ".join(filter(None, [
                    existing.get("notes", ""), f"also:{other_group}"
                ]))
            return

        # New entry
        idx = len(merged)
        merged.append(row)
        for k in keys:
            seen[k] = idx

    # G0 first (highest priority)
    for row in parse_g0_markdown(g0_path):
        _add(row)

    # G1-G6 in order
    for group, path in gx_paths:
        for row in parse_gx_csv(path, group):
            _add(row)

    # Write output (strip internal '_seed_id' key)
    output_path.parent.mkdir(exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(merged)

    return len(merged)


def load_prevalidated_keys(repo_path: Path) -> set[str]:
    """
    Parse G1-G6 CSVs and return a set of dedup keys (DOIs + arXiv IDs + SS IDs)
    to pre-register in existing_keys before snowballing — prevents snowball
    from re-discovering papers already in the undermind corpus.
    """
    gx_files = [
        ("G1", repo_path / "G1_PEFT_methods_beyond_adapters_and_LoRA.csv"),
        ("G2", repo_path / "G2_Adapter_composition_for_multitask_NLP_transformers.csv"),
        ("G3", repo_path / "G3_Decentralized_P2P_machine_learning_systems.csv"),
        ("G4", repo_path / "G4_Adapter_multiplexing_for_efficient_LLM_inference.csv"),
        ("G5", repo_path / "G5_Routing_and_MoE_for_modular_PEFT_transformers.csv"),
        ("G6", repo_path / "G6_Federated_PEFT_for_transformer_NLP.csv"),
    ]
    keys: set[str] = set()
    for group, path in gx_files:
        if not path.exists():
            continue
        for row in parse_gx_csv(path, group):
            keys.update(_dedup_keys(row))
    return keys
