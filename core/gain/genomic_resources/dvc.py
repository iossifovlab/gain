"""The ``.dvc`` sidecar vocabulary.

``dvc add <file>`` drops a ``<file>.dvc`` sidecar next to the data file it
stores and gitignores the file itself. GAIn reads those sidecars in three
places -- the repository scan, ``grr_manage``'s entry collection and the
manifest builder -- and this module is the only place that interprets one,
so the three can never classify the same sidecar differently.
"""
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

import yaml

logger = logging.getLogger(__name__)


def parse_dvc_pointer_out(
    content: str | bytes, basename: str,
) -> dict[str, Any] | None:
    """Parse a ``.dvc`` sidecar; return the output entry describing basename.

    A well-formed ``.dvc`` pointer is a mapping with an ``outs`` list of
    mappings; the output that describes ``basename`` is the one whose
    ``path`` equals it exactly. Anything else - a mapping without ``outs``,
    an ``outs`` that is not a list of mappings, an output for some other
    path, YAML that does not parse, non-UTF-8 bytes - is not a pointer for
    ``basename`` and yields ``None``.

    Parsing NEVER raises. This is the single place a ``.dvc`` file is
    interpreted, so that the repository scan
    (``_is_dvc_managed_leaf``, which must never abort on stray content) and
    ``grr_manage``'s ``collect_dvc_entries`` cannot classify the same sidecar
    differently (#251).

    Args:
        content: raw content of the ``.dvc`` file; bytes are safe to pass -
            ``yaml`` decodes them itself, so a binary file cannot raise a
            ``UnicodeDecodeError`` past this function.
        basename: the base name of the data file the pointer must describe.

    Returns:
        The matching ``outs`` entry, or None if the content is not a pointer
        for ``basename``. The entry is NOT validated beyond its ``path``:
        callers that need an md5 sum and a size must check for them.
    """
    try:
        dvc = yaml.safe_load(content)
        for out in dvc["outs"]:
            if out["path"] == basename:
                return cast(dict[str, Any], out)
    except (yaml.YAMLError, TypeError, KeyError, OSError, ValueError) as error:
        logger.debug(
            "ignoring malformed '.dvc' pointer for <%s>: %s", basename, error)
        return None
    return None


@dataclass(frozen=True)
class DvcContentDrift:
    """A materialised file whose content disagrees with its ``.dvc``.

    Produced only by the verifier (``grr_manage --without-dvc``), which is
    the one mode that reads a DVC-managed file's bytes (#373).
    """

    name: str
    content_md5: str
    dvc_md5: str


class DvcContentDriftError(ValueError):
    """Every file of ONE resource whose content drifted from its sidecar.

    Collected rather than raised on the first offender, so that a single
    ``grr_manage --without-dvc`` run reports all of them (#373). It is a
    ``ValueError`` because it is a fault of the RESOURCE, and
    ``cli._report_resource_failure`` reports those as one line carrying the
    cause, with the traceback demoted to ``DEBUG`` (gain#364).
    """

    def __init__(
        self, resource_id: str, drifts: Sequence[DvcContentDrift],
    ) -> None:
        self.resource_id = resource_id
        self.drifts = tuple(drifts)
        details = "; ".join(
            f"<{drift.name}> hashes to {drift.content_md5}, its sidecar "
            f"declares {drift.dvc_md5}"
            for drift in self.drifts
        )
        super().__init__(
            f"the content of {len(self.drifts)} file(s) disagrees with "
            f"their '.dvc' sidecar: {details}. No manifest was written: a "
            f"'.MANIFEST' is a committed artefact and must not become a "
            f"function of which machine last ran 'grr_manage'. Fix the "
            f"drift with 'dvc add <file>' / 'dvc commit', then repair the "
            f"resource again.",
        )


def is_dvc_directory_out(out: dict[str, Any]) -> bool:
    """Return True if a ``.dvc`` output describes a ``dvc add <dir>`` output.

    DVC writes two signals for a directory output, and either one on its own
    is enough to recognise it:

    * its ``md5`` is the hash of a DVC *cache object* - a listing of the
      directory's files - and carries a ``.dir`` suffix to say so;
    * it declares ``nfiles``, the number of files in the directory.

    Neither is checked in isolation: an out that lost its ``nfiles`` is still
    a directory, and so is one whose md5 sum lost its suffix. GAIn does not
    support directory outputs - it cannot verify a ``.dir`` md5 sum against
    anything it can read - so ``grr_manage`` refuses a resource that has one
    (#255).
    """
    md5 = out.get("md5")
    if isinstance(md5, str) and md5.endswith(".dir"):
        return True
    return "nfiles" in out
