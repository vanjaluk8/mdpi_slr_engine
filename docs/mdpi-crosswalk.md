# MDPI Compliance Crosswalk

How this repository satisfies the auditing / reproducibility expectations of an
MDPI submission ("open code + open evidence", with restricted data held back).

## The expectations

MDPI requires (via the journal's Data Availability and Code Availability
policies and the companion-code guidance):

1. **Full methodological transparency** — a reader can follow exactly how the
   review was conducted.
2. **Reproducible analysis** — with the inputs supplied, results regenerate
   deterministically.
3. **Open evidence where permissible** — aggregate results and study-level
   decisions are published; **licence-bound source data are not**.
4. **Honesty about boundaries** — what is public, what is restricted, and why.

## How this repo maps to each requirement

| Requirement | Where it is met |
|---|---|
| 1. Method transparency | `configs/` (protocol, search strategies, eligibility criteria), `docs/pipeline.md`, `public_data/data_dictionary.csv` |
| 2. Reproducible analysis | `REPRODUCIBILITY.md`, `docs/pipeline.md`, `uv.lock`, `schemas/`, deterministic CLI stages, `pytest` + synthetic fixtures |
| 3. Open evidence | `public_data/*.csv` (identifiers, screening decisions, exclusion reasons, PRISMA counts, extraction coding) + `SHA256SUMS`; restricted content held back |
| 4. Boundary honesty | `DATA_AVAILABILITY.md`, `docs/data-provenance.md`, `docs/restricted-data.md`, `public_data/provenance.json` |
| 5. Safety enforcement | `.github/workflows/verify.yml` runs `scan-restricted` + `verify-public` on every merge/tag |

## Public Data Availability statement

The manuscript should carry a Data Availability statement aligned with
`DATA_AVAILABILITY.md`. Suggested wording:

> The code for the systematic literature review is openly available in a public
> repository (github.com/vanjaluk8/mdpi_slr_engine, v1.0.0-mdpi, archived under
> [Zenodo soft DOI]). Open evidence — study identifiers, screening decisions,
> exclusion reasons, aggregate PRISMA counts and the extraction coding — is
> provided in `public_data/` with a column-level data dictionary and provenance
> record. Full-text abstracts, citation lists, author affiliations/IDs and the
> raw records licensed from Scopus, Web of Science, IEEE and Semantic Scholar
> are **not** redistributed; they are retained in access-controlled storage and
> are available to authorised researchers on reasonable request, subject to the
> vendors' licence terms.

## Code Availability statement

> The code that implements the review pipeline is available at
> github.com/vanjaluk8/mdpi_slr_engine (tag v1.0.0-mdpi) and archived at
> [Zenodo soft DOI]. It is installable with `uv sync --extra dev` and can be
> run offline for the public gates (`scan-restricted`, `verify-public`) and the
> included tests. Full reproduction of the search/enrichment stages requires
> the licensed source data (see `REPRODUCIBILITY.md`).

## Citation metadata

- `CITATION.cff` — human + machine-readable citation for the software release.
- `codemeta.json` — schema.org `SoftwareSourceCode` record for data repositories.
- `public_data/provenance.json` — data-sharing model and paper-ID scheme.
- `pyproject.toml` — package metadata (to be completed: GitHub URL + Zenodo DOI).

## Before submission checklist

- [ ] Replace all `OWNER` GitHub URLs and fill the **Zenodo software DOI** +
      **ORCID** placeholders (marked `OWNER ACTION` throughout the repo).
- [ ] Run the owner action `build-public-data` after licence review to fill the
      value tables, then commit `SHA256SUMS`.
- [ ] Confirm `uv run slr-engine scan-restricted` and `verify-public` pass.
- [ ] Confirm the manuscript Data/Code Availability statements match the
      wording above and the actual tiers in `data_dictionary.csv`.
- [ ] Tag the release `v1.0.0-mdpi` and archive it (Zenodo).
