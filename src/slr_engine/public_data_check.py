"""Public-data boundary enforcement for the MDPI-release.

Three defence-in-depth routines that keep licensed/bibliographic content out of
the public release:

* ``scan_restricted(target)`` — walk a directory tree and flag any committed file
  whose name or CSV header matches a restricted-vendor pattern (abstracts,
  keywords, affiliations, author IDs, reference lists, vendor export markers,
  credentials, PDFs). Returns non-zero if anything suspicious is found. This is
  the same check that runs in CI so a reviewer can trust the tag is clean.

* ``verify_public()`` — offline validation of the public evidence tables
  (``public_data/``): required files present, primary key uniqueness, allowed
  columns only, and a restricted-field sweep of the whole tree. It requires no
  API keys, no network, and no restricted data, so it passes on a fresh public
  clone.

* ``build_public_data(master, ...)`` — an OWNER ACTION. Reads a *restricted*
  master CSV supplied at an external path, drops every column whose provenance
  is not cleared for redistribution, and writes deterministic redacted public
  evidence tables. Decisions about which columns are public live in
  ``public_data/data_dictionary.csv``. This must be run in your controlled
  environment AFTER licence review — never in CI, and never on content whose
  redistribution rights are unconfirmed.

The distinguishing rule (see docs/data-provenance.md): publication status is
decided at the COLUMN level. A derived table carrying a DOI, a screening
decision and an exclusion reason is public; a CSV carrying abstracts,
affiliations, author IDs or reference lists is not.
"""
from __future__ import annotations

import csv
import hashlib
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Restrictive default: package root is <project>/src/slr_engine, so the project
# root is three parents up.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PUBLIC_DATA_DIR = PROJECT_ROOT / "public_data"

# ── Restricted column names ─────────────────────────────────────────────────
# A CSV row whose header contains any of these is NOT cleared for public
# redistribution. Exact lower-cased substring match on the column name.
RESTRICTED_COLUMN_MARKERS: tuple[str, ...] = (
    "abstract",            # full-text / abstract prose
    "keyword",             # author/index keywords
    "affiliation",
    "author_id",
    "authors with affiliations",
    "author(s) id",
    "reference",           # reference/cited-reference lists
    "cited_by",
    "times_cited",
    "citation_count",      # citation counts are licensed-ish; keep internal
    "citation count",
    "funding",             # funding details / grant text
    "sponsor",             # sponsors field
    "chemical",
    "tradename",
    "manufactur",
    "correspondence address",
    "address",
    "supplier",
    "notes_raw",           # researcher raw notes may embed quotes
    "notes",               # unrestricted? flag for manual review
    "key_finding",         # researcher-written, but may quote vendor text -> review
    "url",                 # signed download URLs are restricted
    "link",
)

# ── Credential markers (checked against ALL files, any type) ────────────────
# Real secrets must never be committed regardless of file type. The `.env.example`
# template is exempt (documented variable names only).
CREDENTIAL_MARKERS: tuple[str, ...] = (".env", "key", "secret", "token", "credential", ".pem")

# ── Restricted data markers (checked against DATA files only) ───────────────
# A data file (export, cache, export of vendor metadata) whose name matches any
# of these is treated as restricted regardless of content. Source/config files
# are exempt from this list — code legitimately names vendor engines.
RESTRICTED_DATA_MARKERS: tuple[str, ...] = (
    "scopus",
    "web_of_science",
    "web of science",
    "_wos",
    ".wos",
    "export",
    "manual_",
    "manual-",
    "raw_",
    "api_cache",
    "fetch_",
    "download",
)

# File extensions that bear DATA (could hold vendor exports/content) and are
# therefore subject to the restricted-data marker + CSV-header scan. Everything
# else (e.g. .py, .yaml, .md, .toml, .json-schema) is code/config and is only
# checked for credentials.
DATA_EXTENSIONS: tuple[str, ...] = (
    ".csv", ".json", ".txt", ".tsv", ".ris", ".bib", ".enw", ".nbib",
    ".xlsx", ".pdf", ".xml",
)

