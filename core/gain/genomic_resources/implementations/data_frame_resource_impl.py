"""Provides the ``data_frame`` resource implementation."""

from __future__ import annotations

import copy
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


class DataFrameResourceImplementation(
    GenomicResourceImplementation,
    InfoImplementationMixin,
):
    """DataFrame resource implementation."""

    template_name: ClassVar[str] = "data_frame.jinja"

    @property
    def files(self) -> set[str]:
        # No type-specific file list: a basic resource ships arbitrary files,
        # so every manifest entry (other than the config that
        # _enumerate_resource_files adds itself, and lockfiles) is part of it.
        return {
            entry.name
            for entry in self.resource.get_manifest()
            if entry.name != "genomic_resource.yaml"
            and not entry.name.endswith(".lockfile")
        }

    def _get_template_data(self) -> dict[str, Any]:
        info = copy.deepcopy(self.config)

        if "meta" in info:
            info["meta"] = markdown(str(info["meta"]))
        with self.resource.proto.open_raw_file(
            self.resource, "statistics/describe.csv", mode="rt",
        ) as stats_file:
            df_description = pd.read_csv(stats_file)
        info["df_description"] = df_description.to_html(index=False)
        return info

    def get_info(self, **kwargs: Any) -> str:  # noqa: ARG002
        return InfoImplementationMixin.get_info(self)

    def get_statistics_info(self, **kwargs: Any) -> str:  # noqa: ARG002
        return InfoImplementationMixin.get_statistics_info(self)

    def calc_info_hash(self) -> bytes:
        return b"placeholder"

    def calc_statistics_hash(self) -> bytes:
        payload = (
            str(self.config["file"])
            + str(self.config.get("format", "csv"))
            + str(self.config.get("parameters", {}))
        )
        return payload.encode("utf-8")

    @staticmethod
    def _stats_for_data_frame(resource: GenomicResource) -> None:
        df = load_data_frame_from_resource(resource)
        dsk = df.describe(include="all")

        with resource.proto.open_raw_file(
            resource, "statistics/describe.csv", mode="wt",
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
