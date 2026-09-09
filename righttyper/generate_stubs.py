from typing import Self
import collections.abc as abc
import libcst as cst


class PyiTransformer(cst.CSTTransformer):
    def handle_small_stmt(
        self: Self,
        small: cst.BaseSmallStatement
    ) -> list[cst.BaseSmallStatement]:
        """The declarations `small` contributes, or `small` itself when it
        survives verbatim -- which is how imports, `__all__` and assignments
        RightTyper left bare keep their value.
        """
        if isinstance(small, (cst.Import, cst.ImportFrom)):
            return [small]

        if isinstance(small, cst.Assign):
            if not all(isinstance(target.target, cst.Name) for target in small.targets):
                # can't handle tuples... do we need to?
                return []

            # RightTyper annotates what it can type, so an assignment still bare
            # here is one it declined -- an alias, a TypeVar, or anything at all
            # under --no-variables.  Its value is what says what it is, and a
            # type checker reads that more precisely than a name could.
            return [small]

        if isinstance(small, cst.AnnAssign):
            if not isinstance(small.target, cst.Name):
                # mypy rejects an annotated `c.x` or `d["k"]` in a stub, and
                # function bodies are `...` by the time we get here, so no
                # legitimate declaration is lost by dropping these.
                return []

            if small.target.value == '__all__':
                # Stripping this value would declare an export list with no
                # members -- a silent disagreement with the bare form above.
                return [small]

            # AnnAssign rejects a None value while the `=` token survives
            return [small.with_changes(value=None, equal=cst.MaybeSentinel.DEFAULT)]

        # Everything else -- expressions, `pass`, `del`, `global`, augmented
        # assignments -- declares nothing, so a stub has no place for it.
        return []

    def handle_body(self: Self, body: abc.Sequence[cst.CSTNode]) -> list[cst.CSTNode]:
        result: list[cst.CSTNode] = []
        for stmt in body:
            if isinstance(stmt, (cst.FunctionDef, cst.ClassDef, cst.If, cst.Try, cst.With)):
                result.append(stmt)
            elif isinstance(stmt, cst.SimpleStatementLine):
                kept = [
                    decl
                    for small in stmt.body
                    for decl in self.handle_small_stmt(small)
                ]

                if len(kept) == len(stmt.body) and all(
                    decl is small for decl, small in zip(kept, stmt.body)
                ):
                    result.append(stmt)
                    continue

                # One per line; the semicolons separated line-mates that may be gone.
                result.extend(
                    cst.SimpleStatementLine(body=[
                        decl.with_changes(semicolon=cst.MaybeSentinel.DEFAULT)
                    ])
                    for decl in kept
                )

        return result

    def leave_FunctionDef(
        self: Self,
        original_node: cst.FunctionDef,
        updated_node: cst.FunctionDef
    ) -> cst.FunctionDef:
        return updated_node.with_changes(
            body=cst.SimpleStatementSuite([cst.Expr(cst.Ellipsis())]),
            leading_lines=[]
        )

    def leave_Comment(    # type: ignore[override]
        self: Self,
        original_node: cst.Comment,
        updated_node: cst.Comment
        ) -> cst.RemovalSentinel:
        return cst.RemoveFromParent()

    def leave_ClassDef(
        self: Self,
        original_node: cst.ClassDef,
        updated_node: cst.ClassDef
    ) -> cst.ClassDef:
        return updated_node.with_changes(
            body=updated_node.body.with_changes(
                body=self.handle_body(updated_node.body.body)
            ),
            leading_lines=[]
        )

    def leave_If(
        self: Self,
        original_node: cst.If,
        updated_node: cst.If
    ) -> cst.If:
        return updated_node.with_changes(
            body=updated_node.body.with_changes(
                body=self.handle_body(updated_node.body.body)
            )
        )

    def leave_With(
        self: Self,
        original_node: cst.With,
        updated_node: cst.With
    ) -> cst.With:
        return updated_node.with_changes(
            body=updated_node.body.with_changes(
                body=self.handle_body(updated_node.body.body)
            )
        )

    def leave_Module(
        self: Self,
        original_node: cst.Module,
        updated_node: cst.Module
    ) -> cst.Module:
        return updated_node.with_changes(
            body=self.handle_body(updated_node.body)
        )
