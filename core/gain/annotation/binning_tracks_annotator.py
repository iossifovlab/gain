"""``binning_tracks_annotator``: the tracks of a ``binners:`` list.

The annotator resolves the list with the code of ``binning_tool`` and
answers one float attribute for each track, for each annotatable
(ADR 0041).
"""
from __future__ import annotations

import json
import math
import re
import textwrap
from collections.abc import Hashable, Mapping
from contextlib import ExitStack
from typing import Any

import pandas as pd

from gain.annotation.annotatable import Annotatable
from gain.annotation.annotation_config import AnnotatorInfo
from gain.annotation.annotation_pipeline import (
    AnnotationPipeline,
    Annotator,
    AttributeSpec,
)
from gain.annotation.annotator_base import AnnotatedValues, AnnotatorBase
from gain.binning.binners import (
    TRACKS_COLUMNS,
    BinningJob,
    BoundBinner,
    Track,
    discover_binner_kinds,
    tracks_frame,
)
from gain.binning.h5_reader import track_labels
from gain.binning.run_definition import resolve_binners
from gain.utils.regions import BedRegion

DEFAULT_PREFIX = "track"

#: The most tracks that one annotator gives, one attribute each.
MAX_TRACKS = 2000

#: The pattern of a VCF INFO key, which each attribute name must match.
VCF_KEY_PATTERN = "[A-Za-z_][0-9A-Za-z_.]*"


def build_binning_tracks_annotator(
    pipeline: AnnotationPipeline, info: AnnotatorInfo,
) -> Annotator:
    return BinningTracksAnnotator(pipeline, info)


def track_attribute_name(prefix: str, track: Track) -> str:
    """The name of the attribute of ``track``."""
    return f"{prefix}_{track.binner_index}_{track.track_index}"


def checked_prefix(prefix: Any) -> str:
    """``prefix``, refused unless it matches :data:`VCF_KEY_PATTERN`."""
    if not isinstance(prefix, str) \
            or re.fullmatch(VCF_KEY_PATTERN, prefix) is None:
        raise ValueError(
            f"prefix {prefix!r} does not match the VCF key pattern "
            f"{VCF_KEY_PATTERN}")
    return prefix


def code_span(value: Any) -> str:
    """``value`` as a Markdown code span that shows it verbatim.

    The fence is one backtick longer than the longest backtick run of
    the value, so no backtick of the value closes the span.  A renderer
    escapes HTML inside a code span.  Each line ending of the value
    becomes a space, as a renderer shows it inside a code span, so a
    blank line of the value cannot end the paragraph and the span.
    """
    text = re.sub(r"\r\n|\r|\n", " ", str(value))
    runs = re.findall(r"`+", text)
    fence = "`" * (max((len(run) for run in runs), default=0) + 1)
    return f"{fence} {text} {fence}"


def track_documentation(label: str, row: Mapping[Hashable, Any]) -> str:
    """The Markdown documentation of a track: its label, then its fields.

    ``row`` is the track's row of :func:`gain.binning.tracks_frame`.  The
    label and the field values are code spans, so a renderer shows them
    verbatim.  The ``binner_def`` field is shown as indented JSON.
    """
    fields = "\n".join(
        f"* `{field}`: {code_span(row[field])}"
        for field in TRACKS_COLUMNS if field != "binner_def")
    definition = json.dumps(
        json.loads(row["binner_def"]), indent=2, sort_keys=True)
    return (
        f"{code_span(label)}\n\n{fields}\n\n`binner_def`:\n\n"
        f"```\n{definition}\n```")


def labeled_tracks_frame(jobs: list[BinningJob]) -> pd.DataFrame:
    """:func:`tracks_frame` of ``jobs``, with a refusal that names the entry.

    ``tracks_frame`` refuses a definition with a NaN or an infinity, as
    ``binning_tool`` does.  This refusal names the annotator and the
    ``binners`` entry, so the author finds the wrong entry.
    """
    for job in jobs:
        try:
            tracks_frame([job])
        except ValueError as error:
            raise ValueError(
                f"{job.entry}: binning_tracks_annotator refuses a "
                f"definition with a NaN or an infinity, as binning_tool "
                f"does: {error}") from error
    return tracks_frame(jobs)


