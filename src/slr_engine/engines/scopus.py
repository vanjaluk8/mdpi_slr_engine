"""Elsevier Scopus API engine."""
from __future__ import annotations

import time

import urllib3
import requests

from slr_engine.config import SCOPUS_BASE, MAX_CITS, SLEEP_S, SSL_VERIFY
from slr_engine.models import Paper
from slr_engine.engines.semantic_scholar import resolve_doi

# Module-level session so SSL_VERIFY is applied uniformly across all calls.
_session = requests.Session()
_session.verify = SSL_VERIFY
if not SSL_VERIFY:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def fetch(paper_id: str, api_key: str, title: str = "") -> Paper | None:
    """
    Fetch paper + references + citations from the Scopus API.
    Resolves arXiv IDs to DOIs via Semantic Scholar first.
    Falls back to title search when DOI resolution fails and a title is given.
    """
    headers = {"X-ELS-APIKey": api_key, "Accept": "application/json"}

    # Step 1: resolve to DOI; fall back to title search if unavailable
    doi = resolve_doi(paper_id)
    if not doi:
        if title:
            print(f"  [Scopus] No DOI for {paper_id} — trying title search")
            return _search_by_title(title, headers)
        print(f"  [Scopus] Cannot resolve DOI for {paper_id} — skipping Scopus")
        return None

    # Step 2: abstract (standard view — FULL requires elevated access)
    paper = None
    try:
        r = _session.get(
            f"{SCOPUS_BASE}/abstract/doi/{doi}",
            headers=headers,
            timeout=20,
        )
        if r.status_code == 429:
            print("  [Scopus 429] Rate limited — sleeping 15s")
            time.sleep(15)
            return fetch(paper_id, api_key, title)
        if r.status_code == 401:
            print(f"  [Scopus 401] Unauthorized — key={api_key[:8]}… body={r.text[:200]}")
            return None
        if r.status_code == 404:
            # Common for 10.5555/ ACM-DL mirror DOIs (PMLR/JMLR) — try Search API fallback
            print(f"  [Scopus 404] Abstract endpoint has no entry for DOI={doi} — trying search fallback")
            paper = _search_by_doi(doi, headers)
            if paper is None:
                if title:
                    print(f"  [Scopus] DOI search also failed — trying title search")
                    return _search_by_title(title, headers)
                print(f"  [Scopus 404] {doi} not found in Scopus — skipping")
                return None
        elif not r.ok:
            print(f"  [Scopus {r.status_code}] Abstract fetch failed for DOI={doi}: {r.text[:120]}")
            if title:
                print(f"  [Scopus] Falling back to title search")
                return _search_by_title(title, headers)
            return None
        else:
            core     = r.json().get("abstracts-retrieval-response", {})
            coredata = core.get("coredata", {})
            paper    = _entry_to_paper(coredata)

            refs_raw = _ensure_list(
                core.get("item", {})
                    .get("bibrecord", {})
                    .get("tail", {})
                    .get("bibliography", {})
                    .get("reference")
            )
            paper.references = [_ref_to_paper(ref) for ref in refs_raw]
    except Exception as e:
        print(f"  [Scopus ERROR] {paper_id}: {e}")
        return None

    time.sleep(SLEEP_S)

    # Step 3: forward citations — search for papers citing this EID
    eid = paper.paper_id
    if eid:
        paper.citations = _fetch_citations(eid, headers, title=paper.title)

    return paper


# ── Private helpers ───────────────────────────────────────────────────────────

def _fetch_citations(eid: str, headers: dict, title: str = "") -> list:
    """
    Fetch forward citations for a given Scopus EID.
    Primary: REFEID(eid).  Fallback: REFTITLE(title) when REFEID fails or returns nothing.
    """
    results = _citations_by_refeid(eid, headers)
    if results:
        return results
    if title:
        print(f"  [Scopus cits] REFEID returned nothing — trying REFTITLE fallback")
        return _citations_by_reftitle(title, headers)
    return []


def _citations_by_refeid(eid: str, headers: dict) -> list:
    try:
        r = _session.get(
            f"{SCOPUS_BASE}/search/scopus",
            headers=headers,
            params={
                "query": f"REFEID({eid})",
                "count": min(MAX_CITS, 25),  # Scopus Starter caps all search queries at 25
                "start": 0,
                "field": "eid,prism:doi,dc:title,dc:creator,prism:coverDate,"
                         "prism:publicationName,citedby-count,author",
            },
            timeout=20,
        )
        if not r.ok:
            print(f"  [Scopus cits {r.status_code}] REFEID({eid}) failed: {r.text[:200]}")
            return []
        entries = r.json().get("search-results", {}).get("entry", [])
        if not entries or entries[0].get("error"):
            print(f"  [Scopus cits] REFEID({eid}) → 0 results")
            return []
        papers = [_entry_to_paper(e) for e in entries]
        print(f"  [Scopus cits] REFEID({eid}) → {len(papers)} citations")
        return papers
    except Exception as e:
        print(f"  [Scopus cits ERROR] REFEID({eid}): {e}")
    return []


