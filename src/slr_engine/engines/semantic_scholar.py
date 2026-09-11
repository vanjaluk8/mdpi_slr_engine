"""Semantic Scholar engine."""
from __future__ import annotations

import time
import warnings

import urllib3
import requests

from slr_engine.config import SS_BASE, SLEEP_S, SSL_VERIFY
from slr_engine.models import Paper

_session = requests.Session()
_session.verify = SSL_VERIFY
if not SSL_VERIFY:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def normalize_id(paper_id: str) -> str:
    """Convert arXiv:XXXX or bare DOI to Semantic Scholar lookup format."""
    pid = paper_id.strip()
    if pid.lower().startswith("arxiv:"):
        return pid
    if pid.startswith("10."):
        return f"DOI:{pid}"
    return pid


def fetch(paper_id: str, title: str = "") -> Paper | None:
    """Fetch paper metadata from Semantic Scholar including refs and citations.
    Falls back to title search if the ID-based lookup returns 404 and a title is given.
    """
    url = f"{SS_BASE}/{normalize_id(paper_id)}"
    params = {
        "fields": (
            "paperId,externalIds,title,authors,year,citationCount,venue,"
            "references.paperId,references.title,references.authors,references.year,"
            "references.venue,references.citationCount,references.externalIds,"
            "citations.paperId,citations.title,citations.authors,citations.year,"
            "citations.venue,citations.citationCount,citations.externalIds"
        ),
    }
    try:
        r = _session.get(url, params=params, timeout=20)
        if r.status_code == 404:
            print(f"  [SS 404] Not found by ID: {paper_id}")
            if title:
                print(f"  [SS] Falling back to title search: {title[:60]}")
                time.sleep(SLEEP_S)
                return search_by_title(title)
            return None
        if r.status_code == 429:
            print("  [SS 429] Rate limited — sleeping 10s")
            time.sleep(10)
            return fetch(paper_id, title)
        r.raise_for_status()
        return _data_to_paper(r.json())
    except Exception as e:
        print(f"  [SS ERROR] {paper_id}: {e}")
        return None


def search_by_title(title: str) -> Paper | None:
    """Search Semantic Scholar by title; resolves to full paper data via paperId."""
    try:
        r = _session.get(
            f"{SS_BASE}/search",
            params={"query": title, "fields": "paperId,title", "limit": 1},
            timeout=20,
        )
        if r.status_code == 429:
            print("  [SS 429] Rate limited — sleeping 10s")
            time.sleep(10)
            return search_by_title(title)
        r.raise_for_status()
        items = r.json().get("data", [])
        if not items:
            print(f"  [SS] No title match: {title[:60]}")
            return None
        found_id    = items[0]["paperId"]
        found_title = items[0].get("title", "")
        print(f"  [SS] Title search matched: {found_title[:70]}")
        time.sleep(SLEEP_S)
        return fetch(found_id)   # full fetch by paperId; won't 404
    except Exception as e:
        print(f"  [SS title ERROR] {e}")
        return None


def resolve_doi(paper_id: str) -> str:
    """
    Return a DOI for a seed ID.
    For arXiv IDs, fetches from Semantic Scholar to extract the DOI,
    since WoS and Scopus don't index arXiv IDs directly.
    """
    if paper_id.startswith("10."):
        return paper_id
    if paper_id.lower().startswith("arxiv:"):
        paper = fetch(paper_id)
        time.sleep(SLEEP_S)
        if paper:
            return paper.doi
    return ""


# ── Private helpers ───────────────────────────────────────────────────────────

def _data_to_paper(data: dict) -> Paper:
    ext   = data.get("externalIds") or {}
    doi   = ext.get("DOI", "")
    arxiv = ext.get("ArXiv", "")
    authors = [a.get("name", "") for a in (data.get("authors") or [])]
    refs = [_data_to_paper(r) for r in (data.get("references") or [])]
    cits = [_data_to_paper(c) for c in (data.get("citations")  or [])]
    return Paper(
        paper_id      = data.get("paperId", ""),
        doi           = doi,
        arxiv_id      = arxiv,
        title         = data.get("title", ""),
        authors       = authors,
        year          = str(data.get("year", "") or ""),
        venue         = data.get("venue", ""),
        citation_count= int(data.get("citationCount", 0) or 0),
        references    = refs,
        citations     = cits,
    )
