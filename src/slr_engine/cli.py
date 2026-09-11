"""Unified `slr-engine` command-line interface.

Replaces the former `run.py` subprocess dispatcher with direct function calls
into the `slr_engine` package. Every pipeline stage maps to one subcommand:

    slr-engine import          parse G0+G1-G6 corpus -> 00_prevalidated_*.csv
    slr-engine retrieve        snowball G0 seeds (engines ss|scopus|acl|ieee|all)
    slr-engine screen [--llm]  keyword (+ optional LLM) title screening
    slr-engine merge           merge prevalidated corpus + snowball inclusions
    slr-engine enrich          fetch abstracts + relevance filter
    slr-engine review          interactive abstract review (manual)
    slr-engine extract         build data-extraction tables
    slr-engine finalize        produce the final reading list
    slr-engine pdfs            download reference PDFs (restricted/local)
    slr-engine prisma          regenerate the PRISMA 2020 summary
    slr-engine figures         regenerate SLR/PRISMA figures

Public / release commands (no API keys, no restricted data required):

    slr-engine verify-public        offline checks on public_data schemas +
                                    restricted-field scan (works on a clean clone)
    slr-engine scan-restricted      assert no forbidden vendor fields leak into
                                    a path (default: the whole repo)
    slr-engine build-public-data    derive redacted public evidence tables from a
                                    restricted master (OWNER ACTION — see
                                    docs/restricted-data.md for licence checks)

Restricted-data staging | The live stages (`retrieve`, `enrich`, `extract`,
`finalize`, `pdfs`) require authorized access to licensed bibliographic data and
API keys. A public clone has none of this. Supply it externally by pointing
SLR_DATA_ROOT (environment variable) at an access-controlled directory outside
the repo — see docs/restricted-data.md.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date


def _argv_tail(args: argparse.Namespace) -> list[str]:
    """Extra positional/flag arguments forwarded to an underlying stage CLI."""
    tail: list[str] = []
    for extra in getattr(args, "extra", []):
        tail.append(extra)
    return tail


def _stage_main(app_main, argv: list[str], *passthrough) -> int:
    """Invoke an existing argparse ``main()`` by pointing it at ``argv``.

    Preserves the original modules' behaviour without a subprocess, mirroring the
    pattern the former top-level ``main.py`` already used.
    """
    import builtins as _b
    old = _b.__import__  # noqa: F841  (kept explicit for clarity)
    saved = sys.argv
    sys.argv = [sys.argv[0], *argv]
    try:
        app_main(*passthrough)
        return 0
    finally:
        sys.argv = saved


# ────────────────────────────────────────────────────────────────────────────
# Pipeline stages
# ────────────────────────────────────────────────────────────────────────────

def cmd_import(args: argparse.Namespace) -> int:
    from slr_engine.config import IMPORT_DIR, PAPERS_REPO
    from slr_engine.corpus_loader import build_prevalidated

    out = IMPORT_DIR / f"00_prevalidated_{date.today()}.csv"
    gx_paths = [
        ("G1", PAPERS_REPO / "G1_PEFT_methods_beyond_adapters_and_LoRA.csv"),
        ("G2", PAPERS_REPO / "G2_Adapter_composition_for_multitask_NLP_transformers.csv"),
        ("G3", PAPERS_REPO / "G3_Decentralized_P2P_machine_learning_systems.csv"),
        ("G4", PAPERS_REPO / "G4_Adapter_multiplexing_for_efficient_LLM_inference.csv"),
        ("G5", PAPERS_REPO / "G5_Routing_and_MoE_for_modular_PEFT_transformers.csv"),
        ("G6", PAPERS_REPO / "G6_Federated_PEFT_for_transformer_NLP.csv"),
    ]
    missing = [str(p) for _, p in gx_paths if not p.exists()]
    if missing:
        print("WARNING: Missing restricted corpus files (will be skipped).\n"
              "  Supply SLR_DATA_ROOT to enable this stage:\n  " + "\n  ".join(missing))
    n = build_prevalidated(
        g0_path=PAPERS_REPO / "G0_seed_papers.md",
        gx_paths=[(g, p) for g, p in gx_paths if p.exists()],
        output_path=out,
    )
    print(f"\nImport complete -> {out.name}")
    print(f"  Total rows : {n}")
    return 0


def cmd_retrieve(args: argparse.Namespace) -> int:
    from slr_engine.config import IEEE_API_KEY, PAPERS_REPO, SCOPUS_API_KEY
    from slr_engine.core import run_snowball
    from slr_engine.corpus_loader import load_prevalidated_keys
    from slr_engine.seeds import ADDITIONAL_SEEDS, SEED_PAPERS

    all_seeds = SEED_PAPERS + ADDITIONAL_SEEDS
    scopus_key = args.scopus_key or SCOPUS_API_KEY
    ieee_key = args.ieee_key or IEEE_API_KEY

    prevalidated_keys: set[str] = set()
    if PAPERS_REPO.exists():
        prevalidated_keys = load_prevalidated_keys(PAPERS_REPO)

    run_snowball(
        all_seeds,
        engines=args.engine,
        scopus_key=scopus_key,
        ieee_key=ieee_key,
        prevalidated_keys=prevalidated_keys,
    )
    return 0


def cmd_screen(args: argparse.Namespace) -> int:
    from slr_engine import screen
    return _stage_main(screen.main, [*(_argv_tail(args))])


def cmd_merge(args: argparse.Namespace) -> int:
    from slr_engine import merge
    return _stage_main(merge.main, [])


def cmd_enrich(args: argparse.Namespace) -> int:
    from slr_engine.commands import enrich_and_filter
    extra = _argv_tail(args)
    enrich_and_filter.main(extra[0] if extra else None)
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    from slr_engine import abstract_review
    return _stage_main(abstract_review.main, [*(_argv_tail(args))])


def cmd_extract(args: argparse.Namespace) -> int:
    from slr_engine.commands import build_extraction
    queue = getattr(args, "queue", None)
    out = getattr(args, "out", None)
    build_extraction.build(queue or build_extraction.DEFAULT_QUEUE,
                           out or build_extraction.DEFAULT_OUT)
    return 0


def cmd_finalize(args: argparse.Namespace) -> int:
    from slr_engine.commands import build_reading_list
    return _stage_main(build_reading_list.main, [*(_argv_tail(args))])


def cmd_pdfs(args: argparse.Namespace) -> int:
    from slr_engine.commands import download_pdfs
    return _stage_main(download_pdfs.main, [*(_argv_tail(args))])


def cmd_prisma(args: argparse.Namespace) -> int:
    from slr_engine import prisma
    return _stage_main(prisma.main, [*(_argv_tail(args))])


def cmd_figures(args: argparse.Namespace) -> int:
    from slr_engine import visualise
    vis_argv = ["--figures"] + args.figures
    if args.output_dir:
        vis_argv += ["--output-dir", args.output_dir]
    return _stage_main(visualise.main, vis_argv)


# ────────────────────────────────────────────────────────────────────────────
# Public / release commands
# ────────────────────────────────────────────────────────────────────────────

def cmd_verify_public(args: argparse.Namespace) -> int:
    from slr_engine.public_data_check import verify_public
    return verify_public(verbose=args.verbose)


def cmd_scan_restricted(args: argparse.Namespace) -> int:
    from slr_engine.public_data_check import scan_restricted
    return scan_restricted(target=args.target, verbose=args.verbose)


def cmd_build_public_data(args: argparse.Namespace) -> int:
    from slr_engine.public_data_check import build_public_data
    return build_public_data(
        master=args.master,
        out_dir=args.out_dir,
        force=args.force,
        verbose=args.verbose,
    )


# ────────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="slr-engine",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="command")

    sub.add_parser("import", help="Parse G0+G1-G6 corpus -> 00_prevalidated_*.csv")

    r = sub.add_parser("retrieve", help="Snowball G0 seeds via a search engine")
    r.add_argument("--engine", nargs="+", default=["ss"],
                   choices=["ss", "scopus", "acl", "ieee", "all"],
                   help="Search engine(s) (default: ss)")
    r.add_argument("--scopus-key", default="", help="Scopus API key override")
    r.add_argument("--ieee-key", default="", help="IEEE API key override")
    r.add_argument("extra", nargs=argparse.REMAINDER, help="Extra flags forwarded to the stage")

    for name, help_ in (
        ("screen", "Keyword (+ optional --llm) title screening"),
        ("merge", "Merge prevalidated corpus + snowball inclusions"),
        ("enrich", "Fetch abstracts + relevance filter"),
        ("review", "Interactive abstract review (manual)"),
        ("finalize", "Produce the final reading list"),
        ("pdfs", "Download reference PDFs (restricted)"),
        ("prisma", "Regenerate the PRISMA 2020 summary"),
    ):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("extra", nargs=argparse.REMAINDER, help="Extra flags forwarded to the stage")

    x = sub.add_parser("extract", help="Build data-extraction tables")
    x.add_argument("--queue", help="Path to 09_fulltext_review_queue CSV (restricted)")
    x.add_argument("--out", help="Output extraction CSV path (restricted)")

    f = sub.add_parser("figures", help="Regenerate all SLR/PRISMA figures")
    f.add_argument("--figures", nargs="+",
                   choices=["1", "2", "3", "4", "5", "6", "7", "8"],
                   default=["1", "2", "3", "4", "5", "6", "7", "8"])
    f.add_argument("--output-dir", default=None)

    vp = sub.add_parser("verify-public", help="Offline checks on public_data + restricted-field scan")
    vp.add_argument("--verbose", action="store_true")

    sc = sub.add_parser("scan-restricted", help="Assert no restricted vendor fields leak into a path")
    sc.add_argument("--target", default=None, help="Path to scan (default: repo root)")
    sc.add_argument("--verbose", action="store_true")

    bd = sub.add_parser("build-public-data",
                        help="Derive redacted public evidence tables from a restricted master (OWNER ACTION)")
    bd.add_argument("--master", required=True,
                    help="Path/GLOB to restricted master CSV(s) to redact")
    bd.add_argument("--out-dir", default=None,
                    help="Output directory (default: public_data/ in repo)")
    bd.add_argument("--force", action="store_true", help="Overwrite existing public tables")
    bd.add_argument("--verbose", action="store_true")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        print("\nPipeline stages + release helpers listed above.")
        return 0

    handlers = {
        "import": cmd_import,
        "retrieve": cmd_retrieve,
        "screen": cmd_screen,
        "merge": cmd_merge,
        "enrich": cmd_enrich,
        "review": cmd_review,
        "extract": cmd_extract,
        "finalize": cmd_finalize,
        "pdfs": cmd_pdfs,
        "prisma": cmd_prisma,
        "figures": cmd_figures,
        "verify-public": cmd_verify_public,
        "scan-restricted": cmd_scan_restricted,
        "build-public-data": cmd_build_public_data,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
