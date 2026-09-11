"""
seeds.py — load G0 snowball seeds from papers_repo/G0_seed_papers.md.

SEED_PAPERS  — list of seed dicts for snowballing (G0 papers with resolvable IDs)
ADDITIONAL_SEEDS — append manually discovered papers here for ad-hoc snowballing
"""
from __future__ import annotations

from slr_engine.config import PAPERS_REPO
from slr_engine.corpus_loader import load_g0_seeds

_G0_PATH = PAPERS_REPO / "G0_seed_papers.md"

if _G0_PATH.exists():
    SEED_PAPERS: list[dict] = load_g0_seeds(_G0_PATH)
else:
    import warnings
    warnings.warn(
        f"G0 seed file not found: {_G0_PATH}\n"
        "Set PAPERS_REPO in app/config.py or place G0_seed_papers.md there.",
        stacklevel=2,
    )
    SEED_PAPERS = []

# Add papers found while reading bibliographies here.
# Example: {"id": "arXiv:2XXX.XXXXX", "group": "G1", "key": "Author Year", "title": "..."}
ADDITIONAL_SEEDS: list[dict] = []
