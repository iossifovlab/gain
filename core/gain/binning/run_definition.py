"""The ``bin_scores`` run definition: parsing and resolution."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gain.binning.binners import Track, discover_binner_kinds
from gain.genomic_resources.reference_genome import ReferenceGenome
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.utils.regions import BedRegion, Region


class RunDefinitionError(ValueError):
    """A run definition that cannot be resolved into a run."""


@dataclass(frozen=True)
class RunDefinition:
    """A parsed run definition with every query resolved."""

    bin_size: int
    regions: list[BedRegion]
    tracks: list[Track]


def parse_run_definition(
    config: dict[str, Any],
    grr: GenomicResourceRepo,
    genome: ReferenceGenome,
) -> RunDefinition:
    """Resolve ``config`` against ``grr`` and ``genome``."""
    return RunDefinition(
        bin_size=config["bins"]["bin_size"],
        regions=_resolve_regions(config["bins"].get("regions"), genome),
        tracks=_resolve_tracks(config["binners"], grr),
    )


def _resolve_regions(
    regions: list[str] | None, genome: ReferenceGenome,
) -> list[BedRegion]:
    """Expand region notation against the genome, in the listed order.

    Omitted regions mean every chromosome of the genome in genome order; a
    bare chromosome name is the whole chromosome; a window keeps its
    inclusive bounds.
    """
    if regions is None:
        regions = list(genome.chromosomes)
    resolved = []
    for index, notation in enumerate(regions):
        region = Region.from_str(notation)
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


def _resolve_tracks(
    binners: list[dict[str, Any]], grr: GenomicResourceRepo,
) -> list[Track]:
    kinds = discover_binner_kinds()
    tracks: list[Track] = []
    for index, entry in enumerate(binners):
        (kind, entry_config), = entry.items()
        if kind not in kinds:
            raise RunDefinitionError(
                f"binners[{index}]: unknown binner kind {kind!r}; "
                f"registered kinds: {', '.join(sorted(kinds))}")
        entry_tracks = kinds[kind].parse_entry(entry_config, grr)
        if not entry_tracks:
            raise RunDefinitionError(
                f"binners[{index}]: resource_query "
                f"{entry_config['resource_query']!r} matches no "
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
