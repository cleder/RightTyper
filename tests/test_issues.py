import textwrap
import subprocess
import sys
from pathlib import Path


def test_issue_22(tmp_path, monkeypatch):
    t = textwrap.dedent("""\
        def extracted_function(A):
            return all(A[i] <= A[i + 1] for i in range(len(A) - 1)) or all(A[i] >=
                A[i + 1] for i in range(len(A) - 1))

        def optimized(A):
            return all(A[i] <= A[i + 1] for i in range(len(A) - 1)) and all(A[i] >=
                A[i + 1] for i in range(len(A) - 1))

        def main():
            assert extracted_function([6, 5, 4, 4]) == True
            assert extracted_function([1, 2, 2, 3]) == True
            assert extracted_function([1, 3, 2]) == False

        if __name__ == '__main__':
            main()
        """)

    monkeypatch.chdir(tmp_path)
    Path("t.py").write_text(t)

    subprocess.run([sys.executable, '-m', 'righttyper', 'run', 't.py'])

    assert "def extracted_function(A: list[int]) -> bool" in Path("t.py").read_text()


def test_issue_193(tmp_path, monkeypatch):
    # unittest.mock's _Call answers any attribute access with a child _Call,
    # so probing for __code__ used to yield an unhashable object that crashed
    # the (process-global) call handler.
    t = textwrap.dedent("""\
        from unittest.mock import call

        def build():
            return [call(a=1), call(a=2)]

        build()
        """)

    monkeypatch.chdir(tmp_path)
    Path("t.py").write_text(t)

    p = subprocess.run(
        [sys.executable, '-m', 'righttyper', 'run', '--only-collect', 't.py'],
        capture_output=True, text=True
    )

    assert p.returncode == 0, p.stderr
    assert "_Call" not in p.stderr


def test_issue_193_dataclass_init(tmp_path, monkeypatch):
    # The dataclass/attrs/NamedTuple branch of the call handler reads __code__ off
    # __init__/__new__, on a second path that the fix above didn't cover.  A class
    # whose __init__ synthesizes a truthy non-code __code__ reached
    # setup_monitoring_for_code() and killed the (process-global) handler.
    t = textwrap.dedent("""\
        import dataclasses
        from unittest.mock import call

        @dataclasses.dataclass
        class Point:
            x: int
            y: int

        class FakeInit:
            __code__ = call.something   # truthy, non-code, unhashable

            def __call__(self, *args, **kwargs):
                pass

        Point.__init__ = FakeInit()

        print(type(Point(1, 2)).__name__)
        """)

    monkeypatch.chdir(tmp_path)
    Path("t.py").write_text(t)

    p = subprocess.run(
        [sys.executable, '-m', 'righttyper', 'run', '--only-collect', 't.py'],
        capture_output=True, text=True
    )

    assert p.returncode == 0, p.stderr
    assert "code must be a code object" not in p.stderr
    assert "Point" in p.stdout      # the script really did run to completion


def test_issue_199(tmp_path, monkeypatch):
    """A wrapper that supplies an argument its caller never passed must not
    put a None inside a CallTrace.

    _get_arg_types returns None for a parameter absent from the locals mapping.
    That never happens for a real PY_START frame, but the synthetic ArgInfo built
    for wrapped-function propagation uses bind_partial, which leaves unpassed
    parameters unbound. The None used to survive into the CallTrace and crash
    whichever type transformer ran first in finish_recording -- after the traced
    run had already finished, so righttyper exited 0 having written nothing.
    """
    t = textwrap.dedent("""\
        import functools

        def deco(f):
            @functools.wraps(f)
            def wrapper(a):
                return f(a, 99)     # `b` comes from here, not from the caller
            return wrapper

        @deco
        def target(a, b):
            return a + b

        print(target(1))
        """)

    monkeypatch.chdir(tmp_path)
    Path("t.py").write_text(t)

    subprocess.run(
        [sys.executable, '-m', 'righttyper', 'run', '--only-collect', 't.py'],
        capture_output=True, text=True,
    )

    # The failure was silent: exit 0, nothing on the console, no .rt file, and
    # the traceback only in righttyper.log. Assert on the artifact, not the code,
    # which is why the CompletedProcess is deliberately not inspected.
    assert list(Path().glob("*.rt")), (
        "no .rt written; righttyper.log says:\n"
        + (Path("righttyper.log").read_text() if Path("righttyper.log").exists() else "(no log)")
    )
    log = Path("righttyper.log")
    assert not log.exists() or "exception after target execution" not in log.read_text()


