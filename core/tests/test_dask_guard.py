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

    # The probe refuses the construction inside the test's call phase,
    # so the test fails right where the cluster would have booted.
    result.assert_outcomes(failed=1)
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
    """A cluster a fixture boots belongs to the test that requested it."""
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

    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*dask_executor*"])


@pytest.mark.dask_executor
def test_a_session_scoped_fixture_cluster_does_not_escape(
    pytester: pytest.Pytester,
) -> None:
    """The suite's real dask clients come from SESSION-scoped fixtures.

    Pytest instantiates higher-scoped fixtures before function-scoped
    autouse ones, so a snapshot-and-diff guard taken at its own setup
    never sees this construction.  The guard must catch it anyway --
    this is exactly the shape of ``tests/small/task_graph/conftest.py``'s
    ``dask_client``.
    """
    pytester.makeconftest("pytest_plugins = ['tests.dask_guard']")
    pytester.makepyfile(
        helper=CHEAP_CLIENT,
        test_session_client="""
            import pytest
            from helper import make_client

            @pytest.fixture(scope="session")
            def client():
                client = make_client()
                yield client
                client.close(); client.cluster.close()

            def test_uses_a_session_cluster_unmarked(client):
                pass
        """,
    )

    result = pytester.runpytest_inprocess("-p", "no:randomly")

    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*dask_executor*"])
