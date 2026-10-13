"""Provides LiftOver chain resource."""

from __future__ import annotations

from threading import RLock
from typing import Any, cast

from pyliftover import LiftOver

from gain import logging
from gain.genomic_resources import GenomicResource
from gain.genomic_resources.memo import Memo
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)
from gain.genomic_resources.resource_implementation import (
    ResourceConfigValidationMixin,
    get_base_resource_schema,
    get_required_filename_schema,
)
from gain.genomic_resources.utils import read_resource_id_label

logger = logging.getLogger(__name__)


class LiftoverChain(ResourceConfigValidationMixin):
    """Convert coordinates between assemblies with a UCSC chain file.

    The class wraps a ``pyliftover`` object. Build it with
    `build_liftover_chain_from_resource_id` and call `open` before the
    first conversion.

    Parameters
    ----------
    resource : GenomicResource
        A resource of type ``liftover_chain``.

    Attributes
    ----------
    resource : GenomicResource
        The resource that holds the chain file.
    config : dict
        The validated and normalized resource configuration.
    source_genome_id : str or None
        The id from the ``source_genome`` label, or ``None`` when the
        label is absent or unusable.
    target_genome_id : str or None
        The id from the ``target_genome`` label, or ``None`` when the
        label is absent or unusable.

    Raises
    ------
    ValueError
        If the type of the resource is not ``liftover_chain``.
    """

    def __init__(self, resource: GenomicResource):

        self.resource = resource
        self.lock = RLock()

        config = resource.get_config()
        if resource.get_type() != "liftover_chain":
            logger.error(
                "trying to use genomic resource %s "
                "as a liftover chain but its type is %s; %s",
                resource.resource_id, resource.get_type(), config)
            raise ValueError(f"wrong resource type: {config}")

        # The chain declares a schema, so it runs it (ADR 0031).
        self.config = self.validate_and_normalize_schema(config, resource)

        chrom_prefix = self.config.get("chrom_prefix")
        if chrom_prefix is None:
            self.chrom_variant_coordinates = None
            self.chrom_target_coordinates = None
        else:
            self.chrom_variant_coordinates = chrom_prefix.get(
                "variant_coordinates", None)
            self.chrom_target_coordinates = chrom_prefix.get(
                "target_coordinates", None)

        self.liftover: LiftOver | None = None

        # Asked of the resource rather than read off the config, and
        # through the label narrowing rather than straight off the
        # mapping: the schema just run holds `meta.labels` to a mapping,
        # but says nothing about the values in it. An int or a list used to
        # satisfy these annotations and reach `get_resource` as itself,
        # where it raised `TypeError` naming neither the chain nor the
        # label.  The annotator that consumes both ids already handles
        # `None` -- it takes each as a parameter and falls back to the
        # label -- so an unusable one now behaves as absent (gain#1053).
        self.source_genome_id: str | None = read_resource_id_label(
            resource, "source_genome")
        self.target_genome_id: str | None = read_resource_id_label(
            resource, "target_genome")

    # A chain is not a context manager and holds nothing to release. The
    # method exists for symmetry with the other resource objects.
    def close(self) -> None:
        """Do nothing: a chain holds no resource to release."""

    def open(self) -> LiftoverChain:
        """Read the chain file.

        Returns
        -------
        LiftoverChain
            This chain, so the call chains. A call on an open chain does
            nothing.
        """
        with self.lock:
            if self.is_open():
                return self

            filename: str = self.config["filename"]
            with self.resource.open_raw_file(
                    filename, "rb", compression=True) as chain_file:
                self.liftover = LiftOver(chain_file)
            return self

    def is_open(self) -> bool:
        """Return True when the chain file is read."""
        with self.lock:
            return self.liftover is not None

    @property
    def files(self) -> set[str]:
        """Return the names of the files of the resource that the chain uses."""
        return {self.config["filename"]}

    @staticmethod
    def map_chromosome(chrom: str, mapping: dict[str, str] | None) -> str:
        """Rename a chromosome (contig) name.

        Parameters
        ----------
        chrom : str
            The contig name.
        mapping : dict, optional
            A ``chrom_prefix`` entry. Its ``del_prefix`` is removed first,
            then its ``add_prefix`` is prepended.

        Returns
        -------
        str
            The renamed contig. The name is unchanged when ``mapping`` is
            ``None`` or empty.
        """
        if not mapping:
            return chrom
        if "del_prefix" in mapping:
            del_prefix = mapping["del_prefix"]
            chrom = chrom.removeprefix(del_prefix)
        if "add_prefix" in mapping:
            add_prefix = mapping["add_prefix"]
            chrom = f"{add_prefix}{chrom}"
        return chrom

    def convert_coordinate(
        self, chrom: str,
        pos: int,
    ) -> tuple[str, int, str, int] | None:
        """Lift over a genomic coordinate.

        The ``variant_coordinates`` rename applies before the lift and the
        ``target_coordinates`` rename applies after it.

        Parameters
        ----------
        chrom : str
            The contig name in the naming of the caller.
        pos : int
            The 1-based position.

        Returns
        -------
        tuple of (str, int, str, int) or None
            The target contig, the target 1-based position, the strand of
            the target and the chain score. ``None`` when the position is
            in no aligned block of the chain. When the chain maps the
            position to more than one target, the first one is returned.
        """
        chrom = self.map_chromosome(chrom, self.chrom_variant_coordinates)
        assert self.liftover is not None
        lo_coordinates = self.liftover.convert_coordinate(chrom, pos - 1)

        if not lo_coordinates:
            return None
        if len(lo_coordinates) > 1:
            logger.info(
                "liftover_variant: liftover returns more than one target "
                "position: %s", lo_coordinates)

        coordinates = list(lo_coordinates[0])
        coordinates[0] = self.map_chromosome(
            coordinates[0], self.chrom_target_coordinates)
        coordinates[1] += 1
        assert coordinates[2] in {"+", "-"}
        return cast(tuple[str, int, str, int], tuple(coordinates))

    @staticmethod
    def get_schema() -> dict[str, Any]:
        """Return the cerberus schema of the resource configuration.

        Returns
        -------
        dict
            The schema. It has the ``filename`` key and the optional
            ``chrom_prefix`` block with ``variant_coordinates`` and
            ``target_coordinates``.
        """
        return {
            **get_base_resource_schema(),
            **get_required_filename_schema(),
            # Nullable for the bare `chrom_prefix:` line a curator leaves
            # when commenting its subsections out; it reads as absent.
            "chrom_prefix": {"type": "dict", "nullable": True, "schema": {
                "variant_coordinates": {"type": "dict", "schema": {
                    "del_prefix": {"type": "string"},
                    "add_prefix": {"type": "string"},
                }},
                "target_coordinates": {"type": "dict", "schema": {
                    "del_prefix": {"type": "string"},
                    "add_prefix": {"type": "string"},
                }},
            }},
        }


