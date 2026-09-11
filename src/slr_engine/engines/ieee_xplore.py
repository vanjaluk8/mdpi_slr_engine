"""IEEE Xplore API engine.

The IEEE Xplore REST API v1 provides paper metadata and citation counts.
References and forward citations are NOT available via the public API, so this
engine is used for metadata enrichment and cross-validation only.  Pair it with
"ss" so Semantic Scholar provides the citation graph.

API key must be passed as ``api_key`` (sent as the ``apikey`` query parameter).

Paper ID formats accepted:
  10.1109/TPAMI.2021.12345   — any DOI
  arxiv:2301.12345            — resolved to DOI via Semantic Scholar first
  Any other string            — treated as a title search (if title arg provided)
"""
from __future__ import annotations

import time

import urllib3
import requests

from slr_engine.config import IEEE_BASE, SLEEP_S, SSL_VERIFY
from slr_engine.models import Paper

_session = requests.Session()
_session.verify = SSL_VERIFY
if not SSL_VERIFY:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def fetch(paper_id: str, api_key: str, title: str = "") -> Paper | None:
    """
    Fetch paper metadata from IEEE Xplore.
    Tries DOI lookup first; falls back to title search when a title is provided.
    references and citations are always empty — use "ss" alongside this engine.
    """
    doi = ""
    if paper_id.startswith("10."):
        doi = paper_id
    elif paper_id.lower().startswith("arxiv:"):
        # Resolve arXiv → DOI via Semantic Scholar
        from slr_engine.engines.semantic_scholar import resolve_doi
        doi = resolve_doi(paper_id)
        time.sleep(SLEEP_S)

    article = None
    if doi:
        article = _search_by_doi(doi, api_key)
    if article is None and title:
        print(f"  [IEEE] DOI lookup failed — trying title search")
        article = _search_by_title(title, api_key)

    if article is None:
        return None

    return _article_to_paper(article)


# ── Private helpers ───────────────────────────────────────────────────────────

def _search_by_doi(doi: str, api_key: str) -> dict | None:
    try:
        r = _session.get(
            f"{IEEE_BASE}/search/articles",
            params={"apikey": api_key, "doi": doi, "max_records": 1},
            timeout=20,
        )
        if r.status_code == 429:
            print("  [IEEE 429] Rate limited — sleeping 15s")
            time.sleep(15)
            return _search_by_doi(doi, api_key)
        if r.status_code == 401:
            print(f"  [IEEE 401] Unauthorized — check your IEEE_API_KEY")
            return None
        if not r.ok:
            print(f"  [IEEE {r.status_code}] DOI search failed for {doi}: {r.text[:120]}")
            return None
        articles = r.json().get("articles", [])
        if not articles:
            print(f"  [IEEE] No results for DOI={doi}")
            return None
        print(f"  [IEEE] DOI matched: {articles[0].get('title', '')[:70]}")
        return articles[0]
    except Exception as e:
        print(f"  [IEEE ERROR] DOI search {doi}: {e}")
        return None


def _search_by_title(title: str, api_key: str) -> dict | None:
    try:
        r = _session.get(
            f"{IEEE_BASE}/search/articles",
            params={
                "apikey": api_key,
                "querytext": f'"{title}"',
                "max_records": 1,
            },
            timeout=20,
        )
        if r.status_code == 429:
            print("  [IEEE 429] Rate limited — sleeping 15s")
            time.sleep(15)
            return _search_by_title(title, api_key)
        if not r.ok:
            print(f"  [IEEE {r.status_code}] Title search failed: {r.text[:120]}")
            return None
        articles = r.json().get("articles", [])
        if not articles:
            print(f"  [IEEE] No title match: {title[:60]}")
            return None
        print(f"  [IEEE] Title search matched: {articles[0].get('title', '')[:70]}")
        return articles[0]
    except Exception as e:
        print(f"  [IEEE ERROR] Title search: {e}")
        return None


def _article_to_paper(article: dict) -> Paper:
    doi   = article.get("doi", "")
    title = article.get("title", article.get("article_title", ""))
    year  = str(article.get("publication_year", "") or "")
    venue = article.get("publication_title", "")
    tc    = int(article.get("citing_paper_count", 0) or 0)

    authors: list[str] = []
    for a in (article.get("authors", {}).get("authors") or []):
        name = a.get("full_name", a.get("author_full_name", ""))
        if name:
            authors.append(name)

    return Paper(
        paper_id      = doi or article.get("article_number", ""),
        doi           = doi,
        title         = title,
        authors       = authors,
        year          = year,
        venue         = venue,
        citation_count= tc,
        # references and citations unavailable via IEEE Xplore public API
    )
