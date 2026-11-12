"""The manifest and index commands of ``grr_manage``.

The ``repo-manifest``, ``resource-manifest`` and ``repo-index``
commands, and the build of the repository contents file and of the
contents database that the other commands publish.
"""
import argparse
import dataclasses
import gzip
import operator
import os
import pathlib
from collections.abc import Sequence
from typing import NamedTuple

import apsw

from gain import logging
from gain.genomic_resources.cli_common import (
    CommandResult,
    _add_dry_run_and_force_parameters_group,
    _add_dvc_parameters_group,
    _add_repository_resource_parameters_group,
    _note_stale_repository_index,
)
from gain.genomic_resources.cli_errors import (
    RESOURCE_ERRORS,
    report_resource_failure,
)
from gain.genomic_resources.dvc import (
    DvcContentDriftError,
)
from gain.genomic_resources.fsspec_protocol import (
    FsspecReadWriteProtocol,
)
from gain.genomic_resources.repository import (
    GenomicResource,
    ReadWriteRepositoryProtocol,
    collect_dvc_entries,
)
from gain.genomic_resources.repository_factory import (
    build_resource_implementation,
)
from gain.genomic_resources.repository_ids import (
    GR_SQLITE_META_FILE_NAME,
)
from gain.genomic_resources.resource_implementation import (
    IndexColumn,
    merge_index_columns,
    validate_index_columns,
)
from gain.utils.verbosity_configuration import VerbosityConfiguration

logger = logging.getLogger("grr_manage")


def _configure_repo_manifest_subparser(
        subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "repo-manifest", help="Create/update manifests for whole GRR")

    _add_repository_resource_parameters_group(parser, use_resource=False)
    _add_dry_run_and_force_parameters_group(parser)
    _add_dvc_parameters_group(parser)
    VerbosityConfiguration.set_arguments(parser)


def _configure_resource_manifest_subparser(
        subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "resource-manifest", help="Create/update manifests for a resource")

    _add_repository_resource_parameters_group(parser)
    _add_dry_run_and_force_parameters_group(parser)
    _add_dvc_parameters_group(parser)
    VerbosityConfiguration.set_arguments(parser)


def _do_resource_manifest_command(
    proto: ReadWriteRepositoryProtocol,
    res: GenomicResource,
    dry_run: bool,  # ruff: ignore[boolean-type-hint-positional-argument]
    force: bool,  # ruff: ignore[boolean-type-hint-positional-argument]
    use_dvc: bool,  # ruff: ignore[boolean-type-hint-positional-argument]
) -> bool:
    """Check, and outside a dry run save, one resource's manifest.

    Returns whether anything came of it: a dry run reports finding the
    manifest stale, a real run reports saving it.
    """
    # '.dvc' entries are always collected, in EVERY mode: they are the md5
    # sum of the file they describe by default, and the thing `--without-dvc`
    # verifies the bytes against. Gating this on `use_dvc` also deleted every
    # entry a sidecar is the only possible source for - a file that is not
    # materialised - from every manifest under `-D` (#251).
    prebuild_entries = collect_dvc_entries(proto, res)
    verify_content = not use_dvc

    # A dry run reports; it does not record. Recording the states it derived
    # on the way would leave `.grr/<file>.state` files behind in a tree the
    # caller asked us only to inspect (#257).
    manifest_update = proto.check_update_manifest(
        res, prebuild_entries, verify_content=verify_content,
        save_state=not dry_run)
    if not bool(manifest_update):
        logger.debug(
            "manifest of <%s> is up to date",
            res.get_full_id())
    else:
        msg = (
            f"manifest of "
            f"<{res.get_full_id()}> "
            f"should be updated; "
            f"entries to update in manifest "
            f"{sorted(manifest_update.entries_to_update)}"
        )
        if manifest_update.entries_to_delete:
            msg = (
                f"{msg}; "  # ruff: ignore[hardcoded-sql-expression]
                f"entries to delete from manifest "
                f"{sorted(manifest_update.entries_to_delete)}"
            )
        logger.warning(msg)

    if dry_run:
        return bool(manifest_update)

    if force or bool(manifest_update):
        # The manifest `check_update_manifest` returned IS the updated one:
        # every entry already carries its md5 sum, and the entries the scan
        # did not yield are already merged in. Deriving it again through
        # `build_manifest`/`update_manifest` would read every materialised
        # file a SECOND time under `--without-dvc`, which deliberately
        # bypasses the size and timestamp fast path (#251).
        logger.info(
            "updating manifest for resource <%s>...", res.resource_id)
        proto.save_manifest(res, manifest_update.manifest)
        return True

    return False


