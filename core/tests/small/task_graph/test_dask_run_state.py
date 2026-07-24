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


def a_claimed_submit_batch_of(
    state: RunState, task_ids: list[str],
) -> SubmitBatch:
    """Enqueue several tasks and take them into the in-flight submit state."""
    state.enqueue([a_task_desc(task_id) for task_id in task_ids])
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


def test_a_batch_that_fails_to_submit_is_delivered_as_error_results() -> None:
    """A batch the submit worker could not hand to the cluster (gain#372).

    ``Client.map()`` can raise -- a dead scheduler connection, a
    serialization error. The batch is in the in-flight submit state at that
    instant, so without a terminal transition out of it the batch would be
    counted as outstanding forever and the run loop would spin without end.
    ``submit_failed`` delivers the failure as the result of every task in
    the batch, exactly as a task that dies on the worker is delivered, so
    the run yields it as an error and then terminates.
    """
    state = RunState()
    batch = a_claimed_submit_batch(state, "A")
    error = RuntimeError("map failed: the scheduler is gone")

    state.submit_failed(batch, error)

    assert state.has_outstanding(), "delivered as a result, still to be yielded"
    assert state.take_results() == [(Task("A"), error)]
    assert not state.has_outstanding(), "the failed batch is no longer counted"


def test_a_batch_that_fails_to_gather_is_delivered_as_error_results() -> None:
    """A batch the results worker could not collect (gain#372).

    ``Client.gather()`` can raise -- a lost comm, a dead worker -- and
    ``errors="skip"`` suppresses task errors, not transport ones. The batch
    is in the in-flight gather state at that instant; ``gather_failed``
    delivers the failure as the result of every task and drops the batch
    from the gather state, so the run terminates instead of spinning. The
    caller releases the batch's futures, as it does after a normal gather.
    """
    state = RunState()
    future = MagicMock()

    batch = a_claimed_submit_batch(state, "A")
    state.submitted(batch, [future])
    state.task_finished(future)
    gather_batch = state.claim_for_gather()
    assert gather_batch is not None
    error = RuntimeError("gather failed: the connection is gone")

    state.gather_failed(gather_batch, error)

    assert state.has_outstanding(), "delivered as a result, still to be yielded"
    assert state.take_results() == [(Task("A"), error)]
    assert not state.has_outstanding(), "the failed batch is no longer counted"


def test_a_batch_that_fails_after_being_submitted_is_delivered_as_errors(
) -> None:
    """A batch map() returned but wiring-up could not finish (gain#372).

    ``Client.map()`` hands the futures back and the submit worker then moves
    them into ``running`` and attaches their completion callbacks. That can
    raise part way -- a client tearing down under ``add_done_callback``,
    which (unlike ``release()``) does not swallow it -- with the whole batch
    already in ``running``. ``submit_aborted`` delivers the failure as the
    result of every task and clears every one of its futures from
    ``running``, so none lingers counted as outstanding forever and the run
    terminates with exactly one result per task.
    """
    state = RunState()
    batch = a_claimed_submit_batch_of(state, ["A", "B", "C"])
    futures = [MagicMock(), MagicMock(), MagicMock()]
    state.submitted(batch, futures)
    assert state.unfinished_count() == 3, "all three running on the cluster"
    error = TypeError("add_done_callback failed: the client loop is gone")

    state.submit_aborted(batch, futures, error)

    assert state.unfinished_count() == 0, "no future left stranded in running"
    assert state.take_results() == [
        (Task("A"), error), (Task("B"), error), (Task("C"), error)]
    assert not state.has_outstanding(), "the failed batch is no longer counted"


