"""CLI smoke tests — the installed `slr-engine` command and its subcommand wiring.
"""
from __future__ import annotations

from slr_engine.cli import main


def test_help_exits_zero():
    """argparse raises SystemExit(0) on --help — assert that exit code."""
    import pytest
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0


def test_no_command_lists_subcommands():
    assert main([]) == 0


def test_verify_public_subcommand_offline():
    """The reviewer gate must pass with no data, no keys, no network."""
    assert main(["verify-public"]) == 0


def test_scan_restricted_subcommand_offline():
    assert main(["scan-restricted"]) == 0


def test_build_public_data_refuses_in_repo_master(tmp_path):
    """The redactor must refuse a master CSV that lives inside the public repo."""
    import slr_engine.public_data_check as pdc

    in_repo = pdc.PROJECT_ROOT / "tests" / "fixtures_synthetic" / "master_in_repo.csv"
    in_repo.write_text("doi,title\n10.1/x,Some title\n")
    try:
        rc = pdc.build_public_data(str(in_repo), out_dir=str(tmp_path / "out"))
        assert rc == 2, "in-repo master must be refused"
    finally:
        in_repo.unlink(missing_ok=True)