class ManifestOutcome(NamedTuple):
    """What a manifest pass over a set of resources found.

    ``updates_needed`` is keyed by the resources the pass got through, and
    valued by whether that resource's manifest is stale. ``failed`` names
    the resources whose manifest could not be built at all - today, only a
    resource whose content drifted from its ``.dvc`` sidecars under
    ``--without-dvc``; it has NO entry in ``updates_needed`` (#373).
    ``wrote`` is whether the pass saved any manifest.
    """

    updates_needed: dict[str, bool]
    failed: frozenset[str]
    wrote: bool


def _run_repo_manifest_command_internal(
        proto: ReadWriteRepositoryProtocol,
        resources: Sequence[GenomicResource], *,
        dry_run: bool, force: bool, use_dvc: bool) -> ManifestOutcome:
    updates_needed = {}
    failed: set[str] = set()
    wrote = False
    for res in resources:
        try:
            changed = _do_resource_manifest_command(
                proto, res,
                dry_run=dry_run,
                force=force,
                use_dvc=use_dvc,
            )
            # In a real run `changed` means "saved", which belongs to
            # `wrote` and not to the dry-run staleness count.
            updates_needed[res.resource_id] = changed if dry_run else False
            wrote = wrote or (changed and not dry_run)
        except (DvcContentDriftError, *RESOURCE_ERRORS) as err:
            # Collected, not raised: every drifted resource of the
            # repository is reported by one run, and the resources that
            # agree with their sidecars are still repaired (#373).
            #
            # `RESOURCE_ERRORS` too, not drift alone: a manifest also
            # fails for reasons unrelated to DVC, and those escaped this
            # loop and aborted the run -- so every resource ordered AFTER
            # the offender was silently never visited (gain#503). NOT
            # widened to `Exception`: an unexpected error is still a crash.
            report_resource_failure(
                err, "could not verify", res.resource_id)
            failed.add(res.resource_id)

    return ManifestOutcome(updates_needed, frozenset(failed), wrote)


def _build_content_file(
    proto: FsspecReadWriteProtocol,
    failed: frozenset[str] = frozenset(),
) -> None:
    """Build CONTENTS.json.gz.

    ``failed`` names resources this run could not verify; they are
    published from the manifest they already had, or left out if they
    never had one, so a failed run never rebuilds -- and poisons the
    contents with -- a manifest it just refused to write (#373).
    """
    proto.build_content_file(failed)


# Appended to the stored md5 of an index that left a resource out. No
# value of `md5_contents()` ends with it, so such an index never passes the
# short-circuit check, and the next run rebuilds it (gain#1851).
_INCOMPLETE_INDEX_SUFFIX = ":incomplete"


def _index_contents_md5(current_md5: str, left_out: set[str]) -> str:
    """Return the `contents_md5` value to store with the index."""
    if left_out:
        return current_md5 + _INCOMPLETE_INDEX_SUFFIX
    return current_md5


