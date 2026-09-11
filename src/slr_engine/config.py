"""Constants, paths, .env loading, and API key reads."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

# Load .env from the project root, then from the package dir (for safety).
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass  # dotenv not installed — rely on environment variables or CLI flags

# ── API base URLs ─────────────────────────────────────────────────────────────
SS_BASE     = "https://api.semanticscholar.org/graph/v1/paper"
SCOPUS_BASE = "https://api.elsevier.com/content"
WOS_BASE    = "https://api.clarivate.com/apis/wos-starter/v1"
IEEE_BASE   = "https://ieeexploreapi.ieee.org/api/v1"
# https://api.elsevier.com/content/search/scopus

# ── Rate-limit settings ───────────────────────────────────────────────────────
MAX_REFS = 100   # max references (backward) per paper
MAX_CITS = 100   # max citations  (forward)  per paper
SLEEP_S  = 1.5   # polite delay between API calls (seconds)

# ── Repository paths ──────────────────────────────────────────────────────────
# Layout (src/slr_engine/config.py):
#
#   <project root>/            this file is at <root>/src/slr_engine/config.py,
#   <project root>/src/        so parents[2] = project root
#   <root>/data/               restricted input/output tree (NOT in the public
#                              release — supplied externally via SLR_DATA_ROOT)
#
# This is a PUBLIC code release. Restricted input data (curated G0-G6 corpus,
# raw vendor exports, API caches) must NOT be committed here. An authorized
# researcher supplies them at runtime by pointing SLR_DATA_ROOT at an external,
# access-controlled path (see docs/restricted-data.md). If unset, the pipeline
# falls back to a repo-local <root>/data which will not exist in a fresh public
# clone — stages then fail with a clear "supply SLR_DATA_ROOT" message.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

_DATA_ROOT = os.environ.get("SLR_DATA_ROOT", "").strip()
DATA_DIR   = Path(_DATA_ROOT).expanduser().resolve() if _DATA_ROOT else PROJECT_ROOT / "data"

PAPERS_REPO = DATA_DIR / "inputs"            # curated G0-G6 corpus (machine input)
# (The canonical, hand-maintained copy of the corpus lives alongside the
#  manuscript; data/inputs/ is the self-contained snapshot the pipeline reads.)

# Optional output-tree override. If the SLR_OUTPUT_DIR env var is set, the whole
# pipeline writes under that root instead of the default data/snowball_output +
# data/figures — every stage, figures, and `verify` redirect automatically
# (verify_common.py honours the same var). This lets you run an isolated,
# non-destructive pipeline run without touching your existing results:
#   bash run_pipeline.sh --out /path/to/scratch
_OUTPUT_OVERRIDE = os.environ.get("SLR_OUTPUT_DIR", "").strip()

if _OUTPUT_OVERRIDE:
    OUTPUT_DIR  = Path(_OUTPUT_OVERRIDE).expanduser().resolve()
    FIGURES_DIR = OUTPUT_DIR / "figures"      # figures land with the run's outputs
else:
    OUTPUT_DIR  = DATA_DIR / "snowball_output"
    FIGURES_DIR = DATA_DIR / "figures"        # output of `slr-engine figures`

# ── Output paths (relative to OUTPUT_DIR) ─────────────────────────────────────
# data/snowball_output/ is organised into one subfolder per pipeline stage so the
# workflow reads top-to-bottom (00_import -> 07_final). `snapshots/` holds the
# canonical, date-free S-files that prisma/figures and the reproducibility audit
# consume; `archive/` holds superseded dated copies, kept out of the way.
IMPORT_DIR      = OUTPUT_DIR / "00_import"        # 00_prevalidated_*.csv
RETRIEVAL_DIR   = OUTPUT_DIR / "01_retrieval"     # 01_raw_*, log_retrieval_*, M*/MANUAL_* vendor exports
SCREENING_DIR   = OUTPUT_DIR / "02_screening"     # 02_screened, 03_review_queue, 04_included, log_screening
MERGE_DIR       = OUTPUT_DIR / "03_merge"         # 05_merged_*.csv
ENRICH_DIR      = OUTPUT_DIR / "04_enrich"        # 06_enriched, 07_filtered, 07_excluded, 07_deprioritized
REVIEW_DIR      = OUTPUT_DIR / "05_review"        # 08_abstract_reviewed, 08_zotero_ready.ris, 09_fulltext_queue
EXTRACTION_DIR  = OUTPUT_DIR / "06_extraction"    # 10/11_data_extraction, 12_arxiv_*, vendor exports
FINAL_DIR       = OUTPUT_DIR / "07_final"         # 13_final_reading_list, PRISMA_summary, pdfs/
SNAPSHOTS_DIR   = OUTPUT_DIR / "snapshots"        # canonical S1..S7b + pipeline_unified.csv
ARCHIVE_DIR     = OUTPUT_DIR / "archive"          # superseded / duplicate dated copies

CANDIDATES_FILE = RETRIEVAL_DIR / f"01_raw_{date.today()}.csv"
LOG_FILE        = RETRIEVAL_DIR / f"log_retrieval_{date.today()}.json"

for _d in (OUTPUT_DIR, IMPORT_DIR, RETRIEVAL_DIR, SCREENING_DIR, MERGE_DIR,
           ENRICH_DIR, REVIEW_DIR, EXTRACTION_DIR, FINAL_DIR, SNAPSHOTS_DIR,
           ARCHIVE_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# All stage + snapshot subfolders, in workflow order. Used to locate an output
# file across stages without hardcoding which subfolder it lives in.
STAGE_DIRS = (IMPORT_DIR, RETRIEVAL_DIR, SCREENING_DIR, MERGE_DIR, ENRICH_DIR,
              REVIEW_DIR, EXTRACTION_DIR, FINAL_DIR, SNAPSHOTS_DIR)


def find_output(name: str) -> Path | None:
    """Locate a file (exact name or glob pattern) across every stage folder.

    Returns the newest match in workflow order, or None if not found.
    """
    for d in STAGE_DIRS:
        hits = sorted(d.glob(name), reverse=True)
        if hits:
            return hits[0]
    return None


# ── API keys (populated by CLI flags or env vars) ─────────────────────────────
SCOPUS_API_KEY     = os.environ.get("SCOPUS_API_KEY", "")
WOS_API_KEY        = os.environ.get("WOS_API_KEY", "")
IEEE_API_KEY       = os.environ.get("IEEE_API_KEY", "")
# Optional path to a local acl-anthology repo clone.
# If empty, acl-anthology auto-clones to a platform cache directory (~500 MB).
ACL_DATA_DIR       = os.environ.get("ACL_DATA_DIR", "")
# API key read from environment or .env (never hardcode a secret here).
ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL") or None  # None = SDK default
LLM_SCREEN_MODEL   = os.environ.get("LLM_SCREEN_MODEL", "claude-haiku-4-5-20251001")

# ── TLS/SSL settings ──────────────────────────────────────────────────────────
# Set REQUESTS_SSL_VERIFY=0 in .env or environment to disable SSL verification
# (workaround for institutional proxies with self-signed certificates).
SSL_VERIFY: bool = os.environ.get("REQUESTS_SSL_VERIFY", "1").lower() not in ("0", "false", "no")
