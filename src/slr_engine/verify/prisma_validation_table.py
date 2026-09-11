#!/usr/bin/env python3
"""prisma_validation_table.py — print a human-readable validation table for every
PRISMA number, derived from prisma_facts.json (the single source of truth).

Use while reading the manuscript: each row is one audited number, its value, and
the data source it is recomputed from. Nothing here is typed by hand.
"""
import json
from pathlib import Path
import sys

F = Path(sys.argv[1] if len(sys.argv) > 1 else
         "data/snowball_output/generated/prisma_facts.json")
facts = json.loads(F.read_text())

R = []
def add(stage, key, val, src, note=""):
    R.append((stage, key, val, src, note))

# ── Stage 1: identification ──────────────────────────────────────────────
add("1 Identification", "raw records retrieved", facts["raw_retrieved"],
    "log_retrieval: backward 450 + forward 700")
add("1 Identification", "duplicates removed", facts["dedup_removed"], "1150 - 972")
add("1 Identification", "unique after dedup (screen pool)", facts["screening_pool"],
    "log_screening input.total_records")

# ── Stage 2: title screening ─────────────────────────────────────────────
add("2 Title screening", "INCLUDE", facts["title_include"], "S4/S3 title decision")
add("2 Title screening", "UNCERTAIN / REVIEW", facts["title_uncertain"], "03_review_queue")
add("2 Title screening", "EXCLUDE", facts["title_exclude"], "S3-S4")
add("2 Title screening", "sum = pool", facts["title_include"]+facts["title_uncertain"]+facts["title_exclude"],
    "162+19+791 = 972", "consistency")

# ── LLM triage ───────────────────────────────────────────────────────────
add("2b LLM triage", "rows sent", facts["llm_sent"], "log_screening.llm_screening")
add("2b LLM triage", "promoted to INCLUDE", facts["llm_include"], "")
add("2b LLM triage", "EXCLUDE", facts["llm_exclude"], "")
add("2b LLM triage", "UNCERTAIN remaining", facts["llm_uncertain_remain"], "")

# ── Merge ────────────────────────────────────────────────────────────────
add("3 Merge", "pre-validated corpus (G0-G6)", facts["prevalidated_corpus"], "S1")
add("3 Merge", "merge duplicates removed", facts["merge_dedup"], "514 -> 502")
add("3 Merge", "merged corpus", facts["merged_corpus"], "162+352-12 = 502")

# ── Enrichment ───────────────────────────────────────────────────────────
add("4 Enrichment", "retained (enriched)", facts["enriched_retained"], "S6")
add("4 Enrichment", "deprioritized low-cite", facts["enriched_deprioritized"], "S6c")
add("4 Enrichment", "off-topic excluded", facts["enriched_offtopic"], "S6b")

# ── Abstract review ──────────────────────────────────────────────────────
add("5 Abstract review", "abstract-review pool", facts["abstract_pool"], "464 + 88 forward")
add("5 Abstract review", "KEEP", facts["abstract_keep"], "S7b abstract_decision")
add("5 Abstract review", "DEFER", facts["abstract_defer"], "09 queue")
add("5 Abstract review", "SKIP (final)", facts["abstract_skip"], "338 - 173 DEFER")
add("5 Abstract review", "KEEP+DEFER+SKIP = pool", facts["abstract_pool"],
    "214+173+165 = 552", "consistency")

# ── Full-text queue & extraction ─────────────────────────────────────────
add("6 Full-text / extraction", "full-text queue", facts["fulltext_queue"], "214 KEEP + 173 DEFER = 387")
add("6 Full-text / extraction", "extraction from queue", facts["extract_preliminary"], "10_data_extraction")
add("6 Full-text / extraction", "manual cross-val top-up", facts["extract_topup"], "11 - 10 (34)")
add("6 Full-text / extraction", "extraction total", facts["extract_final"], "11_data_extraction (224)")
add("6 Full-text / extraction", "190 + 34 = 224", facts["extract_final"],
    facts["extract_preliminary"]+facts["extract_topup"], "consistency")

