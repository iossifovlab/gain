"""The ``bin_scores`` run definition: parsing and resolution."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gain.binning.binners import (
    RunDefinitionError,
    Track,
    _check_keys,
    discover_binner_kinds,
)
from gain.genomic_resources.reference_genome import ReferenceGenome
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.utils.regions import BedRegion, Region

__all__ = ["RunDefinition", "RunDefinitionError", "parse_run_definition"]

TOP_LEVEL_KEYS = frozenset({"input_reference_genome", "bins", "binners"})
BINS_KEYS = frozenset({"bin_size", "regions"})
NO_KEYS: frozenset[str] = frozenset()


@dataclass(frozen=True)
class RunDefinition:
    """A parsed run definition with every query resolved."""

    input_reference_genome: str
    bin_size: int
    regions: list[BedRegion]
    tracks: list[Track]


def parse_run_definition(
    config: dict[str, Any],
    grr: GenomicResourceRepo,
    genome: ReferenceGenome,
) -> RunDefinition:
    """Resolve ``config`` against ``grr`` and ``genome``.

    Every key is checked: a mistyped key is an error, never a silently
    applied default.  Raises :class:`RunDefinitionError` naming the
    offending entry.
    """
    _check_keys("run definition", config, TOP_LEVEL_KEYS, NO_KEYS)
    bins = config.get("bins")
    _check_keys("bins", bins, BINS_KEYS, NO_KEYS)
    assert isinstance(bins, dict)
    return RunDefinition(
        input_reference_genome=genome.resource_id,
        bin_size=_resolve_bin_size(bins.get("bin_size")),
        regions=_resolve_regions(bins.get("regions"), genome),
        tracks=_resolve_tracks(config.get("binners"), grr),
    )


def _resolve_bin_size(bin_size: Any) -> int:
    if isinstance(bin_size, bool) or not isinstance(bin_size, int) \
            or bin_size < 1:
        raise RunDefinitionError(
            f"bins.bin_size must be a positive integer, not {bin_size!r}")
    return int(bin_size)


def _resolve_regions(
    regions: Any, genome: ReferenceGenome,
) -> list[BedRegion]:
    """Expand region notation against the genome, in the listed order.

    Omitted regions mean every chromosome of the genome in genome order; a
    bare chromosome name is the whole chromosome; a window keeps its
    inclusive bounds.
    """
    if regions is None:
        regions = list(genome.chromosomes)
    if not isinstance(regions, list) or not regions:
        raise RunDefinitionError(
            "bins.regions must be a non-empty list of regions, or omitted "
            "for every chromosome of the genome")
    resolved = []
    for index, notation in enumerate(regions):
        try:
            region = Region.from_str(str(notation))
        except ValueError as err:
            raise RunDefinitionError(
                f"bins.regions[{index}]: {err}") from err
        if region.chrom not in genome.chromosomes:
            raise RunDefinitionError(
                f"bins.regions[{index}]: {notation!r} names chromosome "
                f"{region.chrom!r}, which the genome "
                f"<{genome.resource_id}> does not have")
        if region.start is None:
            region = BedRegion(
                region.chrom, 1, genome.get_chrom_length(region.chrom))
        resolved.append(region.to_bed_region())
    return resolved


def _resolve_tracks(binners: Any, grr: GenomicResourceRepo) -> list[Track]:
    if not isinstance(binners, list) or not binners:
        raise RunDefinitionError(
            "binners must be a non-empty list of binner entries")
    kinds = discover_binner_kinds()
    tracks: list[Track] = []
    for index, entry in enumerate(binners):
        label = f"binners[{index}]"
        if not isinstance(entry, dict) or len(entry) != 1:
            raise RunDefinitionError(
                f"{label}: an entry is a one-key mapping of binner kind to "
                f"its configuration")
        (kind, entry_config), = entry.items()
        if kind not in kinds:
            raise RunDefinitionError(
                f"{label}: unknown binner kind {kind!r}; "
                f"registered kinds: {', '.join(sorted(kinds))}")
        entry_tracks = kinds[kind].parse_entry(label, entry_config, grr)
        if not entry_tracks:
            raise RunDefinitionError(
                f"{label}: resource_query "
                f"{entry_config.get('resource_query')!r} matches no "
                f"position_score resource")
        tracks.extend(entry_tracks)
    _refuse_repeated_names(tracks)
    return tracks


def _refuse_repeated_names(tracks: list[Track]) -> None:
    """Refuse a track name that would occur twice.

    A track is named by its resource id (D10), so one resource matched by
    two entries -- two aggregators of one track, side by side -- needs the
    ``:<aggregator>`` suffixing of the validation slice (gain#1201).
    Until it lands, such a run is refused here rather than written with
    two columns that cannot be told apart.
    """
    seen: set[str] = set()
    for track in tracks:
        if track.name in seen:
            raise RunDefinitionError(
                f"resource {track.resource_id!r} is matched by more than "
                f"one binner entry; binning one resource under several "
                f"entries is not yet supported")
        seen.add(track.name)
