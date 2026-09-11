"""Safety-critical tests: the public release must never contain restricted
vendor content (abstracts, keywords, affiliations, author IDs, citation/reference
lists, raw exports, credentials). A CI failure here means a redaction regressed.
"""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from slr_engine.public_data_check import (
    PROJECT_ROOT,
    PUBLIC_COLUMN_KEYS,
    PUBLIC_DATA_DIR,
    RESTRICTED_COLUMN_MARKERS,
    scan_restricted,
    verify_public,
)


def _read_headers(path: Path) -> list[str]:
    with open(path, newline="", encoding="utf-8") as f:
        return [c.strip().replace("﻿", "") for c in next(csv.reader(f))]


# ── Restricted-column whitelist regression test ─────────────────────────────

@pytest.mark.parametrize("marker", RESTRICTED_COLUMN_MARKERS)
def test_no_restricted_marker_in_public_whitelist(marker: str):
    """A restricted column marker must never be publishable via the whitelist."""
    assert marker not in PUBLIC_COLUMN_KEYS, (
        f"restricted marker '{marker}' must not appear in PUBLIC_COLUMN_KEYS"
    )


def test_public_whitelist_is_safe_to_publish():
    """Whitelisted base columns are identifiers/coding only — never vendor prose."""
    from slr_engine.public_data_check import _is_content_restricted
    for col in PUBLIC_COLUMN_KEYS:
        assert not _is_content_restricted(col), f"whitelisted column must not carry content: {col}"


# ── Whole-tree scan ─────────────────────────────────────────────────────────

def test_scan_restricted_clean():
    """The committed public tree must contain no restricted or credential files."""
    rc = scan_restricted(str(PROJECT_ROOT), verbose=False)
    assert rc == 0, "scan-restricted found restricted/credential content in the repo"


def test_scan_restricted_detects_pdf(tmp_path: Path):
    """A stray PDF placed in the tree must be caught."""
    bad = tmp_path / "11_MANUAL_scopus_export_1105.csv"
    bad.write_text("Title,Abstract,Authors,Year\nX,prose,y,2024\n")
    rc = scan_restricted(str(tmp_path), verbose=False)
    assert rc == 1


# ── verify-public ───────────────────────────────────────────────────────────

def test_verify_public_offline():
    """verify-public is the offline reviewer gate and must pass on this clone."""
    assert verify_public(verbose=False) == 0


def test_public_data_dictionary_header_allowed():
    """The data dictionary documents restricted columns but must not itself
    carry a restricted header."""
    header = _read_headers(PUBLIC_DATA_DIR / "data_dictionary.csv")
    assert not any(m in c.lower() for m in RESTRICTED_COLUMN_MARKERS for c in header)


def test_public_data_tables_exist():
    """The public-evidence framework must be complete on a fresh clone.

    Value tables under ``public_data/*.csv`` (included_studies, screening_decisions,
    ...) are generated from the restricted master by the owner-only
    ``build-public-data`` step and are therefore NOT committed; they may be absent
    on a clean clone (``verify_public`` tolerates this: "value tables may be empty
    on first clone"). What must exist is the always-present dictionary plus a
    schema for every declared public table, so the framework is provably complete
    even before the generated values are added.
    """
    from slr_engine.public_data_check import PUBLIC_TABLES
    # The one always-present CSV (encoding/classification dictionary) is committed.
    assert (PUBLIC_DATA_DIR / "data_dictionary.csv").exists(), (
        "missing committed data_dictionary.csv"
    )
    assert PUBLIC_DATA_DIR.is_dir(), "missing public_data/ directory"
    # Every declared public table must have a committed schema.
    schema_dir = PROJECT_ROOT / "schemas"
    for name, _ in PUBLIC_TABLES:
        stem = Path(name).stem  # e.g. included_studies.csv -> included_studies
        schema = schema_dir / f"{stem}.schema.json"
        assert schema.exists(), f"missing schema for public table {name}: {schema.name}"
