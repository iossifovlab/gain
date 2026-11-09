"""``binning_tracks_annotator``: the tracks of a ``binners:`` list.

The annotator resolves the list with the code of ``binning_tool`` and
answers one float attribute for each track, for each annotatable
(ADR 0041).
"""
from __future__ import annotations

import math
from contextlib import ExitStack
from typing import Any

from gain.annotation.annotatable import Annotatable
from gain.annotation.annotation_config import AnnotatorInfo
from gain.annotation.annotation_pipeline import (
    AnnotationPipeline,
    Annotator,
    AttributeSpec,
)
from gain.annotation.annotator_base import AnnotatedValues, AnnotatorBase
from gain.binning.binners import BoundBinner, Track, discover_binner_kinds
from gain.binning.run_definition import RunDefinitionError, resolve_binners
from gain.utils.regions import BedRegion

DEFAULT_PREFIX = "track"


def build_binning_tracks_annotator(
    pipeline: AnnotationPipeline, info: AnnotatorInfo,
) -> Annotator:
    return BinningTracksAnnotator(pipeline, info)


def track_attribute_name(prefix: str, track: Track) -> str:
    """The name of the attribute of ``track``."""
    return f"{prefix}_{track.binner_index}_{track.track_index}"


def refuse_local_files(binners: Any) -> None:
    """Refuse an entry whose ``meta`` names a local ``file_name``.

    An annotation pipeline can come from an untrusted user, so the
    annotator reads metadata only from GRR resources.  The refusal comes
    before the binners are resolved, which is when a kind reads the file.
    """
    if not isinstance(binners, list):
        return
    for index, entry in enumerate(binners):
        if not isinstance(entry, dict):
            continue
        for config in entry.values():
            meta = config.get("meta") if isinstance(config, dict) else None
            if isinstance(meta, dict) and "file_name" in meta:
                raise RunDefinitionError(
                    f"binners[{index}].meta: binning_tracks_annotator "
                    f"does not read a local file_name; give meta a "
                    f"resource_id or a resource_label")


class BinningTracksAnnotator(AnnotatorBase):
    """Answers each track of a ``binners:`` list over an annotatable.

    Each track is one attribute, named
    ``<prefix>_<binner_index>_<track_index>``, of type ``float``.  The
    annotator bins an annotatable as the one bin of the grid whose bin
    size is the end of the annotatable.  A NaN value is ``None``.
    """

    def __init__(self, pipeline: AnnotationPipeline, info: AnnotatorInfo):
        self._grr = pipeline.repository
        self._prefix = str(info.parameters.get("prefix", DEFAULT_PREFIX))
        binners = info.parameters.get("binners")
        refuse_local_files(binners)
        self._jobs = resolve_binners(binners, self._grr)
        self._tracks = [track for job in self._jobs for track in job.tracks]
        self._names = [
            track_attribute_name(self._prefix, track)
            for track in self._tracks]
        self._stack: ExitStack | None = None
        self._bound: list[BoundBinner] = []
        super().__init__(pipeline, info)

    def get_attribute_specs(self) -> dict[str, AttributeSpec]:
        return {
            name: AttributeSpec(
                source=name,
                value_type="float",
                description=", ".join(track.resource_ids),
                supports_aggregation=False,
            )
            for name, track in zip(self._names, self._tracks, strict=True)
        }

    def open(self) -> Annotator:
        if self._stack is None:
            kinds = discover_binner_kinds()
            with ExitStack() as stack:
                self._bound = [
                    stack.enter_context(
                        kinds[job.binner].bind(job, self._grr))
                    for job in self._jobs]
                self._stack = stack.pop_all()
        return super().open()

    def close(self) -> None:
        if self._stack is not None:
            self._stack.close()
            self._stack = None
            self._bound = []
        super().close()

    def _do_annotate(
        self, annotatable: Annotatable,
        context: dict[str, Any],  # ruff: ignore[unused-method-argument]
    ) -> AnnotatedValues:
        region = BedRegion(
            annotatable.chrom, annotatable.pos, annotatable.pos_end)
        values: list[float | None] = []
        for bound in self._bound:
            (row,) = bound.bin_region(region, annotatable.pos_end)
            values.extend(
                None if math.isnan(value) else float(value)
                for value in row)
        return self._from_sources(
            dict(zip(self._names, values, strict=True)))
