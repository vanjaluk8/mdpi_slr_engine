#!/usr/bin/env python3
"""verify.py — unified reproducibility runner for slr_engine.

Runs every verification suite in one invocation:
    1. verify_prisma_counts.py      — PRISMA funnel (40 checks)   [committed fixtures]
    2. verify_quality_appraisal.py  — quality-appraisal / descriptive stats (19 checks) [fixtures]
    3. verify_live_run.py           — fresh-pipeline-run funnel   [live data/snowball_output/]
    4. reconcile_text.py            — PRISMA numbers in manuscript prose   [../papers_code .tex]

The first two recompute every number from the committed `verification_fixtures/`
snapshots (never read from the manuscript). The third proves a *fresh pipeline
run* (data/snowball_output/, or an SLR_OUTPUT_DIR/`--out` tree) reproduces the
same manuscript numbers, and cross-checks the committed PRISMA summary. Together
they give a reviewer two independent grounds for trust. On completion it writes
a single combined, reviewer-facing Data-Availability artifact:

    data/snowball_output/verification_runs/FINAL_REPORT.md    (human-readable)
    data/snowball_output/verification_runs/FINAL_REPORT.json  (machine-readable)

Exit code 0 iff every check across every suite passes (i.e. the engine reproduces
the manuscript exactly); non-zero otherwise.

Usage:
    python3 scripts/verify.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from slr_engine.verify import verify_common
from slr_engine.verify import verify_prisma_counts
from slr_engine.verify import verify_quality_appraisal
from slr_engine.verify import verify_live_run


def _run_reconcile_text() -> int:
    """Reconcile the funnel numbers in the manuscript prose (suite 4).

    reconcile_text.py auto-detects the manuscript under
    ../papers_code/writing/mdpi_paper/ (or honours --tex/--dir), prints its own
    per-fact table, and exits non-zero on any prose that has drifted from the
    audited pipeline values. What is NOT in the prose (e.g. final core/background
    counts) is reported as "not located" but does not fail.
    """
    print("#" * 72)
    print("#  Manuscript-prose reconcile — funnel numbers in the .tex text")
    print("#" * 72)
    exe = Path(sys.executable)
    r = subprocess.run([str(exe), str(Path(__file__).with_name("reconcile_text.py"))],
                       cwd=Path(__file__).parent, capture_output=True, text=True)
    print(r.stdout)
    if r.stderr.strip():
        print(r.stderr)
    return r.returncode


def main() -> int:
    print("#" * 72)
    print("#  slr_engine verification — manuscript reproducibility")
    print("#" * 72)

    rc1 = verify_prisma_counts.verify()
    print("\n" + "-" * 72 + "\n")
    rc2 = verify_quality_appraisal.verify()
    print("\n" + "-" * 72 + "\n")
    rc3 = verify_live_run.verify()
    print("\n" + "-" * 72 + "\n")
    rc4 = _run_reconcile_text()

    # Every suite has populated verify_common.ALL_RECORDS -> emit the combined report.
    failed = [r for r in verify_common.ALL_RECORDS if not r["ok"]]
    total = len(verify_common.ALL_RECORDS)
    passed_ = total - len(failed)

    print("\n" + "#" * 72)
    print(f"#  COMBINED: {passed_}/{total} checks passed (plus the prose reconcile suite)")
    if failed:
        print(f"#  FAILURES: {len(failed)} — see FINAL_REPORT for details")
    else:
        print("#  ALL CHECKS PASS — engine reproduces the manuscript exactly")
    print("#" * 72)

    out = verify_common.make_report()
    print(f"\nCombined report written to: {out}")

    return 0 if (rc1 == 0 and rc2 == 0 and rc3 == 0 and rc4 == 0 and not failed) else 1


if __name__ == "__main__":
    sys.exit(main())
