# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Meta-tests for the dask-ownership guard (#851).

Each test drives a nested in-process pytest run (``pytester``) whose
conftest loads ``tests.dask_guard`` alone, so the guard is exercised
through pytest's own boundary -- the way a real test author meets it --
without dragging the rest of this suite's conftest into the nested run.

The nested tests boot a *threaded* ``LocalCluster`` (``processes=False``):
the guard triggers on ``Client`` construction, so nothing here needs to
pay for nanny processes -- and a refused construction never builds a
cluster at all.
"""
import pytest

pytest_plugins = ["pytester"]

# The nested runs construct Clients inside this process, where the outer
# suite's guard is watching too -- these tests own that.
pytestmark = pytest.mark.dask_executor

CHEAP_CLIENT = """
from dask.distributed import Client

def make_client():
    return Client(
        n_workers=1, threads_per_worker=1, processes=False,
        dashboard_address=None)
"""


def test_unmarked_constructions_are_refused_wherever_they_happen(
    pytester: pytest.Pytester,
) -> None:
    """One nested run, three unmarked construction sites, three refusals.

    The fixture-scoped cases are the load-bearing ones: the suite's real
    dask clients come from a SESSION-scoped fixture, which instantiates
    during its first requesting test's setup, before any function-scoped
    autouse fixture could have snapshotted state.  Refusing at
    construction time is what makes the scope irrelevant.  A call-phase
    construction fails the test; a setup-phase one errors it.
    """
    pytester.makeconftest("pytest_plugins = ['tests.dask_guard']")
    pytester.makepyfile(
        helper=CHEAP_CLIENT,
        test_unmarked="""
            import pytest
            from helper import make_client

            def test_boots_a_cluster_in_the_call_phase():
                client = make_client()
                client.close(); client.cluster.close()

            @pytest.fixture
            def client():
                client = make_client()
                yield client
                client.close(); client.cluster.close()

            def test_uses_a_fixture_cluster(client):
                pass

            @pytest.fixture(scope="session")
            def session_client():
                client = make_client()
                yield client
                client.close(); client.cluster.close()

            def test_uses_a_session_cluster(session_client):
                pass
        """,
    )

    result = pytester.runpytest_inprocess("-p", "no:randomly")

    result.assert_outcomes(failed=1, errors=2)
    result.stdout.fnmatch_lines(["*dask_executor*"])
    result.stdout.fnmatch_lines(["*-j*1*"])


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

    result.assert_outcomes(passed=1)
