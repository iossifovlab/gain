from __future__ import annotations

import copy
import itertools
import json
import os
from typing import Any, ClassVar

import yaml

from gain import logging
from gain.genomic_resources import GenomicResource
from gain.genomic_resources.reference_genome import (
    build_reference_genome_from_resource,
    reference_genome_files,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.resource_implementation import (
    DEFAULT_STATISTICS_REGION_SIZE,
    GenomicResourceImplementation,
    InfoImplementationMixin,
    ResourceStatistics,
)
from gain.genomic_resources.statistics.base_statistic import Statistic
from gain.genomic_resources.statistics.percentages import percentages_over
from gain.task_graph.graph import TaskDesc, TaskGraph

logger = logging.getLogger(__name__)

# Window size (in bp) for the whole-chromosome sequence fetch that drives
# nucleotide statistics. The default fetch buffer (512 bp) is tuned for the
# plain-FASTA seek reader; for a bgzipped genome it would turn a single
# chromosome into hundreds of thousands of tiny pysam reads, so the stats
# pass uses a much larger window — bounding memory to ~1 MB per read while
# keeping the number of pysam calls per chromosome small.
CHROMOSOME_STATISTIC_FETCH_BUFFER_SIZE = 1_000_000


class GenomeStatisticsMixin:
    """Mixin for reference genome statistics access."""

    @staticmethod
    def get_global_statistic_file() -> str:
        return "reference_genome_statistic.yaml"

    @staticmethod
    def get_chrom_file(chrom: str) -> str:
        return f"{chrom}_statistic.yaml"


def _distribution(counts: dict[str, int], total: int) -> dict[str, float]:
    """Each count as a percentage of ``total``; all zero without one."""
    if total == 0:
        return dict.fromkeys(counts, 0.0)
    return {key: count / total * 100 for key, count in counts.items()}


def _shares(counts: dict[str, int]) -> dict[str, str]:
    """Each count written as a share of their sum; empty when it is zero."""
    return percentages_over(counts, sum(counts.values())) or {}


class ReferenceGenomeStatistics(
    ResourceStatistics,
    GenomeStatisticsMixin,
):
    """Class for accessing reference genome statistics."""

    def __init__(
            self, resource_id: str,
            global_statistic: GenomeStatistic,
            chrom_statistics: dict[str, ChromosomeStatistic]):
        super().__init__(resource_id)
        self.global_statistic = global_statistic
        self.chrom_statistics = chrom_statistics

    @staticmethod
    def build_statistics(
        genomic_resource: GenomicResource,
    ) -> ReferenceGenomeStatistics | None:
        """Load reference genome statistics.

        The global statistic is summed from the per-chromosome ones, as
        the build does; its stored file supplies only the chromosomes.
        """
        chrom_statistics = {}
        try:
            global_stat_filepath = os.path.join(
                ReferenceGenomeStatistics.get_statistics_folder(),
                ReferenceGenomeStatistics.get_global_statistic_file(),
            )
            with genomic_resource.open_raw_file(
                    global_stat_filepath, mode="r") as infile:
                global_statistic = GenomeStatistic(
                    yaml.safe_load(infile.read())["chromosomes"])
            for chrom in global_statistic.chromosomes:
                chrom_stat_filepath = os.path.join(
                    ReferenceGenomeStatistics.get_statistics_folder(),
                    ReferenceGenomeStatistics.get_chrom_file(chrom),
                )
                with genomic_resource.open_raw_file(
                        chrom_stat_filepath, mode="r") as infile:
                    chrom_statistics[chrom] = ChromosomeStatistic.deserialize(
                        infile.read(),
                    )
                global_statistic.add_value(chrom_statistics[chrom])
            global_statistic.finish()
        except FileNotFoundError:
            logger.exception(
                "Couldn't load statistics of %s", genomic_resource.resource_id,
            )
            return None
        return ReferenceGenomeStatistics(
            genomic_resource.resource_id,
            global_statistic,
            chrom_statistics,
        )


class ChromosomeStatistic(Statistic):
    """Class for individual chromosome statistics."""

    def __init__(
        self, chromosome: str, length: int = 0,
        nucleotide_counts: dict[str, int] | None = None,
        nucleotide_pair_counts: dict[str, int] | None = None,
    ):
        super().__init__(chromosome, "Reference genome chromosome stats")
        if nucleotide_counts is None:
            self.nucleotide_counts = {
                "A": 0,
                "G": 0,
                "C": 0,
                "T": 0,
                "N": 0,
            }
        else:
            assert set(
                nucleotide_counts.keys(),
            ) == {"A", "G", "C", "T", "N"}
            self.nucleotide_counts = nucleotide_counts

        nucleotides = ["A", "G", "C", "T", "N"]
        pairs = map("".join, itertools.product(nucleotides, nucleotides))
        if nucleotide_pair_counts is None:
            self.nucleotide_pair_counts = dict.fromkeys(pairs, 0)
        else:
            assert set(nucleotide_pair_counts.keys()) == set(pairs)
            self.nucleotide_pair_counts = nucleotide_pair_counts

        self.nucleotide_distribution: dict[str, float] = {}
        self.bi_nucleotide_distribution: dict[str, float] = {}
        self.last_nuc = ""
        self.length = length

    def _add_nucleotide(self, nuc: str) -> None:
        assert nuc is not None
        if nuc not in self.nucleotide_counts:
            logger.warning(
                "unexpected nucleotide <%s> in chromosome <%s>",
                nuc, self.statistic_id)
            return

        self.nucleotide_counts[nuc] += 1
        self.length += 1

    def _add_nucleotide_tuple(
            self, nuc1: str | None, nuc2: str) -> None:
        if nuc1 is None:
            return

        assert nuc1 is not None
        assert nuc2 is not None

        pair = f"{nuc1}{nuc2}"
        if pair not in self.nucleotide_pair_counts:
            return
        self.nucleotide_pair_counts[pair] += 1

    def add_value(self, value: tuple[str | None, str]) -> None:
        prev, current = value
        self._add_nucleotide(current)
        self._add_nucleotide_tuple(prev, current)

    def merge(self, other: ChromosomeStatistic) -> None:
        """Add ``other``'s nucleotide and pair counts into this one."""
        assert isinstance(other, ChromosomeStatistic)

        local_keys = set(self.nucleotide_counts.keys())
        other_keys = set(other.nucleotide_counts.keys())
        matching_keys = local_keys.intersection(other_keys)
        missing_keys = other_keys.difference(local_keys)
        for k in matching_keys:
            self.nucleotide_counts[k] += other.nucleotide_counts[k]
        for k in missing_keys:
            self.nucleotide_counts[k] = other.nucleotide_counts[k]

        local_keys = set(self.nucleotide_pair_counts.keys())
        other_keys = set(other.nucleotide_pair_counts.keys())
        matching_keys = local_keys.intersection(other_keys)
        missing_keys = other_keys.difference(local_keys)
        for k in matching_keys:
            self.nucleotide_pair_counts[k] += other.nucleotide_pair_counts[k]
        for k in missing_keys:
            self.nucleotide_pair_counts[k] = other.nucleotide_pair_counts[k]

        self.length += other.length

    def finish(self) -> None:
        self.nucleotide_distribution = _distribution(
            self.nucleotide_counts, self.length)
        self.bi_nucleotide_distribution = _distribution(
            self.nucleotide_pair_counts,
            sum(self.nucleotide_pair_counts.values()))

    def serialize(self) -> str:
        return yaml.dump(
            {
                "chrom": self.statistic_id,
                "length": self.length,
                "nucleotide_counts": self.nucleotide_counts,
                "nucleotide_pair_counts": self.nucleotide_pair_counts,
            },
        )

    @staticmethod
    def deserialize(content: str) -> ChromosomeStatistic:
        res = yaml.safe_load(content)
        stat = ChromosomeStatistic(
            res["chrom"],
            length=res.get("length"),
            nucleotide_counts=res.get("nucleotide_counts"),
            nucleotide_pair_counts=res.get("nucleotide_pair_counts"),
        )
        stat.finish()
        return stat


class GenomeStatistic(Statistic):
    """The genome-wide statistic: every chromosome's counts summed."""

    def __init__(
            self, chromosomes: list[str], length: int = 0,
            nucleotide_distribution: dict[str, float] | None = None,
            bi_nucleotide_distribution: dict[str, float] | None = None,
            chromosome_statistics: dict[
                str, ChromosomeStatistic] | None = None,
    ):
        super().__init__("global", "")
        self.chromosomes = chromosomes
        self.length = 0
        self.nucleotide_counts: dict[str, int] = {}
        self.nucleotide_pair_counts: dict[str, int] = {}

        if chromosome_statistics is not None:
            self.chromosome_statistics = chromosome_statistics
            self.nucleotide_distribution: dict[str, Any] = {}
            self.bi_nucleotide_distribution: dict[str, Any] = {}
            self.finish()
        else:
            self.chromosome_statistics = {}
            if nucleotide_distribution is None:
                self.nucleotide_distribution = {}
            else:
                self.nucleotide_distribution = nucleotide_distribution

            if bi_nucleotide_distribution is None:
                self.bi_nucleotide_distribution = {}
            else:
                self.bi_nucleotide_distribution = bi_nucleotide_distribution

        self.length = length

    @property
    def chrom_count(self) -> int:
        return len(self.chromosomes)

    def add_value(self, value: Any) -> None:
        assert isinstance(value, ChromosomeStatistic)
        self.chromosome_statistics[value.statistic_id] = value

    def finish(self) -> None:
        total_nucs = 0
        total_nucleotide_counts = {
            "A": 0,
            "G": 0,
            "C": 0,
            "T": 0,
            "N": 0,
        }
        nucleotides = ["A", "G", "C", "T", "N"]
        pairs = map("".join, itertools.product(nucleotides, nucleotides))
        total_pair_counts = dict.fromkeys(pairs, 0)
        total_pairs = 0
        for statistic in self.chromosome_statistics.values():
            total_nucs += statistic.length
            for nuc, count in statistic.nucleotide_counts.items():
                total_nucleotide_counts[nuc] += count
            for pair, count in statistic.nucleotide_pair_counts.items():
                total_pairs += count
                total_pair_counts[pair] += count

        self.length = total_nucs
        self.nucleotide_counts = total_nucleotide_counts
        self.nucleotide_pair_counts = total_pair_counts

        self.nucleotide_distribution = _distribution(
            total_nucleotide_counts, total_nucs)
        self.bi_nucleotide_distribution = _distribution(
            total_pair_counts, total_pairs)

    def serialize(self) -> str:
        return yaml.dump({
            "chromosomes": self.chromosomes,
            "length": self.length,
            "nucleotide_distribution": self.nucleotide_distribution,
            "bi_nucleotide_distribution": self.bi_nucleotide_distribution,
        })

    @staticmethod
    def deserialize(content: str) -> GenomeStatistic:
        res = yaml.safe_load(content)
        return GenomeStatistic(
            res["chromosomes"],
            length=res.get("length"),
            nucleotide_distribution=res.get("nucleotide_distribution"),
            bi_nucleotide_distribution=res.get("bi_nucleotide_distribution"),
        )


class ReferenceGenomeImplementation(
    GenomicResourceImplementation,
    InfoImplementationMixin,
):
    """Resource implementation for reference genome."""

    def __init__(self, resource: GenomicResource):
        super().__init__(resource)
        self.reference_genome = build_reference_genome_from_resource(resource)
        # One config on this object: the genome's validated one, so the
        # implementation and the genome it wraps can never read a config
        # apart (gain#1067).
        self.config = self.reference_genome.config

    @property
    def files(self) -> set[str]:
        return reference_genome_files(self.config)

    template_name: ClassVar[str] = "reference_genome.jinja"
    styles_template_name: ClassVar[str] = "reference_genome_styles.jinja"

    def _get_template_data(self) -> dict[str, Any]:
        info = copy.deepcopy(self.config)
        info["chromosomes"] = list(
            self.reference_genome.get_all_chrom_lengths().items())
        info["global_statistic"] = {}
        info["chrom_shares"] = {}
        statistics = self.get_statistics()
        if statistics is None:
            info["global_statistic"]["length"] = None
            info["global_statistic"]["nucleotide_shares"] = {}
            info["global_statistic"]["pair_shares"] = {}
            info["global_statistic"]["chromosome_statistics"] = None
        else:
            global_statistic = statistics.global_statistic

            info["global_statistic"]["length"] = global_statistic.length
            info["global_statistic"]["nucleotide_shares"] = _shares(
                global_statistic.nucleotide_counts)
            info["global_statistic"]["pair_shares"] = _shares(
                global_statistic.nucleotide_pair_counts)
            info["chrom_shares"] = {
                chrom: _shares(statistic.nucleotide_counts)
                for chrom, statistic in statistics.chrom_statistics.items()
            }

        return info

    def get_info(self, **kwargs: Any) -> str:  # ruff: ignore[unused-method-argument]
        return InfoImplementationMixin.get_info(self)

    def get_statistics_info(self, **kwargs: Any) -> str:  # ruff: ignore[unused-method-argument]
        return InfoImplementationMixin.get_statistics_info(self)

    def calc_info_hash(self) -> bytes:
        return b"placeholder"

    def calc_statistics_hash(self) -> bytes:
        manifest = self.resource.get_manifest()
        config = self.get_config()
        genome_filename = config["filename"]
        return json.dumps({
            "score_file": manifest[genome_filename].md5,
        }, sort_keys=True, indent=2).encode()

    def create_statistics_build_tasks(
        self, *,
        region_size: int = DEFAULT_STATISTICS_REGION_SIZE,
        grr: GenomicResourceRepo | None = None,  # ruff: ignore[unused-method-argument]
    ) -> list[TaskDesc]:
        tasks = []
        chrom_save_tasks = []

        with self.reference_genome.open():
            for chrom in self.reference_genome.chromosomes:
                chrom_tasks, chrom_save_task = self._create_chrom_stats_tasks(
                    chrom, region_size,
                )
                chrom_save_tasks.append(chrom_save_task.task)
                tasks.extend(chrom_tasks)

        global_task = TaskGraph.make_task(
            f"{self.resource.resource_id}_global_statistics",
            ReferenceGenomeImplementation._do_global_statistic,
            args=[self.resource, *chrom_save_tasks],
            deps=[],
        )
        tasks.append(global_task)

        return tasks

    def _create_chrom_stats_tasks(
        self, chrom: str, region_size: int,
    ) -> tuple[list[TaskDesc], TaskDesc]:
        tasks = []
        regions = self.reference_genome.split_into_regions(region_size, chrom)
        tasks = [
            TaskGraph.make_task(
                f"{self.resource.resource_id}_count_nucleotides_"
                f"{reg}",
                ReferenceGenomeImplementation._do_chrom_statistic,
                args=[self.resource, reg.chrom, reg.start, reg.end],
                deps=[],
            )
            for reg in regions
        ]

        merge_task = TaskGraph.make_task(
            f"{self.resource.resource_id}_merge_chrom_statistics_{chrom}",
            ReferenceGenomeImplementation._merge_chrom_statistics,
            args=[t.task for t in tasks],
            deps=[],
        )
        tasks.append(merge_task)
        save_task = TaskGraph.make_task(
            f"{self.resource.resource_id}_save_chrom_statistics_{chrom}",
            ReferenceGenomeImplementation._save_chrom_statistic,
            args=[self.resource, chrom, merge_task.task],
            deps=[],
        )
        tasks.append(save_task)

        return tasks, save_task

    @staticmethod
    def _do_chrom_statistic(
        resource: GenomicResource, chrom: str, start: int, end: int | None,
    ) -> ChromosomeStatistic:
        impl = build_reference_genome_from_resource(resource)
        statistic = ChromosomeStatistic(chrom)
        with impl.open():
            if start == 1:
                prev: str | None = None
            else:
                prev = impl.get_sequence(chrom, start - 1, start)
            for nuc in impl.fetch(
                    chrom, start, end,
                    buffer_size=CHROMOSOME_STATISTIC_FETCH_BUFFER_SIZE):
                statistic.add_value((prev, nuc))
                prev = nuc

        statistic.finish()
        return statistic

    @staticmethod
    def _merge_chrom_statistics(
        *chrom_tasks: ChromosomeStatistic,
    ) -> ChromosomeStatistic:
        final_statistic: ChromosomeStatistic | None = None
        for chrom_task_result in chrom_tasks:
            if final_statistic is None:
                final_statistic = chrom_task_result
            else:
                final_statistic.merge(chrom_task_result)
        assert final_statistic is not None
        return final_statistic

    @staticmethod
    def _save_chrom_statistic(
        resource: GenomicResource, chrom: str,
        merged_statistic: ChromosomeStatistic,
    ) -> ChromosomeStatistic:
        proto = resource.proto
        if merged_statistic is None:
            logger.warning("Chrom statistic for %s is None", chrom)
            return {chrom: None}
        with proto.open_raw_file(
            resource,
            f"{ReferenceGenomeStatistics.get_statistics_folder()}"
            f"/{ReferenceGenomeStatistics.get_chrom_file(chrom)}",
            mode="wt",
        ) as outfile:
            outfile.write(merged_statistic.serialize())
        return merged_statistic

    @staticmethod
    def _do_global_statistic(
        resource: GenomicResource, *chrom_save_tasks: ChromosomeStatistic,
    ) -> GenomeStatistic:
        impl = build_reference_genome_from_resource(resource)
        with impl.open():
            statistic = GenomeStatistic(impl.chromosomes)
            for chrom_statistic in chrom_save_tasks:
                statistic.add_value(chrom_statistic)

            statistic.finish()

        proto = resource.proto
        with proto.open_raw_file(
            resource,
            f"{ReferenceGenomeStatistics.get_statistics_folder()}"
            f"/{ReferenceGenomeStatistics.get_global_statistic_file()}",
            mode="wt",
        ) as outfile:
            outfile.write(statistic.serialize())
        return statistic

    def get_statistics(self) -> ReferenceGenomeStatistics | None:
        return ReferenceGenomeStatistics.build_statistics(self.resource)
