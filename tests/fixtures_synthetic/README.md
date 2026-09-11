# Synthetic test fixtures

These fixtures contain **invented papers** so the pipeline's software and
arithmetic can be exercised in CI without any copied vendor metadata. No real
Scopus / Web of Science / Semantic Scholar / IEEE records are present anywhere
under `tests/`.

| File | Purpose |
|------|---------|
| `seed_corpus.csv` | Small hand-built corpus with synthetic DOIs / arXiv IDs / titles / years and an `inclusion` column. |
| `screened_candidates.csv` | Candidates after a mock screening pass with decisions + exclusion reasons. |
| `prisma_expected.csv` | Expected aggregate PRISMA-style counts used by the arithmetic test. |

DO NOT add real bibliographic records here. If you need a record resembling a
real paper, invent it (synthetic DOI like `10.xxxx/synthNNN`).
