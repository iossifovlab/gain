"""Single owner of the Dask run loop's "is anything still outstanding?"."""
from __future__ import annotations

import itertools
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from dask.distributed import Future

from gain import logging
from gain.task_graph.graph import Task, TaskDesc

logger = logging.getLogger(__name__)

WAIT_TIMEOUT = 0.05


@dataclass(frozen=True)
class SubmitBatch:
    """Tasks the submit worker is handing to the cluster.

    Held by the worker for the whole width of ``Client.map()``. It is in no
    collection during that call -- being in flight IS its state.
    """

    batch_id: int
    tasks: tuple[TaskDesc, ...]


@dataclass(frozen=True)
class GatherBatch:
    """Finished futures the results worker is collecting from the cluster.

    Held by the worker for the whole width of ``Client.gather()``, the
    mirror image of :class:`SubmitBatch`.
    """

    batch_id: int
    entries: tuple[tuple[Future, Task], ...]

    @property
    def futures(self) -> tuple[Future, ...]:
        """Futures in this batch, in order."""
        return tuple(future for future, _ in self.entries)

    @property
    def tasks(self) -> tuple[Task, ...]:
        """Tasks in this batch, in the same order as :attr:`futures`."""
        return tuple(task for _, task in self.entries)


class RunState:
    """All the state one Dask run needs, behind one lock.

    A task the run loop takes out of the graph is in exactly one of six
    states until the run loop yields it::

        queued -> in-flight submit -> running
               -> completed -> in-flight gather -> gathered

    Every hand-off between two threads -- run loop, submit worker, dask
    callback thread, results worker -- is a transition here, and each
    transition enters the next state and leaves the previous one under a
    single lock. A task therefore cannot be invisible by being absent from
    every collection, which is what let a run declare itself finished while
    a submission was still in flight (gain#365).

    The two in-flight states exist for exactly that reason: the workers
    must not hold a lock across ``Client.map()`` or ``Client.gather()``, so
    a batch that has left one collection and not yet reached the next is
    represented explicitly, by the worker's batch handle, rather than by
    its absence from both.

    :meth:`has_outstanding` is the single query all of this exists to
    answer, and the run loop's termination decision is that one call.

    The states are not all reachable from every transition, though, and the
    recovery paths are where that bites: a batch aborted mid-wiring can
    evict its futures from the collections it holds, but not from the
    results worker's hands. So "exactly one result per task" is not left to
    the collections to imply -- :meth:`_deliver` is the only way into
    ``gathered`` and it enforces the invariant outright (gain#381).
    """

    def __init__(self) -> None:
        # One condition -- so one lock -- for every state below. Every
        # public method here is short and never calls into dask, so no
        # thread can be held up behind a network round trip.
        self._condition = threading.Condition()
        self._batch_ids = itertools.count()

        self._queued: list[TaskDesc] = []
        self._submitting: dict[int, SubmitBatch] = {}
        self._running: dict[Future, Task] = {}
        self._completed: list[tuple[Future, Task]] = []
        self._gathering: dict[int, GatherBatch] = {}
        self._gathered: list[tuple[Task, Any]] = []
        self._delivered: set[Task] = set()
        self._shutdown = False

    def _deliver(self, results: Iterable[tuple[Task, Any]]) -> None:
        """Deliver results for tasks not delivered already.

        The one way a result reaches :attr:`_gathered`, and so the one place
        "exactly one result per task, never more" is enforced. Every other
        guard in this class keeps a task from being *lost*; this one keeps it
        from being delivered *twice*, which the recovery transitions can
        otherwise do -- see :meth:`submit_aborted` for the windows one of
        them cannot reach (gain#381). Rather than have each transition try to
        reach the others' states, whichever path delivers a task first wins
        and every later one drops its duplicate here.

        Caller holds the lock.
        """
        for task, result in results:
            if task in self._delivered:
                logger.debug(
                    "task %s was already delivered; dropping the duplicate "
                    "result", task.task_id)
                continue
            self._delivered.add(task)
            self._gathered.append((task, result))

    def _outstanding_count(self) -> int:
        """Count everything not yet yielded. Caller holds the lock."""
        return (
            len(self._queued)
            + sum(len(batch.tasks) for batch in self._submitting.values())
            + len(self._running)
            + len(self._completed)
            + sum(len(batch.entries) for batch in self._gathering.values())
            + len(self._gathered)
        )

    def has_outstanding(self) -> bool:
        """Answer whether any task is still on its way to being yielded.

        The single query, under the single lock: true from the instant a
        task is enqueued until the instant its result is taken by the run
        loop, with no gap in between.
        """
        with self._condition:
            return self._outstanding_count() > 0

    # -- run loop ---------------------------------------------------------

    def enqueue(self, tasks: Sequence[TaskDesc]) -> None:
        """Hand tasks extracted from the graph to the submit worker."""
        if not tasks:
            return
        with self._condition:
            assert not self._shutdown, \
                "cannot enqueue tasks after the run has been shut down"
            self._queued.extend(tasks)
            self._condition.notify_all()

    def unfinished_count(self) -> int:
        """Count tasks the cluster still owes a result for.

        Queued, in-flight submit and running -- what the run loop throttles
        new submissions on. Tasks whose result is already computed but not
        yet gathered or yielded are not counted: they take no cluster slot.
        """
        with self._condition:
            return (
                len(self._queued)
                + sum(len(batch.tasks) for batch in self._submitting.values())
                + len(self._running)
            )

    def wait_for_results(self, timeout: float = WAIT_TIMEOUT) -> None:
        """Block until a result is ready to yield, or ``timeout`` elapses.

        The timeout bounds how long the run loop goes without re-checking
        the graph for newly ready tasks.
        """
        with self._condition:
            if not self._gathered:
                self._condition.wait(timeout)

    def take_results(self) -> list[tuple[Task, Any]]:
        """Take every gathered result, in completion order.

        The results stop being outstanding here, so the caller must feed
        them back to the graph and yield them before it asks
        :meth:`has_outstanding` again.

        A task comes out of here at most once for the life of the run, no
        matter which path delivered it -- see :meth:`_deliver` (gain#381).
        """
        with self._condition:
            gathered = self._gathered
            self._gathered = []
            return gathered

    def shutdown(self) -> None:
        """Tell both workers the run is over, and drop unstarted work.

        A run can be shut down with the queue still full -- a consumer that
        stops iterating results abandons the run loop's generator part way.
        Those tasks never reached the cluster and nobody will collect them,
        so they are discarded here, under the same lock that sets the flag.
        Discarding them is what keeps :meth:`has_outstanding` truthful: a
        task left on the queue that no worker will ever claim would be
        counted as outstanding for as long as this object lived.

        Deliberately not what the gather side does -- see
        :meth:`claim_for_gather`. A completed future holds work the run has
        already paid for, so it is still handed over; a queued task has
        cost nothing yet.
        """
        with self._condition:
            self._shutdown = True
            if self._queued:
                logger.warning(
                    "run shutting down with %s task(s) never submitted; "
                    "discarding them...", len(self._queued))
                self._queued.clear()
            self._condition.notify_all()

    # -- submit worker ----------------------------------------------------

    def claim_for_submit(self) -> SubmitBatch | None:
        """Take the queued tasks into the in-flight submit state.

        Blocks until there is something to submit. Returns ``None`` once
        the run is shutting down, which is the worker's cue to stop.
        """
        with self._condition:
            while not self._queued and not self._shutdown:
                self._condition.wait(WAIT_TIMEOUT)

            if self._shutdown:
                # Nothing can be queued here: :meth:`shutdown` empties the
                # queue under this lock and :meth:`enqueue` refuses to add
                # to it afterwards. Silently walking away from a non-empty
                # queue is what this asserts against -- those tasks would
                # stay outstanding forever.
                assert not self._queued
                return None

            batch = SubmitBatch(next(self._batch_ids), tuple(self._queued))
            self._queued.clear()
            self._submitting[batch.batch_id] = batch
            return batch

    def submitted(
        self, batch: SubmitBatch, futures: Sequence[Future],
    ) -> None:
        """Move a submitted batch from in-flight submit to running.

        ``running`` knows every future before the batch leaves the
        in-flight state, so there is no instant at which the batch is in
        neither -- and the caller may only register completion callbacks
        after this returns, so a future that is already done cannot be
        reported before its task mapping exists (gain#355).
        """
        with self._condition:
            for future, task in zip(futures, batch.tasks, strict=True):
                self._running[future] = task.task
            del self._submitting[batch.batch_id]
            self._condition.notify_all()

    # -- dask callback thread ---------------------------------------------

    def task_finished(self, future: Future) -> None:
        """Move a finished future from running to completed.

        Called on a dask callback thread, once per future. Futures are
        handed over only once, but a callback thread is not trusted to
        guarantee that: a second report of the same future is ignored.
        """
        with self._condition:
            task = self._running.pop(future, None)
            if task is None:
                return
            self._completed.append((future, task))
            self._condition.notify_all()

    # -- results worker ---------------------------------------------------

    def claim_for_gather(self) -> GatherBatch | None:
        """Take the completed futures into the in-flight gather state.

        Blocks until something has completed. Returns ``None`` once the run
        is shutting down and everything completed has been claimed, which
        is the worker's cue to stop.
        """
        with self._condition:
            while not self._completed and not self._shutdown:
                self._condition.wait(WAIT_TIMEOUT)

            if not self._completed:
                return None

            batch = GatherBatch(next(self._batch_ids), tuple(self._completed))
            self._completed.clear()
            self._gathering[batch.batch_id] = batch
            return batch

    def gathered(
        self, batch: GatherBatch, results: Sequence[tuple[Task, Any]],
    ) -> None:
        """Move a gathered batch from in-flight gather to results.

        A task an aborted submit batch already delivered as an error is not
        delivered again here -- see :meth:`_deliver` -- but the batch leaves
        the in-flight gather state either way.
        """
        with self._condition:
            self._deliver(results)
            del self._gathering[batch.batch_id]
            self._condition.notify_all()

    def submit_failed(
        self, batch: SubmitBatch, error: BaseException,
    ) -> None:
        """Move a batch that could not be submitted out of in-flight submit.

        The mirror of :meth:`submitted` for the failure path: ``Client.map()``
        can raise (a dead scheduler connection, a serialization error) while
        the batch sits in the in-flight submit state, where it would be
        counted as outstanding forever and spin the run loop without end
        (gain#372). The failure is delivered as the result of every task in
        the batch -- exactly as a task that dies on the worker is delivered
        -- so the run loop yields it as an error and then terminates, and
        the batch leaves the submit state in the same lock hold.

        Delivers through :meth:`_deliver` like every other path. Nothing can
        have delivered these tasks already -- ``map()`` raised, so no future
        of theirs ever existed to complete -- but "every result goes through
        the ledger" is a rule worth having no exception to: an exception is
        the kind of thing a later change quietly grows a duplicate behind.
        """
        with self._condition:
            self._deliver((task.task, error) for task in batch.tasks)
            del self._submitting[batch.batch_id]
            self._condition.notify_all()

    def submit_aborted(
        self, batch: SubmitBatch, futures: Sequence[Future],
        error: BaseException,
    ) -> None:
        """Recover a batch whose wiring-up failed after ``map()`` returned.

        ``Client.map()`` handed the futures back, but moving them into
        ``running`` and attaching their completion callbacks raised part way
        -- a client tearing down under ``Future.add_done_callback``, which
        (unlike ``release()``) does not swallow it (gain#372). Some of the
        batch's futures may already sit in ``running`` with a callback
        attached, one of which may even have fired and moved to
        ``completed``; others never got one. Deliver the whole batch as a
        per-task error and drop every one of its futures from ``running``
        (and the batch from ``submitting``, in case ``submitted`` raised
        before it left it), so none lingers there counted as outstanding
        forever and the run terminates with one result per task. A future a
        callback already took out of ``running`` is simply absent there -- the
        pop shrugs -- but it now sits in ``completed``, so it is evicted from
        there too; otherwise the results worker would gather it and deliver
        its task a second time, on top of the batch error.

        Eviction reaches only as far as the collections this transition can
        see. The results worker runs in parallel and may have carried a
        callback-completed future beyond all of them -- into the in-flight
        gather state, into ``gathered``, or out to the run loop, which cannot
        be taken back at all. Those are :meth:`_deliver`'s to handle: the
        error is delivered only for the tasks nothing has delivered yet, so
        whichever of the two paths arrives first is the one result the task
        gets (gain#381). If every task in the batch already has a result, the
        wiring failure cost the run nothing and surfaces only in the log.
        """
        with self._condition:
            self._submitting.pop(batch.batch_id, None)
            future_set = set(futures)
            for future in futures:
                self._running.pop(future, None)
            self._completed = [
                (f, t) for f, t in self._completed if f not in future_set
            ]
            self._deliver((task.task, error) for task in batch.tasks)
            self._condition.notify_all()

    def gather_failed(
        self, batch: GatherBatch, error: BaseException,
    ) -> None:
        """Move a batch that could not be gathered out of in-flight gather.

        The mirror of :meth:`gathered` for the failure path: ``Client.gather()``
        can raise (a lost comm, a dead worker), and ``errors="skip"``
        suppresses task errors, not transport ones (gain#372). While the
        batch sits in the in-flight gather state such a failure would be
        counted as outstanding forever; the failure is delivered as the
        result of every task in the batch, so the run loop yields it as an
        error and then terminates, and the batch leaves the gather state in
        the same lock hold. The caller releases the batch's futures, as it
        does after a normal gather -- ``future.release()`` is a dask call and
        must not run under this lock.
        """
        with self._condition:
            self._deliver((task, error) for task in batch.tasks)
            del self._gathering[batch.batch_id]
            self._condition.notify_all()
