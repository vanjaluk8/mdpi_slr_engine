"""
visualise.py — Publication-quality SLR figures (Watson et al. 2017).

Produces figures from snowball_output files plus the pre-curated G-group CSVs:

  fig_slr1_prisma_flow.png    — PRISMA-style screening flowchart
  fig_slr2_wave_productivity.png — Snowball wave productivity per G-group
  fig_slr3_year_distribution.png — Publication year distribution of included papers
  fig_slr4_screening_funnel.png  — Screening decision breakdown (exclusion funnel)
  fig_slr5_venues.png            — Top venues + outlet type breakdown (conf/journal/preprint)
  fig_slr6_tier_breakdown.png    — Paper count per tier + citation box plot
  fig_slr7_abstract_review.png   — Abstract review decisions (KEEP/SKIP/DEFER)
  fig_slr8_groups.png            — Papers per G-group (G1–G6) by outlet type

Usage:
    python -m slr_engine.visualise
    python -m slr_engine.visualise --output-dir data/figures
    python -m slr_engine.visualise --figures 1 3 8
    python -m slr_engine.visualise --retrieval log_retrieval_YYYY-MM-DD.json \\
                             --screening log_screening_YYYY-MM-DD.json \\
                             --included  04_included_YYYY-MM-DD.csv
"""
from __future__ import annotations

import argparse
import json
import csv
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

# Curated G-group inputs (G0 seeds + G1-G6 CSVs) live in data/inputs; figure
# output goes to data/figures/ (config defines both under the data/ umbrella).
try:
    from slr_engine.config import PAPERS_REPO, FIGURES_DIR, OUTPUT_DIR
except ImportError:
    from pathlib import Path
    _root = Path(__file__).resolve().parent.parent
    PAPERS_REPO = _root / "data" / "inputs"
    FIGURES_DIR = _root / "data" / "figures"
    OUTPUT_DIR  = _root / "data" / "snowball_output"

# ── Palette (colorblind-safe, works in B&W) ───────────────────────────────────
C_INCLUDE    = "#2166AC"   # blue
C_EXCLUDE    = "#D6604D"   # red-orange
C_REVIEW     = "#4DAC26"   # green
C_NEUTRAL    = "#636363"   # dark grey
C_LIGHT      = "#F0F0F0"   # box background
C_HIGHLIGHT  = "#FEE391"   # warm yellow accent

FIG_DPI      = 180
FONT_FAMILY  = "DejaVu Sans"

# ── G-group palette and labels ────────────────────────────────────────────────
GROUP_COLORS = {
    "G0": "#8856A7",
    "G1": "#4393C3",
    "G2": "#74ADD1",
    "G3": "#D73027",
    "G4": "#F46D43",
    "G5": "#A50026",
    "G6": "#FDAE61",
    "Other": "#AAAAAA",
}
GROUP_LABELS = {
    "G0": "G0 — Seed papers (9 foundational)",
    "G1": "G1 — PEFT methods beyond adapters / LoRA",
    "G2": "G2 — Adapter composition (multi-task NLP)",
    "G3": "G3 — Decentralised / P2P ML systems",
    "G4": "G4 — Adapter multiplexing (LLM inference)",
    "G5": "G5 — Routing / MoE (modular PEFT)",
    "G6": "G6 — Federated PEFT (transformer NLP)",
    "Other": "Other / manual addition",
}

def _enrich_groups(final_rows: list[dict], reviewed_rows: list[dict]) -> list[dict]:
    """Add '_seed_group' to each final_row by DOI / arxiv_id lookup in reviewed_rows."""
    by_doi:   dict[str, str] = {}
    by_arxiv: dict[str, str] = {}
    for r in reviewed_rows:
        doi   = (r.get("doi")       or "").strip()
        arxiv = (r.get("arxiv_id")  or "").strip()
        grp   = (r.get("seed_group") or "").strip()
        if doi:
            by_doi[doi] = grp
        if arxiv:
            by_arxiv[arxiv] = grp
    for row in final_rows:
        doi   = (row.get("doi")      or "").strip()
        arxiv = (row.get("arxiv_id") or "").strip()
        row["_seed_group"] = by_doi.get(doi) or by_arxiv.get(arxiv) or "Other"
    return final_rows


plt.rcParams.update({
    "font.family":       FONT_FAMILY,
    "font.size":         9,
    "axes.titlesize":    10,
    "axes.titleweight":  "bold",
    "axes.spines.top":   False,
    "axes.spines.right": False,
    "figure.dpi":        FIG_DPI,
})


# ─────────────────────────────────────────────────────────────────────────────
# Fig 1 — PRISMA flow diagram
# ─────────────────────────────────────────────────────────────────────────────

