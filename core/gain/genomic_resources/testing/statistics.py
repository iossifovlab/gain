"""Run a fixture resource's REAL statistics build, the way grr_manage does.

A test about what a statistics build leaves behind -- a histogram it drew,
one it nullified, a manifest that lists the result -- wants the build
itself, not a hand-written ``statistics/`` directory that the build might
never produce.  :func:`build_statistics` runs the implementation's own
tasks to completion; :func:`refresh_manifest` rewrites the stored manifest
afterwards, which is what ``repo-stats`` does after its build and without
which the resource's loaded manifest still describes the tree from
before.  :func:`publish_statistics` is the two in the order the CLI runs
them.  Tests that want the build without the refresh -- to show a manifest
going stale -- call the two apart.

Beside them live the score recipes a build nullifies, one per way it can:
a histogram the min/max pass finds no range for, and one the accumulation
overflows.  What a nullification leaves on disk is a build-internals fact,
so the shapes that provoke it are stated here once.
"""
from __future__ import annotations

from typing import Any

from gain.genomic_resources.histogram import CategoricalHistogram
from gain.genomic_resources.repository import (
    GenomicResource,
    ReadWriteRepositoryProtocol,
)
from gain.genomic_resources.repository_factory import (
    build_resource_implementation,
)
from gain.genomic_resources.testing.builders import (
    GeneScoreBuilder,
    PositionScoreBuilder,
    a_gene_score,
    a_position_score,
)
from gain.task_graph.cli_tools import task_graph_run
from gain.task_graph.executor import TaskGraphExecutor
from gain.task_graph.graph import TaskGraph
from gain.task_graph.sequential_executor import SequentialExecutor


def build_statistics(
    resource: GenomicResource, *,
    executor: TaskGraphExecutor | None = None,
    **build_kwargs: Any,
) -> None:
    """Run ``resource``'s statistics build tasks to completion.

    ``build_kwargs`` go to the implementation's
    ``create_statistics_build_tasks`` as given (``region_size=`` for a
    genomic score); its defaults stand otherwise.  ``executor`` defaults
    to a plain sequential one.
    """
    impl = build_resource_implementation(resource)
    graph = TaskGraph()
    graph.add_tasks(impl.create_statistics_build_tasks(**build_kwargs))
    task_graph_run(graph, executor or SequentialExecutor())


def refresh_manifest(resource: GenomicResource) -> None:
    """Rewrite the stored manifest, as grr_manage does after a build.

    ``save_manifest`` drops the resource's memoised manifest itself, so
    the next ``get_loaded_manifest`` reads the rewritten one.
    """
    proto = resource.proto
    assert isinstance(proto, ReadWriteRepositoryProtocol), (
        "a manifest is written into the resource; the protocol "
        f"<{proto.proto_id}> cannot")
    proto.save_manifest(resource, proto.build_manifest(resource))


def publish_statistics(resource: GenomicResource, **build_kwargs: Any) -> None:
    """Build ``resource``'s statistics and refresh its stored manifest."""
    build_statistics(resource, **build_kwargs)
    refresh_manifest(resource)


def a_score_with_no_values() -> PositionScoreBuilder:
    """A configured number histogram the build nullifies in its min/max pass.

    Every value is NA, so no range is found and nothing is drawn -- and
    no histogram file is written for it either.
    """
    return (
        a_position_score()
        .with_score("score", "float")
        .with_histogram({"type": "number", "number_of_bins": 10})
        .with_na_values("NA")
        .with_data("""
            chrom  pos_begin  score
            1      10         NA
            1      20         NA
        """)
    )


def _too_many_labels() -> list[str]:
    return [
        f"label{i}"
        for i in range(CategoricalHistogram.UNIQUE_VALUES_LIMIT + 1)
    ]


def a_score_with_too_many_categories() -> PositionScoreBuilder:
    """A default categorical histogram the build nullifies accumulating.

    No ``histogram:`` block, so the ``str`` score gets the default
    categorical config, which enforces the unique-values limit -- and the
    values exceed it.  A null histogram file IS written for this one;
    still, nothing is drawn.
    """
    rows = "\n".join(
        f"1  {10 * (i + 1)}  {label}"
        for i, label in enumerate(_too_many_labels()))
    return (
        a_position_score()
        .with_score("score", "str")
        .with_data("chrom  pos_begin  score\n" + rows)
    )


def a_gene_score_with_too_many_categories() -> GeneScoreBuilder:
    """The gene-score twin of :func:`a_score_with_too_many_categories`.

    ``pli`` has values and gets its histogram drawn; ``label`` overflows
    the default categorical histogram and gets none.
    """
    rows = "\n".join(
        f"G{i}  {i / 1000}  {label}"
        for i, label in enumerate(_too_many_labels()))
    return (
        a_gene_score()
        .with_score("pli", "float")
        .with_score("label", "str")
        .with_data("gene  pli  label\n" + rows)
    )