def test_submit_aborted_tolerates_a_future_a_callback_already_took() -> None:
    """A callback can fire between ``submitted`` and the raise (gain#372).

    ``add_done_callback`` registers before it raises, so a future whose
    callback wins the race has already left ``running`` for ``completed``.
    ``submit_aborted`` must not trip over its absence, and still delivers
    every task in the batch as the failure.
    """
    state = RunState()
    batch = a_claimed_submit_batch_of(state, ["A", "B"])
    futures = [MagicMock(), MagicMock()]
    state.submitted(batch, futures)
    state.task_finished(futures[0])  # its callback fired first
    error = RuntimeError("add_done_callback failed: the client loop is gone")

    state.submit_aborted(batch, futures, error)

    assert futures[0] not in state._running
    assert futures[1] not in state._running
    assert futures[0] not in [f for f, _ in state._completed], (
        "a future its callback already moved to completed was left there; "
        "the results worker will gather and deliver its task a second time"
    )
    assert (Task("A"), error) in state._gathered
    assert (Task("B"), error) in state._gathered


def test_submit_aborted_does_not_deliver_a_completed_future_twice() -> None:
    """The batch error must not be duplicated by a future already completed.

    An already-finished future's ``task_finished`` callback can fire --
    moving it from ``running`` to ``completed`` -- before
    ``add_done_callback`` raises on a later future in the same batch.
    ``submit_aborted`` delivers that task as the batch error; if it also
    left the future in ``completed`` the results worker would gather it and
    deliver the SAME task a second time. So the batch is counted and
    delivered exactly once per task, and the stray completed future is
    evicted (gain#372).
    """
    state = RunState()
    batch = a_claimed_submit_batch_of(state, ["A", "B"])
    futures = [MagicMock(), MagicMock()]
    state.submitted(batch, futures)
    state.task_finished(futures[0])  # its callback won the race to completed
    error = RuntimeError("add_done_callback failed: the client loop is gone")

    state.submit_aborted(batch, futures, error)

    assert futures[0] not in [f for f, _ in state._completed], (
        "a completed future was left behind and will be gathered and "
        "delivered a second time (gain#372)"
    )
    assert state._outstanding_count() == 2, (
        f"a 2-task batch is outstanding as {state._outstanding_count()}; the "
        f"stray completed future is counted on top of the batch error"
    )

    # Drain the way the run loop would, gathering anything left in completed;
    # each task must come out exactly once, never twice.
    state.shutdown()
    while (gather_batch := state.claim_for_gather()) is not None:
        state.gathered(
            gather_batch,
            [(task, "SUCCESS") for task in gather_batch.tasks])
    delivered = [task for task, _ in state.take_results()]
    assert delivered.count(Task("A")) == 1, (
        f"Task A was delivered {delivered.count(Task('A'))} times; a completed "
        f"future left by submit_aborted is gathered and re-delivers it "
        f"(gain#372)"
    )
    assert delivered.count(Task("B")) == 1
    assert len(delivered) == 2, (
        f"the 2-task batch delivered {len(delivered)} results; a completed "
        f"future left behind delivers a task twice (gain#372)"
    )
    assert not state.has_outstanding()


def test_submit_aborted_does_not_duplicate_a_task_claimed_for_gather() -> None:
    """The results worker can hold the future the abort wants to evict.

    ``submit_aborted`` evicts the batch's futures from ``running`` and
    ``completed``, but the results worker runs in parallel and can have
    claimed a callback-completed future for gather already -- putting it in
    the in-flight gather state, which no eviction reaches. The batch error
    and the real result would then both be delivered for that one task
    (gain#381). Exactly one result per task holds across that window: the
    abort gets there first, so its error is the one delivered and the later
    gather drops its duplicate.
    """
    state = RunState()
    batch = a_claimed_submit_batch_of(state, ["A", "B"])
    futures = [MagicMock(), MagicMock()]
    state.submitted(batch, futures)
    state.task_finished(futures[0])  # its callback fired first
    gather_batch = state.claim_for_gather()  # ...and the results worker has it
    assert gather_batch is not None
    error = RuntimeError("add_done_callback failed: the client loop is gone")

    state.submit_aborted(batch, futures, error)
    state.gathered(
        gather_batch, [(task, "SUCCESS") for task in gather_batch.tasks])

    delivered = state.take_results()
    delivered_tasks = [task for task, _ in delivered]
    assert delivered_tasks.count(Task("A")) == 1, (
        f"Task A was delivered {delivered_tasks.count(Task('A'))} times; the "
        f"abort cannot reach a future the results worker claimed for gather, "
        f"so it delivers the batch error on top of the real result (gain#381)"
    )
    assert delivered_tasks.count(Task("B")) == 1
    assert delivered == [(Task("A"), error), (Task("B"), error)], (
        "the abort delivered first, so its error is the result of record"
    )
    assert not state.has_outstanding()