def fig1_prisma(
    screening_log: dict,
    retrieval_log: dict,
    out_path: Path,
    filtered_rows: list[dict] | None = None,
    reviewed_rows: list[dict] | None = None,
    final_list_rows: list[dict] | None = None,
) -> None:
    """PRISMA 2020-style flowchart derived from screening audit log."""
    s = screening_log

    # ── Pull numbers ──────────────────────────────────────────────────────────
    total_retrieved = sum(
        v.get("backward_examined", 0) + v.get("forward_examined", 0)
        for v in retrieval_log.values()
    )
    total_unique  = s["input"].get("total_records") or s["input"].get("n_rows", 0)
    duplicates    = total_retrieved - total_unique

    excl_breakdown  = s["exclusion_breakdown"]
    excl_year_total = sum(v for k, v in excl_breakdown.items() if k.startswith("Year"))
    excl_no_kw      = excl_breakdown.get("No keyword match in title", 0)
    excl_llm_broad  = excl_breakdown.get("LLM-only match (too broad)", 0)

    n_include = s["decisions"]["INCLUDE"]
    n_review  = s["decisions"]["REVIEW"]
    n_exclude = s["decisions"]["EXCLUDE"]

    llm = s.get("llm_screening", {})
    llm_run          = isinstance(llm, dict)
    llm_resolved_exc = llm.get("resolved_exclude", 0) if llm_run else 0
    llm_rows_sent    = llm.get("rows_sent", 0) if llm_run else 0
    n_uncertain      = llm.get("uncertain_remaining", 0) if llm_run else n_review

    # Title-screening exclusions (Layer 1 + Layer 2) — the sum of the listed
    # reasons. The 791 final EXCLUDE additionally includes the 60 resolved by
    # LLM triage (Box 3), so the box below must show 731, not 791, to stay
    # internally consistent with its own breakdown (192+284+255 = 731).
    n_title_excl = excl_year_total + excl_no_kw + excl_llm_broad

    if filtered_rows:
        tier_counts = Counter(int(r.get("tier") or 3) for r in filtered_rows)
        t1 = tier_counts.get(1, 0)
        t2 = tier_counts.get(2, 0)
        t3 = tier_counts.get(3, 0)
        n_enriched = len(filtered_rows)
    else:
        t1 = t2 = t3 = 0
        n_enriched = n_include

    if reviewed_rows:
        rev_dec    = Counter(r.get("abstract_decision", "") for r in reviewed_rows)
        n_rev_keep  = rev_dec.get("KEEP",  0)
        n_rev_skip  = rev_dec.get("SKIP",  0)
        n_rev_defer = rev_dec.get("DEFER", 0)

    # ── Layout constants ───────────────────────────────────────────────────────
    BOX_H    = 0.90   # main box height (data units)
    STEP     = 1.55   # center-to-center vertical spacing between consecutive boxes
    BAND_PAD = 0.25   # padding above/below outermost box in each stage band

    LBL_XC = 0.82    # stage label column x-center
    LBL_W  = 1.40    # stage label box width
    MN_XC  = 5.50    # main flow box x-center
    MN_W   = 5.00    # main flow box width (right edge at 8.00)
    EX_XC  = 9.75    # exclusion box x-center
    EX_W   = 2.30    # exclusion box width (left edge at 8.60, gap = 0.60)

    # box[0] = top of diagram (first step); box[n-1] = bottom (last step)
    # With reviewed_rows: 8 boxes (Snowball, Dedup, TitleScreen, LLMTriage,
    #                               Eligible, Enriched, AbsReview, FinalList)
    # Without:            6 boxes (Snowball, Dedup, TitleScreen, LLMTriage,
    #                               Eligible, FinalIncluded)
    n_boxes = 8 if reviewed_rows else 6
    BTM_PAD = 0.80
    TOP_PAD = 1.10

    # y[i] = vertical center of box i; y[0] is highest (top of figure)
    y = [BTM_PAD + BOX_H / 2 + (n_boxes - 1 - i) * STEP for i in range(n_boxes)]
    fig_h = y[0] + BOX_H / 2 + TOP_PAD

    # ── Canvas ────────────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(11, fig_h))
    ax.set_xlim(0, 11)
    ax.set_ylim(0, fig_h)
    ax.axis("off")
    fig.patch.set_facecolor("white")

    # ── Stage definitions: (label, [box_indices], band_color) ────────────────
    if reviewed_rows:
        stage_defs = [
            ("Identification",   [0, 1], "#EDE7F6"),
            ("Screening",        [2, 3], "#E8F5E9"),
            ("Eligibility",      [4],    "#FFF8E1"),
            ("Enriched",         [5],    "#FBE9E7"),
            ("Abstract\nReview", [6],    "#F3E5F5"),
            ("Included",         [7],    "#E0F7FA"),
        ]
    else:
        stage_defs = [
            ("Identification", [0, 1], "#EDE7F6"),
            ("Screening",      [2, 3], "#E8F5E9"),
            ("Eligibility",    [4],    "#FFF8E1"),
            ("Included",       [5],    "#E0F7FA"),
        ]

    # ── Draw stage background bands + label boxes ─────────────────────────────
    for label, ids, band_color in stage_defs:
        s_top = y[ids[0]]  + BOX_H / 2 + BAND_PAD
        s_bot = y[ids[-1]] - BOX_H / 2 - BAND_PAD
        s_h   = s_top - s_bot

        # Full-width tinted background band (behind everything)
        ax.add_patch(FancyBboxPatch(
            (0.10, s_bot), 10.80, s_h,
            boxstyle="round,pad=0.0", linewidth=0,
            facecolor=band_color, alpha=0.40, zorder=0,
        ))

        # Stage label box — opaque, left column, spans full band height
        ax.add_patch(FancyBboxPatch(
            (LBL_XC - LBL_W / 2, s_bot), LBL_W, s_h,
            boxstyle="round,pad=0.06", linewidth=0.7,
            edgecolor="#888888", facecolor=band_color, zorder=1,
        ))
        ax.text(LBL_XC, (s_top + s_bot) / 2, label,
                ha="center", va="center", fontsize=8, color="#222222",
                weight="bold", rotation=90, zorder=2)

    # ── Helper: main-flow box ─────────────────────────────────────────────────
    def mbox(idx: int, text: str, color: str = C_LIGHT, bold: bool = False) -> None:
        ax.add_patch(FancyBboxPatch(
            (MN_XC - MN_W / 2, y[idx] - BOX_H / 2), MN_W, BOX_H,
            boxstyle="round,pad=0.08", linewidth=0.9,
            edgecolor=C_NEUTRAL, facecolor=color, zorder=3,
        ))
        ax.text(MN_XC, y[idx], text,
                ha="center", va="center", fontsize=8.5,
                color="black", weight="bold" if bold else "normal",
                multialignment="center", zorder=4)

    # ── Helper: exclusion side-box + horizontal arrow ─────────────────────────
    def ebox(y_pos: float, text: str, h: float = 0.80) -> None:
        ax.add_patch(FancyBboxPatch(
            (EX_XC - EX_W / 2, y_pos - h / 2), EX_W, h,
            boxstyle="round,pad=0.06", linewidth=0.6,
            edgecolor="#BBBBBB", facecolor="#FFDDDD", linestyle="--", zorder=3,
        ))
        ax.text(EX_XC, y_pos, text,
                ha="center", va="center", fontsize=7.5,
                color="#555555", multialignment="center", zorder=4)
        # Arrow: main box right edge → exclusion box left edge
        ax.annotate(
            "", xy=(EX_XC - EX_W / 2, y_pos),
            xytext=(MN_XC + MN_W / 2, y_pos),
            arrowprops=dict(arrowstyle="-|>", color="#AAAAAA", lw=0.8),
            zorder=5,
        )

    # ── Helper: downward arrow between consecutive main boxes ─────────────────
    def darrow(fi: int, ti: int) -> None:
        ax.annotate(
            "", xy=(MN_XC, y[ti] + BOX_H / 2),
            xytext=(MN_XC, y[fi] - BOX_H / 2),
            arrowprops=dict(arrowstyle="-|>", color=C_NEUTRAL, lw=0.9),
            zorder=5,
        )

    # ── Box 0: Citation snowballing ───────────────────────────────────────────
    # G0 corpus = 9 seed papers; 7 were submitted to the retrieval API
    # (retrieval_log has 7 seed entries + a "_meta" key, so len() would be 8).
    n_seeds_api = max(0, len(retrieval_log) - 1) if "_meta" in retrieval_log else len(retrieval_log)
    mbox(0,
         f"Citation snowballing on G0 seeds (9; {n_seeds_api} submitted to API)\n"
         f"G0 → Undermind AI queries → G1–G6 pre-validated corpus (n = 352)\n"
         f"Semantic Scholar · Scopus · ACL Anthology  —  {total_retrieved:,} raw records retrieved")
    darrow(0, 1)

    # ── Box 1: After deduplication ────────────────────────────────────────────
    mbox(1, f"Records after duplicate removal\nn = {total_unique:,}")
    ebox(y[1], f"Duplicates removed\nn = {duplicates:,}", h=0.75)
    darrow(1, 2)

    # ── Box 2: Title screening ────────────────────────────────────────────────
    mbox(2,
         f"Records screened by title\n"
         f"Layer 1: year filter;  Layer 2: keyword scoring\n"
         f"n = {total_unique:,}")
    ebox(y[2],
         f"Excluded (Layer 1 + Layer 2)\nn = {n_title_excl:,}\n"
         f"  • Year < 2021: {excl_year_total:,}\n"
         f"  • No keyword match: {excl_no_kw:,}\n"
         f"  • LLM-only (too broad): {excl_llm_broad:,}",
         h=1.30)
    darrow(2, 3)

    # ── Box 3: LLM triage ────────────────────────────────────────────────────
    triage_n = llm_rows_sent if llm_run else (n_include + n_review)
    mbox(3,
         f"Keyword-matched records assessed by LLM triage\n"
         f"n = {triage_n:,}")
    if llm_run:
        ebox(y[3],
             f"Excluded (LLM triage)\nn = {llm_resolved_exc:,}\n"
             f"Uncertain (manual): {n_uncertain:,}",
             h=0.90)
    darrow(3, 4)

    # ── Box 4: Eligible ───────────────────────────────────────────────────────
    mbox(4,
         f"Records eligible — full-text assessment\nn = {n_include:,}")
    if n_uncertain:
        ebox(y[4],
             f"Manual review queue\n(UNCERTAIN): n = {n_uncertain:,}",
             h=0.75)

    if reviewed_rows:
        darrow(4, 5)

        # ── Box 5: Enriched reading pool ──────────────────────────────────────
        enr_text = (
            f"Enriched reading pool (post-eligibility)\nn = {n_enriched:,}"
            + (f"  (T1: {t1}  T2: {t2}  T3: {t3})" if filtered_rows else "")
        )
        mbox(5, enr_text)
        darrow(5, 6)

        # ── Box 6: Abstract review ────────────────────────────────────────────
        mbox(6,
             f"Abstract review (AI-assisted · manual decision)\n"
             f"n = {len(reviewed_rows):,}   "
             f"KEEP: {n_rev_keep}   SKIP: {n_rev_skip}   DEFER: {n_rev_defer}")
        ebox(y[6],
             f"Excluded at abstract\nn = {n_rev_skip:,}",
             h=0.75)
        darrow(6, 7)

        # ── Box 7: Final reading list ─────────────────────────────────────────
        if final_list_rows:
            fl_tier = Counter(str(r.get("tier") or "?") for r in final_list_rows)
            fl_t1 = fl_tier.get("1", 0)
            fl_t2 = fl_tier.get("2", 0)
            fl_t3 = fl_tier.get("3", 0)
            final_text = (
                f"Final reading list (Stage 8)\n"
                f"n = {len(final_list_rows):,}   "
                f"Tier 1: {fl_t1}   Tier 2: {fl_t2}   Tier 3: {fl_t3}"
            )
        else:
            final_text = (
                f"Studies included in systematic review\n"
                f"n = {n_rev_keep:,}  (KEEP after abstract review)"
            )
        mbox(7, final_text, color=C_HIGHLIGHT, bold=True)

    else:
        # No abstract review — final included box at position 5
        if filtered_rows:
            final_text = (
                f"Studies included in systematic review\n"
                f"n = {n_enriched:,}\n"
                f"Tier 1: {t1}  |  Tier 2: {t2}  |  Tier 3: {t3}"
            )
        else:
            final_text = (
                f"Studies included in systematic review\n"
                f"n = {n_enriched:,}  (pending full-text review)"
            )
        mbox(5, final_text, color=C_HIGHLIGHT, bold=True)

    ax.set_title(
        "PRISMA Screening Flow — Decentralised Adapter-Based LLM Systems SLR",
        fontsize=10, weight="bold", pad=10,
    )
    fig.savefig(out_path, bbox_inches="tight", facecolor="white", dpi=FIG_DPI)
    plt.close(fig)
    print(f"  [fig1] {out_path.name}")


