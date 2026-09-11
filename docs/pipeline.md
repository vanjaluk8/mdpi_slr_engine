# Pipeline

The SLR Engine implements the citation-snowballing methodology (Wohlin, 2014)
with a PRISMA 2020-compatible audit trail. Each stage is a subcommand of the
single `slr-engine` CLI; each reads a versioned CSV and writes a versioned CSV
so the flow is deterministic and auditable end-to-end.

Everything below the horizontal rule is **restricted-data staging** (requires
the licensed corpus + API keys via `SLR_DATA_ROOT`). The release commands above
it run fully offline.

## Stage summary

| # | Command | Input(s) | Output(s) | Notes |
|---|---|---|---|---|
| 1 | `import` | G0 seeds + G1–G6 corpus exports | `00_prevalidated_*.csv` | Normalise seed + corpus records; deduplicate |
| 2 | `retrieve` | G0 seeds | snowball retrieval logs | Snowballing via `ss`/`scopus`/`acl`/`ieee` engines |
| 3 | `screen` | snowball inclusions | screened candidates | Keyword (+ optional `--llm`) title screening |
| 4 | `merge` | prevalidated + screened | merged working set | Union the seed corpus and snowball inclusions |
| 5 | `enrich` | merged set | enriched set | Fetch abstracts; relevance filter |
| 6 | `review` | enriched set | reviewed set | Interactive abstract review (manual) |
| 7 | `extract` | review queue | extraction tables | Build data-extraction tables |
| 8 | `finalize` | extraction | final reading list | Produce the analysed study list |
| 9 | `prisma` | stage outputs | PRISMA 2020 summary | Recompute/visualise the flow diagram |
| 10 | `figures` | stage outputs | figures | Regenerate all SLR/PRISMA figures |
| 11 | `pdfs` | final list | reference PDFs | **Restricted/local** download |

## Public / release commands (offline)

| Command | Purpose |
|---|---|
| `verify-public` | Offline validation of `public_data/` schemas + restricted-field sweep |
| `scan-restricted` | Assert no restricted vendor fields leak into a path |
| `build-public-data` | Derive redacted public tables from a restricted master (**OWNER ACTION**) |

## Data flow

```
         G0 seeds + G1–G6 corpus exports        (RESTRICTED inputs)
                       │
                       ▼
   [1 import] ──► 00_prevalidated_*.csv
                       │
   [2 retrieve] snowball ──► ─┐
   [3 screen]  screened ──────┤
                       ▼      │
   [4 merge] ──► merged working set
                       │
   [5 enrich] ──► enriched (abstracts + relevance)
                       │
   [6 review] ──► reviewed (manual abstract decisions)
                       │
   [7 extract] —► extraction tables
                       │
   [8 finalize] —► final reading list
                       │
   [9 prisma] [10 figures] [11 pdfs]  (audit trail, figures, PDFs)
                       │
                       ▼
   [build-public-data] ──► public_data/*.csv   (redacted, owner action)
                       │
                       ▼
   [verify-public] [scan-restricted]  (offline gates, also in CI)
```

## PRISMA 2020 mapping

Each screening decision is recorded with the stage it was made at and the
transition it feeds. The pipeline preserves the conservation property of the
PRISMA flow — every screened record is either included at a stage, excluded with
a reason, or carried forward. `prisma` regenerates the summary from these
records, so the numbers in the manuscript are derived, not hand-typed.

The arithmetic is tested over synthetic fixtures in
`tests/test_synthetic_pipeline.py`.

## Paths & configuration

- Stage I/O paths and search-engine keys resolve in `src/slr_engine/config.py`.
- `SLR_DATA_ROOT` overrides the data root. When unset it defaults to the repo's
  `data/` directory (empty in a public clone). Example:
  ```bash
  export SLR_DATA_ROOT=/absolute/path/to/your/access-controlled/data
  ```
- API keys come from optional `--*` CLI flags or environment variables
  (`SCOPUS_API_KEY`, `IEEE_API_KEY`). See `.env.example` for the full list of
  variable names (values are never committed).
