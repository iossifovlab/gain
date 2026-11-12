"""Fluent, immutable test-data builders for GRR resources.

This module offers a small builder DSL for composing genomic resources
into a filesystem GRR that a test can open and read back.  Builders are
immutable (frozen dataclasses); every ``with_*`` method returns a NEW
builder, so a partly-configured builder can be shared across test
variations without leaking state.

The builders assemble a pure in-memory recipe with no side effects; the
``build_*`` methods delegate the actual file writing and repository
construction to the existing helpers in
:mod:`gain.genomic_resources.testing` (``setup_directories`` and
``build_filesystem_test_repository``, or ``build_inmemory_test_resource``
for the one builder with an in-memory exit).

This module holds the shared core: the :class:`ResourceBuilder` seam,
:class:`GRRBuilder`, :class:`BasicResourceBuilder` and the single-realize
path ``_build_single_resource``.  Each resource kind lives in a sibling
module of :mod:`gain.genomic_resources.testing` with its factory, for
example ``a_position_score`` in ``table_score_builder``.

Example, with ``a_grr`` from this module and ``a_position_score`` from
:mod:`gain.genomic_resources.testing.table_score_builder`::

    def test_it(tmp_path):
        repo = (
            a_grr()
            .with_resource(
                "scores/pos",
                a_position_score()
                .with_score("phastCons", "float")
                .with_data('''
                    chrom  pos_begin  phastCons
                    1      10         0.1
                    1      11         0.2
                '''),
            )
            .build_repo(tmp_path)
        )
        score = PositionScore(repo.get_resource("scores/pos")).open()
        assert score.get_scores_at_position("1", 10) == (0.1,)
"""
# The core of the builder DSL.  Each sibling builder module imports this
# module one way, and this module imports none of them: a moved name is
# read from its sibling only through the alias below, when a caller asks.
from __future__ import annotations

import contextlib
import dataclasses
import importlib
import pathlib
import tempfile
import warnings
from collections.abc import Generator
from typing import Any, Protocol, runtime_checkable

import yaml

from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceProtocolRepo,
)
from gain.genomic_resources.repository_ids import GR_CONF_FILE_NAME
from gain.genomic_resources.testing import (
    build_filesystem_test_repository,
    build_inmemory_test_resource,
    setup_directories,
)
from gain.genomic_resources.testing.extra_files import ExtraFilesMixin
from gain.genomic_resources.testing.resource_meta import MetaMixin
from gain.genomic_resources.testing.score_specs import ResourceValidationError

#: The public names that moved out of this module (gain#2270), each with
#: its new sibling module.  They stay importable from here for one release,
#: with a ``DeprecationWarning`` that names the new module.  The siblings
#: are named relative to this package, and this module imports none of
#: them when it loads.
_MOVED: dict[str, str] = {
    **dict.fromkeys((
        "AlleleScoreBuilder",
        "FragmentScoreBuilder",
        "PositionScoreBuilder",
        "a_fragment_score",
        "a_position_score",
        "an_allele_score",
    ), "table_score_builder"),
    **dict.fromkeys((
        "BigWigScoreBuilder",
        "a_bigwig_score",
    ), "bigwig_score_builder"),
    **dict.fromkeys((
        "VcfInfoScoreBuilder",
        "a_vcf_info_score",
    ), "vcf_info_score_builder"),
    **dict.fromkeys((
        "GeneScoreBuilder",
        "a_gene_score",
    ), "gene_score_builder"),
    **dict.fromkeys((
        "ReferenceGenomeBuilder",
        "a_reference_genome",
    ), "reference_genome_builder"),
}


def __getattr__(name: str) -> Any:
    """Serve a name that moved out of this module, with a warning.

    Python calls this module ``__getattr__`` (PEP 562) only for a name
    that this module does not define.  The moved names are not defined
    here, so a read of one finds its sibling module and returns the object
    from there.  An import statement and ``module.name`` both read through
    here, and type checkers see the names through the ``Any`` return.  The
    sibling is imported only when a caller reads a moved name: the siblings
    import this module, and this module imports none of them when it loads.
    """
    moved_to = _MOVED.get(name)
    if moved_to is None:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}")
    new_module = f"{__package__}.{moved_to}"
    warnings.warn(
        f"{name} moved to {new_module}", DeprecationWarning, stacklevel=2)
    return getattr(importlib.import_module(new_module), name)


@runtime_checkable
class ResourceBuilder(Protocol):
    """Structural interface for a single-resource test builder.

    Every resource builder knows how to realize exactly one resource --
    its config plus data/index files -- into a directory.  ``GRRBuilder``
    composes heterogeneous builders through this one seam; each
    implementation delegates to the appropriate ``setup_*`` helper from
    :mod:`gain.genomic_resources.testing`.
    """

    def realize_into(self, resource_dir: pathlib.Path) -> None:
        """Write this resource's directory into ``resource_dir``."""
        ...


