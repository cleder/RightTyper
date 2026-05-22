"""Emit per-degenerate-slot diagnostics JSON at `process` time.

One record per slot whose observation multiset is uniformly degenerate (see
``generalize.degenerate_shape``). The artifact lets follow-up tooling
distinguish "exercise-driver gap" from "developer-broader annotation"
without re-running the workload.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from righttyper.generalize import (
    _is_empty_container,
    _is_never_advanced,
    degenerate_shape,
)
from righttyper.observations import Observations
from righttyper.typeinfo import NoneTypeInfo, TypeInfo


ARTIFACT_NAME = "righttyper-diagnostics.json"


def _matches_shape(t: TypeInfo, shape: str) -> bool:
    if shape == "empty-container":
        return _is_empty_container(t)
    if shape == "never-advanced-generator":
        return _is_never_advanced(t)
    if shape == "always-none-optional":
        return t == NoneTypeInfo
    return False


def _coverage_block(
    file: str,
    qpath: str,
    first_line: int,
    coverage_by_file: dict[str, dict] | None,
) -> dict[str, Any] | None:
    if coverage_by_file is None:
        return None
    entry = coverage_by_file.get(file)
    if entry is None:
        return None
    executed = set(entry.get("executed_lines", []))
    func_block = entry.get("functions", {}).get(f"{qpath}@{first_line}")
    if func_block is not None:
        executed &= set(func_block.get("lines", []))
    return {"function_executed": sorted(executed)}


def _make_record(
    code_id,
    slot: str,
    kind: str,
    observations: set[TypeInfo],
    shape: str,
    n_observations: int,
    n_empty: int,
    coverage_by_file: dict[str, dict] | None,
) -> dict[str, Any]:
    raw_concrete = str(next(iter(observations)))
    return {
        "file": str(code_id.file_name),
        "qpath": str(code_id.func_name),
        "slot": slot,
        "kind": kind,
        "emitted_annotation": raw_concrete,
        "degeneracy": {
            "shape": shape,
            "always_empty": n_empty == n_observations,
            "n_observations": n_observations,
            "n_empty": n_empty,
            "raw_concrete": raw_concrete,
        },
        "coverage": _coverage_block(
            str(code_id.file_name),
            str(code_id.func_name),
            code_id.first_code_line,
            coverage_by_file,
        ),
    }


def emit(
    obs: Observations,
    coverage: dict[str, Any] | None,
    out_path: Path,
) -> None:
    """Walk `obs.func_info`, emit one JSON record per degenerate slot."""
    coverage_by_file = coverage["files"] if coverage and "files" in coverage else None
    if coverage is None:
        print(
            "warning: --with-coverage was passed to process but no coverage "
            "data was found in the .rt pickle(s); the coverage block will be null",
            file=sys.stderr,
        )

    records: list[dict[str, Any]] = []
    for code_id, finfo in obs.func_info.items():
        traces = finfo.traces
        if not traces:
            continue
        n_observations = sum(traces.values())

        # Return slot: last element of each CallTrace. ``always-none-optional``
        # is too noisy on returns -- __init__, descriptor __set__, and
        # procedural methods that exist for their side effect (e.g.
        # ``Flask.add_url_rule`` returns None on every call by design) all
        # land here intentionally. Without comparing to the declared
        # annotation we can't tell those apart from a real signal like
        # ``def first(xs) -> int | None: return xs[0] if xs else None``
        # observed to always return None, so we drop the shape for return
        # slots and keep it only on args.
        return_obs = {t[-1] for t in traces}
        return_shape = degenerate_shape(return_obs)
        if return_shape and return_shape != "always-none-optional":
            n_empty = sum(c for t, c in traces.items() if _matches_shape(t[-1], return_shape))
            records.append(
                _make_record(
                    code_id, "return", "return", return_obs, return_shape,
                    n_observations, n_empty, coverage_by_file,
                )
            )

        # Positional args: index 0..len(args)-1 in each trace.
        for i, arg_info in enumerate(finfo.args):
            arg_obs = {t[i] for t in traces}
            arg_shape = degenerate_shape(arg_obs)
            if arg_shape:
                n_empty = sum(c for t, c in traces.items() if _matches_shape(t[i], arg_shape))
                records.append(
                    _make_record(
                        code_id, str(arg_info.arg_name), "arg",
                        arg_obs, arg_shape, n_observations, n_empty, coverage_by_file,
                    )
                )

    out_path.write_text(json.dumps(records, indent=2))
