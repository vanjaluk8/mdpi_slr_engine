"""
Multi-engine dispatcher.  fetch_paper() tries each requested engine in order
and concatenates references + citations from all that succeed, preserving each
paper's source_engine tag.  Cross-engine deduplication is intentionally deferred
to the write layer (core._add): both a Scopus row and an SS row for the same
paper are written in the first pass, giving the full uncapped SS citation list
alongside Scopus metadata.  In later passes, core._add skips SS entries whose
DOI is already covered by a Scopus row (loaded from the existing candidates CSV).
"""
from __future__ import annotations

import time

from slr_engine.config import SLEEP_S
from slr_engine.models import Paper


def fetch_paper(
    paper_id: str,
    engines: list[str],
    scopus_key: str = "",
    ieee_key: str = "",
    title: str = "",
    alt_id: str = "",
) -> Paper | None:
    """
    Fetch a paper using one or more engines.
    Scopus metadata takes precedence for the root paper object when both succeed.
    References and citations are concatenated from all engines without cross-engine
    dedup — each entry keeps its source_engine tag so core._add can handle it.
    alt_id (e.g. an arXiv ID) is preferred for Semantic Scholar lookups so that
    no title-search fallback is needed.
    IEEE Xplore provides metadata only (no references/citations); pair with "ss".
    """
    from slr_engine.engines import semantic_scholar, scopus, acl_anthology, ieee_xplore  # local import avoids circulars

    scopus_result: Paper | None = None
    ss_result:     Paper | None = None
    acl_result:    Paper | None = None
    ieee_result:   Paper | None = None

    if "acl" in engines:
        acl_result = acl_anthology.fetch(paper_id)

    if "scopus" in engines:
        if not scopus_key:
            print("  [Scopus] No API key — skipping Scopus engine")
        else:
            data = scopus.fetch(paper_id, scopus_key, title=title)
            time.sleep(SLEEP_S)
            if data:
                for r in data.references: r.source_engine = "scopus"
                for c in data.citations:  c.source_engine = "scopus"
                scopus_result = data

    if "ieee" in engines:
        if not ieee_key:
            print("  [IEEE] No API key — skipping IEEE Xplore engine")
        else:
            data = ieee_xplore.fetch(paper_id, ieee_key, title=title)
            time.sleep(SLEEP_S)
            if data:
                ieee_result = data

    if "ss" in engines:
        ss_id = alt_id if alt_id.lower().startswith("arxiv:") else paper_id
        data = semantic_scholar.fetch(ss_id, title=title)
        time.sleep(SLEEP_S)
        if data:
            for r in data.references: r.source_engine = "ss"
            for c in data.citations:  c.source_engine = "ss"
            ss_result = data

    # Build ordered results: Scopus first so its metadata wins for the root paper.
    # IEEE result is used only for metadata enrichment (citation_count) when Scopus
    # is absent — it contributes no references/citations.
    results: list[Paper] = []
    if scopus_result:
        results.append(scopus_result)
    elif ieee_result:
        results.append(ieee_result)
    if acl_result:
        results.append(acl_result)
    if ss_result:
        results.append(ss_result)

    if not results:
        return None
    if len(results) == 1:
        return results[0]

    # Merge: Scopus result is the base; refs + cits from other engines are
    # appended as-is (no cross-engine dedup).  core._add handles dedup at write time.
    merged = results[0]
    for extra in results[1:]:
        merged.references.extend(extra.references)
        merged.citations.extend(extra.citations)

    return merged
