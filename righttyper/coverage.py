"""Adapter for slipcover-based line coverage during a RightTyper run.

Off by default. `enable()` constructs a singleton Slipcover instance and
registers its sys.monitoring LINE callback as a side effect of
``Slipcover.__init__``. The rest of RT reaches into this module via
``is_enabled()``, ``preinstrument()`` (called from the loader's AST
pipeline), and ``snapshot()`` (called when the .rt pickle is written).

Slipcover is lazy-imported inside ``enable()``. Nothing else in this module
imports it, so when ``--with-coverage`` is not set the off-by-default code
path is free of slipcover overhead.
"""

from __future__ import annotations

import ast
import sys
import types
from typing import Any

_sci: Any | None = None

# Per-code-object line sets, populated as the loader instruments each module.
# Keyed by (filename, qualname, first_lineno) -- the same triple RT's CodeId
# already uses, and exactly what SlipCover would key on if it tracked this
# natively. When SlipCover gains the feature, drop this map and read the
# information off the slipcover instance instead. The on-disk shape we emit
# (a "functions" subdict inside each file's coverage entry) already mirrors
# the planned SlipCover output, so consumers won't need to change.
_lines_per_code: dict[tuple[str, str, int], set[int]] = {}


class CoverageSetupError(RuntimeError):
    """Raised when ``--with-coverage`` cannot be honored."""


def enable(source: str) -> None:
    """Construct the singleton ``Slipcover`` instance with branch
    instrumentation. Registers slipcover's LINE callback on
    ``sys.monitoring`` as a side effect of the constructor.

    Preflight: SlipCover hard-codes ``sys.monitoring.COVERAGE_ID``. If
    another tool already holds it, construction would raise a bare
    ``ValueError`` — we intercept and re-raise with an actionable message.
    """
    global _sci
    cov_id = sys.monitoring.COVERAGE_ID
    holder = sys.monitoring.get_tool(cov_id)
    if holder is not None and holder != "SlipCover":
        raise CoverageSetupError(
            f"Cannot enable --with-coverage: sys.monitoring COVERAGE_ID "
            f"(={cov_id}) is already held by {holder!r}. Disable that tool "
            f"or omit --with-coverage."
        )
    from slipcover import Slipcover

    _sci = Slipcover(branch=True, source=[source])


def is_enabled() -> bool:
    return _sci is not None


def preinstrument(tree: ast.Module) -> ast.Module:
    """Apply slipcover's branch-instrumentation AST pass. No-op when not
    enabled, so callers can drop this in unconditionally.
    """
    if _sci is None:
        return tree
    from slipcover import branch as sc_branch

    return sc_branch.preinstrument(tree)


def instrument_code(code: types.CodeType) -> types.CodeType:
    """Register a compiled module's code with slipcover. Enables LINE events
    on the code object and records its line/branch universe so missing-
    coverage data is computable. No-op when not enabled.

    Also walks the code object recursively to populate ``_lines_per_code``
    -- the per-code-object line bookkeeping that's the RT-side stand-in
    for a SlipCover feature.
    """
    if _sci is None:
        return code
    instrumented = _sci.instrument(code)
    _register_code_recursively(instrumented)
    return instrumented


def _register_code_recursively(code: types.CodeType) -> None:
    """Populate `_lines_per_code` for `code` and every nested code object,
    using the same traversal SlipCover does for instrumentation.
    """
    key = (code.co_filename, code.co_qualname, code.co_firstlineno)
    _lines_per_code[key] = _own_lines(code)
    for c in code.co_consts:
        if isinstance(c, types.CodeType) and c.co_name != "__annotate__":
            _register_code_recursively(c)


def _own_lines(code: types.CodeType) -> set[int]:
    """Lines belonging to *this* code object, excluding nested code objects
    (each of which gets its own entry) and SlipCover's branch-marker fake
    line numbers.
    """
    from dis import findlinestarts
    from slipcover import branch as sc_branch

    return {
        line for _, line in findlinestarts(code)
        if line and not sc_branch.is_branch(line)
    }


def snapshot() -> dict[str, Any] | None:
    """Return slipcover's coverage dict, augmented with a per-function block
    under each file's entry, or ``None`` if not enabled.

    All ``files`` keys are normalized to resolved absolute paths so that
    the per-function additions and SlipCover's per-file data sit under
    the same key, and downstream consumers don't have to canonicalize on
    lookup.

    Per-function layout (mirrors the shape SlipCover would naturally
    expose if it tracked this):

        coverage["files"][resolved_path]["functions"][f"{qualname}@{firstline}"]
            = {"lines": [int, ...]}
    """
    if _sci is None:
        return None
    from pathlib import Path

    cov = _sci.get_coverage()

    def _canonical(p: str) -> str:
        try:
            return str(Path(p).resolve())
        except OSError:
            return p

    files = {_canonical(k): v for k, v in cov.get("files", {}).items()}
    for (filename, qualname, first_line), lines in _lines_per_code.items():
        entry = files.setdefault(_canonical(filename), {"executed_lines": []})
        entry.setdefault("functions", {})[f"{qualname}@{first_line}"] = {
            "lines": sorted(lines),
        }
    cov["files"] = files
    return cov
