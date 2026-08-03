"""Provides the ``ann_data`` resource implementation."""

from __future__ import annotations

import copy
import json
from typing import Any, ClassVar

import pandas as pd
from markdown2 import markdown

from gain import logging
from gain.genomic_resources.ann_data_resource import (
    is_10x_matrix_name,
    open_ann_data_from_resource,
    resolve_10x_sidecars,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.resource_implementation import (
    GenomicResourceImplementation,
    InfoImplementationMixin,
)
from gain.task_graph.graph import TaskDesc, TaskGraph

logger = logging.getLogger(__name__)

# The describe statistics table backing the info page.
_DESCRIBE_OBS_STATISTIC = "statistics/describe_obs.csv"
_DESCRIBE_VAR_STATISTIC = "statistics/describe_var.csv"
_DESCRIBE_ANN_DATA_STATISTIC = "statistics/describe_ann_data.txt"


class AnnDataResourceImplementation(
    GenomicResourceImplementation,
    InfoImplementationMixin,
):
    """AnnData resource implementation."""

    template_name: ClassVar[str] = "ann_data.jinja"

    def _manifest_names(self, wanted: set[Any]) -> set[str]:
        return {
            entry.name
            for entry in self.resource.get_manifest()
            if entry.name in wanted
        }

    @property
    def _input_files(self) -> set[str]:
        """Return the declared data -- the input to the statistics."""
        file_name = self.config.get("file")
        if not isinstance(file_name, str):
            # A config with no ``file:`` contributes a ``None``.  The loader
            # is where that gets reported; this property is also reached from
            # the info page, so it degrades to an empty set rather than
            # raising -- the same way data_frame_resource_impl does.
            return set()

        # The 10x matrix-market form is a triple of files: the config names
        # the matrix member, and the two sidecars share its prefix.  All
        # three are statistics inputs, so editing the barcodes has to
        # invalidate the build the same way editing the matrix does.  Which
        # two sidecars depends on the layout, and that call is made once,
        # in the loader, so this and the read path cannot drift apart.
        wanted = {file_name}
        if is_10x_matrix_name(file_name):
            wanted |= resolve_10x_sidecars(
                self.resource.get_manifest(), file_name)

        return self._manifest_names(wanted)

    @property
    def files(self) -> set[str]:
        return self._input_files | self._manifest_names(
            {_DESCRIBE_OBS_STATISTIC, _DESCRIBE_VAR_STATISTIC,
             _DESCRIBE_ANN_DATA_STATISTIC})

    def _get_template_data(self) -> dict[str, Any]:
        info = copy.deepcopy(self.config)

        if "meta" in info:
            info["meta"] = markdown(str(info["meta"]))

        def get_info_key(stat_file: str) -> str:
            return stat_file.split("/")[1].split(".")[0]

        for pd_description_stat in [_DESCRIBE_OBS_STATISTIC,
                                    _DESCRIBE_VAR_STATISTIC]:
            if pd_description_stat in self.files:
                with self.resource.proto.open_raw_file(
                    self.resource, pd_description_stat, mode="rt",
                ) as stats_file:
                    df_description = pd.read_csv(stats_file, index_col=0).T
                    df_description.columns.name = "Columns"
                # statistics/describe_obs.csv"
                info_key = get_info_key(pd_description_stat)
                info[info_key] = df_description.to_html(index=True)

        if _DESCRIBE_ANN_DATA_STATISTIC in self.files:
            info_key = get_info_key(_DESCRIBE_ANN_DATA_STATISTIC)
            info[info_key] = \
                self.resource.get_file_content(_DESCRIBE_ANN_DATA_STATISTIC,
                                               mode="t")
        return info

    def get_info(self, **kwargs: Any) -> str:  # noqa: ARG002
        return InfoImplementationMixin.get_info(self)

    def get_statistics_info(self, **kwargs: Any) -> str:  # noqa: ARG002
        return InfoImplementationMixin.get_statistics_info(self)

    def calc_info_hash(self) -> bytes:
        return b"placeholder"

    def calc_statistics_hash(self) -> bytes:
        manifest = self.resource.get_manifest()
        return json.dumps({
            "config": {
                "format": self.config.get("format", "h5ad"),
                "parameters": self.config.get("parameters", {}),
            },
            "files_md5": {
                file_name: manifest[file_name].md5
                for file_name in sorted(self._input_files)
            },
        }, sort_keys=True, indent=2).encode()

    @staticmethod
    def _describe_annotations(annotations: Any) -> pd.DataFrame | None:
        """Summarise an ``obs``/``var`` annotation table.

        A ``backed="r"`` AnnData hands these out as pandas frames, but the
        attribute is typed ``DataFrame | Dataset2D`` because a lazily read
        one yields the xarray-backed form, which has no ``describe`` --
        ``to_memory`` is its pandas conversion.  Returns ``None`` for a
        table with no columns: ``describe`` of nothing is an empty frame,
        and writing it would put an unreadable statistic in the manifest.
        """
        frame: pd.DataFrame = (
            annotations if isinstance(annotations, pd.DataFrame)
            else annotations.to_memory()
        )
        if len(frame.columns) == 0:
            return None

        return frame.describe(include="all")

    @staticmethod
    def _stats_for_ann_data(resource: GenomicResource) -> None:
        # Through the context manager, not the bare loader: a backed read
        # holds an open h5py file, and a repo sweep builds one of these per
        # resource.
        with open_ann_data_from_resource(resource) as ann_data:
            for table, statistic in (
                (ann_data.obs, _DESCRIBE_OBS_STATISTIC),
                (ann_data.var, _DESCRIBE_VAR_STATISTIC),
            ):
                described = \
                    AnnDataResourceImplementation._describe_annotations(table)
                if described is None:
                    continue

                with resource.proto.open_raw_file(
                    resource, statistic, mode="wt",
                ) as outfile:
                    described.to_csv(outfile)

            with resource.proto.open_raw_file(
                resource, _DESCRIBE_ANN_DATA_STATISTIC, mode="wt",
            ) as outfile:
                print(str(ann_data), file=outfile)

    def create_statistics_build_tasks(
        self, **kwargs: Any,  # noqa: ARG002
    ) -> list[TaskDesc]:
        return [
            TaskGraph.make_task(
                f"{self.resource.get_full_id()}_ann_data_statistics",
                self._stats_for_ann_data,
                args=[self.resource]),
        ]
