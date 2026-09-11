"""Application logic: snowball_paper(), run_snowball(), print_summary()."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone

from slr_engine.config import CANDIDATES_FILE, LOG_FILE, MAX_REFS, MAX_CITS
from slr_engine.engines import fetch_paper
from slr_engine.models import Paper
from slr_engine.storage import load_existing_keys, append_candidates, paper_to_csv_row


def snowball_paper(
    seed: dict,
    existing_keys: set[str],
    scopus_keys: set[str],
    log: dict,
    engines: list[str],
    scopus_key: str = "",
    ieee_key: str = "",
) -> list[dict]:
    """
    Perform one backward + forward snowball step for a single seed paper.
    Returns a list of NEW candidate rows not yet in existing_keys.

    existing_keys  — "dedup_key||engine" strings (plus plain keys for seeds).
                     Same (paper, engine) pair is written at most once.
    scopus_keys    — plain DOIs already covered by a Scopus row in the CSV.
                     SS entries whose DOI is in scopus_keys are skipped so
                     that later passes don't accumulate redundant SS rows for
                     papers where Scopus metadata is already available.
                     This set is NOT updated during the run, so within a single
                     pass both SS and Scopus rows are written for new papers.
    """
    paper_id   = seed["id"]
    seed_key   = seed["key"]
    seed_group = seed["group"]

    seed_title = seed.get("title", "")
    print(f"\nSnowballing: [{seed_group}] {seed_key}  ({paper_id})  engines={engines}")
    paper = fetch_paper(paper_id, engines, scopus_key=scopus_key, ieee_key=ieee_key,
                        title=seed_title, alt_id=seed.get("alt_id", ""))

    if paper is None:
        log[seed_key] = {"error": f"Could not fetch {paper_id}"}
        return []

    new_rows: list[dict] = []

    def _add(p: Paper, direction: str) -> bool:
        """Dedup and append; returns True if added."""
        key    = p.dedup_key
        engine = p.source_engine or ""

        # Plain-key check: blocks seeds (added without engine suffix) and legacy rows
        for k in filter(None, [p.paper_id, key]):
            if k in existing_keys:
                return False

        # Engine-specific dedup: same (paper, engine) already written
        for k in filter(None, [p.paper_id, key]):
            if engine and f"{k}||{engine}" in existing_keys:
                return False

        # Later-pass rule: skip SS if a Scopus row already exists for this paper
        if engine == "ss" and key and key in scopus_keys:
            return False

        # Register as written (engine-specific key)
        for k in filter(None, [p.paper_id, key]):
            if engine:
                existing_keys.add(f"{k}||{engine}")

        new_rows.append(paper_to_csv_row(p, direction, seed_key, seed_group))
        return True

    refs     = paper.references[:MAX_REFS]
    back_new = sum(1 for ref in refs if _add(ref, "BACKWARD"))

    cits    = paper.citations[:MAX_CITS]
    fwd_new = sum(1 for cit in cits if _add(cit, "FORWARD"))

    log[seed_key] = {
        "paper_id":          paper_id,
        "group":             seed_group,
        "engines":           engines,
        "title":             paper.title,
        "citation_count":    paper.citation_count,
        "backward_examined": len(refs),
        "backward_new":      back_new,
        "forward_examined":  len(cits),
        "forward_new":       fwd_new,
        "total_new":         len(new_rows),
    }
    print(f"  ← backward: {back_new} new from {len(refs)} refs")
    print(f"  → forward:  {fwd_new} new from {len(cits)} citations")
    return new_rows


def run_snowball(
    seeds: list[dict],
    engines: list[str],
    scopus_key: str = "",
    ieee_key: str = "",
    prevalidated_keys: set[str] | None = None,
) -> None:
    """
    Run full snowball over a list of seed papers.

    prevalidated_keys — dedup keys (DOIs, arXiv IDs, SS IDs) from the
                        pre-validated corpus (G1-G6).  Any candidate that
                        matches one of these keys is silently skipped so the
                        raw CSV contains only papers not already known.
    """
    from slr_engine.engines.semantic_scholar import normalize_id

    existing_keys, scopus_keys = load_existing_keys(CANDIDATES_FILE)

    # Pre-register prevalidated papers so snowball won't re-add them
    if prevalidated_keys:
        existing_keys.update(prevalidated_keys)
        print(f"Pre-registered {len(prevalidated_keys)} prevalidated keys "
              f"(G1-G6 corpus) — these will be skipped in snowball output.")

    print(f"Loaded {len(existing_keys)} total dedup keys "
          f"(existing raw + prevalidated) from {CANDIDATES_FILE.name}")
    print(f"Active engines: {engines}")

    for s in seeds:
        # Seeds as plain keys — block all engines from re-adding them as candidates
        existing_keys.add(normalize_id(s["id"]))

    log: dict = {
        "_meta": {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "databases": engines,
            "n_seeds": len(seeds),
        }
    }
    total_new = 0

    try:
        for seed in seeds:
            rows = snowball_paper(
                seed, existing_keys, scopus_keys, log,
                engines=engines, scopus_key=scopus_key, ieee_key=ieee_key,
            )
            if rows:
                append_candidates(rows, CANDIDATES_FILE)
                total_new += len(rows)
    finally:
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(log, f, indent=2, ensure_ascii=False)
        print(f"\n{'='*60}")
        print("Snowball complete.")
        print(f"  New candidates added : {total_new}")
        print(f"  Candidates CSV       : {CANDIDATES_FILE}")
        print(f"  Log JSON             : {LOG_FILE}")
        print(f"{'='*60}")
        print_summary(log)


def print_summary(log: dict) -> None:
    """Print per-group summary table."""
    groups = defaultdict(lambda: {"seeds": 0, "backward": 0, "forward": 0, "new": 0})
    for v in log.values():
        if "error" in v or "group" not in v:
            continue
        g = v["group"]
        groups[g]["seeds"]    += 1
        groups[g]["backward"] += v["backward_new"]
        groups[g]["forward"]  += v["forward_new"]
        groups[g]["new"]      += v["total_new"]

    print(f"\n{'Group':<6} {'Seeds':>6} {'Backward':>10} {'Forward':>9} {'New total':>10}")
    print("-" * 45)
    totals = [0, 0, 0, 0]
    for g in sorted(groups):
        d = groups[g]
        print(f"{g:<6} {d['seeds']:>6} {d['backward']:>10} {d['forward']:>9} {d['new']:>10}")
        totals[0] += d["seeds"];   totals[1] += d["backward"]
        totals[2] += d["forward"]; totals[3] += d["new"]
    print("-" * 45)
    print(f"{'TOTAL':<6} {totals[0]:>6} {totals[1]:>10} {totals[2]:>9} {totals[3]:>10}")