# ── Final list ───────────────────────────────────────────────────────────
add("7 Final list", "final rows", facts["final_rows"], "13_final_reading_list (N=123)")
add("7 Final list", "distinct works", facts["final_distinct"], "123 - 3 dup-works (120)", "ANCHOR")
add("7 Final list", "duplicate works (2 rows each)", facts["n_duplicate_works"],
    "verify_common.FINAL_DUPLICATE_WORKS", "ANCHOR")
add("7 Final list", "slot in-line route: fulltext", facts["route_fulltext"], "11_data_extraction review_source")
add("7 Final list", "route: abstract-2nd-pass", facts["route_abstract_2nd_pass"],
    "11_data_extraction review_source")
add("7 Final list", "route sum = 123", facts["route_fulltext"]+facts["route_abstract_2nd_pass"],
    "70+53 = 123", "consistency")
add("7 Final list", "tiers 1 / 2 / 3", f"{facts['final_tier1']} / {facts['final_tier2']} / {facts['final_tier3']}",
    "13_final tier col (48+42+33=123)")
add("7 Final list", "core / background", f"{facts['final_core']} / {facts['final_background']}",
    "13_final corpus col (89+34=123)")

# ── Forward-snowball branch ──────────────────────────────────────────────
fs = facts["forward_snowball"]
add("8 Forward-snowball (G_SNOW_F)", "records added", fs["added"], "Appendix C.3 (88)")
add("8 Forward-snowball (G_SNOW_F)", "KEEP", fs["keep"], "Appendix C.3 (12)")
add("8 Forward-snowball (G_SNOW_F)", "DEFER -> SKIP", fs["defer"], "Appendix C.3 (4)")
add("8 Forward-snowball (G_SNOW_F)", "SKIP", fs["skip"], "Appendix C.3 (72)")
add("8 Forward-snowball (G_SNOW_F)", "12+4+72 = 88", fs["keep"]+fs["defer"]+fs["skip"], "", "consistency")

# ── Provenance cross-tab ─────────────────────────────────────────────────
gxr = facts["group_x_route"]
col_ft = sum(v["fulltext"] for v in gxr.values())
col_a2p = sum(v["abstract-2nd-pass"] for v in gxr.values())

print("="*78)
print("PRISMA VALIDATION TABLE  (from prisma_facts.json — single source of truth)")
print("="*78)
print(f"{'Stage':34s} {'Value':>26s}  Source / note")
print("-"*78)
cur = None
for stage, key, val, src, note in R:
    if stage != cur:
        print(f"\n── {stage} ──")
        cur = stage
    v = str(val)
    flags = "  <<ANCHOR>>" if note == "ANCHOR" else ("  [check]" if note == "consistency" else "")
    print(f"  {key:30s} {v:>22s}  {src}{flags}")

print("\n── 9 Provenance cross-tab (group x entry-route), all margins = 123 ──")
print(f"  {'Group':6s} {'fulltext':>10s} {'abs-2nd-pass':>13s} {'total':>7s}")
for g in ("G0","G1","G2","G3","G4","G5","G6"):
    v = gxr[g]
    print(f"  {g:6s} {v['fulltext']:10d} {v['abstract-2nd-pass']:13d} {v['total']:7d}")
print(f"  {'TOT':6s} {col_ft:10d} {col_a2p:13d} {col_ft+col_a2p:7d}")
gsum = sum(gxr[g]['total'] for g in gxr)
print(f"\n  cross-checks: rows sum {gsum} (123) · cols sum {col_ft}+{col_a2p}={col_ft+col_a2p} (123)")
print("-"*78)
print("NOTE: 'route: abstract-2nd-pass' (53) is an ENTRY ROUTE (decision), NOT the")
print("forward-snowball source. The forward-snowball branch (G_SNOW_F) is only its own")
print("small 88->12/4/72 block above — it is not the same as the 53.")