_INMEMORY_CACHE: Memo[tuple[str, str, str], LiftoverChain] = Memo()


def build_liftover_chain_from_resource(
    resource: GenomicResource,
) -> LiftoverChain:
    """Build the liftover chain of a resource.

    The chain is memoized for each resource. A second call for the same
    resource returns the same chain, which may already be open.

    Parameters
    ----------
    resource : GenomicResource
        A resource of type ``liftover_chain``.

    Returns
    -------
    LiftoverChain
        The chain. It is not open until the caller calls ``open``.

    Raises
    ------
    ValueError
        If ``resource`` is ``None`` or its type is not ``liftover_chain``.
    """
    if resource is None:
        raise ValueError(f"missing resource {resource}")

    if resource.get_type() != "liftover_chain":
        logger.error(
            "trying to use genomic resource %s "
            "as a liftover chaing but its type is %s;",
            resource.resource_id, resource.get_type())
        raise ValueError(f"wrong resource type: {resource.resource_id}")

    return _INMEMORY_CACHE.get_or_build(
        resource.get_memo_key(), lambda: LiftoverChain(resource))


def build_liftover_chain_from_resource_id(
    resource_id: str, grr: GenomicResourceRepo | None = None,
) -> LiftoverChain:
    """Build the liftover chain of a resource, found by its id.

    Parameters
    ----------
    resource_id : str
        The id of a resource of type ``liftover_chain``.
    grr : GenomicResourceRepo, optional
        The repository to search. The default repository is built when
        this is ``None``.

    Returns
    -------
    LiftoverChain
        The memoized chain. It is not open until the caller calls ``open``.

    Raises
    ------
    ValueError
        If the type of the resource is not ``liftover_chain``.
    """
    if grr is None:
        grr = build_genomic_resource_repository()
    return build_liftover_chain_from_resource(
        grr.get_resource(resource_id))
