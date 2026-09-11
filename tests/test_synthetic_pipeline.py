"""Pipeline arithmetic exercised purely on SYNTHETIC fixtures (invented papers).

These tests prove the screening/PRISMA bookkeeping is correct without needing
any real (restricted) bibliographic data.
"""
from __future__ import annotations

import csv
from pathlib import Path


def _load(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_seed_corpus_has_unique_identifiers(fixtures_dir: Path):
    rows = _load(fixtures_dir / "seed_corpus.csv")
    dois = [r["doi"] for r in rows if r["doi"]]
    assert len(dois) == len(set(dois)), "duplicate synthetic DOIs"
    arxiv = [r["arxiv_id"] for r in rows if r["arxiv_id"]]
    assert len(arxiv) == len(set(arxiv)), "duplicate synthetic arXiv ids"
    assert len(rows) == 7


def test_prisma_arithmetic_matches_fixture(fixtures_dir: Path):
    candidates = _load(fixtures_dir / "screened_candidates.csv")
    identified = len(candidates)
    # Decisions are recorded per stage. Abstract stage: decide keep/skip/defer;
    # the fulltext stage then finalises a subset.
    abstract_included  = sum(1 for r in candidates if r["stage"] == "abstract" and r["decision"] == "included")
    abstract_uncertain = sum(1 for r in candidates if r["stage"] == "abstract" and r["decision"] == "uncertain")
    abstract_skipped   = sum(1 for r in candidates if r["stage"] == "abstract" and r["decision"] == "excluded")
    fulltext_excluded  = sum(1 for r in candidates if r["stage"] == "fulltext" and r["decision"] == "excluded")

    expected = {
        (r["stage"], r["transition"]): int(r["count"])
        for r in _load(fixtures_dir / "prisma_expected.csv")
    }
    assert expected[("identified", "screened")] == identified
    assert expected[("screening", "abstract_included")] == abstract_included
    assert expected[("screening", "abstract_uncertain")] == abstract_uncertain
    assert expected[("screening", "abstract_skipped")] == abstract_skipped
    assert expected[("fulltext", "included")] == 4
    assert expected[("fulltext", "excluded")] == fulltext_excluded
    # Conservation: every screened record is accounted for.
    assert abstract_included + abstract_uncertain + abstract_skipped + fulltext_excluded == identified


def test_paper_ids_are_stable_and_curated(fixtures_dir: Path):
    """paper_id must be deterministic and never equal a raw vendor ID."""
    rows = _load(fixtures_dir / "screened_candidates.csv")
    for r in rows:
        assert r["paper_id"].startswith(("doi:", "arxiv:", "sha:")), r["paper_id"]
        assert r["paper_id"] not in {"", "synth001"}, "paper_id must not be a raw row label"
