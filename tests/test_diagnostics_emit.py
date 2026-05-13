"""End-to-end test for the diagnostics emitter.

Target models an input-shape gap: a comprehension whose body is
line-covered but the filter excludes every element.
"""

from __future__ import annotations

import json
import textwrap

import pytest

from test_integration import rt_run


@pytest.fixture(scope="function")
def tmp_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yield tmp_path


def test_emit_records_input_shape_gap_on_return(tmp_cwd):
    target = tmp_cwd / "t.py"
    target.write_text(
        textwrap.dedent(
            """\
            def f(items, threshold):
                return [x for x in items if x > threshold]

            f([1, 2, 3], 100)
            f([1, 2, 3], 100)
            """
        )
    )

    rt_run("--only-collect", "--with-coverage", str(target))
    rt_run("process", "--with-coverage", "--no-output-files")

    artifact_path = tmp_cwd / "righttyper-diagnostics.json"
    assert artifact_path.exists(), "expected righttyper-diagnostics.json to be written"

    artifact = json.loads(artifact_path.read_text())
    assert isinstance(artifact, list), f"expected JSON list, got {type(artifact).__name__}"

    records = [r for r in artifact if r.get("qpath") == "f"]
    assert len(records) == 1, f"expected exactly one record for f, got {records}"
    rec = records[0]

    assert rec["slot"] == "return"
    assert rec["kind"] == "return"
    assert rec["degeneracy"]["shape"] == "empty-container"
    assert rec["degeneracy"]["always_empty"] is True
    assert rec["degeneracy"]["n_observations"] >= 1

    cov = rec["coverage"]
    assert cov is not None, "expected coverage block to be populated when --with-coverage was set at run time"
    assert isinstance(cov["function_executed"], list)
    src_lines = target.read_text().splitlines()
    comp_line = next(i for i, l in enumerate(src_lines, 1) if "return [x for x" in l)
    assert comp_line in cov["function_executed"], (
        f"expected comprehension line {comp_line} in function_executed {cov['function_executed']}"
    )
