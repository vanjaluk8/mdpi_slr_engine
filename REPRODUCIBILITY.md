# Reproducibility Statement

This document explains how the systematic literature review (SLR) results were
produced, and how a reviewer or reader can reproduce, audit and re-run the
**analysis** without access to the licensed source data.

## Reproducibility model

The engine separates two concerns that are easy to conflate:

1. **The analysis code and methodology** — fully public, in this repository.
2. **The licensed input corpus** — restricted, supplied at runtime by the
   authorised researcher through `SLR_DATA_ROOT` (an access-controlled
   directory **outside** the repo).

Once the restricted inputs are supplied, the entire pipeline is deterministic
and auditable: every stage reads a versioned CSV, writes a versioned CSV, and
preserves an audit trail for the PRISMA 2020 flow.

## Offline reproducibility (no restricted data)

Most of the machinery is reproducible with **zero** restricted data. This is
enforced in CI and on any clean clone:

```bash
uv sync --extra dev
uv run slr-engine scan-restricted   # safety gate: no restricted fields in the repo
uv run slr-engine verify-public     # public tables conform to schemas
uv run --extra dev pytest           # all tests, incl. synthetic-pipeline arithmetic
```

Because the committed `public_data/*.csv` value tables are generated from the
restricted master (an owner action), a fresh clone reproduces their **schemas**
and the **safety guarantees**, and the tests re-run the PRISMA bookkeeping over
**synthetic fixtures** (invented papers) that don't require vendor data.

## Full-pipeline reproduction (authorised researchers)

For an authorised researcher holding the licensed corpus and API keys:

```bash
# 1. Point the engine at your access-controlled data (outside this repo)
export SLR_DATA_ROOT=/absolute/path/to/your/access-controlled/data
export SCOPUS_API_KEY=...      # only if using the Scopus engine
export IEEE_API_KEY=...        # only if using the IEEE engine

# 2. Run the review as in the protocol (docs/pipeline.md)
uv run slr-engine import
uv run slr-engine retrieve --engine all   # snowball G0 seeds
uv run slr-engine screen                  # keyword (+ optional --llm) screening
uv run slr-engine merge
uv run slr-engine enrich
uv run slr-engine review                  # interactive abstract review
uv run slr-engine extract
uv run slr-engine finalize
uv run slr-engine prisma                  # PRISMA 2020 summary
uv run slr-engine figures                 # all SLR/PRISMA figures
```

## How the public evidence tables are derived

`public_data/*.csv` are **not** hand-edited. They are derived from a restricted
master by the **owner action** `build-public-data`:

```bash
uv run slr-engine build-public-data \
  --master /abs/path/to/restricted-master.csv \
  --out-dir /abs/path/to/mdpi_slr_engine/public_data \
  --force --verbose
```

This command:

1. **Refuses in-repo masters** — the input must live outside the public repo.
2. **Drops every column** whose provenance is not cleared for redistribution
   (whitelist in `public_data_check.py` + the `data_dictionary.csv` licence
   classes).
3. **Assigns stable public `paper_id`s** (`doi:` → `arxiv:` → `sha256(title|year)`
   fallback — see `public_data/provenance.json`).
4. **Writes a SHA256SUMS manifest** over the resulting tables so a tag can be
   independently verified byte-for-byte.

The owner runs this only **after** licence review, in a controlled environment,
**never in CI**. See `docs/restricted-data.md` for the exact workflow and the
licence checks required before any `controlled`/`review_required` column is
cleared.

## Verification checkpoints

| Checkpoint | Command | Purpose |
|---|---|---|
| Install | `uv sync --extra dev` | Reproducible environment from `uv.lock` |
| Lint | `uv run ruff check …` (CI scoped) | Static quality of MDPI-authored files |
| Tests | `uv run --extra dev pytest` | Unit + integration + synthetic pipeline |
| Restricted scan | `uv run slr-engine scan-restricted` | No licensed content in the repo |
| Public verification | `uv run slr-engine verify-public` | Public tables conform; offline pass |
| Release integrity | `public_data/SHA256SUMS` | Byte-for-byte check of published tables |

## Versioning

- Software release tag: `v1.0.0-mdpi` (see `pyproject.toml`).
- The `uv.lock` pins the exact dependency versions for reproduction.
- `configs/`, `schemas/`, `public_data/provenance.json` are versioned alongside
  the code so the protocol, schemas and evidence stay in lock-step.
