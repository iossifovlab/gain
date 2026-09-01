"""Provides the ``data_frame`` resource implementation."""

from __future__ import annotations

import copy
import json
from typing import Any, ClassVar

import pandas as pd

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
from gain.templates.markdown_support import render_markdown as markdown

logger = logging.getLogger(__name__)

# The describe statistics table backing the info page.
_DESCRIBE_STATISTIC = "statistics/describe.csv"


class DataFrameResourceImplementation(
    GenomicResourceImplementation,
    InfoImplementationMixin,
):
    """DataFrame resource implementation."""

    template_name: ClassVar[str] = "data_frame.jinja"

    def _manifest_names(self, wanted: set[Any]) -> set[str]:
        """Return the names in ``wanted`` the manifest actually lists.

        Everything this implementation names about itself goes through
        here: a name the resource does not have produces a classify
        failure line in the end-of-run cache summary (gain#43), and a
        name the manifest does not list has no md5 to hash.  ``wanted``
        is untyped because a config missing ``file:`` contributes a
        ``None`` -- the loader is where that gets reported.
        """
        return {
            entry.name
            for entry in self.resource.get_manifest()
            if entry.name in wanted
        }

    @property
    def _input_files(self) -> set[str]:
        """Return the declared table -- the input to the statistics."""
        return self._manifest_names({self.config.get("file")})

    @property
    def files(self) -> set[str]:
        # The two files this implementation actually reads: the declared
        # table and the describe statistics behind the info page.  This set
        # drives the cache prefetch worklist (see cached_repository), so
        # describe.csv is listed only once the manifest records it -- it is
        # absent until statistics are built.
        return self._input_files | self._manifest_names(
            {_DESCRIBE_STATISTIC})

    def _get_template_data(self) -> dict[str, Any]:
        info = copy.deepcopy(self.config)

        if "meta" in info:
            info["meta"] = markdown(str(info["meta"]))
        # Statistics may legitimately be absent: a read-only remote GRR
        # cannot build them, and grr_browse must still render the resource
        # rather than 500 on it.  The template guards on the key.  Asked of
        # the manifest rather than of the protocol -- ``file_exists`` is a
        # network round trip per render on http/s3, and the manifest is
        # already in memory and is the same index ``files`` consults.
        if _DESCRIBE_STATISTIC in self.files:
            with self.resource.proto.open_raw_file(
                self.resource, _DESCRIBE_STATISTIC, mode="rt",
            ) as stats_file:
                df_description = pd.read_csv(stats_file, index_col=0).T
                df_description.columns.name = "Columns"
            info["df_description"] = df_description.to_html(index=True)
        return info

    def get_info(self, **kwargs: Any) -> str:  # ruff: ignore[unused-method-argument]
        return InfoImplementationMixin.get_info(self)

    def get_statistics_info(self, **kwargs: Any) -> str:  # ruff: ignore[unused-method-argument]
        return InfoImplementationMixin.get_statistics_info(self)

    def calc_info_hash(self) -> bytes:
        return b"placeholder"

    def calc_statistics_hash(self) -> bytes:
        # Hashing the config alone left the data file invisible: editing the
        # table did not change the hash, so grr_manage never rebuilt
        # describe.csv -- the one thing this hash exists to trigger.  Shaped
        # like gene_models_impl / reference_genome_impl so a package-wide
        # change to statistics hashing sweeps this up too.  ``_input_files``
        # rather than ``files``: it degrades the same way (a resource with
        # no ``file:``, or naming a table the manifest does not list, hashes
        # to an empty files_md5 instead of raising), and it leaves out
        # describe.csv -- the build's own output, which would otherwise make
        # every freshly built resource look stale on the next run.
        manifest = self.resource.get_manifest()
        return json.dumps({
            "config": {
                "format": self.config.get("format", "csv"),
                "parameters": self.config.get("parameters", {}),
            },
            "files_md5": {
                file_name: manifest[file_name].md5
                for file_name in sorted(self._input_files)
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
        self, **kwargs: Any,  # ruff: ignore[unused-method-argument]
    ) -> list[TaskDesc]:
        return [
            TaskGraph.make_task(
                f"{self.resource.get_full_id()}_data_frame_statistics",
                self._stats_for_data_frame,
                args=[self.resource]),
        ]
