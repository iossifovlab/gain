# 39. The task graph API declares the graph, the task executors and the task caches

- **Status:** accepted
- **Date:** 2026-10-16
- **Issues:** [gain#1989](https://github.com/iossifovlab/gain/issues/1989),
  [gain#1991](https://github.com/iossifovlab/gain/issues/1991)

## Context

Before this chapter, the Python interface section of the docs declared two
parts of the API: resources and annotators. The resources chapter said that
the task graph is outside the declared API. All the same, scripts outside
`gain` build a task graph and run it. The statistics build of `grr_manage`,
the parallel runs of `annotate_tabular` and `annotate_vcf`, and the binning
tool do the same inside `gain`.

The packages `gain.task_graph` and `gain.dask` mix two kinds of names. Some
names are what a script calls. Other names are the internals of a task
executor or of a driver. No record said which names are which. The epic
gain#1989 adds a "Task graph" chapter, and the chapter needs that line.

## Decision

1. **The declared surface.** These names are the declared task graph API:
   - the graph: `TaskGraph`, `TaskDesc`, `Task` and `chain_tasks`.
   - the task executors: `TaskGraphExecutor`, `TaskGraphExecutorBase`,
     `SequentialExecutor`, `ProcessPoolTaskExecutor` and `DaskExecutor`.
   - the run functions: `task_graph_run` and `task_graph_run_with_results`.
   - the task caches: `TaskCache`, `FileTaskCache`, `NoTaskCache`,
     `CacheRecord` and `CacheRecordType`.
   - the driver seam: `TaskGraphCli`.
   - the clusters: `setup_client` and `setup_client_from_config` of
     `gain.dask.named_cluster`.
2. **The internal list.** The module index documents these names. The
   chapter does not:
   - `gain.task_graph.dask_run_state`: the dask task executor keeps its run
     state there, and no caller reads it.
   - `gain.task_graph.logging`: the task executors configure the log of
     each task with it.
   - `gain.task_graph.work_dir`: the drivers read their work directory
     options with it.
   - `gain.task_graph.demo_graphs_cli`: a demo command for manual tests.
   - `gain.dask.config`: the named cluster YAML loads through it, and the
     user side documents the YAML.
   - `sync_tasks`: a no-op function. A driver can give it to a task that
     only joins the dependencies of other tasks.
   - `task_graph_status`: `TaskGraphCli` calls it for its `list` and
     `status` commands.
   - `task_graph_all_done`: a helper that checks the task cache for each
     task. No driver and no other code in `gain` calls it.
   - the four `set_up_*` cluster functions: `setup_client` and
     `setup_client_from_config` choose between them, so a caller does not.
   - five `TaskGraph` members, `ready_tasks`, `extract_tasks`,
     `process_completed_tasks`, `get_task_deps` and `as_directed_graph`:
     only a task executor calls them. A later slice of the epic marks them
     `:meta private:`.
3. **One chapter with subpages.** The chapter `development/task_graph`
   follows the shape of the annotators chapter. Later slices add a subpage
   for each part of the declared surface. The autosummary outline test
   checks the subpages as it checks the resource pages.
4. **The user side stays on a user page.** The command-line options and the
   named cluster YAML belong to the user page "Running in parallel"
   (gain#1990). The chapter names that page and does not repeat it.

## The rejected narrow option

A narrow surface was the alternative: only the graph and `TaskGraphCli`
are declared, and the chapter describes the task executors in prose. That
option has a smaller surface to keep stable.

The option does not fit how scripts use the API. A script that does not use
`TaskGraphCli` must pick a task executor and pass a task cache to it.
`task_graph_run` takes a task executor as an argument. With the narrow
option, each of those scripts depends on names that the docs call
internal. The outline test also checks only the classes that a page
declares. Prose about a task executor gets no check, so it drifts from the
class.

## Consequences

- The names of the declared surface take a one-release deprecation window
  before a removal, the same posture as `gain.grr` (ADR 0033).
- A change to a name of the internal list needs no deprecation. A name that
  moves from the internal list to the declared surface needs a change to
  this ADR and to the chapter.
- The outline test collects pages from two directories,
  `development/resources/` and `development/task_graph/`. A skip list entry
  is a path relative to `docs/source/development`.
- The chapter has an empty toctree until the subpage slices land.
