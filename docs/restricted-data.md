# Restricted-Data Staging

This guide is for **authorised researchers** who hold the licensed bibliographic
corpus and need to (a) run the full pipeline, and (b) generate the redacted
public evidence tables. If you only want to audit/re-run the analysis, you do
**not** need anything in this document — see `REPRODUCIBILITY.md`.

## What counts as restricted

Any content whose redistribution rights are not confirmed:

- Raw exports / API responses: Scopus, Web of Science, IEEE, Semantic Scholar
  and other vendors (the `G0`–`G6` corpus files, snowball retrieval logs,
  API caches).
- Abstracts, author keywords, affiliations, author/researcher IDs, reference /
  cited-by lists, citation counts.
- Downloaded reference PDFs and signed download URLs.
- API credentials / `.env` values (never committed, regardless of distribution).

None of this is in the public repo, and none may be added.

## Supplying restricted data at runtime

The engine never stores restricted data in the repo. Point it at an
access-controlled directory **outside** the repository:

```bash
export SLR_DATA_ROOT=/absolute/path/to/your/access-controlled/data
export SCOPUS_API_KEY=...
export IEEE_API_KEY=...
```

The public repo's `data/` stays empty. Restricted inputs and outputs live under
`$SLR_DATA_ROOT` and are excluded from Git (see `.gitignore`).

## Running the full pipeline

Follow `docs/pipeline.md` from stage 1 (`import`) through `prisma` / `figures`.
API keys are read from the environment or from per-command `--*` flags; never
commit them.

## Generating the public evidence tables (OWNER ACTION)

`public_data/*.csv` are derived, **not** hand-edited. Run in your *controlled*
environment, **after licence review**, **never in CI**:

```bash
uv run slr-engine build-public-data \
  --master /abs/path/to/restricted-master.csv \
  --out-dir /abs/path/to/mdpi_slr_engine/public_data \
  --force --verbose
```

The command:

1. **Refuses in-repo masters** (input must be outside the public repo).
2. **Drops** every column whose provenance is not cleared for redistribution
   (whitelist + `data_dictionary.csv`).
3. Assigns stable public `paper_id`s and writes a `SHA256SUMS` manifest.

It intentionally **fails** if the master still carries restricted columns or is
missing.

## Licence review checklist (before releasing any `controlled`/`review_required` column)

- [ ] Every column you intend to publish has a confirmed redistribution licence
      for your repository/Venue (recorded in `data_dictionary.csv`).
- [ ] No column is `restricted` (must be dropped by the build).
- [ ] `scan-restricted` passes on the repo (`no occurrences`).
- [ ] `verify-public` passes offline.
- [ ] The generated `SHA256SUMS` is committed so reviewers can verify the tag.
- [ ] You have not committed any `.env` / API key / credential (a credential
      scan is part of `scan-restricted`).

## Do not

- Do **not** commit restricted vendor exports, abstracts, citation lists,
  affiliations, author IDs, or PDFs to the public repo.
- Do **not** weaken the restricted-marker lists to make a check pass.
- Do **not** run `build-public-data` in CI or on content whose redistribution
  rights are unconfirmed.
- Do **not** alter evidence merely to make verification pass — every step above
  is a guardrail for a real licence boundary.

If in doubt, classify the column `restricted` and drop it. **Publish what is
needed; restrict what is not.**