def _create_contents_db(
    proto: FsspecReadWriteProtocol,
    already_failed: frozenset[str] = frozenset(),
) -> frozenset[str]:
    """Build the FTS SQLite index for the repository.

    Calls collect_index_info() on each resource's implementation to get
    field names and values.  Returns the ids of the resources that could
    not be indexed -- the index is repository-wide, so this walks EVERY
    resource, not only the ones the command selected; the ids returned are
    always the offending ones, never the selected one (gain#364).
    """
    # Read with local-filesystem calls against the protocol's root path,
    # while the publish below goes through the protocol at its url. The
    # two are the same location for the `file` scheme this build supports
    # -- it needs a local path for the database engine anyway -- but they
    # are no longer the same expression, so a scheme where they diverge
    # sees this read miss and rebuild rather than short-circuit. Routing
    # the read too is deliberately not part of gain#948, which fixes the
    # write.
    gzip_sqlite_filepath = os.path.join(
        proto.root_path, GR_SQLITE_META_FILE_NAME)

    current_md5 = proto.md5_contents()
    if _contents_db_is_current(gzip_sqlite_filepath, current_md5):
        return frozenset()

    collected, failed, skipped = _collect_index_infos(proto, already_failed)
    columns, index_infos = _merge_index_headers(collected, failed)
    gz = _build_contents_db_image(
        _index_contents_md5(current_md5, failed | skipped),
        columns, index_infos)

    # Published through the protocol's seam, not written at the live path:
    # the index that is already there is replaced by a completed one in a
    # single move, or not at all. It used to be deleted before the build
    # even started, so an interrupted rebuild left the repository with no
    # search index at all and nothing to roll back to (gain#948).
    with proto.publish_repository_file(GR_SQLITE_META_FILE_NAME) as outfile:
        outfile.write(gz)
    return frozenset(failed)


def _contents_db_is_current(
    gzip_sqlite_filepath: str, current_md5: str,
) -> bool:
    """Return whether the existing index image records ``current_md5``."""
    if not os.path.exists(gzip_sqlite_filepath):
        return False
    try:
        raw = gzip.decompress(
            pathlib.Path(gzip_sqlite_filepath).read_bytes())
        conn = apsw.Connection(":memory:")
        conn.deserialize("main", raw)
        row = conn.execute(
            "SELECT value FROM contents_metadata "
            "WHERE key = 'contents_md5'",
        ).fetchone()
        if row and row[0] == current_md5:
            return True
    except Exception:
        logger.debug(
            "Could not read existing contents db; rebuilding",
            exc_info=True,
        )
    return False


_IndexInfo = tuple[str, tuple[str, ...], tuple[str, ...]]


def _collect_index_infos(
    proto: FsspecReadWriteProtocol,
    already_failed: frozenset[str],
) -> tuple[list[_IndexInfo], set[str], set[str]]:
    """Collect the index info of each resource of the repository.

    Returns the collected ``(resource_id, header, row)`` entries, the ids
    of the resources that failed and the ids of the resources skipped
    because they are in ``already_failed``.
    """
    collected: list[_IndexInfo] = []
    failed: set[str] = set()
    skipped: set[str] = set()
    for res in proto.get_all_resources():
        if res.resource_id in already_failed:
            # Its manifest could not be verified this run; indexing it
            # would read a resource the run is already failing on (#373).
            skipped.add(res.resource_id)
            continue
        try:
            impl = build_resource_implementation(res)
            header, row = impl.collect_index_info()
            # collect_index_info() already vets the label keys it adds; this
            # repeats the check over the whole header so that no column name
            # -- including one an implementation adds on its own -- reaches
            # the interpolated SQL below unvetted (gain#464).
            validate_index_columns(res.resource_id, header)
            collected.append((res.resource_id, header, row))
        except Exception as err:  # ruff: ignore[blind-except]
            report_resource_failure(
                err, "skipping FTS index for", res.resource_id)
            failed.add(res.resource_id)
    return collected, failed, skipped


