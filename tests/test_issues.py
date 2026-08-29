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
