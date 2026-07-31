"""What ``grr_manage`` refuses about a ``.dvc`` sidecar.

Its own module because the refusal has two gates that must say the same
thing: this pre-flight, which runs before a command touches the
repository, and the manifest builder's own check in
``cli.collect_dvc_entries``, which is what makes a manifest impossible to
build from a sidecar GAIn cannot verify (#255, #284).
"""
from __future__ import annotations

import os
from collections.abc import Sequence
from typing import cast

from gain import logging
from gain.genomic_resources.cli_errors import RESOURCE_ERRORS
from gain.genomic_resources.dvc import (
    is_dvc_directory_out,
    parse_dvc_pointer_out,
)
from gain.genomic_resources.repository import (
    GenomicResource,
    ReadWriteRepositoryProtocol,
)

logger = logging.getLogger("grr_manage")


class UnsupportedDvcDirectoryOutputError(Exception):
    """A resource declares a ``dvc add <dir>`` output, which GAIn refuses.

    Raised by :func:`refuse_dvc_directory_outputs` and by
    ``cli.collect_dvc_entries``, and turned into a non-zero exit by
    ``cli.cli_manage`` (#255).
    """


def dvc_directory_output_message(
    resource_id: str, entry_name: str, filename: str,
) -> str:
    """Say why a ``dvc add <dir>`` output is refused, and what to do.

    One text for both gates -- the pre-flight and the manifest builder --
    so what the user is told cannot depend on which of them saw the
    sidecar first (#284).
    """
    return (
        f"resource <{resource_id}> has a 'dvc add <dir>' output: "
        f"the '.dvc' file <{entry_name}> describes the directory "
        f"<{filename}>. 'dvc add <dir>' outputs are not supported by "
        f"GAIn: the '.dir' md5 sum such a sidecar declares is the "
        f"hash of a DVC cache object, not of any file in the "
        f"resource, so GAIn can never verify it against the bytes it "
        f"serves. DVC-manage the individual files instead: run 'dvc "
        f"add <file>' on each file of <{filename}> (and remove "
        f"<{entry_name}>)."
    )


def refuse_dvc_directory_outputs(
    proto: ReadWriteRepositoryProtocol,
    resources: Sequence[GenomicResource],
) -> None:
    """Refuse a ``dvc add <dir>`` output before the command writes anything.

    The gate in ``cli.collect_dvc_entries`` fires where ONE resource's
    manifest is built, which is far too late for a command that spans a
    repository: by then the resources ordered before the offender have
    their ``.MANIFEST`` and ``.grr`` state written, and ``*-stats`` /
    ``*-info`` have run a whole task graph -- so a run that refuses the
    repository could still leave statistics and info pages behind for the
    resources it happened to reach first (#284). A refusal must be
    side-effect-free, so the sidecars are read up front, here, and the
    command fails before it touches anything.

    Scoped to the resources the command SELECTED, which for the ``repo-*``
    subcommands is the whole repository: a resource GAIn refuses does not
    make a ``resource-*`` command on some OTHER resource illegal, in
    keeping with one broken resource never stopping work on the healthy
    ones (gain#503).

    A sidecar this pass cannot read or cannot parse is not its business:
    it asks one question -- does any sidecar describe a directory? -- and
    ``cli.collect_dvc_entries`` remains the one place that reports an
    unusable sidecar, so a run does not warn about it twice. Neither is a
    resource whose files cannot even be LISTED -- an unreadable directory,
    a DVC cache this run may not traverse, a remote store that fails to
    describe a key it just listed. It is skipped for the same reason:
    the command's own per-resource handler is what reports it and fails
    that resource alone, and a pre-flight that raised instead would take
    the whole run down over one broken resource -- the very failure mode
    gain#503 removed.

    It costs one extra listing of the selected resources, since the
    listing is what says which sidecars exist and the answer is needed
    before the first write. Only the sidecars are then read, and a
    ``.dvc`` file is a few hundred bytes of YAML -- nothing next to the
    hashing and statistics the pass protects.

    Raises:
        UnsupportedDvcDirectoryOutputError: some selected resource has a
            ``dvc add <dir>`` output.
    """
    for res in resources:
        try:
            entries = list(proto.collect_resource_entries(res))
        except RESOURCE_ERRORS:
            logger.debug(
                "cannot list <%s> before the command starts; leaving it to "
                "the command itself", res.resource_id, exc_info=True)
            continue
        for entry in entries:
            if not entry.name.endswith(".dvc"):
                continue
            filename = entry.name[:-4]
            try:
                with proto.open_raw_file(res, entry.name, "rb") as infile:
                    content = cast(bytes, infile.read())
            except (OSError, ValueError):
                continue

            out = parse_dvc_pointer_out(content, os.path.basename(filename))
            if out is not None and is_dvc_directory_out(out):
                logger.debug(
                    "refusing <%s> before the command starts: <%s>",
                    res.resource_id, entry.name)
                raise UnsupportedDvcDirectoryOutputError(
                    dvc_directory_output_message(
                        res.resource_id, entry.name, filename))