class LocalFilesError(ValueError):
    """An entry of a ``binners:`` list that reads a local file.

    ``binning_tool`` reads a relative local file from the directory of
    the run definition.  A pipeline has no such directory, so the
    annotator reads metadata only from GRR resources.
    """

    def __init__(self, label: str) -> None:
        super().__init__(
            f"{label}: binning_tracks_annotator does not read a local "
            f"file, such as a meta file_name, because a pipeline has no "
            f"directory of a run definition; give meta a resource_id or "
            f"a resource_label")


def refuse_local_file_names(binners: Any) -> None:
    """Refuse an entry whose ``meta`` names a ``file_name``, unread.

    An annotation pipeline can come from an untrusted user.  A kind
    reads the file when the list is resolved, and an error of that read
    can quote the file, so this refusal comes before the resolution.
    """
    if not isinstance(binners, list):
        return
    for index, entry in enumerate(binners):
        if not isinstance(entry, dict):
            continue
        for config in entry.values():
            meta = config.get("meta") if isinstance(config, dict) else None
            if isinstance(meta, dict) and "file_name" in meta:
                raise LocalFilesError(f"binners[{index}]")


def refuse_local_files(jobs: list[BinningJob]) -> None:
    """Refuse a resolved job whose ``local_files`` is not empty."""
    for job in jobs:
        if job.local_files:
            raise LocalFilesError(job.entry)


def refuse_too_many_tracks(tracks: list[Track]) -> None:
    """Refuse more than :data:`MAX_TRACKS` tracks, naming their number."""
    if len(tracks) > MAX_TRACKS:
        raise ValueError(
            f"binning_tracks_annotator gives at most {MAX_TRACKS} "
            f"tracks; the binners entries give {len(tracks)}")


class BinningTracksAnnotator(AnnotatorBase):
    """Answers each track of a ``binners:`` list over an annotatable.

    Each track is one attribute, named
    ``<prefix>_<binner_index>_<track_index>``, of type ``float``.  The
    annotator bins an annotatable as the one bin of the grid whose bin
    size is the end of the annotatable.  A NaN value is ``None``.
    """

    def __init__(self, pipeline: AnnotationPipeline, info: AnnotatorInfo):
        if info.attributes:
            raise ValueError(
                "binning_tracks_annotator takes no attributes list, "
                "because the binners entries define the attributes: one "
                "for each track")
        self._grr = pipeline.repository
        self._prefix = checked_prefix(
            info.parameters.get("prefix", DEFAULT_PREFIX))
        binners = info.parameters.get("binners")
        refuse_local_file_names(binners)
        self._jobs = resolve_binners(binners, self._grr)
        refuse_local_files(self._jobs)
        self._tracks = [track for job in self._jobs for track in job.tracks]
        refuse_too_many_tracks(self._tracks)
        self._names = [
            track_attribute_name(self._prefix, track)
            for track in self._tracks]
        frame = labeled_tracks_frame(self._jobs)
        self._labels = track_labels(frame)
        documentation = {
            name: track_documentation(label, row)
            for name, label, row in zip(
                self._names, self._labels, frame.to_dict("records"),
                strict=True)}
        self._stack: ExitStack | None = None
        self._bound: list[BoundBinner] = []
        super().__init__(pipeline, info)

        info.documentation += textwrap.dedent(f"""

Annotator that gives one float attribute for each track of a `binners:`
list of `binning_tool` entries.

<a href="{self.BASE_DOC_URL}#binning-tracks-annotator" target="_blank">More info</a>

""")  # ruff: ignore[line-too-long]
        for attr in self._attributes:
            attr._documentation = (  # ruff: ignore[private-member-access]
                documentation[attr.source])

    def get_attribute_specs(self) -> dict[str, AttributeSpec]:
        return {
            name: AttributeSpec(
                source=name,
                value_type="float",
                description=label,
                supports_aggregation=False,
            )
            for name, label in zip(self._names, self._labels, strict=True)
        }

    def open(self) -> Annotator:
        if self._stack is None:
            kinds = discover_binner_kinds()
            with ExitStack() as stack:
                self._bound = [
                    stack.enter_context(
                        kinds[job.binner].bind(
                            job, self._grr, quiet=True))
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
