# SLR Engine

[![DOI](https://img.shields.io/badge/DOI-10.5281%2Fzenodo.22703556-blue)](https://doi.org/10.5281/zenodo.22703556)

Automated systematic literature review (SLR) engine with citation snowballing
(Wohlin, 2014) and a PRISMA 2020-compatible audit trail, applied to the
engineering of parameter-efficient fine-tuning (PEFT) and modular adapter
methods for transformer-based NLP models.

> **Public code-and-open-evidence release (MDPI).** This repository ships **no
> restricted bibliographic data**. Raw records from Scopus, Web of Science,
> Semantic Scholar, IEEE and other licensed services are retained in
> access-controlled storage and are supplied at runtime only to authorised
> researchers via the `SLR_DATA_ROOT` environment variable (pointing at a
> directory *outside* this repo). Everything you need to read, audit and
> re-run the *analysis* is committed here; the licensed source records are
> **not**. See [DATA_AVAILABILITY.md](DATA_AVAILABILITY.md) and
> [docs/restricted-data.md](docs/restricted-data.md).

> **Cite as:** Vanja Luk. *SLR Engine* (v1.0.0-mdpi). Zenodo.
> DOI: https://doi.org/10.5281/zenodo.22703556

---

## Quick start (public / offline)

The public release is designed to install, lint, test and pass its safety gates
**fully offline** — no API keys, no network, no restricted data.

```bash
# 1. Install with uv (Python >= 3.11)
uv sync --extra dev

# 2. Safety + consistency gates (these MUST pass on any clean clone)
uv run slr-engine scan-restricted   # assert no restricted vendor fields leak into the repo
uv run slr-engine verify-public     # offline validation of the public evidence tables
uv run --extra dev pytest           # unit + integration + synthetic-pipeline tests
```

All three pass on a clean clone by construction: the committed tree contains
only code, configs, schemas, empty-but-schema'd public tables, and *synthetic*
fixtures (invented papers) used to exercise the pipeline arithmetic.

## Running the full review pipeline

The live retrieval/enrichment stages (`retrieve`, `enrich`, `extract`,
`finalize`, `pdfs`) require licensed data and API keys, and are **not** part of
the open release. Authorised researchers supply them externally:

```bash
export SLR_DATA_ROOT=/absolute/path/to/your/access-controlled/data
export SCOPUS_API_KEY=...    # only if you use the Scopus engine
export IEEE_API_KEY=...      # only if you use the IEEE engine
uv run slr-engine import
uv run slr-engine retrieve --engine all
uv run slr-engine screen
uv run slr-engine merge
uv run slr-engine enrich
uv run slr-engine review
uv run slr-engine extract
uv run slr-engine finalize
uv run slr-engine prisma     # regenerate the PRISMA 2020 summary
uv run slr-engine figures    # regenerate all SLR/PRISMA figures
```

See [docs/pipeline.md](docs/pipeline.md) for the stage-by-stage guide and
every stage's inputs/outputs, and [docs/restricted-data.md](docs/restricted-data.md)
for the controlled-environment staging rules.

## CLI reference

`slr-engine` is a single entry point ([src/slr_engine/cli.py](src/slr_engine/cli.py)):

| Command | Purpose |
|---|---|
| `import` | Parse G0 seeds + G1–G6 corpus → `00_prevalidated_*.csv` |
| `retrieve` | Snowball G0 seeds (engines `ss` \| `scopus` \| `acl` \| `ieee` \| `all`) |
| `screen` | Keyword (+ optional `--llm`) title screening |
| `merge` | Merge prevalidated corpus + snowball inclusions |
| `enrich` | Fetch abstracts + relevance filter |
| `review` | Interactive abstract review (manual) |
| `extract` | Build data-extraction tables |
| `finalize` | Produce the final reading list |
| `pdfs` | Download reference PDFs (**restricted/local**) |
| `prisma` | Regenerate the PRISMA 2020 summary |
| `figures` | Regenerate all SLR/PRISMA figures |
| **`verify-public`** | Offline checks on `public_data/` schemas + restricted-field sweep |
| **`scan-restricted`** | Assert no restricted vendor fields leak into a path (default: repo root) |
| **`build-public-data`** | Derive redacted public tables from a restricted master (**OWNER ACTION**) |

Run `uv run slr-engine --help` for the full argument reference.

## Repository layout

```
src/slr_engine/          # installable package (src-layout, hatchling)
  cli.py                 # unified entry point
  public_data_check.py   # public-data boundary enforcement (see below)
  config.py              # path / API-key configuration
  core.py, corpus_loader.py, seeds.py, screen.py, merge.py,
  abstract_review.py, prisma.py, visualise.py   # pipeline stages
  commands/              # smaller stage scripts (extraction, reading list, ...)
configs/                 # study protocol, search strategies, eligibility criteria
schemas/                 # JSON-schema for each public evidence table
public_data/             # open-evidence tables + data dictionary + provenance
  data_dictionary.csv    # column-level publication licence classification
  provenance.json        # data-sharing model + paper_id scheme
tests/                   # unit/integration/synthetic tests (no real data)
.github/workflows/       # CI: install, lint, test, scan-restricted, verify-public
```

## The public-data boundary (why this repo is safe to publish)

Publication status is decided at the **column** level, not the file level
(see [docs/data-provenance.md](docs/data-provenance.md)):

- **public** — identifiers (DOI/arXiv/paper_id), year, title, screening
  decisions, exclusion reasons, aggregate PRISMA counts, and researcher-created
  extraction coding.
- **restricted** — abstracts, keywords, affiliations, author IDs, reference /
  citation lists, citation counts, vendor export markers, signed URLs, downloaded
  PDFs, and any credentials.

`public_data_check.py` ships three defence-in-depth routines that keep licensed
content out of the release:

1. **`scan_restricted`** — walks a tree and flags files whose name or CSV header
   matches a restricted pattern. Runs in CI before every merge/tag.
2. **`verify_public`** — offline validation of the public tables (files present,
   primary-key uniqueness, allowed columns only) plus a whole-tree sweep.
3. **`build_public_data`** — an **OWNER ACTION**: reads a *restricted* master at
   an external path, drops every column not cleared for redistribution, and
   refuses in-repo masters.

A committed file that fails `scan_restricted` means the redaction regressed —
this is the reviewer-facing guarantee that the tag is clean.

## License

MIT — see [LICENSE](LICENSE). The `slr_engine` source code is MIT-licensed; data
licence terms depend on the sources used and are recorded per-column in
[public_data/data_dictionary.csv](public_data/data_dictionary.csv).

## Project structure note (sibling repo)

This folder is a **clean, self-contained release** of the engine, built from a
larger private codebase that holds the access-controlled data. The `data/`
directory here is intentionally empty: it is populated at runtime from
`SLR_DATA_ROOT`. See [REPRODUCIBILITY.md](REPRODUCIBILITY.md) for how the
public evidence was derived and verified.
