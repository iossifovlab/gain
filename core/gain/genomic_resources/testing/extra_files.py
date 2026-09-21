"""Extra payload files a score builder ships beside its config and data.

A resource holds more than the files its builder authors: the statistics
build leaves ``statistics/`` behind, and what a resource's manifest lists
is what its readers and pages take to exist.  :class:`ExtraFilesMixin`
lets a test put such a file into a fixture resource by name -- a stand-in
histogram image, say -- without running the build that would have
written it, for a test whose subject is the file's *presence* (an
address, a manifest entry) rather than its content.

A builder mixes it in and passes the content it renders itself through
:meth:`ExtraFilesMixin.merge_extra_files`, which adds the shipped files
beside it -- never over it.  It is a no-op until
:meth:`ExtraFilesMixin.with_file` is called, so a builder that does not use
it realizes byte-identical output.  Like :mod:`.resource_meta`, it lives in
its own module so the builder DSL can keep growing without ``builders.py``
turning into an unreadable slab.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping
from typing import Any, Self

from gain.genomic_resources.repository import GR_CONF_FILE_NAME
from gain.genomic_resources.testing.score_specs import ResourceValidationError


@dataclasses.dataclass(frozen=True)
class ExtraFilesMixin:
    """Immutable extra-file state shared by the score builders."""

    extra_files: tuple[tuple[str, str | bytes], ...] = ()

    def with_file(self, filename: str, content: str | bytes) -> Self:
        """Ship ``content`` as ``filename`` inside the realized resource.

        ``filename`` is relative to the resource directory and may name a
        subdirectory (``statistics/histogram_score.png``), which is
        created.  A name already shipped is replaced in place.

        A file the builder renders itself cannot be shipped: the config is
        refused here, and the data, index and sidecar files are refused
        when the resource is realized, since which of those a builder
        writes is settled only by its other knobs.  Either would let a
        payload silently contradict the config that describes it.
        """
        if filename == GR_CONF_FILE_NAME:
            raise ResourceValidationError(
                f"{filename!r} is the rendered config, not a payload")
        files = dict(self.extra_files)
        files[filename] = content
        return dataclasses.replace(self, extra_files=tuple(files.items()))

    def merge_extra_files(
        self, rendered: Mapping[str, Any], *,
        reserved: Iterable[str] = (),
    ) -> dict[str, Any]:
        """``rendered`` plus the shipped files, for ``setup_directories``.

        ``reserved`` names files the builder writes outside ``rendered``
        (a tabix table and its index, written by pysam after the directory
        is set up).  A shipped file under any rendered or reserved name is
        refused.
        """
        taken = set(rendered) | set(reserved)
        shadowing = [name for name, _ in self.extra_files if name in taken]
        if shadowing:
            raise ResourceValidationError(
                f"{shadowing!r} would shadow files this builder renders "
                f"itself; with_file ships payloads beside them, not over "
                f"them")
        return {**rendered, **dict(self.extra_files)}
