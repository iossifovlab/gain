"""The helpers that the ``grr_manage`` command modules share.

The result type of a management command, the argument groups of the
subcommand parsers, and the lookups of a repository protocol and its
resources.
"""
import argparse
import dataclasses
import fnmatch
import os
from collections.abc import Sequence
from urllib.parse import urlparse

from gain import logging
from gain.genomic_resources.fsspec_protocol import (
    build_fsspec_protocol,
)
from gain.genomic_resources.repository import (
    GenomicResource,
    ReadOnlyRepositoryProtocol,
    ReadWriteRepositoryProtocol,
)
from gain.genomic_resources.repository_ids import (
    GR_CONF_FILE_NAME,
    parse_gr_id_version_token,
    version_tuple_to_string,
)
from gain.genomic_resources.resource_implementation import (
    DEFAULT_STATISTICS_REGION_SIZE,
)
from gain.utils.fs_utils import (
    find_directory_with_a_file,
    find_subdirectories_with_a_file,
)

logger = logging.getLogger("grr_manage")


@dataclasses.dataclass(frozen=True)
class CommandResult:
    """What a repository-management command found and what it could not do.

    Three outcomes, deliberately kept apart (gain#364):

    * ``needs_update`` -- how many resources are OUT OF DATE.  Only a
      ``--dry-run`` reports this; a real run repairs them instead of
      counting them.  This is the meaning the plain ``int`` these commands
      used to return carried.
    * ``failed`` -- the ids of the resources that are BROKEN: whatever
      GAIn was asked to do to them raised, or silently did not happen.  A
      run collects these rather than aborting on the first one, so a
      single broken resource cannot stop the healthy ones from being
      repaired.
    * ``repo_failed`` -- something failed that no single resource can be
      blamed for: the repository's own configuration, or a statistics task
      graph whose failure the per-resource check could not pin on any
      resource.  Inventing a resource id for it would be a lie, but it
      still has to make the run exit non-zero.

    An ``int`` could express only the first, which is why non-dry-run
    repair was structurally incapable of reporting failure.

    ``wrote`` is not a fourth outcome but a fact about the run: whether
    it changed anything on disk.  The resource-scoped commands read it
    to note that the repository-global artifacts are now behind the
    resources they describe (gain#760).  It is set by the command layer,
    not reported by the protocol, so a new write site on a
    resource-scoped path must set it, or the note silently stops firing
    for that write.
    """

    needs_update: int = 0
    failed: frozenset[str] = frozenset()
    repo_failed: bool = False
    wrote: bool = False

    @property
    def has_failures(self) -> bool:
        """Whether anything failed at all, attributable or not."""
        return bool(self.failed) or self.repo_failed


# The exceptions that mean the RESOURCE (or its configuration) is at fault:
# a malformed config, a schema violation, a file that is not there.  They
# are reported as one line carrying the cause, with the traceback demoted to
# DEBUG.  Anything else is a defect in GAIn and keeps its traceback at ERROR.
def _add_repository_resource_parameters_group(
    parser: argparse.ArgumentParser, *, use_resource: bool = True,
) -> None:

    group = parser.add_argument_group(title="Repository/Resource")
    group.add_argument(
        "-R", "--repository", type=str,
        default=None,
        help="URL to the genomic resources repository. If not specified "
        "the tool assumes a local file system repository and starts looking "
        "for .CONTENTS.json.gz file from the current working directory up to "
        "the root directory. If found the directory is assumed for root "
        "repository directory; otherwise error is reported.")
    group.add_argument(
        "--grr", "--definition", "-g", type=str,
        default=None,
        help="Path to an extra GRR definition file. This GRR will be loaded"
        "in a group alongside the local one.")

    group.add_argument(
        "--extra-args", type=str, default=None,
        help="comma separated list of `key=value` pairs arguments needed for "
        "connection to the specific repository protocol. "
        "Ex: if you want to connect to an S3 repository it is often "
        "neccessary to pass additional `endpoint-url` argument.",
    )
    if use_resource:
        group.add_argument(
            "-r", "--resource", type=str,
            help="Specifies the resource whose manifest we want to rebuild. "
            "If not specified the tool assumes local filesystem repository "
            "and starts looking for 'genomic_resource.yaml' file from "
            "current working directory up to the root directory. If found "
            "the directory is assumed for a resource directory; otherwise "
            "error is reported.")


