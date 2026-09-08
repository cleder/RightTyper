import pytest

@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    """Runs a test's registered post-check (see `runmypy`) within the call phase,
    so that the check's verdict is the test's own -- a failure fails the test, and
    an `xfail` marker covers it.  A failing test body re-raises out of the yield,
    leaving the check unrun.
    """
    result = yield
    if (check := getattr(item, "_post_check", None)) is not None:
        check()
    return result

def pytest_addoption(parser):
    parser.addoption(
        "--no-mypy",
        action="store_true",
        default=False,
        help="Disable running mypy on test outputs"
    )
