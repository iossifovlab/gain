"""Files a resource builder ships by name, beside what it renders itself.

A resource holds more than the files its builder authors: the statistics
build leaves ``statistics/`` behind, and what a resource's manifest lists
is what its readers and pages take to exist.  :class:`ExtraFilesMixin`
lets a test put such a file into a fixture resource by name -- a stand-in
histogram image, say -- without running the build that would have
written it, for a test whose subject is the file's *presence* (an
address, a manifest entry) rather than its content.  For the ``basic``
resource, which has no authored content of its own, the shipped files
ARE the payload.

A builder mixes it in and calls :meth:`ExtraFilesMixin.realize_files_into`
last, after everything it writes itself; a shipped file may then sit
beside the builder's own files but never over one.  It is a no-op until
:meth:`ExtraFilesMixin.with_file` is called, so a builder that does not use
it realizes byte-identical output.  Like :mod:`.resource_meta`, it lives in
its own module so the builder DSL can keep growing without ``builders.py``
turning into an unreadable slab.
"""
from __future__ import annotations

import dataclasses
import pathlib
from typing import Self

from gain.genomic_resources.repository import GR_CONF_FILE_NAME
from gain.genomic_resources.testing import setup_directories
from gain.genomic_resources.testing.score_specs import ResourceValidationError


@dataclasses.dataclass(frozen=True)
class ExtraFilesMixin:
    """Immutable shipped-file state shared by the resource builders."""

    files: tuple[tuple[str, str | bytes], ...] = ()

    def with_file(self, filename: str, content: str | bytes) -> Self:
        """Ship ``content`` as ``filename`` inside the realized resource.

        ``filename`` is relative to the resource directory and may name a
        subdirectory (``statistics/histogram_score.png``), which is
        created.  A name already shipped is replaced in place.

        The config is not a payload: it is rendered from the builder's own
        state, and a file under its name would silently win over all of
        it, so that name is refused here.  A file the builder writes
        itself (its data, index or sidecar) is refused when the resource
        is realized, since which of those a builder writes is settled
        only by its other knobs.
        """
        if filename == GR_CONF_FILE_NAME:
            raise ResourceValidationError(
                f"{filename!r} is the rendered config, not a payload; "
                f"declare meta with with_meta, or the whole block with "
                f"with_raw_meta")
        # A dict keeps a reassigned key in its slot, which is the
        # replace-in-place the docstring promises.
        files = dict(self.files)
        files[filename] = content
        return dataclasses.replace(self, files=tuple(files.items()))

    def files_content(self) -> dict[str, str | bytes]:
        """The shipped files as ``setup_directories`` content."""
        return dict(self.files)

    def realize_files_into(self, resource_dir: pathlib.Path) -> None:
        """Write the shipped files into ``resource_dir``, last.

        Called after the builder has written everything of its own, so a
        shipped file that would land on one of those is refused rather
        than replacing it -- a payload the config does not describe.
        """
        for filename, content in self.files:
            if (resource_dir / filename).exists():
                raise ResourceValidationError(
                    f"{filename!r} is a file this builder renders itself; "
                    f"with_file ships payloads beside it, not over it")
            setup_directories(resource_dir / filename, content)
