import os
import sys
import fnmatch
import collections.abc as abc

from functools import cache
from pathlib import Path
from types import CodeType

from righttyper.logger import logger
from righttyper.options import run_options


def safe_issubclass(a: type, b: type) -> bool:
    """``issubclass`` that answers False instead of raising ``TypeError``.

    ``isinstance(x, type)`` does not imply x supports class checks.  A TypedDict
    subclass refuses them by design (``TypedDict does not support instance and
    class checks``), and so does a Protocol with non-method members.  Such a
    class reaches generalize and observations as a *declared* annotation --
    ``_propagate_to_parents`` merges a child's observed argument type with the
    parent's declared one, so a TypedDict arrives as a type_obj even though no
    runtime value ever has that type.  False is the right answer to a subtype
    query that cannot be evaluated.  See #200.

    Note the asymmetry: ``__subclasscheck__`` lives on the *second* argument, so
    only ``issubclass(x, TypedDict_subclass)`` raises -- ``issubclass(Payload,
    Collection)`` is fine.  That is why the many ``issubclass(some.type_obj,
    <fixed ABC>)`` calls across the codebase need no guard, while every call
    whose second argument is caller-derived does -- generalize's Rules 4, 6 and
    6.5, and observations' Self-compatibility and strict-subtype checks.  Those
    are the sites; a new one belongs here too.

    Only ``TypeError`` is caught, and deliberately so: that is how "this class
    does not support class checks" is spelled, both by ``TypedDict``/``Protocol``
    and by ``type.__subclasscheck__`` itself.  A ``__subclasscheck__`` that raises
    anything else is a bug in that class; swallowing it into a False would skew
    the inferred type silently instead of surfacing the fault.
    """
    try:
        return issubclass(a, b)
    except TypeError:
        return False


def unwrap(method: abc.Callable|None) -> abc.Callable|None:
    """Follows a chain of `__wrapped__` attributes to find the original function."""

    # Remember objects by id to work around unhashable items, but point to object so
    # that the object can't go away (possibly reusing the id)
    visited = {}
    while hasattr(method, "__wrapped__"):
        if id(method) in visited: return None
        visited[id(method)] = method

        method = getattr(method, "__wrapped__")

    return method


_SAMPLING_INTERVAL = 0.01


def _get_righttyper_path() -> str:
    import importlib.util
    spec = importlib.util.find_spec(__package__)
    assert spec is not None and spec.origin is not None
    return str(Path(spec.origin).parent)

RIGHTTYPER_PATH = _get_righttyper_path()


def _get_python_libs() -> tuple[str, ...]:
    import sysconfig

    return tuple(
        set(
            sysconfig.get_path(p)
            for p in ('stdlib', 'platstdlib', 'purelib', 'platlib')
        )
    )

PYTHON_LIBS = _get_python_libs()

detected_test_files: set[str] = set()
detected_test_modules: set[str] = set()

def set_test_files_and_modules(files: set[str], modules: set[str]) -> None:
    detected_test_files.update(files)
    detected_test_modules.update(modules)

    # Clear caches, as these functions' results may now change
    is_test_module.cache_clear()
    skip_this_file.cache_clear()


@cache
def is_test_module(m: str) -> bool:
    if m in detected_test_modules:
        return True
    while m:
        if m in run_options.test_modules:
            return True
        if '.' not in m:
            break
        m = m.rsplit('.', 1)[0]
    return False


def skip_this_code(code: CodeType) -> bool:
    if (
        (include_functions := run_options.include_functions_re)
        and not include_functions.search(code.co_name)
    ):
        logger.debug(f"skipping function {code.co_name}")
        return True

    return False


@cache
def skip_this_file(filename: str) -> bool:
    #logger.debug(f"checking skip_this_file {filename=}")
    should_skip = (
        filename.startswith("<")
        or (run_options.exclude_test_files and filename in detected_test_files)
        # FIXME how about packages installed with 'pip install -e' (editable)?
        or any(filename.startswith(p) for p in PYTHON_LIBS)
        or filename.startswith(RIGHTTYPER_PATH)
        or run_options.script_dir not in os.path.abspath(filename)
    )

    if not should_skip:
        if any(fnmatch.fnmatch(filename, exclude) for exclude in run_options.exclude_files):
            logger.debug(f"skipping file {filename}")
            return True

    return should_skip


# TODO compare to https://mypy.readthedocs.io/en/stable/running_mypy.html#mapping-file-paths-to-modules
def _source_relative_to_pkg(file: Path) -> Path|None:
    """Returns a Python source file's path relative to its package"""
    try:
        if not file.is_absolute():
            file = file.resolve()

        parents = list(file.parents)

        for d in sys.path:
            path = Path(d)
            if not path.is_absolute():
                path = path.resolve()

            for p in parents:
                if p == path:
                    return file.relative_to(p)
    except:
        # file.resolve() may throw in case of symlink loops;
        # Also, torch._dynamo seems to throw Unsupported (see issue 93)
        pass

    return None


def source_to_module_fqn(file: Path) -> str|None:
    """Returns a source file's fully qualified package name, if possible."""
    if not (path := _source_relative_to_pkg(file)):
        return None

    path = path.parent if path.name == '__init__.py' else path.parent / path.stem
    return '.'.join(path.parts)


@cache
def get_main_module_fqn() -> str:
    # Note that through caching, we may get this wrong if the __main__ module
    # changes (e.g., trough runpy)
    main = sys.modules['__main__']
    if hasattr(main, "__file__") and main.__file__:
        if fqn := source_to_module_fqn(Path(main.__file__)):
            return fqn

    return "__main__"


def normalize_module_name(module_name: str) -> str:
    if module_name == "__main__":
        # "__main__" isn't generally usable for typing, and only unique in this execution
        return get_main_module_fqn()

    if module_name == "builtins":
        return ""   # we consider these "well-known" and, for brevity, omit the module name

    return module_name
