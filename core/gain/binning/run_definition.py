"""The ``binning_tool`` run definition: parsing and resolution."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from itertools import combinations
from typing import Any

from gain.binning.binners import (
    BinningJob,
    RunDefinitionError,
    Track,
    check_keys,
    discover_binner_kinds,
)
from gain.genomic_resources.reference_genome import ReferenceGenome
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.utils.regions import BedRegion, Region

__all__ = ["RunDefinition", "RunDefinitionError", "parse_run_definition"]

TOP_LEVEL_KEYS = frozenset({"input_reference_genome", "bins", "binners"})
BINS_KEYS = frozenset({"bin_size", "regions"})


@dataclass(frozen=True)
class RunDefinition:
    """A parsed run definition with every query resolved.

    ``jobs`` are what the tasks bind to; ``tracks`` are the output's
    columns, every job's tracks in job order.
    """

    input_reference_genome: str
    bin_size: int
    regions: list[BedRegion]
    jobs: list[BinningJob]

    @property
    def tracks(self) -> list[Track]:
        return [track for job in self.jobs for track in job.tracks]


def parse_run_definition(
    config: dict[str, Any],
    grr: GenomicResourceRepo,
    genome: ReferenceGenome,
    base_dir: str | None = None,
) -> RunDefinition:
    """Resolve ``config`` against ``grr`` and ``genome``.

    Every key is checked: a mistyped key is an error, never a silently
    applied default.  Raises :class:`RunDefinitionError` naming the
    offending entry.  ``base_dir`` is the run-definition file's
    directory, which a relative path in an entry is read from; a config
    parsed from memory leaves it unset, and the kinds read such a path
    from the current directory.
    """
    check_keys("run definition", config, TOP_LEVEL_KEYS)
    bins = config.get("bins")
    check_keys("bins", bins, BINS_KEYS)
    assert isinstance(bins, dict)
    return RunDefinition(
        input_reference_genome=genome.resource_id,
        bin_size=_resolve_bin_size(bins.get("bin_size")),
        regions=_resolve_regions(bins.get("regions"), genome),
        jobs=_resolve_jobs(config.get("binners"), grr, base_dir),
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
        except AssertionError as err:
            # A stand-in: a window whose end precedes its start is the
            # one malformation the shared parser asserts rather than
            # reports.  Once ``BedRegion`` raises ValueError for it, the
            # clause above covers it and this one goes.
            raise RunDefinitionError(
                f"bins.regions[{index}]: {notation!r} ends before it "
                f"starts") from err
        if region.chrom not in genome.chromosomes:
            raise RunDefinitionError(
                f"bins.regions[{index}]: {notation!r} names chromosome "
                f"{region.chrom!r}, which the genome "
                f"<{genome.resource_id}> does not have")
        length = genome.get_chrom_length(region.chrom)
        if region.start is None:
            resolved.append(BedRegion(region.chrom, 1, length))
            continue
        bed = region.to_bed_region()
        if bed.start < 1 or bed.stop > length:
            raise RunDefinitionError(
                f"bins.regions[{index}]: {notation!r} lies outside "
                f"{region.chrom}:1-{length}, the whole of chromosome "
                f"{region.chrom!r} in genome <{genome.resource_id}>")
        resolved.append(bed)
    _refuse_overlapping_regions(regions, resolved)
    return resolved


def _refuse_overlapping_regions(
    notations: list[Any], resolved: list[BedRegion],
) -> None:
    """Two regions sharing a position would bin it twice (D4).

    Named by the notation the user wrote, since that is what they will
    look for; regions are neither sorted nor merged on their behalf.
    Compared within a chromosome only: the common genome-wide run lists
    every contig once, and pays nothing here.
    """
    by_chrom: dict[str, list[int]] = defaultdict(list)
    for index, region in enumerate(resolved):
        by_chrom[region.chrom].append(index)
    for indices in by_chrom.values():
        for earlier, later in combinations(indices, 2):
            if resolved[earlier].intersects(resolved[later]):
                raise RunDefinitionError(
                    f"bins.regions[{earlier}] {notations[earlier]!r} and "
                    f"bins.regions[{later}] {notations[later]!r} overlap")


def _resolve_jobs(
    binners: Any, grr: GenomicResourceRepo, base_dir: str | None,
) -> list[BinningJob]:
    if not isinstance(binners, list) or not binners:
        raise RunDefinitionError(
            "binners must be a non-empty list of binner entries")
    kinds = discover_binner_kinds()
    located = {} if base_dir is None else {"base_dir": base_dir}
    jobs: list[tuple[str, BinningJob]] = []
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
        for job in kinds[kind].parse_entry(
                label, entry_config, grr, **located):
            # A task is named by its job's first track and writes one
            # chunk per track, so a job needs at least one; and the graph
            # finds the binner by the kind the job names.
            if not job.tracks:
                raise RunDefinitionError(
                    f"{label}: {kind} resolved the entry into a job with "
                    f"no tracks; every job needs at least one")
            if job.binner != kind:
                raise RunDefinitionError(
                    f"{label}: {kind} resolved the entry into a job bound "
                    f"by {job.binner!r}; a job is bound by the kind that "
                    f"produced it")
            jobs.append((label, job))
    # Naming reads every track of the run at once; the named tracks come
    # back in the order they went in, so each job takes its own back.
    named = iter(_name_tracks([
        (label, track) for label, job in jobs for track in job.tracks]))
    return [
        replace(
            job, tracks=tuple(next(named) for _ in job.tracks), entry=label)
        for label, job in jobs
    ]


def _name_tracks(tracks: list[tuple[str, Track]]) -> list[Track]:
    """Give every track a unique name (D10).

    A track arrives named by its kind: a position-score track by its
    resource id, a fragment track by its resource id or query and its
    group.  When one name and group occur more than once in the expanded
    list -- two aggregators of one track, side by side -- every member of
    that set carries ``:<aggregator>``, whichever entry came first.  Two
    tracks that still share a name (same base, group and aggregator,
    differing at most in what else decides their values) are refused,
    naming both entries: nothing in the ``/tracks`` table would tell the
    columns apart.
    """
    occurrences = Counter(
        (track.name, track.group) for _, track in tracks)
    named: list[Track] = []
    producers: dict[str, str] = {}
    for label, track in tracks:
        named_track = (
            replace(track, name=f"{track.name}:{track.aggregator}")
            if occurrences[track.name, track.group] > 1 else track)
        if named_track.name in producers:
            raise RunDefinitionError(
                f"{producers[named_track.name]} and {label} both produce "
                f"the track {named_track.name!r}; a resource may be binned "
                f"once per aggregator")
        producers[named_track.name] = label
        named.append(named_track)
    return named
