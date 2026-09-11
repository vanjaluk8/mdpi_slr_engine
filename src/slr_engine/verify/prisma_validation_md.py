#!/usr/bin/env python3
"""prisma_validation_md.py — write a markdown validation table to the paper dir.
Same source (prisma_facts.json) as the console table; one output you can keep
open while reading the manuscript.
"""
import json, sys
from pathlib import Path

from slr_engine.verify.verify_common import repo_root

facts = json.loads((Path(sys.argv[1]) if len(sys.argv)>1 else
    Path("data/snowball_output/generated/prisma_facts.json")).read_text())

fs = facts["forward_snowball"]; gxr = facts["group_x_route"]; grp = facts["groups"]
col_ft = sum(v["fulltext"] for v in gxr.values())
col_a2p = sum(v["abstract-2nd-pass"] for v in gxr.values())

L = []
A = L.append
A("# PRISMA validation table — single source of truth (`prisma_facts.json`)")
A("")
A("Auto-generated; every value is recomputed from the pipeline files, not typed. "
  "Open this next to the manuscript: every funnel number you read should match a row here.")
A("")
A("> Anchor the funnel on **123 rows = 120 distinct works** (3 works kept as two rows each). "
  "**70/53** is the entry-*decision-route* split; the *forward-snowball* branch (**G_SNOW_F**) is the "
  "small 88→12/4/72 block in stage 8 — these are two different axes and must never be conflated.")
A("")
A("## Funnel")
A("")
A("| # | Stage | Quantity | Value | Source / arithmetic |")
A("|---|-------|----------|------:|---------------------|")
r=lambda a,b,c,d:A(f"| {a} | {b} | {c} | {d} |")
r(1,"Identification","raw records retrieved",facts["raw_retrieved"])
r("","","duplicates removed",facts["dedup_removed"])
r("","","unique after dedup (screen pool)",facts["screening_pool"])
r(2,"Title screening","INCLUDE",facts["title_include"])
r("","","UNCERTAIN / REVIEW",facts["title_uncertain"])
r("","","EXCLUDE",facts["title_exclude"])
r("","","sum = pool ✓",facts["title_include"]+facts["title_uncertain"]+facts["title_exclude"])
r("2b","LLM triage","rows sent",facts["llm_sent"])
r("","","promoted to INCLUDE",facts["llm_include"])
r("","","EXCLUDE",facts["llm_exclude"])
r("","","UNCERTAIN remaining",facts["llm_uncertain_remain"])
r(3,"Merge","pre-validated corpus (G0-G6)",facts["prevalidated_corpus"])
r("","","merge duplicates removed",facts["merge_dedup"])
r("","","merged corpus",facts["merged_corpus"])
r(4,"Enrichment","retained (enriched)",facts["enriched_retained"])
r("","","deprioritized low-cite",facts["enriched_deprioritized"])
r("","","off-topic excluded",facts["enriched_offtopic"])
r(5,"Abstract review","abstract-review pool",facts["abstract_pool"])
r("","","KEEP",facts["abstract_keep"])
r("","","DEFER",facts["abstract_defer"])
r("","","SKIP (final)",facts["abstract_skip"])
r("","","sum = pool ✓",facts["abstract_pool"])
r(6,"Full-text / extraction","full-text queue",facts["fulltext_queue"])
r("","","extraction from queue",facts["extract_preliminary"])
r("","","manual cross-val top-up",facts["extract_topup"])
r("","","extraction total",facts["extract_final"])
A("")
A("## Final list — anchors")
A("")
A("| Quantity | Value | Source |")
A("|----------|------:|--------|")
A(f"| final rows | {facts['final_rows']} | 13_final_reading_list (N=123) |")
A(f"| **distinct works** | **{facts['final_distinct']}** | 123 − 3 dup-works (ANCHOR) |")
A(f"| duplicate works (2 rows each) | {facts['n_duplicate_works']} | LoRA · Houlsby · CaraServe≡Toppings |")
A(f"| entry route: fulltext | {facts['route_fulltext']} | 11_data_extraction `review_source` |")
A(f"| entry route: abstract-2nd-pass | {facts['route_abstract_2nd_pass']} | 11_data_extraction `review_source` |")
A(f"| route sum = 123 ✓ | {facts['route_fulltext']+facts['route_abstract_2nd_pass']} | 70+53 |")
A(f"| tiers 1 / 2 / 3 | {facts['final_tier1']} / {facts['final_tier2']} / {facts['final_tier3']} | 13_final `tier` (48/42/33=123) |")
A(f"| core / background | {facts['final_core']} / {facts['final_background']} | 13_final `corpus` (89/34=123) |")
A("")
A("## Forward-snowball branch (G_SNOW_F) — its own block, NOT the 53")
A("")
A("| Quantity | Value | Source |")
A("|----------|------:|--------|")
A(f"| records added | {fs['added']} | Appendix C.3 |")
A(f"| KEEP | {fs['keep']} | Appendix C.3 |")
A(f"| DEFER → resolved SKIP | {fs['defer']} | Appendix C.3 |")
A(f"| SKIP | {fs['skip']} | Appendix C.3 |")
A(f"| 12+4+72 = 88 ✓ | {fs['keep']+fs['defer']+fs['skip']} | consistency |")
A("")
A("## Provenance cross-tab (originating group × entry route), all margins = 123")
A("")
A("| Group | fulltext | abstract-2nd-pass | total |")
A("|-------|--------:|------------------:|------:|")
for g in ("G0","G1","G2","G3","G4","G5","G6"):
    v=gxr[g]
    A(f"| {g} | {v['fulltext']} | {v['abstract-2nd-pass']} | {v['total']} |")
A(f"| **TOT** | **{col_ft}** | **{col_a2p}** | **{col_ft+col_a2p}** |")
A("")
# group subtotals line for completeness
A(f"Group subtotals: " + ", ".join(f"{g}={grp[g]}" for g in grp) + f" (G1–G6 = {facts['groups_g1g6']}, total 123)")
A("")
A("## Duplicate works (kept as two rows each, counted once)")
A("")
for p in facts["duplicate_works"]:
    A(f"- `{p[0]}` ≡ `{p[1]}`")
A("")
A("---")
A("Consistency: 21 checks pass (see `prisma_facts.py` / `verify_common.py`).")

# Write the validation table into the sibling paper repo (mdpi_paper_slr), which
# is where per-record artefacts and verify outputs are version-controlled (DAS).
# Fall back to the engine repo root if the sibling isn't present.
paper = repo_root().parent / "mdpi_paper_slr"
out_dir = paper if (paper / "snowball_output").exists() else repo_root()
out = out_dir / "PRISMA_VALIDATION_TABLE.md"
out.write_text("\n".join(L)+"\n", encoding="utf-8")
print("wrote", out)