# ─────────────────────────────────────────────────────────────────────────────
# Fig 2 — Snowball wave productivity
# ─────────────────────────────────────────────────────────────────────────────

def fig2_wave_productivity(retrieval_log: dict, out_path: Path) -> None:
    """
    Grouped bar chart: for each seed paper, show backward_new vs forward_new,
    sorted by group (G0 → G6) then total_new descending.
    """
    seeds = list(retrieval_log.items())
    # Sort: group first, then total_new descending within group
    seeds.sort(key=lambda kv: (kv[1].get("group", "G0"), -kv[1].get("total_new", 0)))

    labels      = [kv[0] for kv in seeds]
    bwd_new     = [kv[1].get("backward_new", 0) for kv in seeds]
    fwd_new     = [kv[1].get("forward_new",  0) for kv in seeds]
    groups      = [kv[1].get("group", "G?")     for kv in seeds]

    n = len(labels)
    x = range(n)

    # ── Shorten labels for readability ────────────────────────────────────────
    short = []
    for lbl in labels:
        parts = lbl.split()
        # "Houlsby 2019" → "Houlsby\n2019", "Hu 2021 LoRA" → "Hu 2021\nLoRA"
        if len(parts) >= 3:
            short.append(f"{parts[0]} {parts[1]}\n{' '.join(parts[2:])}")
        elif len(parts) == 2:
            short.append(f"{parts[0]}\n{parts[1]}")
        else:
            short.append(lbl)

    fig, (ax_top, ax_bottom) = plt.subplots(
        2, 1, figsize=(14, 7),
        gridspec_kw={"height_ratios": [3.5, 1]},
        facecolor="white",
    )

    bar_w = 0.38
    bars_bwd = ax_top.bar(
        [i - bar_w / 2 for i in x], bwd_new, bar_w,
        color=C_INCLUDE, alpha=0.85, label="Backward (references)",
        zorder=3,
    )
    bars_fwd = ax_top.bar(
        [i + bar_w / 2 for i in x], fwd_new, bar_w,
        color=C_REVIEW, alpha=0.85, label="Forward (citations)",
        zorder=3,
    )

    # Value labels on non-zero bars
    for bar in list(bars_bwd) + list(bars_fwd):
        h = bar.get_height()
        if h > 0:
            ax_top.text(
                bar.get_x() + bar.get_width() / 2, h + 0.15,
                str(int(h)), ha="center", va="bottom", fontsize=6.5, color="#333333",
            )

    # Group separator lines and labels
    group_changes = [0]
    for i in range(1, n):
        if groups[i] != groups[i - 1]:
            group_changes.append(i)
    group_changes.append(n)

    for idx in range(len(group_changes) - 1):
        start = group_changes[idx]
        end   = group_changes[idx + 1]
        mid   = (start + end - 1) / 2
        grp   = groups[start]
        ax_top.axvspan(start - 0.5, end - 0.5,
                       alpha=0.06 if idx % 2 == 0 else 0.0,
                       color="#AAAAAA", zorder=1)
        ax_top.text(mid, ax_top.get_ylim()[1] if ax_top.get_ylim()[1] > 1 else 1,
                    grp, ha="center", va="bottom",
                    fontsize=8, color=C_NEUTRAL, weight="bold")

    ax_top.set_xticks(list(x))
    ax_top.set_xticklabels(short, fontsize=6.2, rotation=45, ha="right")
    ax_top.set_ylabel("New unique papers discovered")
    ax_top.set_title("Snowball Wave Productivity — New Papers per Seed (Backward vs Forward)",
                     weight="bold")
    ax_top.legend(fontsize=8, framealpha=0.6)
    ax_top.yaxis.grid(True, linestyle=":", alpha=0.5, zorder=0)
    ax_top.set_axisbelow(True)

    # ── Bottom panel: total papers examined per seed ───────────────────────────
    bwd_ex  = [kv[1].get("backward_examined", 0) for kv in seeds]
    fwd_ex  = [kv[1].get("forward_examined",  0) for kv in seeds]

    ax_bottom.bar([i - bar_w / 2 for i in x], bwd_ex, bar_w,
                  color=C_INCLUDE, alpha=0.4, label="Backward examined")
    ax_bottom.bar([i + bar_w / 2 for i in x], fwd_ex, bar_w,
                  color=C_REVIEW, alpha=0.4, label="Forward examined")
    ax_bottom.set_xticks(list(x))
    ax_bottom.set_xticklabels([""] * n)
    ax_bottom.set_ylabel("Examined\n(capped at 200)", fontsize=7.5)
    ax_bottom.yaxis.grid(True, linestyle=":", alpha=0.4)
    ax_bottom.set_axisbelow(True)
    ax_bottom.set_ylim(0, 230)

    fig.tight_layout(h_pad=0.3)
    fig.savefig(out_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  [fig2] {out_path.name}")


# ─────────────────────────────────────────────────────────────────────────────
# Fig 3 — Publication year distribution
# ─────────────────────────────────────────────────────────────────────────────

def fig3_year_distribution(included_rows: list[dict], out_path: Path) -> None:
    """
    Stacked bar chart: included papers by year, stacked by tier (1/2/3).
    A second y-axis shows cumulative count (growth curve).
    """
    TIER_ORDER  = [1, 2, 3]
    TIER_COLORS = {1: "#2166AC", 2: "#4DAC26", 3: "#CCCCCC"}
    TIER_LABELS = {
        1: "Tier 1 (PEFT + Systems + LLM)",
        2: "Tier 2 (PEFT + LLM / PEFT + Systems)",
        3: "Tier 3 (Other / foundational)",
    }

    # Collect year → tier → count
    year_tier: dict[str, Counter] = defaultdict(Counter)
    for row in included_rows:
        yr = str(row.get("year", "")).strip()
        if not yr:
            continue
        try:
            tier = int(row.get("tier") or 3)
        except (ValueError, TypeError):
            tier = 3
        year_tier[yr][tier] += 1

    years = sorted(year_tier.keys())
    totals = [sum(year_tier[y].values()) for y in years]
    cumulative: list[int] = []
    running = 0
    for t in totals:
        running += t
        cumulative.append(running)

    fig, ax = plt.subplots(figsize=(9, 5), facecolor="white")
    ax2 = ax.twinx()

    bottoms = [0] * len(years)
    for tier in TIER_ORDER:
        vals = [year_tier[y].get(tier, 0) for y in years]
        ax.bar(years, vals, bottom=bottoms,
               color=TIER_COLORS[tier], label=TIER_LABELS[tier],
               alpha=0.88, zorder=3, edgecolor="white", linewidth=0.3)
        bottoms = [b + v for b, v in zip(bottoms, vals)]

    # Total labels on top of each bar
    for i, (yr, tot) in enumerate(zip(years, totals)):
        ax.text(i, tot + 0.5, str(tot), ha="center", va="bottom",
                fontsize=8, color="#333333", weight="bold")

    # Cumulative line on ax2
    ax2.plot(range(len(years)), cumulative, color="#555555",
             marker="o", markersize=4, linewidth=1.4,
             linestyle="--", label="Cumulative", zorder=4)
    ax2.set_ylabel("Cumulative included", fontsize=8.5, color="#555555")
    ax2.tick_params(axis="y", labelcolor="#555555", labelsize=8)

    ax.set_xticks(range(len(years)))
    ax.set_xticklabels(years, fontsize=9)
    ax.set_ylabel("Papers included per year")
    ax.set_title("Publication Year Distribution of Included Papers by Tier",
                 weight="bold")
    ax.yaxis.grid(True, linestyle=":", alpha=0.4, zorder=0)
    ax.set_axisbelow(True)

    # Combined legend
    handles, lbls = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(handles + h2, lbls + l2,
              fontsize=7.5, loc="upper left", framealpha=0.7,
              ncol=1)

    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  [fig3] {out_path.name}")


# ─────────────────────────────────────────────────────────────────────────────
# Fig 4 — Screening exclusion funnel
# ─────────────────────────────────────────────────────────────────────────────

def fig4_screening_funnel(screening_log: dict, out_path: Path) -> None:
    """
    Two-panel figure:
      Left  — horizontal funnel (record counts at each stage)
      Right — stacked bar per G-group showing INCLUDE / REVIEW / EXCLUDE
    """
    s = screening_log
    total = s["input"].get("total_records") or s["input"].get("n_rows", 0)
    excl  = s["exclusion_breakdown"]
    dec   = s["decisions"]

    excl_year  = sum(v for k, v in excl.items() if k.startswith("Year"))
    excl_no_kw = excl.get("No keyword match in title", 0)
    excl_broad = excl.get("LLM-only match (too broad)", 0)

    llm = s.get("llm_screening", {})
    llm_run = isinstance(llm, dict)
    llm_exc = llm.get("resolved_exclude", 0) if llm_run else 0
    llm_inc = llm.get("resolved_include", 0) if llm_run else 0
    n_uncertain = llm.get("uncertain_remaining", dec["REVIEW"]) if llm_run else dec["REVIEW"]

    # Funnel stages (label, remaining after this stage, bar color)
    funnel_stages = [
        ("Retrieved\n(raw)",         total,                                    "#BBBBBB"),
        ("After year\nfilter",        total - excl_year,                       "#9ECAE1"),
        ("After keyword\nscreening",  dec["INCLUDE"] + dec["REVIEW"],          "#4292C6"),
        ("After LLM\ntriage",         dec["INCLUDE"] + n_uncertain,            "#2166AC"),
        ("Auto-INCLUDE\n+ uncertain", dec["INCLUDE"],                          C_HIGHLIGHT),
    ]

    by_group = s.get("by_seed_group", {})
    groups   = sorted(by_group.keys())

    fig, (ax_l, ax_r) = plt.subplots(1, 2, figsize=(13, 5.5),
                                      gridspec_kw={"width_ratios": [1.2, 1]},
                                      facecolor="white")

    # ── Left: horizontal funnel ───────────────────────────────────────────────
    max_val = funnel_stages[0][1]
    bar_h   = 0.55
    y_pos   = range(len(funnel_stages) - 1, -1, -1)

    for idx, (yp, (label, val, color)) in enumerate(zip(y_pos, funnel_stages)):
        w = val / max_val * 9  # scale to axis width
        left_off = (10 - w) / 2
        rect = FancyBboxPatch(
            (left_off, yp - bar_h / 2), w, bar_h,
            boxstyle="round,pad=0.04", linewidth=0.6,
            edgecolor="white", facecolor=color,
        )
        ax_l.add_patch(rect)
        ax_l.text(5, yp, f"{val:,}", ha="center", va="center",
                  fontsize=9, weight="bold", color="black")
        ax_l.text(0.1, yp, label, ha="left", va="center",
                  fontsize=8, color="#333333")

        # Drop annotation between stages
        if idx < len(funnel_stages) - 1:
            next_val = funnel_stages[idx + 1][1]
            dropped  = val - next_val
            if dropped > 0:
                ax_l.text(9.9, yp - 0.45,
                          f"−{dropped:,}", ha="right", va="center",
                          fontsize=7.5, color=C_EXCLUDE)

    ax_l.set_xlim(0, 10)
    ax_l.set_ylim(-0.8, len(funnel_stages) - 0.2)
    ax_l.axis("off")
    ax_l.set_title("Screening Funnel — Records at Each Stage",
                   weight="bold", fontsize=9.5)

    # ── Right: stacked bar per G-group ────────────────────────────────────────
    inc_vals = [by_group[g].get("INCLUDE", 0) for g in groups]
    rev_vals = [by_group[g].get("REVIEW",  0) for g in groups]
    exc_vals = [by_group[g].get("EXCLUDE", 0) for g in groups]
    x        = range(len(groups))

    ax_r.bar(x, exc_vals, label="EXCLUDE", color=C_EXCLUDE, alpha=0.8, zorder=3)
    ax_r.bar(x, rev_vals, bottom=exc_vals, label="REVIEW / UNCERTAIN",
             color=C_REVIEW, alpha=0.8, zorder=3)
    ax_r.bar(x, inc_vals,
             bottom=[e + r for e, r in zip(exc_vals, rev_vals)],
             label="INCLUDE", color=C_INCLUDE, alpha=0.8, zorder=3)

    # Percentage INCLUDE labels
    for i, (g, inc, rev, exc) in enumerate(zip(groups, inc_vals, rev_vals, exc_vals)):
        tot = inc + rev + exc
        pct = inc / tot * 100 if tot > 0 else 0
        ax_r.text(i, tot + 5, f"{pct:.0f}%", ha="center", va="bottom",
                  fontsize=7.5, color=C_INCLUDE, weight="bold")

    ax_r.set_xticks(list(x))
    ax_r.set_xticklabels(groups, fontsize=9)
    ax_r.set_ylabel("Papers")
    ax_r.set_title("Screening Decisions by Seed Group", weight="bold", fontsize=9.5)
    ax_r.legend(fontsize=8, framealpha=0.6)
    ax_r.yaxis.grid(True, linestyle=":", alpha=0.4, zorder=0)
    ax_r.set_axisbelow(True)

    fig.tight_layout(w_pad=3)
    fig.savefig(out_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  [fig4] {out_path.name}")


# ─────────────────────────────────────────────────────────────────────────────
# Fig 5 — Venue / outlet distribution
# ─────────────────────────────────────────────────────────────────────────────

# Keywords that identify conference proceedings
_CONF_TOKENS = {
    "conference", "workshop", "symposium", "proceedings", "annual meeting",
    "emnlp", "acl", "iclr", "neurips", "nips", "icml", "naacl", "aaai",
    "ijcai", "coling", "lrec", "icassp", "cvpr", "eccv", "iccv", "kdd",
    "ijcnlp", "eacl", "aacl", "wsdm", "sigir", "www ", "icdm", "emnlp",
    "interspeech", "acmmm", "cogsci", "aistats", "uai", "emnlp",
}

# Keywords that identify journals / transactions
_JOUR_TOKENS = {
    "transactions", "journal", "review", "letters", "magazine",
    "patterns", "reports", "computing", "networks", "systems",
    "intelligence", "neurocomputing", "displays", "scientometrics",
    "information sciences", "expert systems", "knowledge-based",
    "neural networks", "ocean engineering",
}


def _classify_venue(raw: str) -> str:
    """Return 'preprint', 'conference', 'journal', or 'other'."""
    v = raw.strip().lower()
    if not v:
        return "other"
    if v.startswith("arxiv") or "arxiv.org" in v:
        return "preprint"
    if "github" in v:
        return "other"
    for tok in _CONF_TOKENS:
        if tok in v:
            return "conference"
    for tok in _JOUR_TOKENS:
        if tok in v:
            return "journal"
    return "other"


def _clean_venue(raw: str) -> str:
    """Normalise display name: collapse arXiv variants, trim whitespace."""
    v = raw.strip()
    if not v:
        return ""
    vl = v.lower()
    if vl.startswith("arxiv") or "arxiv.org" in vl:
        return "arXiv (preprint)"
    if "github" in vl:
        return None   # type: ignore  # signals: drop this row
    return v


def fig5_venues(included_rows: list[dict], out_path: Path, top_n: int = 15) -> None:
    """
    Two-panel figure:
      Left  — horizontal bar chart of top N venues (arXiv collapsed, github dropped)
      Right — donut chart: conference vs journal vs preprint vs other
    """
    venue_counts: Counter = Counter()
    type_counts:  Counter = Counter()

    for row in included_rows:
        raw = row.get("venue", "") or ""
        cleaned = _clean_venue(raw)
        if cleaned is None:          # github — skip entirely
            continue
        vtype = _classify_venue(raw)
        type_counts[vtype] += 1
        if cleaned:
            venue_counts[cleaned] += 1
        # papers with empty venue are still counted in type_counts as "other"

    top_venues = venue_counts.most_common(top_n)
    labels_bar  = [v for v, _ in top_venues]
    values_bar  = [n for _, n in top_venues]

    # Colour each bar by outlet type
    bar_colors = []
    TYPE_COLOR = {
        "preprint":   "#FDAE61",
        "conference": C_INCLUDE,
        "journal":    C_REVIEW,
        "other":      "#AAAAAA",
    }
    for lbl in labels_bar:
        if lbl == "arXiv (preprint)":
            bar_colors.append(TYPE_COLOR["preprint"])
        else:
            raw_for_type = lbl   # close enough for classification
            bar_colors.append(TYPE_COLOR[_classify_venue(raw_for_type)])

    # Donut data
    donut_labels = ["Conference", "Journal", "Preprint", "Other / unknown"]
    donut_values = [
        type_counts.get("conference", 0),
        type_counts.get("journal",    0),
        type_counts.get("preprint",   0),
        type_counts.get("other",      0),
    ]
    donut_colors = [TYPE_COLOR["conference"], TYPE_COLOR["journal"],
                    TYPE_COLOR["preprint"],   TYPE_COLOR["other"]]

    # ── Canvas ────────────────────────────────────────────────────────────────
    fig, (ax_l, ax_r) = plt.subplots(
        1, 2, figsize=(13, 6),
        gridspec_kw={"width_ratios": [2.2, 1]},
        facecolor="white",
    )

    # ── Left: horizontal bar chart ────────────────────────────────────────────
    y_pos = range(len(labels_bar) - 1, -1, -1)   # top-to-bottom order
    bars  = ax_l.barh(list(y_pos), values_bar, color=bar_colors,
                      alpha=0.88, edgecolor="white", linewidth=0.4, zorder=3)

    for bar, val in zip(bars, values_bar):
        ax_l.text(bar.get_width() + 1.5, bar.get_y() + bar.get_height() / 2,
                  str(val), va="center", fontsize=8, color="#333333")

    ax_l.set_yticks(list(y_pos))
    ax_l.set_yticklabels(labels_bar, fontsize=8.5)
    ax_l.set_xlabel("Included papers")
    ax_l.set_title(f"Top {top_n} Publication Venues / Outlets", weight="bold")
    ax_l.xaxis.grid(True, linestyle=":", alpha=0.4, zorder=0)
    ax_l.set_axisbelow(True)

    # Legend for bar colours
    legend_patches = [
        mpatches.Patch(color=TYPE_COLOR["conference"], label="Conference"),
        mpatches.Patch(color=TYPE_COLOR["journal"],    label="Journal"),
        mpatches.Patch(color=TYPE_COLOR["preprint"],   label="Preprint (arXiv)"),
        mpatches.Patch(color=TYPE_COLOR["other"],      label="Other"),
    ]
    ax_l.legend(handles=legend_patches, fontsize=8, loc="lower right",
                framealpha=0.7)

    # ── Right: donut chart ────────────────────────────────────────────────────
    total_donut = sum(donut_values)
    wedge_labels = [
        f"{lbl}\n{val} ({val/total_donut*100:.0f}%)"
        for lbl, val in zip(donut_labels, donut_values)
        if val > 0
    ]
    wedge_values = [v for v in donut_values if v > 0]
    wedge_colors = [c for c, v in zip(donut_colors, donut_values) if v > 0]

    wedges, texts = ax_r.pie(
        wedge_values,
        labels=wedge_labels,
        colors=wedge_colors,
        startangle=90,
        wedgeprops={"width": 0.5, "edgecolor": "white", "linewidth": 1.2},
        textprops={"fontsize": 8},
        labeldistance=1.12,
    )
    ax_r.text(0, 0, f"{total_donut}\ntotal", ha="center", va="center",
              fontsize=10, weight="bold", color="#333333")
    ax_r.set_title("Outlet Type Breakdown", weight="bold")

    fig.tight_layout(w_pad=3)
    fig.savefig(out_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  [fig5] {out_path.name}")


# ─────────────────────────────────────────────────────────────────────────────
# Fig 6 — Tier breakdown
# ─────────────────────────────────────────────────────────────────────────────

def fig6_tier_breakdown(filtered_rows: list[dict], out_path: Path) -> None:
    """
    Two-panel tier analysis:
      Left  — stacked bar: paper count per tier, broken down by discovery direction
      Right — box plot: citation count distribution per tier
    """
    TIER_COLORS = {1: "#2166AC", 2: "#4DAC26", 3: "#CCCCCC"}
    TIER_LABELS = {
        1: "Tier 1\n(PEFT+Systems+LLM)",
        2: "Tier 2\n(PEFT+LLM)",
        3: "Tier 3\n(Other)",
    }
    DIR_COLORS = {
        "PREVALIDATED": "#AAAAAA",
        "BACKWARD":     "#2166AC",
        "FORWARD":      "#4DAC26",
        "SEED":         "#F4A582",
    }
    DIRS_ORDER = ["PREVALIDATED", "BACKWARD", "FORWARD", "SEED"]

    tier_dir:   dict[int, Counter] = {1: Counter(), 2: Counter(), 3: Counter()}
    tier_cites: dict[int, list]    = {1: [], 2: [], 3: []}

    for row in filtered_rows:
        try:
            t = int(row.get("tier") or 3)
        except (ValueError, TypeError):
            t = 3
        d = (row.get("direction") or "PREVALIDATED").upper()
        tier_dir[t][d] += 1
        try:
            c = int(row.get("citation_count") or 0)
        except (ValueError, TypeError):
            c = 0
        tier_cites[t].append(c)

    fig, (ax_l, ax_r) = plt.subplots(
        1, 2, figsize=(13, 6),
        gridspec_kw={"width_ratios": [1.2, 1]},
        facecolor="white",
    )

    # ── Left: stacked bar by direction within each tier ───────────────────────
    x = [1, 2, 3]
    bottoms = [0, 0, 0]
    for direction in DIRS_ORDER:
        vals = [tier_dir[t].get(direction, 0) for t in x]
        bars = ax_l.bar(
            x, vals, bottom=bottoms,
            color=DIR_COLORS.get(direction, "#AAAAAA"),
            label=direction.capitalize(), alpha=0.88,
            edgecolor="white", linewidth=0.5, zorder=3,
        )
        for bar, val in zip(bars, vals):
            if val > 5:
                yc = bar.get_y() + bar.get_height() / 2
                ax_l.text(
                    bar.get_x() + bar.get_width() / 2, yc, str(val),
                    ha="center", va="center", fontsize=8,
                    color="white" if direction in ("BACKWARD", "SEED") else "#333333",
                )
        bottoms = [b + v for b, v in zip(bottoms, vals)]

    for xi, tot in zip(x, bottoms):
        ax_l.text(xi, tot + 2, str(tot), ha="center", va="bottom",
                  fontsize=9, weight="bold", color="#333333")

    ax_l.set_xticks(x)
    ax_l.set_xticklabels([TIER_LABELS[t] for t in x], fontsize=9)
    ax_l.set_ylabel("Papers")
    ax_l.set_title("Paper Count by Tier and Discovery Direction", weight="bold")
    ax_l.legend(fontsize=8, framealpha=0.6)
    ax_l.yaxis.grid(True, linestyle=":", alpha=0.4, zorder=0)
    ax_l.set_axisbelow(True)

    # ── Right: citation count box plot per tier (95th-pct cap) ───────────────
    all_vals = [c for lst in tier_cites.values() for c in lst if c > 0]
    cap = sorted(all_vals)[int(len(all_vals) * 0.95)] if len(all_vals) > 20 else (max(all_vals) if all_vals else 100)

    data_capped = [[min(c, cap) for c in tier_cites[t]] for t in [1, 2, 3]]
    bp = ax_r.boxplot(
        data_capped, patch_artist=True, notch=False,
        medianprops={"color": "black", "linewidth": 1.5},
        flierprops={"marker": ".", "markersize": 3, "alpha": 0.4},
    )
    for patch, t in zip(bp["boxes"], [1, 2, 3]):
        patch.set_facecolor(TIER_COLORS[t])
        patch.set_alpha(0.7)

    ax_r.set_xticks([1, 2, 3])
    ax_r.set_xticklabels([f"Tier {t}" for t in [1, 2, 3]], fontsize=9)
    ax_r.set_ylabel(f"Citation count (capped at {cap:,} for readability)")
    ax_r.set_title("Citation Count Distribution by Tier", weight="bold")
    ax_r.yaxis.grid(True, linestyle=":", alpha=0.4, zorder=0)
    ax_r.set_axisbelow(True)

    # Median annotations
    for i, d in enumerate(data_capped):
        if d:
            median = sorted(d)[len(d) // 2]
            ax_r.text(i + 1.05, median, f" med={median}",
                      va="center", fontsize=7.5, color="#333333")

    fig.tight_layout(w_pad=3)
    fig.savefig(out_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  [fig6] {out_path.name}")


# ─────────────────────────────────────────────────────────────────────────────
# Fig 7 — Abstract review decisions
# ─────────────────────────────────────────────────────────────────────────────

def fig7_abstract_review(reviewed_rows: list[dict], out_path: Path) -> None:
    """
    Two-panel figure summarising abstract review decisions.
      Left  — stacked horizontal bar per tier: KEEP / SKIP / DEFER / undecided
      Right — donut chart of overall KEEP / SKIP / DEFER breakdown
    """
    DECISION_ORDER  = ["KEEP", "DEFER", "undecided", "SKIP"]
    DECISION_COLORS = {
        "KEEP":      "#2166AC",
        "DEFER":     "#FEC44F",
        "undecided": "#CCCCCC",
        "SKIP":      "#D6604D",
    }
    TIER_ORDER  = ["1", "2", "3", "?"]
    TIER_LABELS = {"1": "Tier 1", "2": "Tier 2", "3": "Tier 3", "?": "Unknown"}

    # Aggregate: tier → decision → count
    tier_dec: dict[str, Counter] = {t: Counter() for t in TIER_ORDER}
    for row in reviewed_rows:
        t = str(row.get("tier") or "?")
        if t not in tier_dec:
            t = "?"
        d = row.get("abstract_decision", "") or "undecided"
        tier_dec[t][d] += 1

    # Filter to tiers that have data
    active_tiers = [t for t in TIER_ORDER if sum(tier_dec[t].values()) > 0]

    overall = Counter(r.get("abstract_decision", "") or "undecided" for r in reviewed_rows)

    fig, (ax_l, ax_r) = plt.subplots(
        1, 2, figsize=(13, max(4, len(active_tiers) * 1.4 + 2)),
        gridspec_kw={"width_ratios": [2, 1]},
        facecolor="white",
    )

    # ── Left: stacked horizontal bar per tier ─────────────────────────────────
    y_pos    = range(len(active_tiers))
    lefts    = [0] * len(active_tiers)
    patches  = []

    for dec in DECISION_ORDER:
        vals  = [tier_dec[t].get(dec, 0) for t in active_tiers]
        color = DECISION_COLORS[dec]
        bars  = ax_l.barh(
            list(y_pos), vals, left=lefts,
            color=color, alpha=0.88,
            edgecolor="white", linewidth=0.5, zorder=3,
            label=dec.capitalize(),
        )
        patches.append(mpatches.Patch(color=color, alpha=0.88, label=dec.capitalize()))
        for bar, val in zip(bars, vals):
            if val >= 5:
                xc = bar.get_x() + bar.get_width() / 2
                yc = bar.get_y() + bar.get_height() / 2
                ax_l.text(xc, yc, str(val), ha="center", va="center",
                          fontsize=8, color="white" if dec in ("KEEP", "SKIP") else "#333333",
                          weight="bold")
        lefts = [l + v for l, v in zip(lefts, vals)]

    # Totals at end of each bar
    for i, t in enumerate(active_tiers):
        tot = sum(tier_dec[t].values())
        ax_l.text(lefts[i] + 1, i, f" {tot}", va="center", fontsize=8.5, color="#333333")

    ax_l.set_yticks(list(y_pos))
    ax_l.set_yticklabels([TIER_LABELS.get(t, t) for t in active_tiers], fontsize=10)
    ax_l.set_xlabel("Papers")
    ax_l.set_title("Abstract Review Decisions by Tier", weight="bold")
    ax_l.legend(handles=patches, fontsize=9, loc="lower right", framealpha=0.7)
    ax_l.xaxis.grid(True, linestyle=":", alpha=0.4, zorder=0)
    ax_l.set_axisbelow(True)

    # ── Right: overall donut ───────────────────────────────────────────────────
    donut_order  = [d for d in DECISION_ORDER if overall.get(d, 0) > 0]
    donut_vals   = [overall[d] for d in donut_order]
    donut_colors = [DECISION_COLORS[d] for d in donut_order]
    total_rev    = sum(donut_vals)

    wedge_labels = [
        f"{d.capitalize()}\n{v} ({v/total_rev*100:.0f}%)"
        for d, v in zip(donut_order, donut_vals)
    ]
    ax_r.pie(
        donut_vals,
        labels=wedge_labels,
        colors=donut_colors,
        startangle=90,
        wedgeprops={"width": 0.52, "edgecolor": "white", "linewidth": 1.2},
        textprops={"fontsize": 9},
        labeldistance=1.14,
    )
    ax_r.text(0, 0, f"{total_rev}\nreviewed", ha="center", va="center",
              fontsize=10, weight="bold", color="#333333")
    ax_r.set_title("Overall Abstract Review Breakdown", weight="bold")

    fig.tight_layout(w_pad=3)
    fig.savefig(out_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  [fig7] {out_path.name}")


# ─────────────────────────────────────────────────────────────────────────────
# Fig 9 — Final reading list breakdown (tier · venue quality · G-group · source)
# ─────────────────────────────────────────────────────────────────────────────

def fig9_final_breakdown(
    final_rows: list[dict],
    out_path: Path,
) -> None:
    """
    4-panel figure for the final reading list (13_final_reading_list_*.csv):
      Top-left  — G-group horizontal stacked bar (stacked by tier)
      Top-right — Tier donut
      Bottom-left — Venue quality horizontal bar
      Bottom-right — Source bar (SCOPUS / ARXIV / other)
    """
    TIER_COLORS = {"1": "#2166AC", "2": "#4DAC26", "3": "#CCCCCC", "?": "#AAAAAA"}
    TIER_LABELS = {"1": "Tier 1", "2": "Tier 2", "3": "Tier 3"}
    VQ_COLOR = {
        "top_venue":    "#2166AC",
        "peer_reviewed": "#4DAC26",
        "preprint":     "#FDAE61",
        "unknown":      "#AAAAAA",
    }
    VQ_LABELS = {
        "top_venue":     "Top venue (CORE A* / Scopus Q1)",
        "peer_reviewed": "Peer-reviewed (other conf / journal)",
        "preprint":      "Preprint (arXiv only)",
        "unknown":       "Unknown / missing venue",
    }
    SRC_COLOR = {
        "SCOPUS": "#2166AC",
        "WOS":    "#4DAC26",
        "ARXIV":  "#FDAE61",
        "Other":  "#AAAAAA",
    }

    # ── Aggregate data ────────────────────────────────────────────────────────
    GROUP_ORDER = ["G0", "G1", "G2", "G3", "G4", "G5", "G6", "Other"]
    TIER_ORDER  = ["1", "2", "3"]

    # G-group × tier
    grp_tier: dict[str, Counter] = {g: Counter() for g in GROUP_ORDER}
    for row in final_rows:
        g = row.get("_seed_group") or "Other"
        if g not in grp_tier:
            g = "Other"
        t = str(row.get("tier") or "?")
        grp_tier[g][t] += 1

    active_groups = [g for g in GROUP_ORDER if sum(grp_tier[g].values()) > 0]

    # Tier totals
    tier_counts: Counter = Counter()
    for row in final_rows:
        tier_counts[str(row.get("tier") or "?")] += 1

    # Venue quality
    vq_counts: Counter = Counter()
    for row in final_rows:
        venue  = (row.get("venue")  or "").strip()
        source = (row.get("source") or "").strip().upper()
        doi    = (row.get("doi")    or "").strip()
        if source == "ARXIV" or venue.lower().startswith("arxiv"):
            vq = "preprint"
        elif not venue:
            vq = "unknown"
        else:
            vl = venue.lower()
            is_top = any(tok in vl for tok in {
                "iclr", "neurips", "nips", "icml", "acl", "emnlp", "naacl",
                "mlsys", "aaai", "ijcai", "tacl", "tmlr", "jmlr",
                "annual meeting of the association",
                "future generation computer systems",
                "transactions on machine learning",
            })
            vq = "top_venue" if is_top else "peer_reviewed"
        vq_counts[vq] += 1

    # Source
    src_counts: Counter = Counter()
    for row in final_rows:
        s = (row.get("source") or "").strip().upper()
        if s in ("SCOPUS", "WOS", "ARXIV"):
            src_counts[s] += 1
        else:
            src_counts["Other"] += 1

    # ── Canvas ────────────────────────────────────────────────────────────────
    fig, axes = plt.subplots(2, 2, figsize=(14, 10), facecolor="white")
    ax_tl = axes[0, 0]   # G-group stacked bar
    ax_tr = axes[0, 1]   # Tier donut
    ax_bl = axes[1, 0]   # Venue quality bar
    ax_br = axes[1, 1]   # Source bar

    # ── Top-left: G-group horizontal stacked bar ──────────────────────────────
    y_pos   = range(len(active_groups))
    lefts   = [0] * len(active_groups)
    patches = []
    for tier in TIER_ORDER:
        vals  = [grp_tier[g].get(tier, 0) for g in active_groups]
        color = TIER_COLORS[tier]
        bars  = ax_tl.barh(list(y_pos), vals, left=lefts,
                            color=color, alpha=0.88,
                            edgecolor="white", linewidth=0.5, zorder=3,
                            label=TIER_LABELS[tier])
        patches.append(mpatches.Patch(color=color, alpha=0.88, label=TIER_LABELS[tier]))
        for bar, val in zip(bars, vals):
            if val >= 3:
                xc = bar.get_x() + bar.get_width() / 2
                yc = bar.get_y() + bar.get_height() / 2
                ax_tl.text(xc, yc, str(val), ha="center", va="center",
                           fontsize=7.5, color="white" if tier == "1" else "#333333",
                           weight="bold")
        lefts = [l + v for l, v in zip(lefts, vals)]

    for i, g in enumerate(active_groups):
        tot = sum(grp_tier[g].values())
        ax_tl.text(lefts[i] + 0.4, i, f" {tot}", va="center", fontsize=8.5, color="#333333")

    ax_tl.set_yticks(list(y_pos))
    ax_tl.set_yticklabels(
        [GROUP_LABELS.get(g, g) for g in active_groups], fontsize=8.5
    )
    ax_tl.invert_yaxis()
    ax_tl.set_xlabel("Papers in final reading list")
    ax_tl.set_title("Papers by G-group and Tier", weight="bold")
    ax_tl.legend(handles=patches, fontsize=8.5, loc="lower right", framealpha=0.7)
    ax_tl.xaxis.grid(True, linestyle=":", alpha=0.4, zorder=0)
    ax_tl.set_axisbelow(True)

    # ── Top-right: Tier donut ─────────────────────────────────────────────────
    donut_order  = [t for t in TIER_ORDER if tier_counts.get(t, 0) > 0]
    donut_vals   = [tier_counts[t] for t in donut_order]
    donut_colors = [TIER_COLORS[t] for t in donut_order]
    total_papers = len(final_rows)

    wedge_labels = [
        f"{TIER_LABELS[t]}\n{v} ({v/total_papers*100:.0f}%)"
        for t, v in zip(donut_order, donut_vals)
    ]
    ax_tr.pie(
        donut_vals, labels=wedge_labels, colors=donut_colors,
        startangle=90,
        wedgeprops={"width": 0.52, "edgecolor": "white", "linewidth": 1.2},
        textprops={"fontsize": 9},
        labeldistance=1.14,
    )
    ax_tr.text(0, 0, f"{total_papers}\npapers", ha="center", va="center",
               fontsize=11, weight="bold", color="#333333")
    ax_tr.set_title("Tier Distribution", weight="bold")

    # ── Bottom-left: Venue quality horizontal bar ─────────────────────────────
    vq_order  = ["top_venue", "peer_reviewed", "preprint", "unknown"]
    vq_vals   = [vq_counts.get(vq, 0) for vq in vq_order]
    vq_labels = [VQ_LABELS[vq] for vq in vq_order]
    vq_colors = [VQ_COLOR[vq] for vq in vq_order]
    total_vq  = sum(vq_vals)

    bars = ax_bl.barh(range(len(vq_order)), vq_vals,
                      color=vq_colors, alpha=0.88, edgecolor="white", linewidth=0.4, zorder=3)
    for bar, val in zip(bars, vq_vals):
        pct = f"{val/total_vq*100:.0f}%" if total_vq else ""
        ax_bl.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height() / 2,
                   f"{val}  ({pct})", va="center", fontsize=8.5, color="#333333")

    ax_bl.set_yticks(range(len(vq_order)))
    ax_bl.set_yticklabels(vq_labels, fontsize=8.5)
    ax_bl.invert_yaxis()
    ax_bl.set_xlabel("Papers")
    ax_bl.set_xlim(0, max(vq_vals) * 1.3 if vq_vals else 10)
    ax_bl.set_title("Venue Quality of Final Reading List", weight="bold")
    ax_bl.xaxis.grid(True, linestyle=":", alpha=0.4, zorder=0)
    ax_bl.set_axisbelow(True)

    # ── Bottom-right: Source bar ──────────────────────────────────────────────
    src_order  = ["SCOPUS", "WOS", "ARXIV", "Other"]
    src_vals   = [src_counts.get(s, 0) for s in src_order]
    src_colors = [SRC_COLOR[s] for s in src_order]
    total_src  = sum(src_vals)

    bars2 = ax_br.bar(range(len(src_order)), src_vals,
                      color=src_colors, alpha=0.88, edgecolor="white", linewidth=0.4, zorder=3)
    for bar, val in zip(bars2, src_vals):
        pct = f"{val/total_src*100:.0f}%" if total_src else ""
        ax_br.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,
                   f"{val}\n({pct})", ha="center", va="bottom",
                   fontsize=8.5, color="#333333", weight="bold")

    ax_br.set_xticks(range(len(src_order)))
    ax_br.set_xticklabels(src_order, fontsize=10)
    ax_br.set_ylabel("Papers")
    ax_br.set_ylim(0, max(src_vals) * 1.25 if src_vals else 10)
    ax_br.set_title("Discovery Source", weight="bold")
    ax_br.yaxis.grid(True, linestyle=":", alpha=0.4, zorder=0)
    ax_br.set_axisbelow(True)

    # ── Global title ──────────────────────────────────────────────────────────
    fig.suptitle(
        f"Final Reading List — {total_papers} papers  (Decentralised Adapter-Based LLM Systems SLR)",
        fontsize=11, weight="bold", y=1.01,
    )

    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  [fig9] {out_path.name}")


# ─────────────────────────────────────────────────────────────────────────────
# File auto-discovery helpers
# ─────────────────────────────────────────────────────────────────────────────

def _latest(directory: Path, pattern: str) -> Path | None:
    files = sorted(directory.glob(pattern), reverse=True)
    return files[0] if files else None


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _load_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _load_groups(papers_repo: Path) -> dict[str, list[dict]]:
    """Return {G1: [row, …], …} by reading G[1-6]_*.csv directly from papers_repo."""
    import re
    groups: dict[str, list[dict]] = {}
    for path in sorted(papers_repo.glob("G[1-6]_*.csv")):
        m = re.match(r"(G\d)", path.stem)
        if not m:
            continue
        with path.open(newline="", encoding="utf-8-sig") as fh:
            groups[m.group(1)] = list(csv.DictReader(fh))
    return groups


# ─────────────────────────────────────────────────────────────────────────────
# Fig 8 — G-group breakdown (reads G*.csv directly, no pipeline logs needed)
# ─────────────────────────────────────────────────────────────────────────────

def fig8_groups(papers_repo: Path, out_path: Path) -> None:
    """
    Horizontal stacked bar: paper count per G-group (G1–G6) broken down by
    outlet type (Conference / Journal / Preprint / Other).

    Reads directly from papers_repo/G[1-6]_*.csv — works before the pipeline
    has been run.
    """
    groups = _load_groups(papers_repo)
    if not groups:
        print(f"  [fig8] skipped — no G*.csv files found in {papers_repo}")
        return

    TYPE_COLOR = {
        "conference": C_INCLUDE,
        "journal":    C_REVIEW,
        "preprint":   "#FDAE61",
        "other":      "#AAAAAA",
    }
    outlet_order = ["conference", "journal", "preprint", "other"]
    gids = sorted(groups.keys())

    # {gid: Counter(outlet_type → count)}
    data: dict[str, Counter] = {}
    for gid, rows in groups.items():
        c: Counter = Counter()
        for r in rows:
            raw = r.get("Journal", "").strip()
            c[_classify_venue(raw)] += 1
        data[gid] = c

    fig, ax = plt.subplots(figsize=(10, 4.5), facecolor="white")

    bottoms = [0] * len(gids)
    for otype in outlet_order:
        vals = [data[g].get(otype, 0) for g in gids]
        ax.barh(gids, vals, left=bottoms,
                color=TYPE_COLOR[otype], edgecolor="white",
                linewidth=0.4, label=otype.capitalize())
        bottoms = [b + v for b, v in zip(bottoms, vals)]

    # Total count label at end of each bar
    totals = [len(groups[g]) for g in gids]
    for i, (gid, total) in enumerate(zip(gids, totals)):
        ax.text(total + 1, i, str(total), va="center", fontsize=9, weight="bold")

    # Full group name annotations to the left of bars
    for i, gid in enumerate(gids):
        ax.text(-3, i, GROUP_LABELS.get(gid, gid),
                ha="right", va="center", fontsize=8, color="#444444")

    ax.set_xlim(-60, max(totals) * 1.15)
    ax.set_xlabel("Number of papers")
    ax.set_title("Papers per Snowball Wave (G-group) by Outlet Type", weight="bold")
    ax.invert_yaxis()
    ax.legend(fontsize=8.5, loc="lower right", framealpha=0.85)
    ax.set_yticks([])

    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  [fig8] {out_path.name}")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate SLR visualisation figures (Watson et al. 2017)"
    )
    parser.add_argument("--retrieval", type=Path, default=None,
                        help="Path to log_retrieval_*.json (auto-detected if omitted)")
    parser.add_argument("--screening", type=Path, default=None,
                        help="Path to log_screening_*.json (auto-detected if omitted)")
    parser.add_argument("--included",  type=Path, default=None,
                        help="Path to 07_filtered_*.csv or 04_included_*.csv (auto-detected if omitted)")
    parser.add_argument("--reviewed",  type=Path, default=None,
                        help="Path to 08_abstract_reviewed_*.csv (default: 2026-04-21 canonical, "
                             "then latest)")
    parser.add_argument("--final-list", type=Path, default=None,
                        help="Path to 13_final_reading_list_*.csv (auto-detected if omitted)")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="Directory for output PNGs (default: data/figures/)")
    parser.add_argument("--figures", nargs="+",
                        choices=["1", "2", "3", "4", "5", "6", "7", "8", "9"],
                        default=["1", "2", "3", "4", "5", "6", "7", "8", "9"],
                        help="Which figures to generate (default: all)")
    args = parser.parse_args()

    # ── Locate dirs (repo root = parent of app/) ───────────────────────────────
    here         = Path(__file__).resolve().parent   # app/
    pkg_root     = here.parent                       # repository root
    project_root = pkg_root.parent                   # parent of repo root
    snowball_dir = OUTPUT_DIR                        # honors SLR_OUTPUT_DIR override
    papers_repo  = PAPERS_REPO                       # data/inputs snapshot

    # ── Resolve output directory (data/figures, or the override's figures/) ────
    out_dir = args.output_dir or FIGURES_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output dir    : {out_dir}\n")

    # ── Fig 8 — G-group breakdown (no pipeline logs needed) ───────────────────
    # Rendered first so it works even when the pipeline hasn't been run yet.
    if "8" in args.figures:
        fig8_groups(papers_repo, out_dir / "fig_slr8_groups.png")

    # ── Figs 1–7 require pipeline log files ──────────────────────────────────
    pipeline_figs = [f for f in args.figures if f != "8"]
    if not pipeline_figs:
        print("\nDone.")
        return

    from slr_engine.config import find_output
    retrieval_path = args.retrieval or find_output("log_retrieval_*.json")
    screening_path = args.screening or find_output("log_screening_*.json")
    if args.included:
        included_path = args.included
    else:
        included_path = (
            find_output("S6_enriched_reading_pool.csv")
            or find_output("07_filtered_*.csv")
            or find_output("04_included_*.csv")
        )
    # Canonical abstract review: S7b = final (DEFER resolved), S7a = original base
    reviewed_path = args.reviewed or (
        find_output("S7b_abstract_reviewed_final.csv")
        or find_output("S7a_abstract_reviewed_base.csv")
        or find_output("08_abstract_reviewed_*.csv")
    )
    # Final reading list — canonical file is 13_final_reading_list_*.csv
    final_list_path = args.final_list or find_output("13_final_reading_list_*.csv")

    for label, path in [
        ("retrieval log", retrieval_path),
        ("screening log", screening_path),
        ("included CSV",  included_path),
    ]:
        if path is None or not path.exists():
            print(f"ERROR: Cannot find {label}. Pass --retrieval / --screening / --included.")
            raise SystemExit(1)

    print(f"Retrieval log : {retrieval_path.name}")
    print(f"Screening log : {screening_path.name}")
    print(f"Included CSV  : {included_path.name}")

    retrieval_log = _load_json(retrieval_path)
    screening_log = _load_json(screening_path)
    included_rows = _load_csv(included_path)

    reviewed_rows: list[dict] | None = None
    if reviewed_path and reviewed_path.exists():
        reviewed_rows = _load_csv(reviewed_path)
        print(f"Reviewed CSV  : {reviewed_path.name}  ({len(reviewed_rows)} rows)")
    else:
        print("Reviewed CSV  : not found — fig1 will omit abstract review stage, fig7 skipped")

    final_list_rows: list[dict] | None = None
    if final_list_path and final_list_path.exists():
        final_list_rows = _load_csv(final_list_path)
        # Cross-reference G-group from reviewed CSV
        if reviewed_rows:
            _enrich_groups(final_list_rows, reviewed_rows)
        print(f"Final list    : {final_list_path.name}  ({len(final_list_rows)} papers)")
    else:
        print("Final list    : not found — fig9 skipped")

    print(f"  Seeds: {len(retrieval_log)}  |  Included: {len(included_rows)}"
          + (f"  |  Reviewed: {len(reviewed_rows)}" if reviewed_rows else "")
          + (f"  |  Final list: {len(final_list_rows)}" if final_list_rows else ""))
    print()

    if "1" in pipeline_figs:
        fig1_prisma(screening_log, retrieval_log,
                    out_dir / "fig_slr1_prisma_flow.png",
                    filtered_rows=included_rows,
                    reviewed_rows=reviewed_rows,
                    final_list_rows=final_list_rows)
        # Also emit a vector PDF for the paper (matplotlib infers the format
        # from the .pdf extension); same figure, rebuilt from the same inputs.
        fig1_prisma(screening_log, retrieval_log,
                    out_dir / "fig_slr1_prisma_flow.pdf",
                    filtered_rows=included_rows,
                    reviewed_rows=reviewed_rows,
                    final_list_rows=final_list_rows)
    if "2" in pipeline_figs:
        fig2_wave_productivity(retrieval_log,
                               out_dir / "fig_slr2_wave_productivity.png")
        fig2_wave_productivity(retrieval_log,
                               out_dir / "fig_slr2_wave_productivity.pdf")
    if "3" in pipeline_figs:
        fig3_year_distribution(
            final_list_rows if final_list_rows else included_rows,
            out_dir / "fig_slr3_year_distribution.png",
        )
        fig3_year_distribution(
            final_list_rows if final_list_rows else included_rows,
            out_dir / "fig_slr3_year_distribution.pdf",
        )
    if "4" in pipeline_figs:
        fig4_screening_funnel(screening_log,
                              out_dir / "fig_slr4_screening_funnel.png")
        fig4_screening_funnel(screening_log,
                              out_dir / "fig_slr4_screening_funnel.pdf")
    if "5" in pipeline_figs:
        fig5_venues(
            final_list_rows if final_list_rows else included_rows,
            out_dir / "fig_slr5_venues.png",
        )
        fig5_venues(
            final_list_rows if final_list_rows else included_rows,
            out_dir / "fig_slr5_venues.pdf",
        )
    if "6" in pipeline_figs:
        fig6_tier_breakdown(
            final_list_rows if final_list_rows else included_rows,
            out_dir / "fig_slr6_tier_breakdown.png",
        )
        fig6_tier_breakdown(
            final_list_rows if final_list_rows else included_rows,
            out_dir / "fig_slr6_tier_breakdown.pdf",
        )
    if "7" in pipeline_figs:
        if reviewed_rows:
            fig7_abstract_review(reviewed_rows,
                                 out_dir / "fig_slr7_abstract_review.png")
            fig7_abstract_review(reviewed_rows,
                                 out_dir / "fig_slr7_abstract_review.pdf")
        else:
            print("  [fig7] skipped — no 08_abstract_reviewed_*.csv found")

    if "9" in pipeline_figs:
        if final_list_rows:
            fig9_final_breakdown(final_list_rows,
                                 out_dir / "fig_slr9_final_breakdown.png")
            fig9_final_breakdown(final_list_rows,
                                 out_dir / "fig_slr9_final_breakdown.pdf")
        else:
            print("  [fig9] skipped — no 13_final_reading_list_*.csv found")

    print("\nDone.")


if __name__ == "__main__":
    main()
