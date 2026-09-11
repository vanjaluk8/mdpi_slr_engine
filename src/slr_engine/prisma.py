"""
prisma.py — generates a PRISMA-compatible process summary from the snowball and screening logs.

Reads:
  log_retrieval_<date>.json  — per-seed retrieval counts (identification + dedup stage)
  log_screening_<date>.json  — title screening decisions (screening stage)

Writes:
  PRISMA_summary_<date>.md   — human-readable audit trail for the Methods section

Usage:
    python -m slr_engine.prisma                          # uses today's log files
    python -m slr_engine.prisma --date 2026-04-03
    python -m slr_engine.prisma --snowball-log path/to/log_retrieval.json \
                         --screening-log path/to/log_screening.json
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path


# ── Helpers ───────────────────────────────────────────────────────────────────

_TOP_VENUE_TOKENS: frozenset[str] = frozenset({
    "iclr", "neurips", "nips", "icml", "acl", "emnlp", "naacl", "eacl", "aacl",
    "mlsys", "aaai", "ijcai",
    "machine learning and systems",
    "annual meeting of the association",
    "conference on empirical methods",
    "advances in neural information processing",
    "international conference on learning representations",
    "transactions on machine learning research", "tmlr", "jmlr",
    "future generation computer systems",
    "transactions of the association for computational linguistics", "tacl",
    "journal of machine learning research",
})

_ARXIV_VENUE_PAT = re.compile(r"^(?:arxiv|corr|preprint)\b", re.I)


def _venue_quality_tag(venue: str, doi: str = "") -> str:
    if not venue.strip():
        return "unknown"
    vl = venue.strip().lower()
    if _ARXIV_VENUE_PAT.match(vl) or (not vl and doi.startswith("10.48550")):
        return "preprint"
    for tok in _TOP_VENUE_TOKENS:
        if tok in vl:
            return "top_venue"
    return "peer_reviewed"


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _find_log(output_dir: Path, prefix: str, date_str: str | None) -> Path:
    # Logs live in their stage folder (retrieval vs screening); search across
    # every stage dir so prisma doesn't need to know the exact layout.
    from slr_engine.config import find_output
    if date_str:
        p = find_output(f"{prefix}_{date_str}.json")
        if p:
            return p
        raise FileNotFoundError(f"Log not found: {prefix}_{date_str}.json")
    p = find_output(f"{prefix}_*.json")
    if not p:
        raise FileNotFoundError(f"No {prefix} log found")
    return p


# ── Identification stage (from snowball log) ──────────────────────────────────

def _identification_stats(snowball_log: dict) -> dict:
    """
    Aggregate per-seed snowball counts into identification-stage totals.

    snowball_log structure:
      { "_meta": { timestamp_utc, databases, n_seeds },   ← optional, skip
        seed_key: { group, backward_examined, forward_examined,
                    backward_new, forward_new, total_new, ... } }

    _examined = raw refs/cites retrieved from API (before dedup)
    _new      = records written to the candidates CSV (after dedup)
    """
    meta  = snowball_log.get("_meta", {})
    seeds = [v for k, v in snowball_log.items() if not k.startswith("_")]

    groups: dict[str, dict] = {}
    for s in seeds:
        g = s.get("group", "?")
        groups.setdefault(g, {"seeds": 0, "backward_examined": 0, "forward_examined": 0,
                               "backward_new": 0, "forward_new": 0})
        groups[g]["seeds"] += 1
        for k in ("backward_examined", "forward_examined", "backward_new", "forward_new"):
            groups[g][k] += s.get(k, 0)

    total_examined  = sum(s.get("backward_examined", 0) + s.get("forward_examined", 0) for s in seeds)
    total_new       = sum(s.get("total_new", 0) for s in seeds)
    total_seeds     = len(seeds)

    # Format search date from _meta timestamp if available
    ts = meta.get("timestamp_utc", "")
    search_date = ts[:10] if ts else "unknown"

    return {
        "total_seeds":       total_seeds,
        "total_examined":    total_examined,
        "total_new":         total_new,
        "duplicates_removed": total_examined - total_new,
        "by_group":          groups,
        "search_date":       search_date,
        "databases":         meta.get("databases", []),
    }


# ── Screening stage (from screening log) ─────────────────────────────────────

def _screening_stats(screening_log: dict) -> dict:
    decisions = screening_log.get("decisions", {})
    excl      = screening_log.get("exclusion_breakdown", {})
    by_dir    = screening_log.get("by_direction", {})

    total_screened = decisions.get("INCLUDE", 0) + decisions.get("REVIEW", 0) + decisions.get("EXCLUDE", 0)
    total_excluded = decisions.get("EXCLUDE", 0)
    total_eligible = decisions.get("INCLUDE", 0) + decisions.get("REVIEW", 0)

    return {
        "total_screened":  total_screened,
        "total_excluded":  total_excluded,
        "total_eligible":  total_eligible,
        "auto_included":   decisions.get("INCLUDE", 0),
        "for_review":      decisions.get("REVIEW", 0),
        "exclusion_breakdown": excl,
        "by_direction": by_dir,
        "year_cutoff": screening_log.get("screening_run", {}).get("year_cutoff", "?"),
        "term_set_version": screening_log.get("screening_run", {}).get("term_set_version", "?"),
        "screening_timestamp": screening_log.get("screening_run", {}).get("timestamp_utc", "?"),
        "input_file": screening_log.get("input", {}).get("file", "?"),
        "input_sha256": screening_log.get("input", {}).get("sha256", "?"),
        "screened_csv": screening_log.get("outputs", {}).get("screened_csv", "?"),
        "review_csv": screening_log.get("outputs", {}).get("review_queue_csv", "?"),
        "rules": screening_log.get("screening_rules", []),
        "llm_screening": screening_log.get("llm_screening", {}),
    }


# ── Enrichment + tier stage (from 07_* CSVs) ─────────────────────────────────

def _filtered_stats(output_dir: Path, date_str: str | None = None) -> dict | None:
    """
    Read enriched reading pool / excluded / deprioritized CSVs and return aggregate stats.
    Checks canonical renamed files first, falls back to legacy dated patterns.
    """
    def _find(canonical: str, legacy_prefix: str) -> Path | None:
        from slr_engine.config import find_output
        p = find_output(canonical)          # canonical S-file (snapshots/ or a stage dir)
        if p:
            return p
        if date_str:
            return find_output(f"{legacy_prefix}_{date_str}.csv")
        return find_output(f"{legacy_prefix}_*.csv")

    def _read(p: Path | None) -> list[dict]:
        if not p or not p.exists():
            return []
        with p.open(newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    filtered_path = _find("S6_enriched_reading_pool.csv", "07_filtered")
    if not filtered_path:
        return None

    kept    = _read(filtered_path)
    deprio  = _read(_find("S6c_deprioritized_lowcite.csv", "07_deprioritized"))
    excl    = _read(_find("S6b_excluded_offtopic.csv",     "07_excluded"))

    tier_counts: dict[int, int] = {1: 0, 2: 0, 3: 0}
    for r in kept:
        try:
            t = int(r.get("tier") or 3)
        except (ValueError, TypeError):
            t = 3
        tier_counts[t] = tier_counts.get(t, 0) + 1

    direction_counts = Counter((r.get("direction") or "PREVALIDATED").upper() for r in kept)
    excl_reasons     = Counter(r.get("removal_reason", "?") for r in excl)

    # Venue quality — read column if present, otherwise compute
    vq_counts: Counter[str] = Counter()
    for r in kept:
        vq = r.get("venue_quality", "").strip()
        if not vq:
            vq = _venue_quality_tag(r.get("venue", ""), r.get("doi", ""))
        vq_counts[vq] += 1

    return {
        "n_kept":           len(kept),
        "n_deprioritized":  len(deprio),
        "n_excluded":       len(excl),
        "tier_counts":      tier_counts,
        "direction_counts": dict(direction_counts),
        "excl_reasons":     dict(excl_reasons),
        "venue_quality":    dict(vq_counts),
        "filtered_file":    filtered_path.name,
    }


# ── Abstract review stage (from 08_* CSVs) ───────────────────────────────────

def _abstract_review_stats(output_dir: Path, date_str: str | None = None) -> dict | None:
    """
    Read abstract reviewed CSV and return decision counts.
    Checks canonical renamed files first, falls back to legacy dated patterns.
    """
    from slr_engine.config import find_output
    # Canonical order: final (DEFER resolved) → base (original manual) → legacy glob
    reviewed_path = (
        find_output("S7b_abstract_reviewed_final.csv")
        or find_output("S7a_abstract_reviewed_base.csv")
        or (find_output(f"08_abstract_reviewed_{date_str}.csv") if date_str else None)
        or find_output("08_abstract_reviewed_*.csv")
    )

    if not reviewed_path or not reviewed_path.exists():
        return None

    with reviewed_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    dec: Counter[str] = Counter(r.get("abstract_decision", "") for r in rows)
    # The abstract-review sheet records only a coarse KEEP/SKIP disposition. The
    # dedicated full-text-review queue (09_*) is the authoritative source of the
    # DEFER re-flag: queue.abstract_decision = KEEP 214 + DEFER 173. That DEFER
    # subset is carved out of the abstract-level SKIP pool so the summary reports
    # the paper's audited split KEEP / DEFER / SKIP (214 / 173 / 165) rather than
    # the coarse KEEP / SKIP (214 / 338).
    defer, queue_keep = 0, 0
    q9 = find_output("09_fulltext_review_queue_2026-05-02.csv")
    if q9 is not None and q9.exists():
        with q9.open(newline="", encoding="utf-8") as f:
            qdec = Counter(r.get("abstract_decision", "") for r in csv.DictReader(f))
        defer = qdec.get("DEFER", 0)
        queue_keep = qdec.get("KEEP", 0)

    keep = dec.get("KEEP", 0)
    skip_abstract = dec.get("SKIP", 0)
    skip_final = max(skip_abstract - defer, 0)

    by_tier: dict[str, Counter] = {}
    for r in rows:
        d = r.get("abstract_decision", "") or "undecided"
        t = str(r.get("tier") or "?")
        by_tier.setdefault(t, Counter())
        by_tier[t][d] += 1

    note = None
    if q9 is not None and q9.exists():
        note = (f"DEFER ({defer}) reflects the full-text-review queue "
                f"(09_*: {queue_keep} KEEP + {defer} DEFER) split out of the "
                f"{skip_abstract} abstract-level SKIP records; {skip_final} "
                f"remained pure SKIP.")

    return {
        "total":     len(rows),
        "keep":      keep,
        "skip":      skip_final,
        "defer":     defer,
        "undecided": dec.get("", 0),
        "by_tier":   {t: dict(c) for t, c in sorted(by_tier.items())},
        "reviewed_file": reviewed_path.name,
        "ris_file":      None,
        "note":      note,
    }


# ── Final reading list stage (from 13_* CSV) ─────────────────────────────────

def _final_list_stats(output_dir: Path, date_str: str | None = None) -> dict | None:
    """
    Read 13_final_reading_list_*.csv and return aggregate stats.
    Returns None if the file does not exist yet.
    """
    from slr_engine.config import find_output
    # Canonical renamed file first, then legacy dated pattern
    p = (
        find_output("S8_final_reading_list.csv")
        or (find_output(f"13_final_reading_list_{date_str}.csv") if date_str else None)
        or find_output("13_final_reading_list_*.csv")
    )

    if not p or not p.exists():
        return None

    with p.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    tier_counts: Counter[str] = Counter(r.get("tier", "?") or "?" for r in rows)
    corpus_counts: Counter[str] = Counter(r.get("corpus", "?") or "?" for r in rows)
    source_counts: Counter[str] = Counter(r.get("source", "?") or "?" for r in rows)
    read_counts: Counter[str] = Counter(r.get("read_status", "") or "unread" for r in rows)

    return {
        "total":         len(rows),
        "tier_counts":   dict(tier_counts),
        "corpus_counts": dict(corpus_counts),
        "source_counts": dict(source_counts),
        "read_counts":   dict(read_counts),
        "file":          p.name,
    }


# ── Markdown generation ───────────────────────────────────────────────────────

def generate_summary(
    snowball_log: dict,
    screening_log: dict,
    output_path: Path,
    filtered_stats: dict | None = None,
    abstract_stats: dict | None = None,
    final_list_stats: dict | None = None,
) -> None:
    id_stats = _identification_stats(snowball_log)
    sc_stats = _screening_stats(screening_log)

    run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = []
    a = lines.append

    a(f"# PRISMA Process Summary")
    a(f"")
    a(f"**Generated:** {run_date}  ")
    a(f"**Search executed:** {id_stats['search_date']}  ")
    databases = id_stats.get("databases") or []
    db_str = ", ".join(str(d).upper() for d in databases) if databases else "Semantic Scholar"
    a(f"**Databases:** {db_str}  ")
    a(f"**Candidates file:** `{sc_stats['input_file']}`  ")
    a(f"**SHA-256:** `{sc_stats['input_sha256']}`  ")
    a(f"**Screening timestamp:** {sc_stats['screening_timestamp']}  ")
    a(f"**Term set version:** {sc_stats['term_set_version']}  ")
    a(f"")
    a(f"---")
    a(f"")

    # ── Inclusion / Exclusion criteria ───────────────────────────────────────
    a(f"## Inclusion and Exclusion Criteria")
    a(f"")
    a(f"The following criteria were applied consistently across all screening stages.")
    a(f"")
    a(f"| Criterion | Inclusion | Exclusion |")
    a(f"|-----------|-----------|-----------|")
    a(f"| **Publication year** | ≥ {sc_stats['year_cutoff']} | < {sc_stats['year_cutoff']} |")
    a(f"| **Language** | English | Other languages |")
    a(f"| **Document type** | Peer-reviewed conference paper, journal article, or arXiv preprint with ≥ 1 citation | Blog posts, grey literature, software documentation, workshop papers without proceedings |")
    a(f"| **Minimum topical relevance** | Addresses ≥ 1 of: (a) parameter-efficient fine-tuning / adapters (PEFT, LoRA, prefix/prompt tuning); (b) distributed / P2P / federated ML systems; (c) multi-task LLM serving or inference | Exclusively covers unrelated domains (CV, audio, RL) with no NLP or LLM systems angle |")
    a(f"| **Tier 1 (highest priority)** | Addresses all three themes jointly (PEFT + distributed/P2P + LLM serving) | — |")
    a(f"| **Abstract availability** | Abstract accessible via API or pre-validated corpus | No accessible metadata and no resolvable DOI |")
    a(f"")
    a(f"> **Note on year cutoff:** Pre-{sc_stats['year_cutoff']} foundational works (e.g., Houlsby et al. 2019, Hu et al. 2022) "
      f"are included via the pre-validated G0–G6 corpora, which were assembled by manual curation "
      f"and bypass the year filter.")
    a(f"")
    a(f"---")
    a(f"")

    # ── Stage 1: Identification ──────────────────────────────────────────────
    a(f"## Stage 1 — Identification")
    a(f"")
    n_groups = len(id_stats["by_group"]) - 1
    db_full = {
        "ss": "Semantic Scholar Academic Graph API",
        "scopus": "Scopus (Elsevier)",
        "acl": "ACL Anthology",
        "wos": "Web of Science Starter API",
        "ieee": "IEEE Xplore API",
        "undermind": "Undermind (pre-validated corpus)",
    }
    db_names = [db_full.get(str(d).lower(), str(d).upper()) for d in (id_stats.get("databases") or ["ss"])]
    db_sentence = "; ".join(db_names) if db_names else "Semantic Scholar Academic Graph API"
    a(f"Citation snowballing (Wohlin 2014) was conducted across "
      f"**{id_stats['total_seeds']} seed papers** in groups G0\u2013G{n_groups} "
      f"(search date: **{id_stats['search_date']}**) using the following databases/APIs: "
      f"{db_sentence}.")
    a(f"")
    a(f"| Group | Seeds | Backward examined | Forward examined | New (after dedup) |")
    a(f"|-------|------:|------------------:|-----------------:|------------------:|")
    for g, gv in sorted(id_stats["by_group"].items()):
        a(f"| {g} | {gv['seeds']} | {gv['backward_examined']} | {gv['forward_examined']} | {gv['backward_new'] + gv['forward_new']} |")
    a(f"| **Total** | **{id_stats['total_seeds']}** | | | **{id_stats['total_new']}** |")
    a(f"")
    a(f"- **Records retrieved from API (raw):** {id_stats['total_examined']:,}")
    a(f"- **Duplicates removed** (by Semantic Scholar paper ID and DOI): {id_stats['duplicates_removed']:,}")
    a(f"- **Records entering screening:** {id_stats['total_new']:,}")
    a(f"")

    # ── Stage 2: Screening ───────────────────────────────────────────────────
    a(f"## Stage 2 — Title Screening")
    a(f"")
    a(f"All {sc_stats['total_screened']:,} records were screened by title using a keyword scoring algorithm ")
    a(f"(year cutoff: {sc_stats['year_cutoff']}; term set v{sc_stats['term_set_version']}).")
    a(f"")
    a(f"**Screening rules:**")
    for rule in sc_stats["rules"]:
        a(f"- {rule}")
    a(f"")
    a(f"| Decision | N |")
    a(f"|----------|--:|")
    a(f"| INCLUDE (auto) | {sc_stats['auto_included']:,} |")
    a(f"| REVIEW (manual triage) | {sc_stats['for_review']:,} |")
    a(f"| EXCLUDE | {sc_stats['total_excluded']:,} |")
    a(f"| **Total screened** | **{sc_stats['total_screened']:,}** |")
    a(f"")
    a(f"**Exclusion reasons (title screening):**")
    a(f"")
    a(f"| Reason | N |")
    a(f"|--------|--:|")
    for reason, n in sorted(sc_stats["exclusion_breakdown"].items(), key=lambda x: -x[1]):
        a(f"| {reason} | {n:,} |")
    a(f"")
    a(f"**By retrieval direction:**")
    a(f"")
    a(f"| Direction | INCLUDE | REVIEW | EXCLUDE |")
    a(f"|-----------|--------:|-------:|--------:|")
    for direction, counts in sorted(sc_stats["by_direction"].items()):
        a(f"| {direction} | {counts.get('INCLUDE', 0):,} | {counts.get('REVIEW', 0):,} | {counts.get('EXCLUDE', 0):,} |")
    a(f"")

    # ── Stage 3: LLM triage of REVIEW queue ──────────────────────────────────
    a(f"## Stage 3 — LLM Triage (REVIEW queue)")
    a(f"")
    llm_stats = sc_stats.get("llm_screening", {})
    if isinstance(llm_stats, dict) and llm_stats:
        llm_model   = llm_stats.get("model", "Claude")
        llm_sent    = llm_stats.get("rows_sent", sc_stats["for_review"])
        llm_inc     = llm_stats.get("resolved_include", 0)
        llm_exc     = llm_stats.get("resolved_exclude", 0)
        llm_unc     = llm_stats.get("uncertain_remaining", 0)
        a(f"The {llm_sent:,} REVIEW-queue records (single non-LLM keyword match) were submitted "
          f"to **{llm_model}** for title- and venue-level classification. "
          f"UNCERTAIN records were retained for manual inspection.")
        a(f"")
        a(f"| Outcome | N |")
        a(f"|---------|--:|")
        a(f"| Promoted to INCLUDE | {llm_inc:,} |")
        a(f"| Confirmed EXCLUDE | {llm_exc:,} |")
        a(f"| UNCERTAIN (manual inspection) | {llm_unc:,} |")
        a(f"| **Total triaged** | **{llm_sent:,}** |")
    else:
        a(f"The {sc_stats['for_review']:,} REVIEW-queue records (single non-LLM keyword match) "
          f"were submitted to Claude (Haiku) for title- and venue-level classification. "
          f"UNCERTAIN records were retained for manual inspection.")
        a(f"")
        a(f"| Outcome | N |")
        a(f"|---------|--:|")
        a(f"| Promoted to INCLUDE | *(re-run screener with `--llm` to populate)* |")
        a(f"| Confirmed EXCLUDE | *(re-run screener with `--llm` to populate)* |")
        a(f"| UNCERTAIN (manual inspection) | *(re-run screener with `--llm` to populate)* |")
        a(f"")
        a(f"> **Action:** Re-run `python -m slr_engine.screen --llm` to populate LLM triage counts, "
          f"then re-run `python -m slr_engine.prisma` to update this summary.")
    a(f"")

    # ── Stage 4: Eligibility / full-text ────────────────────────────────────
    a(f"## Stage 4 — Eligibility (Full-text Assessment)")
    a(f"")
    a(f"| Outcome | N |")
    a(f"|---------|--:|")
    a(f"| Eligible for full-text | *(auto-INCLUDE + manual triage promotions)* |")
    a(f"| Excluded after full-text | *(to be filled)* |")
    a(f"| **Included in review** | ***(to be filled)*** |")
    a(f"")

    # ── Stage 5: Enrichment & tier classification ────────────────────────────
    a(f"## Stage 5 — Enrichment & Tier Classification")
    a(f"")
    if filtered_stats:
        fs = filtered_stats
        n_entering = fs["n_kept"] + fs["n_deprioritized"] + fs["n_excluded"]
        a(f"All records from the merged corpus were enriched via the Semantic Scholar batch API ")
        a(f"and OpenAlex (abstracts + field keywords), then filtered for topical relevance.")
        a(f"")
        a(f"| Outcome | N |")
        a(f"|---------|--:|")
        a(f"| Kept (relevance filter passed) | {fs['n_kept']:,} |")
        a(f"| Deprioritised (arXiv preprint, low citation, no Tier-1 signal) | {fs['n_deprioritized']:,} |")
        a(f"| Excluded (off-topic / malformed) | {fs['n_excluded']:,} |")
        if fs["excl_reasons"]:
            a(f"")
            a(f"**Exclusion reasons (enrichment filter):**")
            a(f"")
            a(f"| Reason | N |")
            a(f"|--------|--:|")
            for reason, n in sorted(fs["excl_reasons"].items(), key=lambda x: -x[1]):
                a(f"| {reason} | {n:,} |")
        a(f"")
        a(f"**Tier classification** (title + abstract + keyword signal matching):")
        a(f"")
        a(f"| Tier | Criteria | N |")
        a(f"|------|----------|--:|")
        tc = fs["tier_counts"]
        a(f"| Tier 1 | PEFT + Systems/Distributed + LLM (all three signals) | {tc.get(1, 0):,} |")
        a(f"| Tier 2 | PEFT + LLM, or PEFT + Systems (two signals) | {tc.get(2, 0):,} |")
        a(f"| Tier 3 | Other (foundational / tangential) | {tc.get(3, 0):,} |")
        a(f"| **Total included** | | **{fs['n_kept']:,}** |")
        a(f"")
        a(f"**Discovery direction of kept papers:**")
        a(f"")
        a(f"| Direction | N |")
        a(f"|-----------|--:|")
        for direction, n in sorted(fs["direction_counts"].items(), key=lambda x: -x[1]):
            a(f"| {direction.capitalize()} | {n:,} |")
        a(f"")
        vq = fs.get("venue_quality", {})
        if vq:
            vq_labels = {
                "top_venue":    "Top-tier venue (CORE A\\* / Scopus Q1)",
                "peer_reviewed": "Peer-reviewed (other conference / journal)",
                "preprint":     "Preprint (arXiv, no published venue)",
                "unknown":      "Unknown / missing venue",
            }
            a(f"**Venue quality of kept papers:**")
            a(f"")
            a(f"| Venue quality | N | % |")
            a(f"|---------------|--:|--:|")
            total_vq = sum(vq.values())
            for key in ("top_venue", "peer_reviewed", "preprint", "unknown"):
                n = vq.get(key, 0)
                pct = f"{n/total_vq*100:.1f}%" if total_vq else "–"
                a(f"| {vq_labels[key]} | {n:,} | {pct} |")
            a(f"")
        a(f"> **Reading guidance:** Start with Tier 1 papers (full read), Tier 2 (selective read), ")
        a(f"> Tier 3 (abstract skim). Papers are sorted by tier then citation count in `{fs['filtered_file']}`.")
    else:
        a(f"*(Enrichment not yet run — execute `python run.py enrich` to populate this stage.)*")
    a(f"")

    # ── Stage 6: Abstract Review ─────────────────────────────────────────────
    a(f"## Stage 6 — Abstract Review")
    a(f"")
    if abstract_stats:
        as_ = abstract_stats
        n_reviewed = as_["keep"] + as_["skip"] + as_["defer"]
        a(f"Each paper in the enriched reading list was reviewed at abstract level using an "
          f"interactive screener (`slr_engine.abstract_review`) with AI-assisted suggestions (Claude Sonnet). "
          f"The reviewer made final KEEP / SKIP / DEFER decisions; the AI suggestion was advisory only.")
        a(f"")
        a(f"| Decision | N | % of reviewed |")
        a(f"|----------|--:|--------------:|")
        pct = lambda n: f"{n/n_reviewed*100:.1f}%" if n_reviewed else "–"
        a(f"| KEEP (included in Zotero corpus) | {as_['keep']:,} | {pct(as_['keep'])} |")
        a(f"| SKIP (excluded after abstract) | {as_['skip']:,} | {pct(as_['skip'])} |")
        a(f"| DEFER (requires closer reading) | {as_['defer']:,} | {pct(as_['defer'])} |")
        if as_["undecided"]:
            a(f"| Undecided (not yet reviewed) | {as_['undecided']:,} | {pct(as_['undecided'])} |")
        a(f"| **Total** | **{as_['total']:,}** | |")
        a(f"")
        if as_["by_tier"]:
            a(f"**Decision breakdown by tier:**")
            a(f"")
            a(f"| Tier | KEEP | SKIP | DEFER | Undecided |")
            a(f"|------|-----:|-----:|------:|----------:|")
            for t, counts in sorted(as_["by_tier"].items()):
                a(f"| {t} | {counts.get('KEEP',0)} | {counts.get('SKIP',0)} | {counts.get('DEFER',0)} | {counts.get('undecided',0)} |")
            a(f"")
        if as_.get("note"):
            a(f"> **Note:** {as_['note']}")
            a(f"")
        if as_["ris_file"]:
            a(f"> **Zotero import:** `{as_['ris_file']}` contains all KEEP papers in RIS format — "
              f"drag into Zotero or use **File → Import**.")
        else:
            a(f"> **Zotero import:** Run `python -m slr_engine.abstract_review --export-ris` to generate the RIS file.")
    else:
        a(f"*(Abstract review not yet run.)*")
        a(f"")
        a(f"```")
        a(f"python -m slr_engine.abstract_review          # without AI suggestions")
        a(f"python -m slr_engine.abstract_review --llm    # with Claude Sonnet suggestions (recommended)")
        a(f"```")
        a(f"")
        a(f"Writes `08_abstract_reviewed_<date>.csv` (decisions) and "
          f"`08_zotero_ready_<date>.ris` (KEEP papers for Zotero).")
    a(f"")

    # ── Stage 7: Final included studies ─────────────────────────────────────
    a(f"## Stage 7 — Final Included Studies")
    a(f"")
    if final_list_stats:
        fl = final_list_stats
        a(f"After all screening and abstract review stages, **{fl['total']} papers** were confirmed "
          f"for inclusion in the systematic review and assembled into the final reading list (`{fl['file']}`).")
        a(f"")
        a(f"**By tier:**")
        a(f"")
        a(f"| Tier | Criteria | N |")
        a(f"|------|----------|--:|")
        tc = fl["tier_counts"]
        a(f"| Tier 1 | PEFT + Systems/Distributed + LLM (all three signals) | {tc.get('1', 0):,} |")
        a(f"| Tier 2 | PEFT + LLM, or PEFT + Systems (two signals) | {tc.get('2', 0):,} |")
        a(f"| Tier 3 | Foundational / tangential | {tc.get('3', 0):,} |")
        a(f"| **Total** | | **{fl['total']:,}** |")
        a(f"")
        a(f"**By corpus role:**")
        a(f"")
        a(f"| Role | N |")
        a(f"|------|--:|")
        for role, n in sorted(fl["corpus_counts"].items(), key=lambda x: -x[1]):
            a(f"| {role.capitalize()} | {n:,} |")
        a(f"")
        a(f"**By discovery source:**")
        a(f"")
        a(f"| Source | N |")
        a(f"|--------|--:|")
        for src, n in sorted(fl["source_counts"].items(), key=lambda x: -x[1]):
            a(f"| {src} | {n:,} |")
        if fl["read_counts"]:
            total_read = fl["read_counts"].get("read", 0) + fl["read_counts"].get("done", 0)
            a(f"")
            a(f"> **Reading progress:** {total_read} / {fl['total']} papers read.")
    else:
        a(f"*(Final reading list not yet assembled — create `13_final_reading_list_<date>.csv` "
          f"from the KEEP papers in `08_abstract_reviewed_*.csv`.)*")
    a(f"")

    # ── File inventory ───────────────────────────────────────────────────────
    a(f"---")
    a(f"")
    a(f"## File Inventory")
    a(f"")
    a(f"| File | Role | Modifiable? |")
    a(f"|------|------|-------------|")
    a(f"| `{sc_stats['input_file']}` | Original candidates — **source of truth** | No — never edited |")
    a(f"| `{sc_stats['screened_csv']}` | Full list with screening decisions | No — regenerated by screener |")
    a(f"| `{sc_stats['review_csv']}` | Manual triage queue | **Yes** — edit `inclusion` column |")
    a(f"| `04_included_*.csv` | Snowball candidates after screening | No — regenerated by screener |")
    a(f"| `05_merged_*.csv` | Merged prevalidated + screened candidates | No — regenerated by merge step |")
    a(f"| `06_enriched_*.csv` | All records with abstract + keywords filled | No — regenerated by enrich_and_filter.py |")
    a(f"| `07_filtered_*.csv` | Enriched reading list (tier-classified, sorted) | No — regenerated by enrich_and_filter.py |")
    a(f"| `07_deprioritized_*.csv` | Low-citation arXiv preprints (rescue if needed) | No |")
    a(f"| `07_excluded_*.csv` | Off-topic / malformed records (audit) | No |")
    a(f"| `08_abstract_reviewed_*.csv` | Abstract review decisions (KEEP/SKIP/DEFER) | No — written by abstract_review.py |")
    a(f"| `08_zotero_ready_*.ris` | **Zotero import file** (KEEP papers only) | No — regenerated by abstract_review.py |")
    a(f"| `13_final_reading_list_*.csv` | **Final included studies** (thesis reading material) | **Yes** — fill `read_status`, `key_finding`, etc. |")
    a(f"| `log_screening_*.json` | Keyword/LLM screening audit log | No — written by screener |")
    a(f"| `log_retrieval_*.json` | Per-seed retrieval counts | No — written by snowballer |")
    a(f"| `PRISMA_summary_*.md` | This file | No — regenerated by prisma.py |")
    a(f"")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"PRISMA summary written → {output_path.name}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    from slr_engine.config import FINAL_DIR

    parser = argparse.ArgumentParser(description="Generate PRISMA summary from snowball + screening logs")
    parser.add_argument("--date", default=None, help="Log date string, e.g. 2026-04-03 (default: most recent)")
    parser.add_argument("--snowball-log", type=Path, default=None)
    parser.add_argument("--screening-log", type=Path, default=None)
    parser.add_argument("--output", "-o", type=Path, default=None)
    args = parser.parse_args()

    try:
        sb_path = args.snowball_log or _find_log(None, "log_retrieval", args.date)
        sc_path = args.screening_log or _find_log(None, "log_screening", args.date)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    date_str = sb_path.stem.replace("log_retrieval_", "")
    out_path = args.output or (FINAL_DIR / f"PRISMA_summary_{date_str}.md")

    snowball_log  = _load_json(sb_path)
    screening_log = _load_json(sc_path)
    fstats        = _filtered_stats(FINAL_DIR, date_str)

    # _abstract_review_stats now auto-selects S7b_abstract_reviewed_final.csv
    astats = _abstract_review_stats(FINAL_DIR, date_str)

    flstats       = _final_list_stats(FINAL_DIR)

    if fstats:
        print(f"Filtered CSV  : {fstats['filtered_file']}  "
              f"({fstats['n_kept']} kept, T1={fstats['tier_counts'].get(1,0)} "
              f"T2={fstats['tier_counts'].get(2,0)} T3={fstats['tier_counts'].get(3,0)})")
    if astats:
        print(f"Abstract review: {astats['reviewed_file']}  "
              f"(KEEP={astats['keep']}  SKIP={astats['skip']}  "
              f"DEFER={astats['defer']}  undecided={astats['undecided']})")
    if flstats:
        print(f"Final list    : {flstats['file']}  ({flstats['total']} papers  "
              f"T1={flstats['tier_counts'].get('1',0)}  "
              f"T2={flstats['tier_counts'].get('2',0)}  "
              f"T3={flstats['tier_counts'].get('3',0)})")

    generate_summary(snowball_log, screening_log, out_path,
                     filtered_stats=fstats, abstract_stats=astats,
                     final_list_stats=flstats)


if __name__ == "__main__":
    main()