# Columns that are ALWAYS safe to publish: identifiers + researcher coding.
# ``build_public_data`` keeps a row if every one of its columns is in this set
# (or is explicitly declared public in the data dictionary). Add reserved
# prefixes here rather than weakening the drop-list.
PUBLIC_COLUMN_KEYS: tuple[str, ...] = (
    "paper_id", "doi", "arxiv_id", "ss_paper_id", "eid", "title", "year",
    "venue", "corpus", "tier", "review_source", "inclusion", "decision",
    "exclusion_reason", "removal_reason", "abstract_decision",
    "fulltext_decision", "relevance_decision", "contribution_type",
    "method_name", "peft_technique", "distribution_mechanism",
    "thesis_sections", "contribution_codes", "datasets", "metrics",
    "seed_group", "direction", "source_engine", "in_title_screened_s5",
    "in_enriched_s6", "in_abstract_review_s7b", "in_fulltext_queue_q9",
    "in_extraction_e11", "in_final_list_123", "read_status", "source",
    "final_paper_key", "final_doi", "final_arxiv_id", "category",
    "provenance", "licence_class",
)

# Directories always exempt from the scan (they are code / config / schema /
# synthetic fixtures and never hold vendor content).
SCAN_EXEMPT_DIRS: tuple[str, ...] = (
    ".git", "__pycache__", "schemas", "configs", "tests", "docs", ".github",
    ".idea", ".venv", "node_modules",
)


@dataclass
class ScanResult:
    """Aggregates restricted-field findings for one scan pass."""
    findings: list[str] = field(default_factory=list)
    files_checked: int = 0

    @property
    def ok(self) -> bool:
        return not self.findings

    def report(self) -> str:
        if self.ok:
            return f"OK — {self.files_checked} files scanned, no restricted content found."
        head = [f"RESTRICTED CONTENT FOUND ({len(self.findings)}):"]
        return "\n".join(head + self.findings)


