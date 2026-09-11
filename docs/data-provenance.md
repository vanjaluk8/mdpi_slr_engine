# Data Provenance

This document defines **what** each field in the pipeline holds, **where it came
from**, and **whether it may be redistributed publicly**. It is the contract
that `public_data_check.py` (and the `public_data/data_dictionary.csv` table)
enforces.

## Guiding rule: classify at the **column** level, not the file level

A derived CSV can mix public and restricted columns. For example:

- `doi, year, title, decision, exclusion_reason` → **public** (identifiers +
  screening decisions are safe to publish).
- `doi, abstract, keywords, author_id, citation_count` → **restricted** (vendor
  prose/IDs/licensed metrics are not).

So the release decision is made **per column**, recorded in
`public_data/data_dictionary.csv`, and enforced mechanically by
`build_public_data` (drop anything not cleared) and by `scan_restricted`
(flag anything restricted that appears in the committed tree).

## Provenance classes

| Class | Meaning | Publish? |
|---|---|---|
| `public` | Researcher-created, or licence-cleared basic identifiers/metadata | Yes |
| `low_risk_confirm` | Probably fine but worth a human glance before publishing | Confirm then yes |
| `controlled_external` | Contains limited licence-cleared vendor metadata | Institutional/approved repo, by owner |
| `restricted` | Licensed vendor content/IDs, credentials, PDFs | Never in Git |
| `review_required` | Researcher-written but may quote vendor text | Manual review before any release |

## Paper-ID scheme

Public records carry a stable `paper_id` assigned deterministically, **never
copied from a vendor**:

1. Normalised DOI → `doi:<10.…>`
2. Else arXiv id → `arxiv:<id>`
3. Else `sha256(title + year)` truncated to 16 hex → `sha:<hex>` (fallback for
   items with only title/year)

Details in `public_data/provenance.json`.

## Field classification reference

**Public** (safe): `paper_id, doi, arxiv_id, year, title, venue, decision,
stage, reason_code, exclusion_reason, contribution_*, peft_technique,
distribution_mechanism, thesis_sections, contribution_codes, tier, corpus`.

**Review required** (may quote vendor text): `notes_raw, key_finding`,
sometimes `exclusion_reason` / `removal_reason` — check for quoted prose.

**Restricted** (never public): `abstract, keywords, affiliations, author_id,
citation_count, times_cited, references, eid, ss_paper_id, linked_url, funding,
Downloaded PDF`, plus any vendor export marker.

## How it is enforced

1. **`public_data_check.py`** defines the whitelist (`PUBLIC_COLUMN_KEYS`) and
   the restricted markers (`RESTRICTED_COLUMN_MARKERS`,
   `RESTRICTED_DATA_MARKERS`, `CREDENTIAL_MARKERS`).
2. **`scan_restricted`** walks a directory tree and flags any committed file
   whose name or header matches a restricted pattern → runs in CI.
3. **`verify_public`** checks the public tables conform (files present, PK
   uniqueness, allowed columns only) and sweeps the whole tree.
4. **`build_public_data`** (owner action) drops every column not cleared and
   refuses in-repo masters.

## Guardrails

- Add a safe column by extending the **whitelist** or the data dictionary —
  never by weakening the restricted marker list.
- The data dictionary documents restricted columns but must itself never carry a
  restricted **header** (a regression test asserts this).
- A committed file that fails `scan_restricted` means the redaction regressed
  and the release must be corrected before tagging.