_DATA_FILENAME = "data.txt"

# ``build_definition`` writes the GRR definition alongside, not inside, the
# directory it points at.  Public because the sibling group builder writes
# the same layout, and one definition of it is what keeps the two agreeing.
GRR_DEFINITION_FILENAME = "grr.yaml"
GRR_RESOURCES_DIRNAME = "grr"

#: Repository id a ``build_definition`` writes when a test names none.
_DEFAULT_GRR_ID = "test_grr"


def write_grr_definition(
    root: pathlib.Path, definition: dict[str, Any],
) -> pathlib.Path:
    """Write ``definition`` as ``root/grr.yaml`` and return its path.

    Shared by the GRR and group builders so the serialization policy and
    the "definition sits OUTSIDE the directory it points at" rule are
    stated once.  A stray ``grr.yaml`` among the resources would be walked
    as though it were one.
    """
    definition_path = root / GRR_DEFINITION_FILENAME
    setup_directories(definition_path, yaml.safe_dump(
        definition, default_flow_style=False, sort_keys=False))
    return definition_path


@dataclasses.dataclass(frozen=True)
class GRRBuilder:
    """Immutable builder composing resources into a filesystem GRR.

    Resources are held behind the shared :class:`ResourceBuilder` seam, so
    a single GRR can compose heterogeneous resource types (e.g. a genome
    plus a position score).  ``build_repo`` realizes each builder into its
    own ``root / resource_id`` directory; the id is known here, so any
    ``ValueError`` a builder raises is annotated with it centrally.
    """

    resources: tuple[tuple[str, ResourceBuilder], ...] = ()
    public_url: str | None = None

    def with_public_url(self, public_url: str) -> GRRBuilder:
        """Advertise this GRR under ``public_url``.

        ``public_url`` is the address a deployment publishes its GRR at,
        and the only one that means anything once a rendered document or
        an API response leaves the server -- the repository's own url may
        be a directory mounted into a container.  A resource's public
        address is this url with the resource id joined to it.
        """
        return dataclasses.replace(self, public_url=public_url)

    def with_resource(
        self, resource_id: str, resource_builder: ResourceBuilder,
    ) -> GRRBuilder:
        """Attach a resource, assigning its repo id here.

        Rejects a duplicate id fast at the call site: two resources sharing
        an id would realize into the same directory with the second
        silently winning.
        """
        if any(rid == resource_id for rid, _ in self.resources):
            raise ResourceValidationError(
                f"duplicate resource id {resource_id!r} declared "
                f"more than once")
        return dataclasses.replace(
            self,
            resources=(*self.resources, (resource_id, resource_builder)),
        )

    def build_repo(
        self, tmp_path: pathlib.Path, *, proto_id: str | None = None,
    ) -> GenomicResourceProtocolRepo:
        """Realize a filesystem GRR into ``tmp_path``.

        ``proto_id`` names the repository, for a caller that has to look it
        up by name -- a group child, whose id is how a resource is asked
        for.  Left unset, the id is derived from the root and the
        advertised url.
        """
        self.realize_all(tmp_path)
        return build_filesystem_test_repository(
            tmp_path, proto_id=proto_id, public_url=self.public_url)

    def definition(
        self, root: pathlib.Path, *, grr_id: str = _DEFAULT_GRR_ID,
    ) -> dict[str, Any]:
        """Render this GRR as a ``dir`` repository definition.

        Shared by ``build_repo`` and ``build_definition`` so the in-process
        repository and the written yaml cannot describe different GRRs.
        ``public_url`` is omitted entirely when none was advertised, rather
        than written as an explicit null.  The schema accepts either and
        both mean "no public mirror", but a definition a test reads back is
        also documentation of the shape a deployment writes -- and a
        deployment with no mirror simply has no such key.
        """
        definition: dict[str, Any] = {
            "id": grr_id,
            "type": "dir",
            "directory": str(root),
        }
        if self.public_url is not None:
            definition["public_url"] = self.public_url
        return definition

    def build_definition(
        self, root: pathlib.Path, *, grr_id: str = _DEFAULT_GRR_ID,
    ) -> pathlib.Path:
        """Realize into ``root/grr`` and write a ``root/grr.yaml``.

        Returns the path of the written definition file.  A CLI tool such
        as ``annotate_tabular`` is given a ``--grr`` definition *file*, not
        a repository object, so ``build_repo`` alone cannot drive one.  The
        definition is written OUTSIDE the resources directory: a stray
        ``grr.yaml`` sitting among the resources would be walked as though
        it were one.
        """
        resources_dir = root / GRR_RESOURCES_DIRNAME
        self.realize_all(resources_dir)
        return write_grr_definition(
            root, self.definition(resources_dir, grr_id=grr_id))

    def realize_all(self, root: pathlib.Path) -> None:
        """Realize every attached resource into ``root/<resource_id>``."""
        for resource_id, builder in self.resources:
            resource_dir = root / resource_id
            try:
                builder.realize_into(resource_dir)
            except ResourceValidationError as exc:
                raise ResourceValidationError(
                    f"resource {resource_id!r}: {exc}") from exc


