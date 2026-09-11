"""Web of Science Starter API engine — enrichment only (no citation graph).

The Starter tier supports:
  - Lookup by DOI → WoS UID (accession number) + times-cited count
It does NOT provide reference or citing-article lists (Expanded tier only).

Use this as a post-processing enrichment step, not as a snowball engine.
"""
from __future__ import annotations

import time

import urllib3
import requests

from slr_engine.config import WOS_BASE, SLEEP_S, SSL_VERIFY

_session = requests.Session()
_session.verify = SSL_VERIFY
if not SSL_VERIFY:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def lookup(doi: str, api_key: str) -> dict:
    """
    Look up a single paper in WoS by DOI.

    Returns a dict with:
      wos_uid         — e.g. "WOS:000267144200002", or "" if not found
      wos_times_cited — integer string, or "" if not found / not available
    """
    empty = {"wos_uid": "", "wos_times_cited": ""}
    if not doi or not api_key:
        return empty

    doi = doi.strip()
    if doi.lower().startswith("doi:"):
        doi = doi[4:]

    url = f"{WOS_BASE}/documents"
    headers = {"X-ApiKey": api_key}
    params  = {"q": f"DO={doi}", "limit": 1}

    try:
        r = _session.get(url, headers=headers, params=params, timeout=20)
        if r.status_code == 404:
            return empty
        if r.status_code == 429:
            print("  [WoS 429] Rate limited — sleeping 10s")
            time.sleep(10)
            return lookup(doi, api_key)
        r.raise_for_status()
        hits = r.json().get("hits", [])
        if not hits:
            return empty
        hit = hits[0]
        uid = hit.get("uid", "")
        # citations is a list of {"db": "WOS", "count": N} objects
        times_cited = ""
        for c in hit.get("citations", []):
            if isinstance(c, dict) and c.get("db") == "WOS":
                times_cited = str(c.get("count", ""))
                break
        return {"wos_uid": uid, "wos_times_cited": times_cited}
    except Exception as e:
        print(f"  [WoS ERROR] {doi}: {e}")
        return empty