def test_issue_200(tmp_path, monkeypatch):
    """A TypedDict annotation on an overridden method must not kill `process`.

    A TypedDict subclass satisfies isinstance(x, type) but raises on issubclass
    by design. It reaches lub() because _propagate_to_parents merges the child's
    observed argument type with the parent's *declared* one, so the TypedDict
    arrives as a type_obj even though no runtime value ever has that type.

    The crash is intermittent by nature: Rule 4 is gated on both operands being
    argless, so calling the override with a dict yields dict[str, int]|Payload
    and survives. Passing a str keeps the observed type argless, which is what
    makes it reach issubclass.
    """
    t = textwrap.dedent("""\
        from typing import TypedDict

        class Payload(TypedDict):
            a: int

        class Base:
            def handle(self, p: Payload) -> None: ...

        class Child(Base):
            def handle(self, p):
                return len(p)

        Child().handle("xy")
        """)

    monkeypatch.chdir(tmp_path)
    Path("t.py").write_text(t)

    subprocess.run([sys.executable, '-m', 'righttyper', 'run', '--root', '.', 't.py'])

    # Silent failure: exit 0 with no output. Assert on the artifact.
    log = Path("righttyper.log")
    assert not log.exists() or "TypedDict does not support" not in log.read_text()
    # Union member order differs between the rewrite and the diff paths, so
    # assert on the members rather than on a rendered ordering.
    annotated = Path("t.py").read_text()
    child_sig = next(
        line for line in annotated.splitlines()
        if line.strip().startswith("def handle") and "-> int" in line
    )
    assert "Payload" in child_sig and "str" in child_sig, child_sig


def test_issue_193_mock_in_class_dict(tmp_path, monkeypatch):
    """unwrap() must terminate on an object that synthesizes __wrapped__.

    unwrap's cycle guard remembers objects by id, which cannot catch _Call: every
    __wrapped__ access returns a brand-new child, so the id is never seen twice
    and the loop allocated until the process was OOM-killed (exit 137, no output
    at all). mock.patch.object() on a base-class method leaves exactly such an
    object in a class __dict__, which recorder walks looking for overrides -- so
    this hit ordinary mock-using test suites, righttyper's main workload.
    """
    t = textwrap.dedent("""\
        from unittest.mock import call

        class Base:
            def m(self):
                return 0

        class Child(Base):
            def m(self):
                return 1

        Base.m = call.patched

        print(Child().m())
        """)

    monkeypatch.chdir(tmp_path)
    Path("t.py").write_text(t)

    p = subprocess.run(
        [sys.executable, '-m', 'righttyper', 'run', '--root', '.', 't.py'],
        capture_output=True, text=True, timeout=120,
    )

    assert p.returncode == 0, f"exit {p.returncode} (137 = OOM-killed)\n{p.stderr}"
    annotated = Path("t.py").read_text()
    assert "-> int" in annotated, annotated


def test_issue_193_raising_getattr(tmp_path, monkeypatch):
    """The process-global CALL handler must not raise on a hostile __getattr__.

    getattr suppresses only AttributeError, and the handler probes __code__ on
    every callable in the process and __wrapped__ along every wrapper chain. An
    object whose __getattr__ raises something else -- a lazy-import proxy's
    ImportError, a dict-backed proxy's KeyError -- had that exception surface
    inside the program under observation, at the call instruction. Same failure
    class as the _Call crash: see #193.
    """
    t = textwrap.dedent("""\
        class Proxy:
            "__code__ probe: __getattr__ raises something getattr won't suppress"
            def __getattr__(self, name):
                raise ImportError(f"cannot resolve {name}")

            def __call__(self, x):
                return x + 1

        class RaisingLink:
            def __getattr__(self, name):
                raise KeyError(name)

            def __call__(self, x):
                return x

        class Wrapper:
            "__wrapped__ probe: in __dict__, so unwrap() walks into RaisingLink"
            def __init__(self, inner):
                self.__wrapped__ = inner

            def __call__(self, x):
                return self.__wrapped__(x)

        def observed(v):
            return v * 2

        print(Proxy()(1))
        print(Wrapper(RaisingLink())(2))
        print(observed(3))
        """)

    monkeypatch.chdir(tmp_path)
    Path("t.py").write_text(t)

    p = subprocess.run(
        [sys.executable, '-m', 'righttyper', 'run', '--root', '.', 't.py'],
        capture_output=True, text=True, timeout=120,
    )

    assert p.returncode == 0, f"exit {p.returncode}\n{p.stderr}"
    assert "ImportError" not in p.stderr and "KeyError" not in p.stderr, p.stderr

    # and recording carried on rather than being derailed
    annotated = Path("t.py").read_text()
    assert "def observed(v: int) -> int:" in annotated, annotated


