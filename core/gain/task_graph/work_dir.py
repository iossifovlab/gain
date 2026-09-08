"""The work-dir convention a task-graph CLI tool follows.

Shared by the annotate tools and ``binning_tool`` (gain#1215, gain#1234):
the paths the user typed are absolutized before the tool ``chdir``s into
its work directory, the work directory and the task status/log directories
are defaulted from the output, and a work directory the tool created is
removed after a clean run.

The parsed-argument keys read here are the task-graph ones
:meth:`gain.task_graph.cli_tools.TaskGraphCli.add_arguments` defines
(``task_status_dir``, ``task_log_dir``, ``dask_cluster_config_file``,
``command``) and the ones the convention itself names, which each tool
defines on its own parser: ``output``, ``work_dir``, ``keep_work_dir`` and
the annotate tools' ``keep_parts``.  Any further path option a tool has
(its GRR options) is the caller's to name; see :func:`absolutize_path_args`.
"""
from __future__ import annotations

import os
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from gain import logging
from gain.utils.fs_utils import strip_compression_suffix

logger = logging.getLogger(__name__)


def _absolutize(args: dict[str, Any], *keys: str) -> None:
    """Absolutize the named path arguments; absent or empty ones stay."""
    for key in keys:
        if args.get(key):
            args[key] = os.path.abspath(args[key])


def absolutize_path_args(
    args: dict[str, Any], *, input_key: str,
    extra_keys: Iterable[str] = (),
) -> None:
    """Absolutize the tool's primary input and the common path arguments.

    ``input_key`` names the tool's primary input (``input`` for the
    annotate tools, ``run_definition`` for ``binning_tool``);
    ``extra_keys`` names the tool's own further path options (its GRR
    options, typically). A key that is absent or empty is left alone.
    """
    _absolutize(
        args, input_key, "output", "work_dir", "task_status_dir",
        "task_log_dir", "dask_cluster_config_file", *extra_keys)


def apply_work_dir_defaults(args: dict[str, Any]) -> None:
    """Default and create the work directory and the task directories.

    The convention the annotate tools and ``binning_tool`` share: the work
    directory is the output with its compression suffix and its extension
    stripped plus ``_work``, the task status and log directories are
    ``.task-status`` and ``.task-log`` inside it. ``work_dir_created``
    records whether the tool created the directory (it did not pre-exist)
    -- :func:`maybe_remove_work_dir` reads it -- and every non-empty path
    set here is absolute, so the tool can ``chdir`` into the work
    directory afterwards.
    """
    if args.get("work_dir") is None:
        path = Path(strip_compression_suffix(args["output"]))
        args["work_dir"] = f"{path.with_suffix('')}_work"
    _absolutize(args, "work_dir")

    args["work_dir_created"] = not os.path.exists(args["work_dir"])
    if args["work_dir_created"]:
        os.makedirs(args["work_dir"])

    for key, name in (("task_status_dir", ".task-status"),
                      ("task_log_dir", ".task-log")):
        if args.get(key) is None:
            args[key] = os.path.join(args["work_dir"], name)
    _absolutize(args, "task_status_dir", "task_log_dir")


def maybe_remove_work_dir(args: dict[str, Any], *, result: bool) -> None:
    """Remove the working directory after a clean run, if the tool made it.

    The directory is removed only when every condition holds:

    - the tool created it (it did not pre-exist; see ``work_dir_created``),
    - the command actually ran annotation (not ``list``/``status``),
    - the run succeeded (``result`` is ``True`` -- a ``--keep-going`` run that
      finished with task errors returns ``False`` and is preserved),
    - neither ``--keep-parts`` nor ``--keep-work-dir`` was requested,
    - the output file does not live inside the working directory.

    Removal is best-effort: a failure to remove logs a warning and is not
    fatal, since the annotation has already succeeded.
    """
    if not args.get("work_dir_created"):
        return
    if args.get("command") not in (None, "run"):
        return
    if not result:
        return
    if args.get("keep_parts") or args.get("keep_work_dir"):
        return

    work_dir = Path(os.path.abspath(args["work_dir"]))
    output = Path(os.path.abspath(args["output"]))
    if output.is_relative_to(work_dir):
        logger.warning(
            "output %s is inside the working directory %s; not removing it",
            output, work_dir)
        return

    try:
        shutil.rmtree(work_dir)
    except OSError as err:
        logger.warning(
            "could not remove working directory %s: %s", work_dir, err)
        return
    logger.info("removed working directory %s", work_dir)
