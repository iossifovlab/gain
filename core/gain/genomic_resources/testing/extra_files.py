"""Extra payload files a score builder ships beside its config and data.

A resource holds more than the files its builder authors: the statistics
build leaves ``statistics/`` behind, and what a resource's manifest lists
is what its readers and pages take to exist.  :class:`ExtraFilesMixin`
lets a test put such a file into a fixture resource by name -- a stand-in
histogram image, say -- without running the build that would have
written it, for a test whose subject is the file's *presence* (an
address, a manifest entry) rather than its content.

A builder mixes it in and spreads :meth:`ExtraFilesMixin.render_extra_files`
into the content it realizes.  It is a no-op until
:meth:`ExtraFilesMixin.with_file` is called, so a builder that does not use
it realizes byte-identical output.  Like :mod:`.resource_meta`, it lives in
its own module so the builder DSL can keep growing without ``builders.py``
turning into an unreadable slab.
"""
from __future__ import annotations

import dataclasses
from typing import Self

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
        created.  A name already shipped is replaced in place.  The config
        is not a payload: it is rendered from the builder's own state, and
        a file under its name would silently win over all of it, so that
        name is refused.
        """
        if filename == GR_CONF_FILE_NAME:
            raise ResourceValidationError(
                f"{filename!r} is the rendered config, not a payload")
        files = dict(self.extra_files)
        files[filename] = content
        return dataclasses.replace(self, extra_files=tuple(files.items()))

    def render_extra_files(self) -> dict[str, str | bytes]:
        """The shipped files as ``setup_directories`` content."""
        return dict(self.extra_files)
