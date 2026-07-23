# pylint: disable=W0621,C0114,C0115,C0116,W0212,W0613
from unittest.mock import MagicMock

from gain.task_graph.dask_run_state import RunState, SubmitBatch
from gain.task_graph.graph import Task, TaskDesc


def noop() -> None:
    pass


def a_task_desc(task_id: str) -> TaskDesc:
    return TaskDesc(
        task=Task(task_id), func=noop, args=[], kwargs={}, deps=[],
        input_files=[], output_files=[], intermediate_output_files=[],
    )


def a_claimed_submit_batch(state: RunState, task_id: str) -> SubmitBatch:
    """Enqueue one task and take it into the in-flight submit state."""
    state.enqueue([a_task_desc(task_id)])
    batch = state.claim_for_submit()
    assert batch is not None
    return batch


def test_a_task_is_outstanding_at_every_step_from_queued_to_yielded() -> None:
    """The whole contract in one walk (gain#367).

    Every hand-off is a transition on the run state, and the task is
    outstanding at every instant in between -- including the two windows
    where a worker holds it and no collection does: while ``map()`` is in
    flight (gain#365) and while ``gather()`` is in flight.
    """
    state = RunState()
    future = MagicMock()

    state.enqueue([a_task_desc("A")])
    assert state.has_outstanding(), "queued"

    batch = state.claim_for_submit()
    assert batch is not None
    assert state.has_outstanding(), "in flight to the cluster: map() running"

    state.submitted(batch, [future])
    assert state.has_outstanding(), "running on the cluster"

    state.task_finished(future)
    assert state.has_outstanding(), "finished, waiting to be gathered"

    gather_batch = state.claim_for_gather()
    assert gather_batch is not None
    assert state.has_outstanding(), "in flight from the cluster: gather()"

    state.gathered(gather_batch, [(Task("A"), 42)])
    assert state.has_outstanding(), "gathered, waiting to be yielded"

    assert state.take_results() == [(Task("A"), 42)]
    assert not state.has_outstanding(), "yielded: nothing left"


def test_a_fresh_run_state_has_nothing_outstanding() -> None:
    assert not RunState().has_outstanding()


def test_the_submit_worker_is_told_to_stop_on_shutdown() -> None:
    state = RunState()
    state.shutdown()

    assert state.claim_for_submit() is None


def test_the_results_worker_gathers_what_completed_before_shutdown() -> None:
    """Shutdown must not strand futures that already finished."""
    state = RunState()
    future = MagicMock()

    batch = a_claimed_submit_batch(state, "A")
    state.submitted(batch, [future])
    state.task_finished(future)
    state.shutdown()

    gather_batch = state.claim_for_gather()
    assert gather_batch is not None
    assert gather_batch.tasks == (Task("A"),)

    state.gathered(gather_batch, [(Task("A"), 42)])
    assert state.claim_for_gather() is None


def test_the_same_future_reported_twice_is_only_completed_once() -> None:
    """Callback threads are not trusted to fire exactly once."""
    state = RunState()
    future = MagicMock()

    batch = a_claimed_submit_batch(state, "A")
    state.submitted(batch, [future])

    state.task_finished(future)
    state.task_finished(future)

    gather_batch = state.claim_for_gather()
    assert gather_batch is not None
    assert gather_batch.entries == ((future, Task("A")),)


def test_only_tasks_the_cluster_still_owes_a_result_are_unfinished() -> None:
    """What the run loop throttles on: gathered results take no slot."""
    state = RunState()
    future = MagicMock()

    batch = a_claimed_submit_batch(state, "A")
    assert state.unfinished_count() == 1, "in flight to the cluster"

    state.submitted(batch, [future])
    assert state.unfinished_count() == 1, "running on the cluster"

    state.task_finished(future)
    assert state.unfinished_count() == 0, "computed; only its result is left"
    assert state.has_outstanding()


def test_shutdown_discards_tasks_that_never_reached_the_cluster() -> None:
    """The mirror of the gather side, and deliberately not the same answer.

    A task still on the submit queue has cost the run nothing, so shutdown
    drops it; a future that already finished holds work the run has paid
    for, so shutdown still hands it over (see
    ``test_the_results_worker_gathers_what_completed_before_shutdown``).

    What neither side may do is leave the queue in limbo -- neither
    delivered nor discarded. ``claim_for_submit`` used to return ``None``
    and leave the tasks sitting in ``_queued``, where nothing would ever
    take them and ``has_outstanding()`` would keep answering "yes" about
    them for as long as the state object lived.

    This is not hypothetical: a consumer that abandons the result iterator
    shuts the run down with the queue still full.
    """
    state = RunState()
    state.enqueue([a_task_desc("A"), a_task_desc("B")])

    state.shutdown()

    assert state.claim_for_submit() is None
    assert not state.has_outstanding(), (
        "tasks discarded at shutdown are still counted as outstanding"
    )
