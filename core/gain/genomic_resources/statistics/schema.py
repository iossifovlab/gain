"""Which stored statistics files a build writes, and at what version.

Each stored statistic stamps a ``format_version`` in its JSON, and its
writer's current one is a named constant in the statistic's module.  A
resource implementation declares the files its build writes for this
resource as :class:`StatisticsFile` records carrying those constants, and
:func:`stale_statistics_files` compares what the resource holds against
that declaration.

The comparison is a report, never a rebuild trigger.  ``calc_statistics_hash``
is an input hash (ADR 0020, "Alternatives considered"): a release that adds
a statistic or bumps a stored format leaves every built resource's hash
current, so the file stays as it was and the page reads "not computed"
under a heading the code knows how to fill.  The repair flow reports what
this module finds so that gap is visible, and leaves the rollout to a
forced rebuild (gain#1586).
"""
from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterable

from gain.genomic_resources.repository import GenomicResource


@dataclasses.dataclass(frozen=True)
class StatisticsFile:
    """One stored statistics file and the ``format_version`` its writer stamps.

    ``format_version`` is the constant the writer's ``serialize`` stamps,
    so the check and the writer cannot drift.
    """

    path: str
    format_version: int


@dataclasses.dataclass(frozen=True)
class StaleStatisticsFile:
    """A declared file the resource holds at an older schema, or not at all.

    ``stored`` is the version the file carries -- ``0`` when it carries
    none -- or ``None`` when the file is missing.
    """

    path: str
    stored: int | None
    current: int

    def describe(self) -> str:
        """``<path> <stored> -> <current>`` for a log line."""
        stored = "missing" if self.stored is None else str(self.stored)
        return f"{self.path} {stored} -> {self.current}"


class _Unreadable(Exception):
    """A declared file that does not parse as a versioned JSON object."""


def _stored_format_version(
    resource: GenomicResource, path: str,
) -> int | None:
    """The ``format_version`` the file carries; ``None`` if it is absent.

    A file present but carrying no version is at version 0.  Raises
    :class:`_Unreadable` for a file that cannot be read, or does not
    parse as a JSON object with an integer version: such a file is left
    to the failure paths that already report it, and is not what this
    check is about -- so it never moves a dry run's exit status either.
    """
    try:
        content = resource.get_file_content(path)
    except FileNotFoundError:
        return None
    except OSError as err:
        raise _Unreadable(path) from err
    try:
        document = json.loads(content)
    except ValueError as err:
        raise _Unreadable(path) from err
    if not isinstance(document, dict):
        raise _Unreadable(path)
    stored = document.get("format_version", 0)
    # ``bool`` is an ``int``; ``true`` is not a version.
    if not isinstance(stored, int) or isinstance(stored, bool):
        raise _Unreadable(path)
    return stored


def stale_statistics_files(
    resource: GenomicResource, declared: Iterable[StatisticsFile],
) -> list[StaleStatisticsFile]:
    """The declared files the resource is missing or holds at an older version.

    Parses each declared file and takes only its version out of it.  A
    declared file that cannot be read or parsed is skipped: it is broken
    rather than stale, and the paths that read it report that.
    """
    stale = []
    for file in declared:
        try:
            stored = _stored_format_version(resource, file.path)
        except _Unreadable:
            continue
        if stored is None or stored < file.format_version:
            stale.append(StaleStatisticsFile(
                file.path, stored, file.format_version))
    return stale
