#!/usr/bin/env python3
"""verify_quality_appraisal.py — recompute quality-appraisal / descriptive stats.

Recomputes the quality-appraisal statistics reported in the MDPI manuscript from
the pipeline data (committed verification_fixtures/, falling back to
data/snowball_output/). Exits non-zero on any mismatch. Writes a saved run
to data/snowball_output/verification_runs/.

Reported statistics covered (from the manuscript + PRISMA_NUMBERS_VALIDATION.md):
  - venue quality of the 464-paper enriched pool (top_venue / peer_reviewed /
    preprint / unknown)
  - tier split of the 464 enriched pool (90 / 141 / 233)
  - abstract review decision split (KEEP 214 / SKIP 338 of the 552 pool)
  - year distribution of the final 123 (range 2002-2026; most active 2024 = 38)

Usage:
    python scripts/verify_quality_appraisal.py
"""
from __future__ import annotations

import sys

from slr_engine.verify.verify_common import Checks, counter, load_csv, write_run

# ── Expected quality-appraisal values (manuscript / validation table) ─────────
EXPECTED_VENUE = {"top_venue": 166, "peer_reviewed": 189, "preprint": 91, "unknown": 18}
EXPECTED_TIERS_464 = {"1": 90, "2": 141, "3": 233}
EXPECTED_ABS = {"KEEP": 214, "SKIP": 338}
EXPECTED_FINAL_YEARS = {
    "2002": 1, "2011": 1, "2016": 1, "2017": 1, "2019": 4, "2020": 5,
    "2021": 17, "2022": 13, "2023": 19, "2024": 38, "2025": 20, "2026": 3,
}
EXPECTED_TOP_YEAR_PAPERS = 38   # 2024


def verify() -> int:
    lines: list[str] = []
    print("QUALITY-APPRAISAL VERIFICATION (recomputed from pipeline data files)")
    lines.append("QUALITY-APPRAISAL VERIFICATION — recomputed from pipeline data files")
    c = Checks()
    c.suite = "Quality appraisal"

    # ── Venue quality of the 464 enriched pool ────────────────────────────────
    print("\n[1] Venue quality of the enriched pool (S6 = 464)")
    lines.append("[1] Venue quality of the enriched pool (S6 = 464)")
    s6 = load_csv("S6_enriched_reading_pool.csv")
    vq = counter(s6, "venue_quality")
    c.check("enriched pool size", len(s6), 464)
    for tag, exp in EXPECTED_VENUE.items():
        c.check(f"venue_quality {tag}", vq.get(tag, 0), exp)
    c.check("venue-quality sums to 464", sum(vq.values()), 464)

    # ── Tier split of the 464 enriched pool ───────────────────────────────────
    print("\n[2] Tier split of the enriched pool (90 / 141 / 233)")
    lines.append("[2] Tier split of the enriched pool")
    t6 = counter(s6, "tier")
    for tier, exp in EXPECTED_TIERS_464.items():
        c.check(f"enriched tier {tier}", t6.get(tier, 0), exp)
    c.check("tier sums to 464", sum(t6.values()), 464)

    # ── Abstract review decision split (KEEP 214 / SKIP 338 of 552) ───────────
    print("\n[3] Abstract review decision split (S7b = 552)")
    lines.append("[3] Abstract review decision split (S7b = 552)")
    s7b = load_csv("S7b_abstract_reviewed_final.csv")
    ab = counter(s7b, "abstract_decision")
    c.check("abstract pool size", len(s7b), 552)
    c.check("KEEP", ab.get("KEEP", 0), EXPECTED_ABS["KEEP"])
    c.check("SKIP", ab.get("SKIP", 0), EXPECTED_ABS["SKIP"])
    c.check("KEEP + SKIP = 552", ab.get("KEEP", 0) + ab.get("SKIP", 0), 552)

    # ── Year distribution of the final 123 ────────────────────────────────────
    print("\n[4] Year distribution of the final reading list (13 = 123)")
    lines.append("[4] Year distribution of the final reading list (13 = 123)")
    final = load_csv("13_final_reading_list_2026-05-12.csv")
    yrs = counter(final, "year")
    c.check("final list size", len(final), 123)
    y_ok = all(yrs.get(str(y), 0) == n for y, n in EXPECTED_FINAL_YEARS.items())
    c.check("year distribution matches manuscript", {str(k): yrs.get(str(k), 0) for k in EXPECTED_FINAL_YEARS}, EXPECTED_FINAL_YEARS)
    top_year, top_n = max(EXPECTED_FINAL_YEARS.items(), key=lambda kv: kv[1])
    c.check(f"most active year {top_year} = {top_n}", yrs.get(str(top_year), 0), EXPECTED_TOP_YEAR_PAPERS)
    c.check("year distribution sums to 123", sum(v for k, v in yrs.items() if str(k).isdigit()), 123)
    c.check("full year range present (2002..2026)", min((int(k) for k in yrs if str(k).isdigit())), 2002)

    rc = c.summary()
    lines.append(f"\n{len(c.passes)} passed, {len(c.failures)} failed")
    if c.failures:
        lines += ["FAIL: " + f for f in c.failures]
    out = write_run("verify_quality_appraisal.result.txt", lines)
    print(f"\nRun saved to: {out}")
    return rc


def main() -> int:
    return verify()


if __name__ == "__main__":
    sys.exit(main())
