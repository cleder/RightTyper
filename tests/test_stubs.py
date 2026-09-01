import textwrap
import libcst as cst
from righttyper.generate_stubs import PyiTransformer


def generate_stub(orig_code: str) -> str:
    m = cst.parse_module(orig_code)
#    print(m)
    m = m.visit(PyiTransformer())
#    print(m)
    return m.code


def test_stubs(tmp_path, monkeypatch):
    code = textwrap.dedent("""\
        import sys

        A = B = 42
        CALC = 1+1
        CALC += 2

        # blah blah blah

        class C:
            '''blah blah blah'''
            class D:
                PI = 314

            def __init__(self: Self, x: int) -> None:  # initializes me
                self.x = x

            def f(self: Self) -> int:
                return self.x

        def f(x: int) -> int:
            return C(x).f()
        """
    )

    output = generate_stub(code)
    assert output == textwrap.dedent("""\
        import sys
        from typing import Any
        A: int
        B: int
        CALC: Any
        class C:
            class D:
                PI: int
            def __init__(self: Self, x: int) -> None: ...
            def f(self: Self) -> int: ...
        def f(x: int) -> int: ...
        """)

def test_stubs_no_any(tmp_path, monkeypatch):
    code = textwrap.dedent("""\
        import sys

        A = 42

        def f(x: int) -> int:
            return C(x).f()
        """
    )

    output = generate_stub(code)
    assert output == textwrap.dedent("""\
        import sys
        A: int
        def f(x: int) -> int: ...
        """)


def test_stubs_assign_tuple(tmp_path, monkeypatch):
    code = textwrap.dedent("""\
        X, Y, Z = 'a', 10, .0
        """
    )

    output = generate_stub(code)
    assert output == textwrap.dedent("""\

        """)


def test_stubs_empty_class(tmp_path, monkeypatch):
    code = textwrap.dedent("""\
        class Foo:
            '''Maybe one day we'll write more'''

        def f(x: int) -> int:
            return 42
        """
    )

    output = generate_stub(code)
    assert output == textwrap.dedent("""\
        class Foo:
            pass
        def f(x: int) -> int: ...
        """)


def test_stubs_conditional(tmp_path, monkeypatch):
    code = textwrap.dedent("""\
        from typing import TYPE_CHECKING
        if TYPE_CHECKING:
            "this should go away"
            import ast

        def f(x: "ast.AST") -> int:
            return 42
        """
    )

    output = generate_stub(code)
    assert output == textwrap.dedent("""\
        from typing import TYPE_CHECKING
        if TYPE_CHECKING:
            import ast
        def f(x: "ast.AST") -> int: ...
        """)


def test_stubs_context_handler(tmp_path, monkeypatch):
    code = textwrap.dedent("""\
        with something():
            "this should go away"
            import ast

        def f(x: "ast.AST") -> int:
            return 42
        """
    )

    output = generate_stub(code)
    assert output == textwrap.dedent("""\
        with something():
            import ast
        def f(x: "ast.AST") -> int: ...
        """)



def test_stubs_try(tmp_path, monkeypatch):
    code = textwrap.dedent("""\
        try:
            from foo import bar
        except ImportError:
            import foobar as bar

        def f(x: bar) -> int:
            return 42
        """
    )

    output = generate_stub(code)
    assert output == textwrap.dedent("""\
        try:
            from foo import bar
        except ImportError:
            import foobar as bar
        def f(x: bar) -> int: ...
        """)


def test_stubs_all_variable(tmp_path, monkeypatch):
    # __all__ is included in many typeshed "pyi"s.
    code = textwrap.dedent("""\
        __all__ = [
            "foo",
            "Bar"
        ]

        def foo() -> int:
            return 42

        class Bar(object):
            def __init__(self, x):
                pass

        def baz() -> float:
            pass
        """
    )

    output = generate_stub(code)
    assert output == textwrap.dedent("""\
        __all__ = [
            "foo",
            "Bar"
        ]
        def foo() -> int: ...
        class Bar(object):
            def __init__(self, x): ...
        def baz() -> float: ...
        """)


def test_stubs_annassign_with_value():
    code = textwrap.dedent("""\
        COUNT: int = 5

        def f(x: int) -> int:
            return x + COUNT
        """
    )
    output = generate_stub(code)
    assert output == textwrap.dedent("""\
        COUNT: int
        def f(x: int) -> int: ...
        """)


def test_stubs_annotated_all_variable_keeps_value():
    # An annotated __all__ must survive whole, like the bare form above: stripping
    # the value leaves a stub that declares an export list with no members, which
    # is a silent version of the crash the AnnAssign branch was added to avoid.
    code = textwrap.dedent("""\
        __all__: list[str] = [
            "foo",
            "Bar"
        ]

        COUNT: int = 5

        def foo() -> int:
            return 42

        class Bar(object):
            pass
        """
    )
    output = generate_stub(code)
    assert '"foo"' in output and '"Bar"' in output, output
    assert "COUNT: int\n" in output, output      # other AnnAssigns still stripped


def test_stubs_all_on_a_shared_line_keeps_only_all():
    # Keeping __all__ whole means keeping __all__, not its line-mates: a small
    # statement after a semicolon would otherwise ride into the stub with its
    # value, though handle_body never looked at it.
    code = textwrap.dedent("""\
        __all__ = ["foo"]; COUNT = 5
        __version__: str = "1.0"; DEBUG: bool = False

        def foo() -> int:
            return 42
        """
    )
    output = generate_stub(code)
    assert output.splitlines()[0] == '__all__ = ["foo"]', output
    assert "= 5" not in output, output
    # ... and no semicolon left dangling behind what does survive
    assert "__version__: str\n" in output, output
    assert "= False" not in output, output
