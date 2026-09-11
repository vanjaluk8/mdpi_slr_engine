"""
enrich_and_filter.py — end-to-end enrichment + relevance filtering pipeline.

Usage
-----
    python enrich_and_filter.py [INPUT_CSV]

    INPUT_CSV  path to merged CSV (default: latest 05_merged_*.csv; falls back
               to 04_included_*.csv if no merged file exists).

Pipeline
--------
  1. Semantic Scholar batch API  → abstract + s2FieldsOfStudy
     (skipped for rows that already have an abstract, e.g. from undermind)
  2. OpenAlex API                → keywords / topics for rows still missing them
     (skipped for rows that already have keywords)
  3. Relevance filter            → remove off-topic / malformed entries
     Pre-validated rows (source_engine ∈ {undermind, seed}) bypass domain
     filters and are always kept unless the title is malformed / non-paper.

Outputs (written to data/snowball_output/04_enrich/)
----------------------------------------------
  06_enriched_<date>.csv  — all rows with abstract + keywords filled
  07_filtered_<date>.csv  — kept rows + relevance_decision + removal_reason
  07_excluded_<date>.csv  — removed rows (audit)
  .enrich_cache.json      — resume-safe API cache (keyed by SS ID / OA DOI)
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

import requests
import urllib3

# ── Bootstrap ─────────────────────────────────────────────────────────────────
# slr_engine.config loads .env and defines the single output root (data/snowball_output/).
# Enrichment reads the merged list from 03_merge/ (or 04_included from
# 02_screening/ as a fallback) and writes 06/07_* always to 04_enrich/.
from slr_engine.config import ENRICH_DIR, MERGE_DIR, SCREENING_DIR

SSL_VERIFY: bool = os.environ.get("REQUESTS_SSL_VERIFY", "1").lower() not in ("0", "false", "no")
if not SSL_VERIFY:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── Resolve input file ─────────────────────────────────────────────────────────
def _resolve_input(arg: str | None) -> Path:
    if arg:
        p = Path(arg)
        if not p.is_absolute():
            p = Path.cwd() / p
        if not p.exists():
            sys.exit(f"ERROR: file not found: {p}")
        return p
    # Prefer 05_merged_*.csv (post-merge) in 03_merge/; fall back to
    # 04_included_*.csv (unmerged) in 02_screening/.
    for base, pattern in ((MERGE_DIR, "05_merged_*.csv"),
                          (SCREENING_DIR, "04_included_*.csv")):
        candidates = sorted(base.glob(pattern), reverse=True)
        candidates = [c for c in candidates if not any(
            tag in c.name for tag in ("enriched", "filtered", "excluded")
        )]
        if candidates:
            return candidates[0]
    sys.exit("ERROR: no 05_merged_*.csv (03_merge/) or 04_included_*.csv (02_screening/) found")

# Module globals, resolved lazily by _bootstrap() so importing this module is
# side-effect free (the CLI imports it and only invokes main() on demand).
INPUT_CSV: Path
OUT_DIR: Path
ENRICHED: Path
FILTERED: Path
DEPRIORITIZED: Path
EXCLUDED: Path
CACHE_FILE: Path
cache: dict[str, dict] = {}


def _bootstrap(input_arg: str | None) -> None:
    """Resolve input + output paths and load the resume cache.

    Called once at the top of main(); keeps import side-effect free.
    """
    global INPUT_CSV, OUT_DIR, ENRICHED, FILTERED, DEPRIORITIZED, EXCLUDED
    global CACHE_FILE, cache

    INPUT_CSV = _resolve_input(input_arg)
    OUT_DIR   = ENRICH_DIR
    STEM      = INPUT_CSV.stem           # e.g. "05_merged_2026-04-10"

    # Extract date suffix for sequential output naming
    _dm = re.search(r"\d{4}-\d{2}-\d{2}", STEM)
    _DATE = _dm.group(0) if _dm else STEM

    ENRICHED      = OUT_DIR / f"06_enriched_{_DATE}.csv"
    FILTERED      = OUT_DIR / f"07_filtered_{_DATE}.csv"
    DEPRIORITIZED = OUT_DIR / f"07_deprioritized_{_DATE}.csv"
    EXCLUDED      = OUT_DIR / f"07_excluded_{_DATE}.csv"
    CACHE_FILE    = OUT_DIR / ".enrich_cache.json"

    print(f"Input : {INPUT_CSV}")
    print(f"Outputs: {ENRICHED.name}, {FILTERED.name}, {DEPRIORITIZED.name}, {EXCLUDED.name}")
    print()

    # ── Cache ──────────────────────────────────────────────────────────────────
    cache = {}
    if CACHE_FILE.exists():
        with open(CACHE_FILE) as f:
            cache = json.load(f)
        print(f"Loaded cache: {len(cache)} entries.")


def _save_cache() -> None:
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f)

# ── HTTP session ───────────────────────────────────────────────────────────────
_session = requests.Session()
_session.verify = SSL_VERIFY

# ══════════════════════════════════════════════════════════════════════════════
# PHASE 1 — Semantic Scholar batch API
# ══════════════════════════════════════════════════════════════════════════════
SS_BATCH_URL  = "https://api.semanticscholar.org/graph/v1/paper/batch"
SS_FIELDS     = "abstract,s2FieldsOfStudy,tldr"
SS_BATCH_SIZE = 500
SS_SLEEP      = 3      # seconds between batch calls
SS_BACKOFF    = 70     # seconds after a 429

def _parse_ss_result(item: dict | None) -> tuple[str, str]:
    """SS API response → (abstract_str, keywords_str)."""
    if not item:
        return "", ""
    abstract = item.get("abstract") or ""
    s2fos    = item.get("s2FieldsOfStudy") or []
    cats     = list({d["category"] for d in s2fos if d.get("category")})
    tldr_obj = item.get("tldr") or {}
    tldr     = tldr_obj.get("text", "") if isinstance(tldr_obj, dict) else ""
    if not abstract and tldr:
        abstract = f"[TLDR] {tldr}"
    return abstract, "; ".join(sorted(cats))


def enrich_semantic_scholar(rows: list[dict]) -> None:
    """Fill cache for rows that have a SS paper ID and no abstract yet."""
    ids_needed = list(dict.fromkeys(
        r["ss_paper_id"] for r in rows
        if r.get("ss_paper_id")
        and r["ss_paper_id"] not in cache
        and not r.get("abstract", "").strip()   # skip if abstract already present
    ))
    if not ids_needed:
        print("Phase 1 — SS: all IDs already cached, skipping.")
        return

    print(f"Phase 1 — Semantic Scholar batch: {len(ids_needed)} IDs to fetch …")
    batches = [ids_needed[i:i+SS_BATCH_SIZE] for i in range(0, len(ids_needed), SS_BATCH_SIZE)]
    for i, batch in enumerate(batches, 1):
        print(f"  Batch {i}/{len(batches)} ({len(batch)} IDs) …", end=" ", flush=True)
        while True:
            try:
                resp = _session.post(
                    SS_BATCH_URL,
                    params={"fields": SS_FIELDS},
                    json={"ids": batch},
                    timeout=40,
                )
                if resp.status_code == 429:
                    print(f"429, sleeping {SS_BACKOFF}s …", end=" ", flush=True)
                    time.sleep(SS_BACKOFF)
                    continue
                resp.raise_for_status()
                hits = 0
                for req_id, item in zip(batch, resp.json()):
                    abstract, keywords = _parse_ss_result(item)
                    cache[req_id] = {"abstract": abstract, "keywords": keywords}
                    if abstract or keywords:
                        hits += 1
                _save_cache()
                print(f"done ({hits}/{len(batch)} with content).")
                break
            except Exception as e:
                print(f"ERROR: {e}")
                _save_cache()
                break
        if i < len(batches):
            time.sleep(SS_SLEEP)


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 2 — OpenAlex (for rows still missing keywords after SS)
# ══════════════════════════════════════════════════════════════════════════════
OA_BASE   = "https://api.openalex.org/works"
OA_SLEEP  = 0.12
OA_BACKOFF = 30

_session.headers.update({"User-Agent": "SLR-enrichment/1.0 (peft-p2p-adapters)"})


def _reconstruct_abstract(inv_idx: dict | None) -> str:
    if not inv_idx:
        return ""
    pairs: list[tuple[int, str]] = []
    for word, positions in inv_idx.items():
        for pos in positions:
            pairs.append((pos, word))
    pairs.sort()
    return " ".join(w for _, w in pairs)


def _oa_key(doi: str) -> str:
    return f"oa:{doi}"


def _fetch_openalex(doi: str) -> tuple[str, str]:
    """Returns (abstract, keywords_str) from OpenAlex."""
    key = _oa_key(doi)
    if key in cache:
        return cache[key]["abstract"], cache[key]["keywords"]
    if doi.startswith("10.48550"):          # arXiv DOI alias — SS already handles these
        cache[key] = {"abstract": "", "keywords": ""}
        return "", ""
    while True:
        try:
            resp = _session.get(
                f"{OA_BASE}/doi:{doi}",
                params={"select": "abstract_inverted_index,keywords,topics,concepts"},
                timeout=20,
            )
            if resp.status_code == 429:
                print(f"  [OA 429] sleeping {OA_BACKOFF}s …")
                time.sleep(OA_BACKOFF)
                continue
            if resp.status_code in (404, 422):
                cache[key] = {"abstract": "", "keywords": ""}
                return "", ""
            resp.raise_for_status()
            d        = resp.json()
            abstract = _reconstruct_abstract(d.get("abstract_inverted_index"))
            kws      = [k["display_name"] for k in (d.get("keywords")  or []) if k.get("score", 0) >= 0.3]
            topics   = [t["display_name"] for t in (d.get("topics")    or [])[:3]]
            concepts = [c["display_name"] for c in (d.get("concepts")  or []) if c.get("score", 0) >= 0.4]
            keywords = "; ".join(dict.fromkeys(kws + topics + concepts))
            cache[key] = {"abstract": abstract, "keywords": keywords}
            return abstract, keywords
        except Exception as e:
            print(f"  [OA ERROR] doi:{doi}: {e}")
            cache[key] = {"abstract": "", "keywords": ""}
            return "", ""


def enrich_openalex(rows: list[dict]) -> None:
    """Fill keywords (and abstract when available) for rows still missing them.
    Skips rows that already have both abstract and keywords populated."""
    targets = [
        r for r in rows
        if not r.get("keywords", "").strip()
        and not r.get("abstract", "").strip()   # skip if abstract already present
        and r.get("doi", "").strip()
        and _oa_key(r["doi"]) not in cache
    ]
    if not targets:
        print("Phase 2 — OpenAlex: all targets already cached, skipping.")
        return

    print(f"Phase 2 — OpenAlex: {len(targets)} DOIs to fetch …")
    fetched = 0
    for n, r in enumerate(targets, 1):
        abstract, keywords = _fetch_openalex(r["doi"])
        if abstract or keywords:
            fetched += 1
        if n % 50 == 0 or n == len(targets):
            _save_cache()
            print(f"  [{n}/{len(targets)}] {fetched} enriched so far")
        time.sleep(OA_SLEEP)
    _save_cache()
    print(f"  Done — {fetched}/{len(targets)} enriched from OpenAlex.")


# ══════════════════════════════════════════════════════════════════════════════
# MERGE cache → rows
# ══════════════════════════════════════════════════════════════════════════════

def _merge(rows: list[dict]) -> None:
    """Write abstract + keywords into each row dict, combining SS and OA cache entries.

    Also sets ``enrichment_sources`` — a semicolon-separated note of which
    external sources actually contributed data for this paper:
      acl              — paper metadata came from ACL Anthology (source_engine=acl)
      semantic_scholar — SS provided abstract or keywords
      openalex         — OpenAlex provided abstract or keywords
    """
    for r in rows:
        r.setdefault("abstract", "")
        r.setdefault("keywords", "")
        r.setdefault("enrichment_sources", "")

        sources: list[str] = []

        # ACL Anthology: source of paper metadata, not abstract/keywords
        if r.get("source_engine", "").strip().lower() == "acl":
            sources.append("acl")

        ss_key = r.get("ss_paper_id", "")
        doi    = r.get("doi", "").strip()

        # Collect all available entries (SS then OA — order matters for abstract priority)
        ss_entry = cache.get(ss_key) if ss_key else None
        oa_entry = cache.get(_oa_key(doi)) if doi else None

        for label, entry in [("semantic_scholar", ss_entry), ("openalex", oa_entry)]:
            if not entry:
                continue
            contributed = False
            # Abstract: take first non-empty value found
            if not r["abstract"].strip() and entry.get("abstract", "").strip():
                r["abstract"] = entry["abstract"]
                contributed = True
            # Keywords: accumulate all unique values
            new_kw = entry.get("keywords", "").strip()
            if new_kw:
                existing = r["keywords"].strip()
                if new_kw not in existing:
                    r["keywords"] = "; ".join(filter(None, [existing, new_kw]))
                    contributed = True
            if contributed:
                sources.append(label)

        r["enrichment_sources"] = "; ".join(sources)


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 3 — Relevance filtering
# ══════════════════════════════════════════════════════════════════════════════

# A) NLP / LLM domain — unconditional KEEP
NLP_KEEP = re.compile(
    r"natural language|language model|\bllm\b|large language|\bnlp\b"
    r"|text classification|question answer|named entity|machine translation"
    r"|text generation|sentiment|summariz|dialogue|chatbot"
    r"|\bbert\b|\bgpt\b|\bt5\b|\bllama\b|\bgpt-?\d|\broberta\b|\bbloom\b"
    r"|token|tokeniz|sentence embed|reading comprehension"
    r"|instruction.tun|chat.?\bfine|conversational",
    re.I,
)

# B) Systems / distributed / federated — unconditional KEEP
SYS_KEEP = re.compile(
    r"federat(?:ed)?\s+(?:learn|fine|train|lora|adapt)"
    r"|distribut(?:ed)?\s+(?:train|infer|fine|learn|serv)"
    r"|p2p\b|peer.to.peer"
    r"|multi.tenant|lora\s+serv|serv(?:ing)?\s+lora|lora.*infer.*system"
    r"|decentrali[sz]|split\s+(?:learn|infer|fed)"
    r"|edge\s+(?:deploy|infer|comput|llm)|fog\s+comput"
    r"|multi.lora|adapter\s+rout|rout.*adapter"
    r"|communication.efficient.*(?:lora|adapt|fine)"
    r"|heterogeneous.*(?:lora|adapt|fed)|(?:fed|distributed).*lora",
    re.I,
)

# B2) MoE — only relevant when co-occurring with NLP/LLM context
MOE_FLAG = re.compile(r"mixture.of.expert|moe.*(?:lora|adapt)|(?:lora|adapt).*moe", re.I)
MOE_NLP  = re.compile(
    r"language model|llm|large language|nlp|text|token|natural language"
    r"|lora.*(?:serv|infer|routing)|routing.*lora|expert.*lora",
    re.I,
)

# C) Adapter library / discovery / reuse — KEEP even if benchmarked on CV
ADAPTER_LIBRARY_KEEP = re.compile(
    r"adapter\s+(?:retriev|discover|librar|assembl|sourc|market)"
    r"|lora\s+(?:retriev|librar|sourc|recycl)"
    r"|library\s+of\s+(?:lora|adapter)"
    r"|modular\s+(?:llm|model|adapter)"
    r"|adapter\s+reuse|lora\s+reuse|recycling\s+lora"
    r"|(?:lora|adapter)\s+rank\s+(?:allocat|assign)",
    re.I,
)

# D) Hard CV/domain removes — always removed unless NLP_KEEP fires first
CV_HARD = re.compile(
    r"scanning electron microscop|electron microscop"
    r"|bronze inscription|cultural heritage.*(?:recogni|classif)"
    r"|skin lesion|dermatolog|histopath|chest.?x.ray"
    r"|cardiac\s+(?:mri|segment)|medical\s+(?:image|segment|diagnos)"
    r"|fundus|ecg|eeg|patholog\b"
    r"|bronze.*(?:recogni|classif)|inscription\s+recogni"
    r"|racial slur|hate speech detect"
    r"|ship design|urban\s+foundation\s+model",
    re.I,
)

# E) Soft CV removes — removed only if B/B2/C also miss
CV_SOFT = re.compile(
    r"action recogni|pose estimat|video correspond|video understand"
    r"|no.reference\s+image\s+quality|image\s+quality\s+assess"
    r"|object detect|human\s+(?:pose|action)|gait recogni"
    r"|text.to.image\s+diffusion|stable\s+diffusion|diffusion\s+model.*image"
    r"|image.?video\s+(?:generat|synthes)"
    r"|point cloud|lidar"
    r"|misinformation detect|fake news detect",
    re.I,
)

# F) Garbled / non-paper titles
MALFORMED_TITLE = re.compile(
    r"^(?:of\s+concurrent|we\s+want\s+to|s-lora\s+random|different\s+baselines"
    r"|:\s*multi|^\.\s+multi"
    r"|s-lora\s*:?\s*serving\s+thou.{0,5}sands?of"
    r"|2022\.\s+lora|2023\.\s+(?:adapter|punica)|2024\.\s+(?:choice|moelora|punica|roselora)"
    r"|aws\s+blogs"
    r"|ppt:\s+pre-trained\s+prompt\s+tuning\s+for\s+few.{0,15}learn.*proceedings)",
    re.I,
)

# G) Blog / software documentation (not peer-reviewed)
BLOG_FLAG = re.compile(
    r"^aws\s+blogs"
    r"|^peft:\s+(?:state-of-the-art\s+parameter.efficient|parameter.efficient\s+fine-tuning\s+methods?\s*$)"
    r"|^peft:\s+parameter.efficient\s+fine-tuning\s+methods?\s*$",
    re.I,
)

# H) No-DOI + no-content rescue: known relevant paper titles
TITLE_KEEP_NODOI = re.compile(
    r"modular\s+llm|library\s+of\s+lora|model\s+moerging|moerging"
    r"|\bdora\b|weight.decomposed\s+low.rank"
    r"|lora.drop|lora.*reuse|reuse.*lora"
    r"|turbotrans|pagedattention|kv.cache"
    r"|vb.lora|ensembles\s+of.*(?:lora|adapter)"
    r"|compass.*(?:peft|multilingual)|compeft"
    r"|multi.head\s+adapter|cross.task\s+generali"
    r"|federated\s+residual|federat.*low.rank"
    r"|beyond\s+zero\s+init",
    re.I,
)


# ══════════════════════════════════════════════════════════════════════════════
# VENUE QUALITY CLASSIFICATION
#
# Assigns a venue_quality tag used in PRISMA quality assessment:
#   top_venue   — CORE A* conferences or Scopus/WoS Q1 journals in AI/ML/NLP
#   peer_reviewed — other indexed conferences and journals
#   preprint    — arXiv-only with no published venue
#   unknown     — venue missing or unrecognised
# ══════════════════════════════════════════════════════════════════════════════

# CORE A* + top-tier NLP/ML/systems conferences (case-insensitive substring match)
_TOP_CONF_TOKENS: set[str] = {
    "iclr", "neurips", "nips", "icml", "acl", "emnlp", "naacl", "eacl", "aacl",
    "mlsys", "aaai", "ijcai", "osdi", "sosp", "nsdi", "eurosys", "usenix",
    "sigcomm", "coling", "findings of acl", "findings of emnlp",
    "machine learning and systems", "international conference on machine learning",
    "annual meeting of the association",
    "conference on empirical methods",
    "north american chapter",
    "advances in neural information processing",
    "international conference on learning representations",
}

# Q1 journals (Scopus/WoS) in AI, ML, NLP, CS systems
_TOP_JOUR_TOKENS: set[str] = {
    "transactions on machine learning research", "tmlr",
    "journal of machine learning research", "jmlr",
    "transactions on pattern analysis", "tpami",
    "transactions on neural networks", "tnnls",
    "ieee transactions on knowledge",
    "future generation computer systems",
    "expert systems with applications",
    "knowledge-based systems",
    "information sciences",
    "artificial intelligence",
    "neural networks",
    "neurocomputing",
    "pattern recognition",
    "journal of artificial intelligence research", "jair",
    "computational linguistics",
    "transactions of the association for computational linguistics", "tacl",
}

_ARXIV_RE = re.compile(r"^(?:arxiv|corr|preprint)\b", re.I)


def _venue_quality(venue: str, doi: str = "") -> str:
    """Return a quality tier for the venue string."""
    if not venue.strip():
        return "unknown"
    vl = venue.strip().lower()
    # arXiv-only preprint
    if _ARXIV_RE.match(vl) or (not vl and doi.startswith("10.48550")):
        return "preprint"
    # Top-tier conference
    for tok in _TOP_CONF_TOKENS:
        if tok in vl:
            return "top_venue"
    # Q1 journal
    for tok in _TOP_JOUR_TOKENS:
        if tok in vl:
            return "top_venue"
    return "peer_reviewed"


_PREVALIDATED_ENGINES = {"undermind", "seed"}


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 3b — arXiv recency-adjusted relevance filter
#
# Logic: if a paper is an arXiv-only preprint (no published venue / real DOI)
# AND its citation count is below a year-adjusted threshold
# AND it lacks a strong Tier-1 signal (PEFT + systems both present)
# → deprioritise rather than hard-exclude (goes to 07_deprioritized_*.csv)
#
# Tier-1 papers (PEFT + systems/distributed signal) stay unconditionally.
# Pre-validated corpus rows (source_engine ∈ {undermind, seed}) are never
# touched by this filter.
# ══════════════════════════════════════════════════════════════════════════════

_ARXIV_VENUE_RE = re.compile(r"^(?:arxiv|corr|preprint)\b", re.I)

# Year-adjusted citation thresholds.  Papers from before 2021 are presumed to
# have accumulated citations on their published record — skip filtering them.
_ARXIV_CITE_THRESH: dict[int, int] = {
    2021: 20,
    2022: 10,
    2023: 5,
    2024: 3,
    2025: 2,
}

_TIER1_PEFT_RE = re.compile(
    r"\b(?:adapter|lora|peft|low.rank.adapt(?:ation)?"
    r"|prefix.tun(?:ing)?|prompt.tun(?:ing)?|ia3|bitfit"
    r"|parameter.efficient)\b",
    re.I,
)


def _is_arxiv_preprint(r: dict) -> bool:
    """True when the record is an arXiv preprint with no published venue."""
    if not r.get("arxiv_id", "").strip():
        return False
    doi   = r.get("doi",   "").strip()
    venue = r.get("venue", "").strip()
    # A real DOI (not the 10.48550 arXiv alias) means it was published elsewhere.
    if doi and not doi.startswith("10.48550"):
        return False
    # A non-empty venue that doesn't look like arXiv/CoRR → published.
    if venue and not _ARXIV_VENUE_RE.match(venue):
        return False
    return True


def _below_arxiv_threshold(r: dict) -> bool:
    """True when citation_count is below the year-appropriate threshold."""
    try:
        year = int(r.get("year") or 0)
    except (ValueError, TypeError):
        year = 0
    if year < 2021:
        return False                        # old enough — citations settled
    threshold = _ARXIV_CITE_THRESH.get(year, 2)
    try:
        count = int(r.get("citation_count") or 0)
    except (ValueError, TypeError):
        count = 0
    return count < threshold


def _is_tier1_topic(text: str) -> bool:
    """Tier 1: PEFT signal AND systems/distributed signal both present."""
    return bool(_TIER1_PEFT_RE.search(text) and SYS_KEEP.search(text))


def _assign_tier(r: dict) -> int:
    """
    Classify a kept paper into one of three relevance tiers:
      Tier 1 — PEFT + systems/distributed + NLP/LLM  (core intersection)
      Tier 2 — PEFT + NLP/LLM                         (PEFT-focused, no systems angle)
      Tier 3 — everything else that passed the filter  (foundational / peripheral)
    """
    text = f"{r.get('title','')} {r.get('abstract','')} {r.get('keywords','')}"
    has_peft = bool(_TIER1_PEFT_RE.search(text))
    has_sys  = bool(SYS_KEEP.search(text))
    has_nlp  = bool(NLP_KEEP.search(text))
    if has_peft and has_sys and has_nlp:
        return 1
    if has_peft and has_nlp:
        return 2
    return 3


def _classify(r: dict) -> tuple[str, str]:
    title    = r.get("title", "")
    abstract = r.get("abstract", "")
    keywords = r.get("keywords", "")
    doi      = r.get("doi", "").strip()
    text     = f"{title} {abstract} {keywords}"

    # Data-quality checks apply to all rows regardless of source
    if MALFORMED_TITLE.search(title.strip()):
        return "REMOVE", "malformed_title"

    if BLOG_FLAG.search(title.strip()):
        return "REMOVE", "non_paper_source"

    # Pre-validated papers (undermind corpus + G0 seeds) bypass domain filters —
    # they were curated by targeted queries and reviewed for relevance.
    if r.get("source_engine", "") in _PREVALIDATED_ENGINES:
        return "KEEP", "prevalidated"

    if NLP_KEEP.search(text):
        return "KEEP", "nlp_signal"

    if ADAPTER_LIBRARY_KEEP.search(text):
        return "KEEP", "adapter_library"

    if CV_HARD.search(text):
        return "REMOVE", "off_topic_domain"

    if SYS_KEEP.search(text):
        return "KEEP", "systems_signal"

    if MOE_FLAG.search(text) and MOE_NLP.search(text):
        return "KEEP", "moe_systems_signal"

    if CV_SOFT.search(text):
        return "REMOVE", "off_topic_domain"

    if not doi and not abstract.strip() and not keywords.strip():
        if TITLE_KEEP_NODOI.search(title):
            return "KEEP", "known_relevant_title"
        return "REMOVE", "unverifiable_no_doi"

    return "KEEP", "peft_foundational"


def filter_rows(rows: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Returns (keep, remove, deprioritized).

    deprioritized — arXiv preprints whose citation count is below the
    year-adjusted threshold AND which lack a Tier-1 topic signal
    (PEFT + systems).  These are written to 07_deprioritized_*.csv rather
    than the main filtered output, but are NOT hard-excluded so they can be
    rescued manually if needed.
    """
    keep, remove, deprioritized = [], [], []
    for r in rows:
        dec, reason = _classify(r)
        r["relevance_decision"] = dec
        r["removal_reason"]     = reason if dec == "REMOVE" else ""

        if dec == "REMOVE":
            remove.append(r)
        elif (
            r.get("source_engine", "") not in _PREVALIDATED_ENGINES
            and _is_arxiv_preprint(r)
            and _below_arxiv_threshold(r)
        ):
            text = f"{r.get('title','')} {r.get('abstract','')} {r.get('keywords','')}"
            if _is_tier1_topic(text):
                r["tier"] = _assign_tier(r)
                keep.append(r)
            else:
                r["relevance_decision"] = "DEPRIORITIZE"
                r["removal_reason"]     = "arxiv_peripheral_low_citation"
                r["tier"] = _assign_tier(r)
                deprioritized.append(r)
        else:
            r["tier"] = _assign_tier(r)
            keep.append(r)

    return keep, remove, deprioritized


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main(input_arg: str | None = None) -> None:
    _bootstrap(input_arg)

    # ── Load CSV ──────────────────────────────────────────────────────────────
    with open(INPUT_CSV, newline="", encoding="utf-8") as f:
        reader    = csv.DictReader(f)
        orig_cols = list(reader.fieldnames or [])
        rows      = list(reader)
    print(f"Loaded {len(rows)} rows from {INPUT_CSV.name}\n")

    # Add enrichment columns if not already present
    for col in ("abstract", "keywords", "enrichment_sources"):
        if col not in orig_cols:
            for r in rows:
                r[col] = ""

    # ── Phase 1: Semantic Scholar ─────────────────────────────────────────────
    enrich_semantic_scholar(rows)
    _merge(rows)

    # ── Phase 2: OpenAlex ─────────────────────────────────────────────────────
    print()
    enrich_openalex(rows)
    _merge(rows)

    # ── Write enriched CSV ────────────────────────────────────────────────────
    enrich_cols = orig_cols + [c for c in ("abstract", "keywords", "enrichment_sources") if c not in orig_cols]
    with open(ENRICHED, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=enrich_cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    n_abs = sum(1 for r in rows if r.get("abstract", "").strip())
    n_kw  = sum(1 for r in rows if r.get("keywords", "").strip())
    print(f"\nEnriched CSV: {ENRICHED.name}  "
          f"({n_abs}/{len(rows)} abstracts, {n_kw}/{len(rows)} keywords)\n")

    # ── Phase 3: Relevance filtering ──────────────────────────────────────────
    print("Phase 3 — Relevance filtering …")
    keep_rows, remove_rows, deprio_rows = filter_rows(rows)

    # Removal order: DOI-present first, then no-DOI
    doi_removed    = [r for r in remove_rows if r.get("doi", "").strip()]
    no_doi_removed = [r for r in remove_rows if not r.get("doi", "").strip()]
    excluded       = doi_removed + no_doi_removed

    reason_counts = Counter(r["removal_reason"] for r in excluded)
    print(f"  KEEP: {len(keep_rows)}   DEPRIORITIZE: {len(deprio_rows)}   REMOVE: {len(excluded)}")
    print(f"  Removal breakdown: {dict(reason_counts)}")

    if deprio_rows:
        print(f"\n  Deprioritized arXiv preprints ({len(deprio_rows)}):")
        print(f"  {'year':>4}  {'cites':>5}  title")
        print("  " + "-" * 80)
        for r in sorted(deprio_rows, key=lambda x: int(x.get("year") or 0), reverse=True):
            yr = r.get("year", "?")
            ct = r.get("citation_count", "?") or "0"
            print(f"  {yr:>4}  {ct:>5}  {r['title'][:65]}")

    print()
    print(f"  {'reason':<25}  {'DOI':>3}  title")
    print("  " + "-" * 90)
    for r in excluded:
        d = "Y" if r.get("doi", "").strip() else "N"
        print(f"  {r['removal_reason']:<25}  {d}    {r['title'][:75]}")

    # ── Venue quality annotation ──────────────────────────────────────────────
    for r in keep_rows + deprio_rows + excluded:
        r.setdefault("venue_quality", "")
        if not r["venue_quality"]:
            r["venue_quality"] = _venue_quality(r.get("venue", ""), r.get("doi", ""))

    vq_counts = Counter(r["venue_quality"] for r in keep_rows)
    print(f"\n  Venue quality (kept): {dict(sorted(vq_counts.items(), key=lambda x: -x[1]))}")

    tier_counts = Counter(r.get("tier", 3) for r in keep_rows)
    print(f"  Tier distribution  : {dict(sorted(tier_counts.items()))}")

    # Sort kept rows by tier (ascending) then citation count (descending)
    keep_rows.sort(key=lambda r: (int(r.get("tier") or 3), -int(r.get("citation_count") or 0)))

    # ── Write filtered CSVs ───────────────────────────────────────────────────
    out_cols = enrich_cols + ["relevance_decision", "removal_reason", "venue_quality", "tier"]
    with open(FILTERED, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(keep_rows)

    # Excluded rows don't get tier (they were removed); set a blank so DictWriter is happy
    for r in excluded:
        r.setdefault("tier", "")

    with open(DEPRIORITIZED, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(deprio_rows)

    with open(EXCLUDED, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(excluded)

    print("\nSaved:")
    print(f"  {FILTERED.name:<40}  {len(keep_rows)} rows")
    print(f"  {DEPRIORITIZED.name:<40}  {len(deprio_rows)} rows  (arXiv peripheral/low-citation)")
    print(f"  {EXCLUDED.name:<40}  {len(excluded)} rows")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