def _merge_index_headers(
    collected: list[_IndexInfo],
    failed: set[str],
) -> tuple[list[str], list[tuple[tuple[str, ...], tuple[str, ...]]]]:
    """Merge the collected headers into the columns of the index table.

    Adds the id of each resource that cannot join the union to ``failed``.
    Returns the column spellings and the ``(header, row)`` entries of the
    resources that joined.
    """
    # The index table's columns are the union of the headers, and a header
    # that is sound on its own can still be unusable next to another
    # resource's -- SQLite compares column names case-insensitively, so
    # `assay` in one resource and `Assay` in another cannot both be
    # columns.  Vetting each header alone cannot see that by construction,
    # so the union is assembled resource by resource here, and a resource
    # that cannot join it is skipped and reported by id like any other
    # (gain#464).  Sorting by resource id makes which of the two colliding
    # resources is rejected independent of the order the repository
    # happens to list them in.
    claimed: dict[str, IndexColumn] = {}
    index_infos: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    for resource_id, header, row in sorted(
            collected, key=operator.itemgetter(0)):
        try:
            claimed = merge_index_columns(resource_id, header, claimed)
        except Exception as err:  # ruff: ignore[blind-except]
            report_resource_failure(
                err, "skipping FTS index for", resource_id)
            failed.add(resource_id)
            continue
        index_infos.append((header, row))
    return [spelling for spelling, _ in claimed.values()], index_infos


def _build_contents_db_image(
    contents_md5: str,
    columns: list[str],
    index_infos: list[tuple[tuple[str, ...], tuple[str, ...]]],
) -> bytes:
    """Build the gzip image of the FTS SQLite index.

    Each name in ``columns`` must already be a vetted SQL identifier, as
    ``validate_index_columns`` and ``merge_index_columns`` produce: the
    names are interpolated into the SQL, not bound.
    """
    # Built in memory and serialized, rather than built into a file that
    # then has to be read back and cleaned up. The database used to be
    # made inside the repository and removed on the way out, so a build
    # that raised left its scratch behind; with no file there is no
    # scratch to leave, on any path out (gain#948). The read above
    # already treats this database as an image rather than a file, which
    # is the same trade in the other direction.
    conn = apsw.Connection(":memory:")
    with conn:
        conn.execute(
            "CREATE TABLE contents_metadata (key TEXT PRIMARY KEY, value TEXT)",
        )
        conn.execute(
            "INSERT INTO contents_metadata (key, value) VALUES (?, ?)",
            ("contents_md5", contents_md5),
        )

        if columns:
            # The caller passes only names that validate_index_columns
            # vetted, so each is a bare non-keyword SQL identifier and cannot
            # carry anything else into these two statements (gain#464).  SQL
            # identifiers cannot be bound as parameters, so interpolation is
            # the only way to name a column -- vetting the names is what
            # makes it safe.
            cols_str = ", ".join(columns)
            conn.execute(
                f"CREATE VIRTUAL TABLE contents USING fts5({cols_str})",
            )
            insert_sql = (
                # S608 fires on any SQL built by interpolation and cannot
                # see the vetting the comment above describes; the values
                # are bound, and only vetted identifiers are spliced.
                f"INSERT INTO contents ({cols_str}) "  # ruff: ignore[hardcoded-sql-expression]
                f"VALUES ({', '.join(['?'] * len(columns))})"
            )
            for header, row in index_infos:
                header_idx = {col: i for i, col in enumerate(header)}
                full_row = tuple(
                    row[header_idx[col]] if col in header_idx else ""
                    for col in columns
                )
                conn.execute(insert_sql, full_row)

    raw_data = bytes(conn.serialize("main"))

    # mtime=0 strips the current-time stamp from the gzip header
    # so re-running this on an unchanged repo produces identical
    # bytes (gzip.open's default writes the wall-clock time, which
    # changes every run). The OS byte at offset 9 is normalised to
    # 0xff for cross-Python-distribution determinism — see the
    # matching note in fsspec_protocol.build_content_file.
    gz = gzip.compress(raw_data, mtime=0)
    return gz[:9] + b"\xff" + gz[10:]


def _configure_repo_index_subparser(
        subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "repo-index",
        help="Publish the repository index (.CONTENTS files, search index, "
        "repository index pages) from the manifests already on disk")
    _add_repository_resource_parameters_group(parser, use_resource=False)
    VerbosityConfiguration.set_arguments(parser)