@dataclasses.dataclass(frozen=True)
class BasicResourceBuilder(ExtraFilesMixin, MetaMixin):
    """Immutable builder for a single ``basic`` resource.

    ``basic`` is the catch-all type: no schema, no type-specific file
    list, whatever files the author put there.  A bare builder realizes
    ``type: basic`` plus one placeholder ``data.txt`` payload -- the
    resource every repository-layout test reaches for when it needs *a*
    resource and does not care which -- and the config it renders for
    that is byte-identical to the hand-written ``"type: basic\\n"``
    literal it replaces, so fixtures pinning that literal's size and md5
    can move onto it.  ``with_meta`` and its siblings come from
    :class:`MetaMixin`, ``with_file`` from :class:`ExtraFilesMixin` --
    here the shipped files are the whole payload.

    Two exits: :meth:`build_resource` writes the resource under a
    ``tmp_path`` like every other builder, and :meth:`build_inmemory`
    hands it back with no directory at all, for a fixture that only
    renders the resource's page.  Both realize the same content dict.
    """

    files: tuple[tuple[str, str | bytes], ...] = (
        (_DATA_FILENAME, "alabala"),)

    def realize_into(self, resource_dir: pathlib.Path) -> None:
        """Write this basic resource into ``resource_dir``."""
        setup_directories(resource_dir, _build_basic_resource_content(self))

    def build_resource(self, tmp_path: pathlib.Path) -> GenomicResource:
        """Realize this single resource (repo id ``""``) into ``tmp_path``."""
        return _build_single_resource(self, tmp_path)

    def build_inmemory(self) -> GenomicResource:
        """Realize this single resource in memory, with no ``tmp_path``.

        For a fixture that has no directory to write into -- the
        template tests render a resource's page and never touch a file.
        """
        return build_inmemory_test_resource(
            _build_basic_resource_content(self))


def _build_basic_resource_content(
    builder: BasicResourceBuilder,
) -> dict[str, Any]:
    """Build the pure filesystem content dict for one basic resource.

    The one recipe both exits realize -- ``setup_directories`` on the
    filesystem, ``build_inmemory_test_resource`` in memory -- so the two
    cannot describe different resources.
    """
    return {
        GR_CONF_FILE_NAME: "type: basic\n" + builder.render_meta(),
        **builder.files_content(),
    }


def _build_single_resource(
    builder: ResourceBuilder, tmp_path: pathlib.Path,
) -> GenomicResource:
    """Realize one builder as the sole resource (repo id ``""``).

    Shared single-realize path for every ``ResourceBuilder.build_resource``,
    so all resource types route through the same GRR-builder seam.
    """
    return (
        a_grr()
        .with_resource("", builder)
        .build_repo(tmp_path)
        .get_resource("")
    )


def a_basic_resource() -> BasicResourceBuilder:
    """Return an immutable ``basic`` resource builder."""
    return BasicResourceBuilder()


def a_grr() -> GRRBuilder:
    """Return an immutable GRR-composition builder."""
    return GRRBuilder()


@contextlib.contextmanager
def build_repo_tempdir(
    grr_builder: GRRBuilder,
) -> Generator[GenomicResourceProtocolRepo, None, None]:
    """Realize ``grr_builder`` into a self-managed temporary directory.

    A ``tmp_path``-free realize form for non-pytest callers: the GRR is
    realized into a fresh :class:`tempfile.TemporaryDirectory`, yielded as an
    open repository, and the directory is removed on exit.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        yield grr_builder.build_repo(pathlib.Path(tmp_dir))


@contextlib.contextmanager
def build_resource_tempdir(
    builder: ResourceBuilder,
) -> Generator[GenomicResource, None, None]:
    """Realize one ``builder`` into a self-managed temporary directory.

    The single-resource counterpart of :func:`build_repo_tempdir`: yields the
    sole realized resource and cleans the temporary directory up on exit.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        yield _build_single_resource(builder, pathlib.Path(tmp_dir))
