"""Tests for the --with-coverage loader integration.

These verify (a) that --with-coverage causes slipcover to instrument the user
code and persist per-file line coverage in the .rt pickle, and (b) the
off-by-default contract: without the flag, slipcover is never imported and
no coverage data appears in the pickle. The latter is a hard priority — RT's
near-zero-overhead promise depends on it.
"""

from __future__ import annotations

import pickle
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from test_integration import rt_run


@pytest.fixture(scope="function")
def tmp_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yield tmp_path


def _write_target(tmp_cwd: Path) -> Path:
    """A target with a branch we exercise (truthy) and one we don't (falsy)."""
    src = textwrap.dedent(
        """\
        import sys

        def f(x: int) -> str:
            if x:
                return "yes"
            return "no"

        f(1)

        with open("slipcover_imported.txt", "w") as _fh:
            _fh.write("yes" if "slipcover" in sys.modules else "no")
        """
    )
    target = tmp_cwd / "t.py"
    target.write_text(src)
    return target


def _load_only_pickle(tmp_cwd: Path) -> dict:
    pickles = sorted(tmp_cwd.glob("righttyper-*.rt"))
    assert len(pickles) == 1, f"expected exactly one .rt pickle, got {pickles}"
    with pickles[0].open("rb") as f:
        return pickle.load(f)


def test_with_coverage_populates_pickle(tmp_cwd):
    target = _write_target(tmp_cwd)

    rt_run("--only-collect", "--with-coverage", str(target))

    pkl = _load_only_pickle(tmp_cwd)

    assert "coverage" in pkl, "expected 'coverage' key in pickle"
    cov = pkl["coverage"]
    assert isinstance(cov, dict)
    assert "files" in cov, f"slipcover coverage dict shape changed: {cov.keys()}"

    target_resolved = str(target.resolve())
    file_entries = {Path(k).resolve(): v for k, v in cov["files"].items()}
    assert target.resolve() in file_entries, (
        f"target file {target_resolved} not found in coverage files: "
        f"{list(file_entries.keys())}"
    )

    entry = file_entries[target.resolve()]
    executed = set(entry.get("executed_lines", ()))
    # `if x:` was taken (truthy), `return "yes"` ran, `return "no"` did not.
    src_lines = target.read_text().splitlines()
    yes_line = next(i for i, l in enumerate(src_lines, 1) if 'return "yes"' in l)
    no_line = next(i for i, l in enumerate(src_lines, 1) if 'return "no"' in l)
    assert yes_line in executed, (
        f"expected line {yes_line} (return \"yes\") in executed_lines {executed}"
    )
    assert no_line not in executed, (
        f"expected line {no_line} (return \"no\") NOT in executed_lines {executed}"
    )


def test_default_run_does_not_import_slipcover(tmp_cwd):
    target = _write_target(tmp_cwd)

    rt_run("--only-collect", str(target))

    pkl = _load_only_pickle(tmp_cwd)
    assert "coverage" not in pkl, (
        f"unexpected 'coverage' key in pickle for default run: pkl keys={list(pkl)}"
    )

    flag = (tmp_cwd / "slipcover_imported.txt").read_text().strip()
    assert flag == "no", (
        "slipcover was imported during a default run; that breaks the "
        "off-by-default zero-overhead contract"
    )


def test_with_coverage_fails_clearly_when_coverage_id_taken(tmp_cwd):
    """If another tool already holds sys.monitoring's COVERAGE_ID, slipcover
    can't claim it. RT should fail with a clear, actionable message rather
    than a raw ValueError from CPython.
    """
    target = _write_target(tmp_cwd)

    wrapper = tmp_cwd / "wrapper.py"
    wrapper.write_text(
        textwrap.dedent(
            f"""\
            import sys
            sys.monitoring.use_tool_id(sys.monitoring.COVERAGE_ID, "PreclaimedByTest")
            sys.argv = [
                "righttyper", "run", "--no-use-multiprocessing",
                "--allow-runtime-exceptions",
                "--with-coverage", "--only-collect", {str(target)!r},
            ]
            from righttyper.righttyper import cli
            try:
                cli()
            except SystemExit as e:
                sys.exit(e.code if isinstance(e.code, int) else 1)
            """
        )
    )

    proc = subprocess.run(
        [sys.executable, str(wrapper)],
        capture_output=True,
        text=True,
    )

    assert proc.returncode != 0, (
        f"expected non-zero exit; stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    assert "COVERAGE_ID" in combined and "PreclaimedByTest" in combined, (
        f"expected clear error mentioning COVERAGE_ID and the holder; got: "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