def _run_repo_index_command(
        proto: ReadWriteRepositoryProtocol) -> CommandResult:
    """Publish the repository-global artifacts from on-disk manifests.

    Builds ``.CONTENTS.json.gz``, then the FTS index, then the
    repository index pages.  Nothing is verified and nothing is written
    inside any resource directory: a resource without a committed
    manifest is left out of all three, reported by id, and fails the
    run (#373).
    """
    assert isinstance(proto, FsspecReadWriteProtocol)
    skipped: set[str] = set()
    for res in proto.get_all_resources():
        # The repository walk already loaded every manifest that exists,
        # so this is an attribute read; only a genuinely manifest-less
        # resource pays one probe.
        if res.get_loaded_manifest() is None:
            logger.error(
                "not publishing <%s> in the repository index: "
                "it has no manifest", res.resource_id)
            skipped.add(res.resource_id)
    result = _publish_repository_contents(
        proto, CommandResult(failed=frozenset(skipped)))
    proto.build_index_info(failed=result.failed)
    return result


def _run_manifest_core(
    proto: ReadWriteRepositoryProtocol,
    resources: Sequence[GenomicResource], *,
    dry_run: bool, force: bool, use_dvc: bool,
) -> CommandResult:
    """Create/update the selected resources' manifests.

    Writes only inside the selected resources' directories; publishing
    the repository-global artifacts is its callers' decision.
    """
    if dry_run and force:
        # A usage error, not a count of anything.
        logger.warning("please choose one of 'dry_run' and 'force' options")
        return CommandResult(repo_failed=True)
    outcome = _run_repo_manifest_command_internal(
        proto, resources, dry_run=dry_run, force=force, use_dvc=use_dvc)
    if dry_run:
        # `updates_needed` is keyed by EVERY resource and valued by whether
        # that resource's manifest is stale, so its LENGTH is the size of
        # the repository -- which made `--dry-run` report a fully settled
        # repository as inconsistent, with a status equal to its resource
        # count.  Count the stale ones (gain#364).  A resource that could
        # not even be checked is certainly not up to date, so it counts too.
        return CommandResult(
            needs_update=sum(
                1 for stale in outcome.updates_needed.values() if stale
            ) + len(outcome.failed),
            failed=outcome.failed)
    return CommandResult(failed=outcome.failed, wrote=outcome.wrote)


def _run_repo_manifest_command(
    proto: ReadWriteRepositoryProtocol,
    resources: Sequence[GenomicResource], *,
    dry_run: bool, force: bool, use_dvc: bool,
) -> CommandResult:
    result = _run_manifest_core(
        proto, resources, dry_run=dry_run, force=force, use_dvc=use_dvc)
    if dry_run:
        return result
    assert isinstance(proto, FsspecReadWriteProtocol)
    _build_content_file(proto, result.failed)
    return result


def _run_resource_manifest_command(
    proto: ReadWriteRepositoryProtocol,
    resources: Sequence[GenomicResource], *,
    dry_run: bool, force: bool, use_dvc: bool,
) -> CommandResult:
    return _note_stale_repository_index(
        proto, _run_manifest_core(
            proto, resources,
            dry_run=dry_run, force=force, use_dvc=use_dvc))


def _publish_repository_contents(
        proto: ReadWriteRepositoryProtocol,
        result: CommandResult) -> CommandResult:
    """Publish ``.CONTENTS.json.gz`` and the FTS index.

    The FTS index walks the whole repository, so the ids it returns may
    name resources the calling command did not select -- they are reported
    under their own ids, and the run fails, but the selected resource is
    not blamed for them. A resource that already failed this run is
    left out of both artifacts: rebuilding an index for a resource the
    run is failing on is exactly the poison #373 removes.
    """
    assert isinstance(proto, FsspecReadWriteProtocol)
    _build_content_file(proto, result.failed)
    failed = result.failed | _create_contents_db(proto, result.failed)
    return dataclasses.replace(result, failed=frozenset(failed))