def test_submit_aborted_does_not_duplicate_an_already_gathered_task() -> None:
    """The results worker can have finished before the abort even runs.

    One step further on than the in-flight gather window: the real result is
    in ``gathered`` already, waiting for the run loop to take it, and no
    eviction reaches there either (gain#381). The gather got there first, so
    its real result is the one that stands and the abort's error is dropped
    for that task -- while the task that never completed still gets the
    error, so the failure is not silently swallowed.
    """
    state = RunState()
    batch = a_claimed_submit_batch_of(state, ["A", "B"])
    futures = [MagicMock(), MagicMock()]
    state.submitted(batch, futures)
    state.task_finished(futures[0])
    gather_batch = state.claim_for_gather()
    assert gather_batch is not None
    state.gathered(
        gather_batch, [(task, "SUCCESS") for task in gather_batch.tasks])
    error = RuntimeError("add_done_callback failed: the client loop is gone")

    state.submit_aborted(batch, futures, error)

    assert state.take_results() == [(Task("A"), "SUCCESS"), (Task("B"), error)]
    assert not state.has_outstanding()


def test_submit_aborted_does_not_redeliver_a_task_the_run_loop_took() -> None:
    """A result the run loop already yielded cannot be taken back.

    The last and widest of the three windows no eviction reaches: the run
    loop has taken the real result and yielded it to the caller, so the only
    way not to deliver that task twice is to remember that it was delivered
    at all (gain#381). The abort still delivers the task that never
    completed, so the run still learns of the failure.
    """
    state = RunState()
    batch = a_claimed_submit_batch_of(state, ["A", "B"])
    futures = [MagicMock(), MagicMock()]
    state.submitted(batch, futures)
    state.task_finished(futures[0])
    gather_batch = state.claim_for_gather()
    assert gather_batch is not None
    state.gathered(
        gather_batch, [(task, "SUCCESS") for task in gather_batch.tasks])
    assert state.take_results() == [(Task("A"), "SUCCESS")], "yielded already"
    error = RuntimeError("add_done_callback failed: the client loop is gone")

    state.submit_aborted(batch, futures, error)

    assert state.take_results() == [(Task("B"), error)], (
        "Task A was delivered a second time, after the run loop had already "
        "yielded its real result (gain#381)"
    )
    assert not state.has_outstanding()


def test_gather_failed_does_not_duplicate_a_task_the_abort_delivered() -> None:
    """Two recovery transitions can reach for the same task.

    The abort delivers the batch error for a task whose future the results
    worker is already gathering; that gather then fails and delivers its own
    error for the very same task. Both are recovery paths, and neither can
    see the other's state -- so the ledger, not the collections, is what
    keeps this to one result per task (gain#381).
    """
    state = RunState()
    batch = a_claimed_submit_batch_of(state, ["A", "B"])
    futures = [MagicMock(), MagicMock()]
    state.submitted(batch, futures)
    state.task_finished(futures[0])
    gather_batch = state.claim_for_gather()
    assert gather_batch is not None
    abort_error = RuntimeError("add_done_callback failed: the loop is gone")
    state.submit_aborted(batch, futures, abort_error)
    gather_error = OSError("gather failed: the connection is gone")

    state.gather_failed(gather_batch, gather_error)

    assert state.take_results() == [
        (Task("A"), abort_error), (Task("B"), abort_error)], (
        "the gather failure re-delivered a task the abort had already "
        "delivered (gain#381)"
    )
    assert not state.has_outstanding()
