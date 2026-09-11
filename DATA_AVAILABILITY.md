# Data Availability Statement

This manuscript is accompanied by an open code-and-open-evidence release. In
line with the principle **"publish what is needed, restrict what is not"**, the
release makes every artifact required to reproduce, audit and re-run the
*analysis* public, while **not** redistributing licensed bibliographic content.

## What is public (in this repository)

All of the following are committed and redistributable under the repository
licence (code: MIT; data: per-column, see `public_data/data_dictionary.csv`):

| Artifact | Location |
|---|---|
| Full pipeline source code (installable package + unified CLI) | `src/` |
| Review protocol, search strategies, eligibility criteria | `configs/` |
| Schemas for every public evidence table | `schemas/` |
| Column-level data-dictionary / licence classification | `public_data/data_dictionary.csv` |
| Data-sharing model + paper-ID scheme | `public_data/provenance.json` |
| Open evidence tables (identifiers, screening decisions, exclusion reasons, aggregate PRISMA counts, extraction coding) | `public_data/*.csv` |
| Synthetic fixtures (invented papers) used to exercise the pipeline | `tests/fixtures_synthetic/` |
| Tests + CI (install, lint, restricted-scan, verify-public) | `tests/`, `.github/` |

The committed open-evidence tables validate offline with:

```bash
uv run slr-engine scan-restricted   # no restricted content in the repo
uv run slr-engine verify-public     # public tables conform to their schemas
```

## What is restricted (NOT in this repository)

The following are **licensed, proprietary, or personally identifiable** and are
**deliberately absent** — they must not be derived from this repository and are
available only for authorised use in access-controlled storage:

- Raw exports / API responses from Scopus, Web of Science, IEEE, Semantic
  Scholar and similar vendors (the `G0`–`G6` corpus files, snowball retrieval
  logs, API caches).
- Abstra**cts**, author keywords, affiliations, author/researcher IDs,
  reference / cited-by lists, and citation counts.
- Downloaded reference PDFs and any signed download URLs.
- API credentials, `.env` values.

These are categorised as `restricted` or `review_required` in
`public_data/data_dictionary.csv` and are covered by
[`docs/restricted-data.md`](docs/restricted-data.md).

## Data tiers at a glance

Three publication tiers (column-level classification; full detail in
`public_data/data_dictionary.csv` and `docs/data-provenance.md`):

| Tier | Contents | Where published |
|---|---|---|
| **Public** | Code, configs, queries, aggregate counts, identifiers, screening decisions, exclusion reasons, researcher extraction coding, synthetic fixtures | This repository |
| **Controlled** | Minimal derived records with limited, licence-cleared vendor metadata | Institutional repository or post-approval GitHub — by owner |
| **Restricted** | Raw vendor exports, abstracts, citation lists, affiliations/ids, PDFs, credentials | Access-controlled storage; never in Git |

## Reproducibility pointer

[`REPRODUCIBILITY.md`](REPRODUCIBILITY.md) and
[`docs/restricted-data.md`](docs/restricted-data.md) document exactly how
authorised researchers supply the restricted tier (via `SLR_DATA_ROOT`, outside
the repo) and how the public tables were derived and verified.