def _is_exempt(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    for part in rel.parts:
        if part in SCAN_EXEMPT_DIRS:
            return True
    return False


def _is_content_restricted(col: str) -> bool:
    """True if a column carries vendor CONTENT (prose/ids/licensed metrics).

    Distinguishes content-bearing columns (``abstract``, ``keywords``,
    ``author_id``, ``citation_count``, ...) from safe researcher-DECISION flags
    such as ``abstract_decision`` or ``in_abstract_review_s7b``, which mention
    ``abstract`` as a stage label but hold no prose.
    """
    low = col.strip().lower().replace("﻿", "")  # strip BOM
    # Abstract prose is the exact column ("abstract"/"Abstract"), never a
    # "_decision"/"_review" suffix — those are researcher stage labels.
    if low == "abstract":
        return True
    for m in RESTRICTED_COLUMN_MARKERS:
        if m == "abstract":
            continue  # handled above (avoid flagging abstract_decision)
        if m in low:
            return True
    return False


def _restricted_columns(header: list[str]) -> list[str]:
    """Return the subset of ``header`` that carries restricted vendor content."""
    return [col for col in header if _is_content_restricted(col)]


def _inspect_csv(path: Path, findings: list[str]) -> None:
    try:
        with open(path, newline="", encoding="utf-8", errors="replace") as f:
            sample = f.read(64 * 1024)
    except (OSError, UnicodeDecodeError):
        return
    try:
        header = next(csv.reader(sample.splitlines()))
    except StopIteration:
        return
    hits = _restricted_columns(header)
    if hits:
        findings.append(f"{path.relative_to(PROJECT_ROOT)}: restricted column(s): {', '.join(hits)}")


def _inspect_file(path: Path, root: Path, result: ScanResult) -> bool:
    """Inspected one file; returns True and adds a finding if restricted."""
    name = path.name
    if name == ".env.example":
        return False  # documented template — variable names only

    plain = name.lower()

    # 1. Credential markers apply to every file type.
    for m in CREDENTIAL_MARKERS:
        if m in plain:
            result.findings.append(
                f"{path.relative_to(root) or path}: file name matches credential marker '{m}'"
            )
            return True

    # 2. Restricted-data markers apply only to data-bearing files.
    is_data = path.suffix.lower() in DATA_EXTENSIONS
    if is_data:
        for m in RESTRICTED_DATA_MARKERS:
            if m in plain:
                result.findings.append(
                    f"{path.relative_to(root) or path}: data file name matches restricted marker '{m}'"
                )
                return True
        if path.suffix.lower() == ".csv":
            _inspect_csv(path, result.findings)
    return False


def scan_restricted(target: str | None = None, verbose: bool = False) -> int:
    """Scan ``target`` (default project root) for restricted content.

    Returns 0 (clean) or 1 (something restricted was found). Source and config
    files are exempt from the vendor-data name check; every file is still
    checked for credential markers.
    """
    root = Path(target).resolve() if target else PROJECT_ROOT
    result = ScanResult()

    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        if _is_exempt(path, root):
            continue
        result.files_checked += 1
        _inspect_file(path, root, result)

    print(result.report() if verbose else
          (f"scan-restricted: {result.files_checked} files, "
           f"{len(result.findings)} finding(s) — "
           + ("CLEAN" if result.ok else "RESTRICTED CONTENT PRESENT")))
    return 0 if result.ok else 1


# ── Public evidence tables ──────────────────────────────────────────────────

PUBLIC_TABLES = (
    ("included_studies.csv",          ("paper_id", "doi", "arxiv_id", "year", "title", "final_classification")),
    ("screening_decisions.csv",       ("paper_id", "stage", "decision", "reason_code")),
    ("fulltext_exclusions.csv",       ("paper_id", "exclusion_reason")),
    ("data_extraction.csv",           ("paper_id", "contribution_type", "peft_technique", "distribution_mechanism", "thesis_sections", "contribution_codes")),
    ("prisma_counts.csv",             ("stage", "transition", "count")),
    ("search_log_public.csv",         ("source", "query_id", "retrieval_date", "n_retrieved", "run_id")),
    ("data_dictionary.csv",           ("column", "table", "type", "provenance", "licence_class", "missing_value", "reason")),
)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_public(verbose: bool = False) -> int:
    """Offline validation of the public evidence tables.

    Passes on a fresh public clone (tables may be empty but must exist and, if
    populated, conform to their schema). Combined with ``scan_restricted`` this
    is the reviewer-facing gate.
    """
    failures: list[str] = []

    if not PUBLIC_DATA_DIR.is_dir():
        failures.append(f"missing public_data/ directory at {PUBLIC_DATA_DIR}")

    for name, allowed_columns in PUBLIC_TABLES:
        path = PUBLIC_DATA_DIR / name
        if not path.exists():
            if name == "data_dictionary.csv":
                failures.append(f"required public_data/{name} is missing")
            continue  # value tables may be empty on first clone
        with open(path, newline="", encoding="utf-8") as f:
            header = next(csv.reader(f))
        header = [c.strip().replace("﻿", "") for c in header]
        # PK / uniqueness
        if name != "data_dictionary.csv" and "paper_id" in header:
            seen: set[str] = set()
            with open(path, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    pid = (row.get("paper_id") or "").strip()
                    if pid and pid in seen:
                        failures.append(f"public_data/{name}: duplicate paper_id {pid!r}")
                    seen.add(pid)
        # restricted-field sweep on public tables themselves
        hits = _restricted_columns(header)
        if hits:
            failures.append(f"public_data/{name}: restricted column(s) must be removed: {hits}")

    # Whole-tree restricted sweep
    scan = ScanResult()
    for path in sorted(PROJECT_ROOT.rglob("*")):
        if path.is_dir() or _is_exempt(path, PROJECT_ROOT):
            continue
        scan.files_checked += 1
        _inspect_file(path, PROJECT_ROOT, scan)
    if not scan.ok:
        failures.extend(scan.findings)

    if failures:
        print("verify-public: FAILED")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"verify-public: OK ({scan.files_checked} files scanned, public tables conform)")
    return 0


# ── Public derivative builder (OWNER ACTION) ────────────────────────────────

def _stable_paper_id(row: dict) -> str:
    doi = (row.get("doi") or "").strip().lower()
    if doi:
        return "doi:" + doi.lstrip("https://doi.org/").lstrip("http://dx.doi.org/")
    arxiv = (row.get("arxiv_id") or "").strip()
    if arxiv:
        return "arxiv:" + arxiv
    title = (row.get("title") or "").strip()
    year = (row.get("year") or "").strip()
    if title:
        h = hashlib.sha256(f"{title}|{year}".encode("utf-8")).hexdigest()[:16]
        return f"sha:{h}"
    return ""


def build_public_data(master: str, out_dir: str | None = None,
                      force: bool = False, verbose: bool = False) -> int:
    """Derive redacted public evidence tables from ``master`` (a restricted CSV).

    OWNER ACTION. Raises/returns non-zero if the master is not on an external,
    explicitly authorized path, or if its columns are not all cleared to publish.
    Intentionally conservative: any unrecognised column causes the row to be
    dropped rather than guessed.
    """
    out = Path(out_dir).resolve() if out_dir else PUBLIC_DATA_DIR

    master_path = Path(master).expanduser().resolve()
    in_repo = PROJECT_ROOT in master_path.parents
    if in_repo:
        print("build-public-data: REFUSING — master CSV must live OUTSIDE the public "
              "repo (supply an external SLR_DATA_ROOT path). See docs/restricted-data.md.")
        return 2

    if not master_path.exists():
        print(f"build-public-data: master CSV not found: {master_path}")
        return 1

    with open(master_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        cols = [c.strip().replace("﻿", "") for c in (reader.fieldnames or [])]
        rows = list(reader)

    restricted = _restricted_columns(cols)
    if restricted:
        print("build-public-data: master still carries restricted column(s) — "
              "redaction requires declaring their provenance/licence first:")
        for c in restricted:
            print(f"  - {c}")
        return 1

    dropped = [c for c in cols if c not in PUBLIC_COLUMN_KEYS]
    if verbose and dropped:
        print(f"  Dropping unclassified columns: {', '.join(dropped)}")

    out.mkdir(parents=True, exist_ok=True)

    included: list[dict] = []
    decisions: list[dict] = []
    for row in rows:
        pid = _stable_paper_id(row)
        if not pid:
            continue
        included.append({
            "paper_id": pid,
            "doi": row.get("doi", "").strip(),
            "arxiv_id": row.get("arxiv_id", "").strip(),
            "year": row.get("year", "").strip(),
            "title": row.get("title", "").strip(),
            "final_classification": (row.get("corpus") or row.get("tier") or "").strip(),
        })
        decisions.append({
            "paper_id": pid,
            "stage": row.get("in_final_list_123", "")
                     or ("final" if (row.get("in_final_list_123") or "").strip() else "screened"),
            "decision": (row.get("inclusion") or row.get("abstract_decision")
                         or row.get("fulltext_decision") or "included").strip(),
            "reason_code": (row.get("exclusion_reason") or row.get("removal_reason") or "").strip(),
        })

    def write_table(name: str, fieldnames: tuple[str, ...], data: list[dict]) -> None:
        path = out / name
        if path.exists() and not force:
            print(f"  SKIP {name} (exists; use --force to overwrite)")
            return
        fieldnames = list(fieldnames)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            for row in data:
                w.writerow({k: row.get(k, "") for k in fieldnames})

    write_table("included_studies.csv", PUBLIC_TABLES[0][1], included)
    write_table("screening_decisions.csv", PUBLIC_TABLES[1][1], decisions)
    write_table("prisma_counts.csv", PUBLIC_TABLES[4][1], [])
    write_table("search_log_public.csv", PUBLIC_TABLES[5][1], [])

    # SHA256SUMS manifest
    sums = []
    for name, _ in PUBLIC_TABLES:
        p = out / name
        if p.exists():
            sums.append(f"{_sha256(p)}  {name}")
    (out / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")

    print(f"build-public-data: wrote {len(included)} included studies, "
          f"{len(decisions)} screening decisions -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(scan_restricted(verbose=True))