def _citations_by_reftitle(title: str, headers: dict) -> list:
    try:
        r = _session.get(
            f"{SCOPUS_BASE}/search/scopus",
            headers=headers,
            params={
                "query": f'REFTITLE("{title}")',
                "count": min(MAX_CITS, 25),  # Scopus Starter caps REFTITLE at 25
                "start": 0,
                "field": "eid,prism:doi,dc:title,dc:creator,prism:coverDate,"
                         "prism:publicationName,citedby-count,author",
            },
            timeout=20,
        )
        if not r.ok:
            print(f"  [Scopus cits {r.status_code}] REFTITLE query failed: {r.text[:200]}")
            return []
        entries = r.json().get("search-results", {}).get("entry", [])
        if not entries or entries[0].get("error"):
            print(f"  [Scopus cits] REFTITLE(\"{title[:50]}\") → 0 results")
            return []
        papers = [_entry_to_paper(e) for e in entries]
        print(f"  [Scopus cits] REFTITLE(\"{title[:50]}\") → {len(papers)} citations")
        return papers
    except Exception as e:
        print(f"  [Scopus cits ERROR] REFTITLE: {e}")
    return []


def _search_by_title(title: str, headers: dict) -> "Paper | None":
    """
    Fallback: search Scopus by title when DOI resolution fails.
    Also fetches forward citations via EID.
    """
    try:
        r = _session.get(
            f"{SCOPUS_BASE}/search/scopus",
            headers=headers,
            params={
                "query": f'TITLE("{title}")',
                "count": 1,
                "field": "eid,prism:doi,dc:title,dc:creator,prism:coverDate,"
                         "prism:publicationName,citedby-count,author",
            },
            timeout=20,
        )
        if not r.ok:
            print(f"  [Scopus title search {r.status_code}] {r.text[:120]}")
            return None
        entries = r.json().get("search-results", {}).get("entry", [])
        if not entries or entries[0].get("error"):
            print(f"  [Scopus] No title match: {title[:60]}")
            return None
        paper = _entry_to_paper(entries[0])
        print(f"  [Scopus] Title search matched: {paper.title[:70]}")
        eid = paper.paper_id
        if eid:
            paper.citations = _fetch_citations(eid, headers, title=paper.title)
        return paper
    except Exception as e:
        print(f"  [Scopus title search ERROR] {e}")
        return None


def _search_by_doi(doi: str, headers: dict) -> "Paper | None":
    """
    Fallback for DOIs not found via the abstract retrieval endpoint.
    Uses the Scopus Search API (DOI(...) query) to locate the paper's EID.
    References are unavailable this way; forward citations are fetched in Step 3.
    """
    try:
        r = _session.get(
            f"{SCOPUS_BASE}/search/scopus",
            headers=headers,
            params={
                "query": f"DOI({doi})",
                "count": 1,
                "field": "eid,prism:doi,dc:title,dc:creator,prism:coverDate,"
                         "prism:publicationName,citedby-count,author",
            },
            timeout=20,
        )
        if not r.ok:
            return None
        entries = r.json().get("search-results", {}).get("entry", [])
        if not entries or entries[0].get("error"):
            return None
        return _entry_to_paper(entries[0])
    except Exception as e:
        print(f"  [Scopus search fallback ERROR] DOI={doi}: {e}")
        return None


def _entry_to_paper(entry: dict) -> Paper:
    eid   = entry.get("eid", entry.get("prism:eid", ""))
    doi   = entry.get("prism:doi", entry.get("doi", ""))
    title = entry.get("dc:title", entry.get("title", ""))
    cover = entry.get("prism:coverDate", entry.get("year", "")) or ""
    year  = cover[:4]
    venue = entry.get("prism:publicationName", entry.get("venue", ""))
    tc    = int(entry.get("citedby-count", 0) or 0)

    authors = []
    for a in (entry.get("author") or []):
        if isinstance(a, dict):
            authors.append(a.get("authname", a.get("ce:indexed-name", "")))
    if not authors and entry.get("dc:creator"):
        authors = [entry["dc:creator"]]

    return Paper(
        paper_id      = eid,
        eid           = eid,
        doi           = doi,
        title         = title,
        authors       = authors,
        year          = year,
        venue         = venue,
        citation_count= tc,
    )


def _ref_to_paper(ref: dict) -> Paper:
    ref_info = ref.get("ref-info", {})

    doi = eid = ""
    for item in _ensure_list(ref_info.get("refd-itemidlist", {}).get("itemid")):
        if isinstance(item, dict):
            id_type = item.get("@idtype", "")
            val     = item.get("$", "")
            if id_type == "DOI":
                doi = val
            elif id_type == "SGR":
                eid = f"2-s2.0-{val}"

    title_obj = ref_info.get("ref-title", {})
    title = title_obj.get("ref-titletext", "") if isinstance(title_obj, dict) else ""
    year  = str((ref_info.get("ref-publicationyear") or {}).get("@first", "") or "")

    authors = []
    for a in _ensure_list((ref_info.get("ref-authors") or {}).get("author")):
        if isinstance(a, dict):
            authors.append(a.get("ce:indexed-name", ""))

    return Paper(
        paper_id = eid,
        eid      = eid,
        doi      = doi,
        title    = title,
        authors  = authors,
        year     = year,
        venue    = ref_info.get("ref-sourcetitle") or "",
    )


def _ensure_list(val) -> list:
    if val is None:
        return []
    return val if isinstance(val, list) else [val]
