# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Meta-tests for the dask-ownership guard (#851).

Each test drives a nested in-process pytest run (``pytester``) whose
conftest loads ``tests.dask_guard`` alone, so the guard is exercised
through pytest's own boundary -- the way a real test author meets it --
without dragging the rest of this suite's conftest into the nested run.

The nested tests boot a *threaded* ``LocalCluster`` (``processes=False``):
the guard triggers on ``Client`` construction, so nothing here needs to
pay for nanny processes.
"""
import pytest

pytest_plugins = ["pytester"]

CHEAP_CLIENT = """
from dask.distributed import Client

def make_client():
    return Client(
        n_workers=1, threads_per_worker=1, processes=False,
        dashboard_address=None)
"""


@pytest.mark.dask_executor
def test_an_unmarked_test_that_boots_a_cluster_fails(
    pytester: pytest.Pytester,
) -> None:
    pytester.makeconftest("pytest_plugins = ['tests.dask_guard']")
    pytester.makepyfile(
        helper=CHEAP_CLIENT,
        test_unmarked="""
            from helper import make_client

            def test_boots_a_cluster():
                client = make_client()
                client.close(); client.cluster.close()
        """,
    )

    result = pytester.runpytest_inprocess("-p", "no:randomly")

    # The guard raises in fixture teardown, so pytest reports the red
    # outcome as an ERROR -- the same shape as the suite's
    # deprecation-notice ownership guard.
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*dask_executor*"])
    result.stdout.fnmatch_lines(["*-j*1*"])


@pytest.mark.dask_executor
def test_a_marked_test_that_boots_a_cluster_passes(
    pytester: pytest.Pytester,
) -> None:
    pytester.makeconftest("pytest_plugins = ['tests.dask_guard']")
    pytester.makepyfile(
        helper=CHEAP_CLIENT,
        test_marked="""
            import pytest
            from helper import make_client

            @pytest.mark.dask_executor
            def test_boots_a_cluster_it_owns():
                client = make_client()
                client.close(); client.cluster.close()
        """,
    )

    result = pytester.runpytest_inprocess("-p", "no:randomly")

    result.assert_outcomes(passed=1, errors=0)


@pytest.mark.dask_executor
def test_a_client_built_by_a_fixture_is_charged_to_the_requesting_test(
    pytester: pytest.Pytester,
) -> None:
    """The suite's real dask clients come from (session-scoped) fixtures.

    The construction happens during some test's setup phase, so the guard
    must charge it to that test rather than let it slip between tests.
    """
    pytester.makeconftest("pytest_plugins = ['tests.dask_guard']")
    pytester.makepyfile(
        helper=CHEAP_CLIENT,
        test_fixture_client="""
            import pytest
            from helper import make_client

            @pytest.fixture
            def client():
                client = make_client()
                yield client
                client.close(); client.cluster.close()

            def test_uses_a_fixture_cluster_unmarked(client):
                pass
        """,
    )

    result = pytester.runpytest_inprocess("-p", "no:randomly")

    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*dask_executor*"])
