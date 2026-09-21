"""Run a fixture resource's REAL statistics build, the way grr_manage does.

A test about what a statistics build leaves behind -- a histogram it drew,
one it nullified, a manifest that lists the result -- wants the build
itself, not a hand-written ``statistics/`` directory that the build might
never produce.  This runs the implementation's own tasks to completion and
then refreshes the stored manifest, which is what ``repo-stats`` does
after its build: without the refresh the resource's loaded manifest still
describes the tree from before the build.
"""
from __future__ import annotations

from gain.genomic_resources.repository import (
    GenomicResource,
    ReadWriteRepositoryProtocol,
)
from gain.genomic_resources.repository_factory import (
    build_resource_implementation,
)
from gain.task_graph.cli_tools import task_graph_run
from gain.task_graph.graph import TaskGraph
from gain.task_graph.sequential_executor import SequentialExecutor


def build_statistics(
    resource: GenomicResource, *, region_size: int = 1_000_000,
) -> None:
    """Build ``resource``'s statistics and refresh its stored manifest."""
    proto = resource.proto
    assert isinstance(proto, ReadWriteRepositoryProtocol), (
        "a statistics build writes into the resource; the protocol "
        f"<{proto.proto_id}> cannot")
    impl = build_resource_implementation(resource)
    graph = TaskGraph()
    graph.add_tasks(impl.create_statistics_build_tasks(
        region_size=region_size))
    task_graph_run(graph, SequentialExecutor())
    proto.save_manifest(resource, proto.build_manifest(resource))
    resource.invalidate()