def _add_dry_run_and_force_parameters_group(
        parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group(title="Force/Dry run")
    group.add_argument(
        "-n", "--dry-run", default=False, action="store_true",
        help="report whether the manifest needs updating and write nothing: "
        "no manifest, and no recorded file state either. The run still reads "
        "and hashes whatever answering the question takes, it just keeps no "
        "receipt - so it leaves the repository byte-identical, and seeds "
        "nothing for the next run to reuse")
    group.add_argument(
        "-f", "--force", default=False,
        action="store_true",
        help="ignore resource state and rebuild manifest")


def _add_dvc_parameters_group(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group(title="DVC params")
    group.add_argument(
        "--with-dvc", default=True,
        action="store_true", dest="use_dvc",
        help="trust a '.dvc' sidecar as the md5 sum and size of the file it "
        "describes, and never hash a DVC-managed file (default). A file GAIn "
        "has already hashed keeps its recorded md5 sum while its size and "
        "timestamp are unchanged; a file with no sidecar and no usable state "
        "is hashed")
    group.add_argument(
        "-D", "--without-dvc",
        action="store_false", dest="use_dvc",
        help="verify mode: ignore every recorded state, compute from its "
        "content the md5 sum of every resource file that is on disk, and "
        "check it against the file's '.dvc' sidecar. The run reports every "
        "file that disagrees and exits non-zero, writing no manifest for the "
        "resources they belong to; fix the drift with 'dvc add' / "
        "'dvc commit'. A file that is NOT on disk still takes its md5 sum "
        "and size from its sidecar - there is no content to hash, and its "
        "manifest entry is never dropped")


def _non_negative_int(value: str) -> int:
    try:
        size = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"not an integer: {value}") from None
    if size < 0:
        raise argparse.ArgumentTypeError(
            f"must not be negative: {value}")
    return size


def _add_hist_parameters_group(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group(title="Statistics")
    group.add_argument(
        "--region-size", type=_non_negative_int,
        default=DEFAULT_STATISTICS_REGION_SIZE,
        help="Region size to use for splitting statistics calculation into "
        "tasks. The default is one region per contig; 0 does not split. "
        "A smaller size balances one large resource across -j workers: "
        "keep it at or below about genome / (4 x -j), e.g. "
        "--region-size 20000000 for hg38 at -j 32")


def _note_stale_repository_index(
        proto: ReadWriteRepositoryProtocol,
        result: CommandResult) -> CommandResult:
    """Note that a run left the repository-global artifacts behind.

    A resource-scoped command writes only inside the selected resources'
    directories, so anything it changed is not yet reflected in
    ``.CONTENTS``, the search index or the repository index pages.
    """
    if result.wrote:
        logger.info(
            "repository index of <%s> not updated; "
            "run 'grr_manage repo-index' before publishing",
            proto.get_url())
    return result


def _find_resources(
    proto: ReadOnlyRepositoryProtocol,
    repo_url: str, *,
    resource: str | None = None,
) -> Sequence[GenomicResource]:
    if resource is not None:
        return [
            res for res in proto.get_all_resources()
            if fnmatch.fnmatch(res.resource_id, resource)
        ]

    if urlparse(repo_url).scheme not in {"file", ""}:
        logger.error(
            "resource not specified but the repository URL %s "
            "is not local filesystem repository", repo_url)
        return []

    cwd = os.getcwd()
    resource_dir = find_directory_with_a_file(GR_CONF_FILE_NAME, cwd)
    if resource_dir is not None:

        rid_ver = os.path.relpath(resource_dir, repo_url)
        resource_id, version = parse_gr_id_version_token(rid_ver)

        res = proto.get_resource(
            resource_id,
            version_constraint=f"={version_tuple_to_string(version)}")
        return [res]

    result = []
    for res_dir in find_subdirectories_with_a_file(GR_CONF_FILE_NAME, cwd):
        rid_ver = os.path.relpath(res_dir, repo_url)
        resource_id, version = parse_gr_id_version_token(rid_ver)

        res = proto.get_resource(
            resource_id,
            version_constraint=f"={version_tuple_to_string(version)}")
        result.append(res)
    if result:
        return result

    logger.error("Can't find resource starting from %s", cwd)
    return []


def _create_proto(
    repo_url: str, extra_args: str = "",
) -> ReadWriteRepositoryProtocol:
    url = urlparse(repo_url)

    if url.scheme in {"file", ""} and not os.path.isabs(repo_url):
        repo_url = os.path.abspath(repo_url)

    kwargs: dict[str, str] = {}
    if extra_args:
        parsed = [tuple(a.split("=")) for a in extra_args.split(",")]
        kwargs = {p[0]: p[1] for p in parsed}

    proto = build_fsspec_protocol(
        proto_id="manage", root_url=repo_url, **kwargs)
    if not isinstance(proto, ReadWriteRepositoryProtocol):
        raise TypeError(f"repository protocol is not writable: {repo_url}")
    return proto
