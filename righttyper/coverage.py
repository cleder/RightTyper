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
    """
    if _sci is None:
        return code
    return _sci.instrument(code)


def snapshot() -> dict[str, Any] | None:
    """Return slipcover's coverage dict, or ``None`` if not enabled."""
    if _sci is None:
        return None
    return _sci.get_coverage()
