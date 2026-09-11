# MDPI Release Checklist

Owner-facing checklist for turning this public release into a citable,
verifiable MDPI artifact. Items marked **OWNER ACTION** need you to supply
information or run a command; they are deliberately absent from the committed
tree.

## 1. Safety gates (must all pass)

```bash
uv sync --extra dev
uv run slr-engine scan-restricted        # expect: CLEAN, 0 finding(s)
uv run slr-engine verify-public          # expect: OK
uv run --extra dev pytest                # expect: all pass
uv run ruff check <new files>            # CI-scoped lint
```

If `scan-restricted` flags anything, resolve it before proceeding — do **not**
weaken the check.

## 2. Generate the public evidence values (OWNER ACTION)

The committed `public_data/*.csv` value tables are empty-by-design. Fill them
from your restricted master, **after licence review**, in a controlled
environment:

```bash
uv run slr-engine build-public-data \
  --master /abs/path/to/restricted-master.csv \
  --out-dir /abs/path/to/mdpi_slr_engine/public_data \
  --force --verbose
```

- Confirm every `restricted` column is dropped.
- Re-run `scan-restricted` + `verify-public` after the build.
- Commit the generated `public_data/SHA256SUMS`.

## 3. Fill metadata placeholders (OWNER ACTION)

Replace every `OWNER` / `0000-0000-0000-0000` / `10.5281/zenodo.0000000`
placeholder in:

- `pyproject.toml` — `[project.urls]` GitHub URL + Zenodo DOI comment
- `CITATION.cff` — authors, ORCID(s), GitHub URL, Zenodo DOI
- `codemeta.json` — authors, ORCID(s), GitHub URL
- `public_data/provenance.json` — GitHub URL
- `README.md`, `DATA_AVAILABILITY.md`, `docs/mdpi-crosswalk.md` — GitHub URL
- `configs/study.yaml` and related configs — ORCID / manuscript DOI

## 4. Verify nothing sensitive is committed

- Grep the committed tree for real API keys / secrets (the credential markers in
  `scan_restricted` cover `.env`, `key`, `secret`, `token`, `.pem`). The
  `.env.example` file is exempt — it holds variable names only.
- Confirm `data/`, `raw/`, `**/pdfs/`, vendor exports and API caches are
  git-ignored (see `.gitignore`).

## 5. Tag and archive

1. Commit the final tree on `main`.
2. Tag `v1.0.0-mdpi`.
3. Push to `github.com/vanjaluk8/mdpi_slr_engine`.
4. Archive the tag to **Zenodo** to mint a software DOI; paste it into
   `pyproject.toml`, `CITATION.cff`, `provenance.json` and the manuscript.

## 6. Final reviewer pass

- [ ] `scan-restricted` clean, `verify-public` ok, tests green — on a **fresh
      clone** (clone + `uv sync` to be sure).
- [ ] Manuscript Data/Code Availability statements match `DATA_AVAILABILITY.md`
      and the actual tiers in `data_dictionary.csv`.
- [ ] `CITATION.cff` + `codemeta.json` filled and valid.
- [ ] The released `SHA256SUMS` matches the published `public_data/` tables.

---

**Principle:** *publish what is needed, restrict what is not.* Every gate here
exists to keep licensed bibliographic content out of the public release.
