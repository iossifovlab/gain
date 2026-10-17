from __future__ import annotations

import datetime
import os
import pickle  # ruff: ignore[suspicious-pickle-import]
from abc import abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, cast

import fsspec

from gain import logging
from gain.task_graph.graph import Task, TaskDesc
from gain.utils import fs_utils

logger = logging.getLogger(__name__)


class CacheRecordType(Enum):
    """The state of a task in a task cache.

    Attributes
    ----------
    NEEDS_COMPUTE : CacheRecordType
        The task must run.
    COMPUTED : CacheRecordType
        The task ran without an error, and its record is still valid.
    ERROR : CacheRecordType
        The task ran and failed.
    """

    NEEDS_COMPUTE = 0
    COMPUTED = 1
    ERROR = 2


@dataclass(frozen=True)
class CacheRecord:
    """The record of one task in a task cache.

    Attributes
    ----------
    type : CacheRecordType
        The state of the task.
    result_or_error : Any
        The result of the task, the exception of a failed task, or
        ``None`` when the task cache holds no outcome.
    """

    type: CacheRecordType
    result_or_error: Any = None

    @property
    def result(self) -> Any:
        """The result of a task whose record type is ``COMPUTED``."""
        assert self.type == CacheRecordType.COMPUTED
        return self.result_or_error

    @property
    def error(self) -> Any:
        """The exception of a task whose record type is ``ERROR``."""
        assert self.type == CacheRecordType.ERROR
        return self.result_or_error

    def invalidate(self) -> CacheRecord:
        """Return a copy of the record whose type is ``NEEDS_COMPUTE``.

        :meta private:
        """
        return CacheRecord(CacheRecordType.NEEDS_COMPUTE, self.result_or_error)


class TaskCache:
    """The interface of a task cache.

    A task executor asks the task cache whether a task must run, and
    records the outcome of each task that runs in it.
    """

    @abstractmethod
    def get_record(
        self, task_desc: TaskDesc,
    ) -> CacheRecord:
        """Return the record of a task.

        Parameters
        ----------
        task_desc : TaskDesc
            The description of the task, from
            :meth:`~gain.task_graph.graph.TaskGraph.get_task_desc`.

        Returns
        -------
        CacheRecord
            The record of the task. The task must run unless the record
            type is ``COMPUTED``. ``NEEDS_COMPUTE`` means that the task
            cache has no valid record of the task.
        """

    @abstractmethod
    def cache(
        self, task: Task, *,
        is_error: bool, result: Any,
    ) -> None:
        """Record the outcome of a task.

        Parameters
        ----------
        task : Task
            The task that ran.
        is_error : bool
            True when the task failed.
        result : Any
            The result of the task, or its exception when it failed.
        """

    @staticmethod
    def create(
        *,
        force: bool = False,
        task_progress_mode: bool = True,
        cache_dir: str | None = None,
    ) -> TaskCache:
        """Create the task cache for a set of options.

        Parameters
        ----------
        force : bool
            When true, the method returns a :class:`NoTaskCache`.
        task_progress_mode : bool
            The status mode. When false, the method returns a
            :class:`NoTaskCache`.
        cache_dir : str | None
            The task status directory of a :class:`FileTaskCache`.
            ``None`` means the current directory.

        Returns
        -------
        TaskCache
            A :class:`NoTaskCache` when ``force`` is true or the status
            mode is off. Otherwise a :class:`FileTaskCache` over
            ``cache_dir``.
        """
        if force or not task_progress_mode:
            return NoTaskCache()
        if cache_dir is None:
            cache_dir = os.getcwd()
        return FileTaskCache(cache_dir=cache_dir)


class NoTaskCache(dict[Any, Any], TaskCache):
    """A task cache that runs every task and records nothing."""

    def get_record(
        self, task_desc: TaskDesc,  # ruff: ignore[unused-method-argument]
    ) -> CacheRecord:
        """Return a record whose type is ``NEEDS_COMPUTE``.

        Parameters
        ----------
        task_desc : TaskDesc
            The description of the task. The method does not read it.

        Returns
        -------
        CacheRecord
            A record whose type is ``NEEDS_COMPUTE`` and that holds no
            result.
        """
        return CacheRecord(CacheRecordType.NEEDS_COMPUTE)

    def cache(
        self, task: Task, *,
        is_error: bool, result: Any,
    ) -> None:
        """Do nothing: the task cache records no outcome.

        Parameters
        ----------
        task : Task
            The task that ran.
        is_error : bool
            True when the task failed.
        result : Any
            The result of the task, or its exception when it failed.
        """