def test_issue_197_unhashable_class_on_recording_path(tmp_path, monkeypatch):
    """The recording path keys tables by type too, ahead of lub() and TypeMap.

    get_value_type/get_type_name look the observed type up in _BUILTINS and
    _type2handler. Those are plain dicts, so an unhashable class raised there
    before generalize or typemap ever saw it, and the function was left with no
    annotation at all -- not even the return type, which has nothing to do with
    the offending argument.
    """
    t = textwrap.dedent("""\
        class Meta(type):
            def __eq__(cls, other):
                return NotImplemented
            __hash__ = None

        class Unhashable(metaclass=Meta):
            pass

        def f(x):
            return 1

        f(Unhashable())
        """)

    monkeypatch.chdir(tmp_path)
    Path("t.py").write_text(t)

    subprocess.run([sys.executable, '-m', 'righttyper', 'run', '--root', '.', 't.py'])

    log = Path("righttyper.log")
    assert not log.exists() or "as a dict key" not in log.read_text()
    # The argument stays unannotated -- TypeMap cannot name a class it could not
    # enter -- but the rest of the signature must still be inferred.
    annotated = Path("t.py").read_text()
    assert "def f(x) -> int:" in annotated, annotated


def test_issue_199_synthetic_arg_does_not_erase_observations(tmp_path, monkeypatch):
    """The filler for an unbound synthetic parameter must not outrank real data.

    The synthetic ArgInfo leaves `b` unbound, so PendingCallTrace fills that slot.
    Filling it with UnknownTypeInfo -- i.e. Any -- made that trace *subsume* every
    genuine observation of `b`, because Any absorbs a union rather than vanishing
    from it, and the parameter came out unannotated despite being observed as int
    on every call. Never is the union identity, so it drops out instead.
    """
    t = textwrap.dedent("""\
        import functools

        def deco(f):
            @functools.wraps(f)
            def wrapper(a):
                return f(a, 99)
            return wrapper

        def target(a, b):
            return a + b

        print(deco(target)(1))
        print(target(2, 3))
        print(target(4, 5))
        """)

    monkeypatch.chdir(tmp_path)
    Path("t.py").write_text(t)

    subprocess.run([sys.executable, '-m', 'righttyper', 'run', '--root', '.', 't.py'])

    annotated = Path("t.py").read_text()
    sig = next(
        line for line in annotated.splitlines()
        if line.strip().startswith("def target")
    )
    assert sig.strip() == "def target(a: int, b: int) -> int:", (
        f"{sig!r}\n--- full file ---\n{annotated}"
    )


def test_issue_200_self_compatibility_check(tmp_path, monkeypatch):
    """The Self-compatibility check in _clone_for_context needs the same guard.

    Routing _is_subtype through safe_issubclass covered generalize, but
    _CloneForContextT.__init__ has its own `issubclass(source, dest)` whose second
    operand is caller-derived too -- and it sits in _propagate_to_parents, the very
    path by which such classes arrive. A non-runtime_checkable Protocol with data
    members raises there, and the run failed the same silent way: exit 0, nothing
    rewritten.
    """
    t = textwrap.dedent("""\
        from typing import Protocol

        class Base(Protocol):
            x: int
            def handle(self, p): ...

        class Child(Base):
            x = 1
            def handle(self, p):
                return len(p)

        Child().handle("xy")
        """)

    monkeypatch.chdir(tmp_path)
    Path("t.py").write_text(t)

    subprocess.run([sys.executable, '-m', 'righttyper', 'run', '--root', '.', 't.py'])

    log = Path("righttyper.log")
    assert not log.exists() or "runtime_checkable" not in log.read_text()
    annotated = Path("t.py").read_text()
    assert "-> int" in annotated, annotated
