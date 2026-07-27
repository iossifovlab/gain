"""Provides the ``data_frame`` resource implementation."""

from __future__ import annotations

import copy
import json
from typing import Any, ClassVar

import pandas as pd
from markdown2 import markdown

from gain import logging
from gain.genomic_resources.data_frame_resource import (
    load_data_frame_from_resource,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.resource_implementation import (
    GenomicResourceImplementation,
    InfoImplementationMixin,
)
from gain.task_graph.graph import TaskDesc, TaskGraph

logger = logging.getLogger(__name__)

# The describe statistics table backing the info page.
_DESCRIBE_STATISTIC = "statistics/describe.csv"


class DataFrameResourceImplementation(
    GenomicResourceImplementation,
    InfoImplementationMixin,
):
    """DataFrame resource implementation."""

    template_name: ClassVar[str] = "data_frame.jinja"

    @property
    def files(self) -> set[str]:
        # The two files this implementation actually reads: the declared
        # table and the describe statistics behind the info page.  This set
        # drives the cache prefetch worklist (see cached_repository), so it
        # is intersected with the manifest rather than stated flat: naming a
        # file the resource does not have produces a classify failure line
        # in the end-of-run cache summary (gain#43), and describe.csv is
        # absent until statistics are built.  ``.get`` because a config
        # missing ``file:`` must still yield a set here -- the loader is
        # where that misconfiguration gets reported.
        wanted = {self.config.get("file"), _DESCRIBE_STATISTIC}
        return {
            entry.name
            for entry in self.resource.get_manifest()
            if entry.name in wanted
        }

    def _get_template_data(self) -> dict[str, Any]:
        info = copy.deepcopy(self.config)

        if "meta" in info:
            info["meta"] = markdown(str(info["meta"]))
        # Statistics may legitimately be absent: a read-only remote GRR
        # cannot build them, and grr_browse must still render the resource
        # rather than 500 on it.  The template guards on the key.
        if self.resource.file_exists(_DESCRIBE_STATISTIC):
            with self.resource.proto.open_raw_file(
                self.resource, _DESCRIBE_STATISTIC, mode="rt",
            ) as stats_file:
                df_description = pd.read_csv(stats_file, index_col=0).T
                df_description.columns.name = "Columns"
            info["df_description"] = df_description.to_html(index=True)
        return info

    def get_info(self, **kwargs: Any) -> str:  # noqa: ARG002
        return InfoImplementationMixin.get_info(self)

    def get_statistics_info(self, **kwargs: Any) -> str:  # noqa: ARG002
        return InfoImplementationMixin.get_statistics_info(self)

    def calc_info_hash(self) -> bytes:
        return b"placeholder"

    def calc_statistics_hash(self) -> bytes:
        # Hashing the config alone left the data file invisible: editing the
        # table did not change the hash, so grr_manage never rebuilt
        # describe.csv -- the one thing this hash exists to trigger.  Shaped
        # like gene_models_impl / reference_genome_impl so a package-wide
        # change to statistics hashing sweeps this up too.
        manifest = self.resource.get_manifest()
        file_name = str(self.config["file"])
        return json.dumps({
            "config": {
                "format": self.config.get("format", "csv"),
                "parameters": self.config.get("parameters", {}),
            },
            "files_md5": {
                file_name: manifest[file_name].md5,
            },
        }, sort_keys=True, indent=2).encode()

    @staticmethod
    def _stats_for_data_frame(resource: GenomicResource) -> None:
        df = load_data_frame_from_resource(resource)
        dsk = df.describe(include="all")

        with resource.proto.open_raw_file(
            resource, _DESCRIBE_STATISTIC, mode="wt",
        ) as outfile:
            dsk.to_csv(outfile)

    def create_statistics_build_tasks(
        self, **kwargs: Any,  # noqa: ARG002
    ) -> list[TaskDesc]:
        return [
            TaskGraph.make_task(
                f"data_frame_{self.resource}",
                self._stats_for_data_frame,
                args=[self.resource]),
        ]