class FileTaskCache(TaskCache):
    """A task cache that keeps one record file for each task.

    The record file of a task is ``<task id>.flag`` in the task status
    directory. It holds the record type and the result or the exception
    of the task. The modification times of the files of a task decide
    whether a computed record is still valid.

    Parameters
    ----------
    cache_dir : str
        The task status directory. The task cache creates it when it
        writes the first record.
    """

    def __init__(self, cache_dir: str):
        self.cache_dir = cache_dir

    def get_record(self, task_desc: TaskDesc) -> CacheRecord:
        """Return the record of a task from its record file.

        A computed record stays valid while the output files of the task
        exist and are not older than its input files. A missing input file
        makes the record invalid. A missing intermediate output file does
        not, because a later task can consume it. A task without output
        files compares its input files with its record file. A missing or
        unreadable record file means that the task must run.

        Parameters
        ----------
        task_desc : TaskDesc
            The description of the task, with its input and output files.

        Returns
        -------
        CacheRecord
            The record of the task. Its type is ``NEEDS_COMPUTE`` when the
            record file is missing or unreadable, or when the record is
            computed but no longer valid. Otherwise it is the record that
            the file holds, ``COMPUTED`` or ``ERROR``.
        """
        flag_filename = self._get_flag_filename(task_desc.task)
        try:
            with fsspec.open(flag_filename, "rb") as cache_file:
                task_record = cast(
                    CacheRecord,
                    pickle.load(cache_file))  # pyright: ignore
        except FileNotFoundError:
            return CacheRecord(CacheRecordType.NEEDS_COMPUTE)
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "Cannot read status for task %s. Ignoring and continuing.",
                task_desc,
            )
            return CacheRecord(CacheRecordType.NEEDS_COMPUTE)

        if task_record.type != CacheRecordType.COMPUTED:
            return task_record

        if self._needs_recompute(task_desc):
            task_record = CacheRecord(
                CacheRecordType.NEEDS_COMPUTE,
                result_or_error=task_record.result,
            )

        return task_record

    def cache(self, task: Task, *, is_error: bool, result: Any) -> None:
        """Write the record file of a task.

        A failure to write the file is logged, and the run continues.

        Parameters
        ----------
        task : Task
            The task that ran.
        is_error : bool
            True when the task failed. The record type is then ``ERROR``,
            otherwise ``COMPUTED``.
        result : Any
            The result of the task, or its exception when it failed. It
            must be picklable.
        """
        record_type = (
            CacheRecordType.ERROR if is_error else CacheRecordType.COMPUTED
        )
        record = CacheRecord(
            record_type,
            result,
        )
        cache_fn = self._get_flag_filename(task)
        try:
            with fsspec.open(cache_fn, "wb") as cache_file:
                pickle.dump(record, cache_file)  # pyright: ignore
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "Cannot write cache for task %s. Ignoring and continuing.",
                task,
            )

    def _get_flag_filename(self, task: Task) -> str:
        return fs_utils.join(self.cache_dir, f"{task.task_id}.flag")

    def _needs_recompute(
        self, task: TaskDesc,
    ) -> bool:
        """
        Determine if a task needs to be recomputed.

        Phase 1 — output_files (final outputs the user may delete):
          If any are missing → recompute. If all exist, compare against inputs.

        Phase 2 — intermediate_output_files (consumed by downstream tasks):
          If all exist, compare against inputs. If missing → fall through to
          the flag-file check so the task is not needlessly recomputed.

        Phase 3 — flag-file check (fallback when no output files are declared
          or when intermediate outputs are missing).
        """
        input_files = task.input_files

        if task.output_files:
            out_mtime = self._get_oldest_mod_time(task.output_files)
            if out_mtime is None:
                return True  # missing final output → recompute
            in_mtime = self._get_newest_mod_time(input_files)
            if len(input_files) == 0:
                return False  # no inputs, outputs exist
            if in_mtime is None:
                return True  # missing input file
            return in_mtime > out_mtime

        if task.intermediate_output_files:
            out_mtime = self._get_oldest_mod_time(
                task.intermediate_output_files)
            if out_mtime is not None:
                in_mtime = self._get_newest_mod_time(input_files)
                if len(input_files) == 0:
                    return False  # no inputs, outputs exist
                if in_mtime is None:
                    return True  # missing input file
                return in_mtime > out_mtime
            # files missing (consumed by downstream) → fall through

        output_files = [self._get_flag_filename(task.task)]
        input_mtime = self._get_newest_mod_time(input_files)
        output_mtime = self._get_oldest_mod_time(output_files)
        if len(input_files) == 0 and output_mtime is not None:
            return False  # no inputs, flag exists
        if input_mtime is None or output_mtime is None:
            return True  # cannot determine mod times

        return input_mtime > output_mtime

    def _get_oldest_mod_time(
        self, filenames: list[str],
    ) -> datetime.datetime | None:
        mtimes = [self._safe_getmtime(path) for path in filenames]
        if any(p is None for p in mtimes):
            # cannot determine the mtime of a filename. Assume it needs recalc.
            return None
        if len(mtimes) > 0:
            return min(cast(list[datetime.datetime], mtimes))
        return None

    def _get_newest_mod_time(
        self, filenames: list[str],
    ) -> datetime.datetime | None:
        mtimes = [self._safe_getmtime(path) for path in filenames]
        if any(p is None for p in mtimes):
            # cannot determine the mtime of a filename. Assume it needs recalc.
            return None
        if len(mtimes) > 0:
            return max(cast(list[datetime.datetime], mtimes))
        return None

    def _safe_getmtime(self, path: str) -> datetime.datetime | None:
        if fs_utils.exists(path):
            return fs_utils.modified(path)
        return None
