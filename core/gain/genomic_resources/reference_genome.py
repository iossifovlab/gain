from __future__ import annotations

import logging
import os
from collections.abc import Generator
from types import TracebackType
from typing import IO, Any

from gain.genomic_resources import GenomicResource
from gain.genomic_resources.fsspec_protocol import build_local_resource
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)
from gain.genomic_resources.resource_implementation import (
    ResourceConfigValidationMixin,
    get_base_resource_schema,
)
from gain.utils.fs_utils import COMPRESSED_EXTENSIONS
from gain.utils.regions import Region

logger = logging.getLogger(__name__)


def reference_genome_files(config: dict[str, Any]) -> set[str]:
    """Return all files a reference-genome resource consists of.

    The set always contains the genome FASTA and its ``.fai`` index
    (honoring the optional ``index_file`` config key). For a bgzipped
    genome (filename ending in ``.gz``/``.bgz``) htslib random access also
    needs the ``.gzi`` BGZF index, so it is included as well.
    """
    file_name = config["filename"]
    index_file_name = config.get("index_file", f"{file_name}.fai")
    files = {file_name, index_file_name}
    if file_name.endswith(COMPRESSED_EXTENSIONS):
        files.add(f"{file_name}.gzi")
    return files


class _SequenceBackend:
    """Read nucleotide sequence for a reference genome.

    Two implementations exist: a byte-offset seek reader for plain ``.fa``
    files (:class:`_RawSeekSequence`) and a pysam-based reader for bgzipped
    ``.fa.gz``/``.bgz`` files (:class:`_PysamFastaSequence`).
    """

    def open(self, resource: GenomicResource, filename: str) -> None:
        raise NotImplementedError

    def is_open(self) -> bool:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def fetch(
        self, chrom: str, start: int, length: int, buffer_size: int,
    ) -> Generator[str, None, None]:
        raise NotImplementedError


class _RawSeekSequence(_SequenceBackend):
    """Read plain (uncompressed) FASTA via byte-offset seeking.

    Needs the parsed ``.fai`` index to turn a genomic position into a byte
    offset; the index dict is shared (by reference) with the owning
    ``ReferenceGenome`` and populated when the genome is opened.
    """

    def __init__(self, index: dict[str, Any]) -> None:
        self._sequence: IO | None = None
        self._index = index

    def open(self, resource: GenomicResource, filename: str) -> None:
        self._sequence = resource.open_raw_file(
            filename, "rb", uncompress=False, seekable=True)

    def is_open(self) -> bool:
        return self._sequence is not None

    def close(self) -> None:
        if self._sequence is not None:
            self._sequence.close()
            self._sequence = None

    def fetch(
        self, chrom: str, start: int, length: int, buffer_size: int,
    ) -> Generator[str, None, None]:
        # While line feed calculation can be inaccurate because not every
        # fetch starts at the start of a line, line feeds add extra characters
        # to read and the output is limited by the expected nucleotide count.
        assert self._sequence is not None
        index_entry = self._index[chrom]
        self._sequence.seek(
            index_entry["startBit"]
            + start
            - 1
            + (start - 1) // index_entry["seqLineLength"],
        )
        line_feeds = 1 + length // index_entry["seqLineLength"]
        total_length = length + line_feeds
        read_progress = 0
        while read_progress < length:
            read_length = min(buffer_size, total_length - read_progress)
            sequence = self._sequence.read(read_length).decode("ascii")
            sequence = sequence.replace("\n", "").upper()
            end = min(read_progress + read_length, length - read_progress)
            sequence = sequence[:end]
            yield from sequence
            read_progress += len(sequence)


class _PysamFastaSequence(_SequenceBackend):
    """Read bgzipped FASTA via ``pysam.FastaFile`` random access."""

    def __init__(self) -> None:
        self._fasta: Any = None

    def open(self, resource: GenomicResource, filename: str) -> None:
        self._fasta = resource.open_fasta_file(filename)

    def is_open(self) -> bool:
        return self._fasta is not None

    def close(self) -> None:
        if self._fasta is not None:
            self._fasta.close()
            self._fasta = None

    def fetch(
        self, chrom: str, start: int, length: int, buffer_size: int,
    ) -> Generator[str, None, None]:
        # Chunk pysam reads into ``buffer_size``-bp windows so a whole-
        # chromosome fetch does not materialise the entire sequence at once.
        assert self._fasta is not None
        stop = start + length - 1
        pos = start
        while pos <= stop:
            win_end = min(pos + buffer_size - 1, stop)
            # 1-based inclusive [pos, win_end] -> pysam 0-based half-open.
            sequence = self._fasta.fetch(chrom, pos - 1, win_end).upper()
            yield from sequence
            pos = win_end + 1


class ReferenceGenome(
    ResourceConfigValidationMixin,
):
    """Provides an interface for quering a reference genome."""

    def __init__(self, resource: GenomicResource):
        self.resource = resource
        if resource.get_type() != "genome":
            raise ValueError(
                f"wrong type of resource passed: {resource.get_type()}")
        self._index: dict[str, Any] = {}
        self._chromosomes: list[str] = []
        self._chromosome_lengths: dict[str, int] = {}

        config = resource.get_config()
        filename = config["filename"]
        if filename.endswith((".gz", ".bgz")):
            self._backend: _SequenceBackend = _PysamFastaSequence()
        else:
            self._backend = _RawSeekSequence(self._index)

        self.pars: dict = self._parse_pars(config)

    @property
    def resource_id(self) -> str:
        return self.resource.resource_id

    @staticmethod
    def _parse_pars(config: dict[str, Any]) -> dict:
        if "PARS" not in config:
            return {}

        assert config["PARS"]["X"] is not None
        regions_x = [
            Region.from_str(region) for region in config["PARS"]["X"]
        ]
        chrom_x = regions_x[0].chrom

        result = {
            chrom_x: regions_x,
        }

        if config["PARS"]["Y"] is not None:
            regions_y = [
                Region.from_str(region) for region in config["PARS"]["Y"]
            ]
            chrom_y = regions_y[0].chrom
            result[chrom_y] = regions_y
        return result

    @property
    def chromosomes(self) -> list[str]:
        """Return a list of all chromosomes of the reference genome."""
        self._load_genome_index()
        return self._chromosomes

    @property
    def chrom_prefix(self) -> str:
        """Return a prefix of all chromosomes of the reference genome."""
        self._load_genome_index()
        chrom = self._chromosomes[0]
        if chrom.startswith("chr"):
            return "chr"
        return ""

    def _load_genome_index(self) -> None:
        if self._index:
            return
        config = self.resource.get_config()
        file_name = config["filename"]
        index_file_name = config.get(
            "index_file", f"{file_name}.fai")

        index_content = self.resource.get_file_content(index_file_name)
        self._parse_genome_index(index_content)

    def _parse_genome_index(self, index_content: str) -> None:
        for line in index_content.split("\n"):
            line = line.strip()
            if not line:
                break

            rec = line.split()
            self._index[rec[0]] = {
                "length": int(rec[1]),
                "startBit": int(rec[2]),
                "seqLineLength": int(rec[3]),
                "lineLength": int(rec[4]),
            }
        self._chromosome_lengths = {
            chrom: data["length"]
            for chrom, data in self._index.items()
        }
        self._chromosomes = list(self._chromosome_lengths.keys())

    def close(self) -> None:
        """Close reference genome sequence file-like objects."""
        self._backend.close()
        self._index.clear()

    def open(self) -> ReferenceGenome:
        """Open reference genome resources."""
        if self.is_open():
            logger.info(
                "opening already opened reference genome %s",
                self.resource.resource_id)
            return self

        self._load_genome_index()

        config = self.resource.get_config()
        file_name = config["filename"]
        self._backend.open(self.resource, file_name)

        return self

    def is_open(self) -> bool:
        return self._backend.is_open()

    def __enter__(self) -> ReferenceGenome:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            logger.error(
                "exception while using reference genome: %s, %s, %s",
                exc_type, exc_value, exc_tb)
        try:
            self.close()
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "exception during closing reference genome")

    def get_chrom_length(self, chrom: str) -> int:
        """Return the length of a specified chromosome."""
        self._load_genome_index()
        if chrom not in self._chromosome_lengths:
            raise ValueError(f"can't find chromosome {chrom}")

        return self._chromosome_lengths[chrom]

    def get_all_chrom_lengths(self) -> dict[str, int]:
        """Return list of all chromosomes lengths."""
        self._load_genome_index()
        return self._chromosome_lengths

    def split_into_regions(
        self, region_size: int, chromosome: str | None = None,
    ) -> Generator[Region, None, None]:
        """
        Split the reference genome into regions and yield them.

        Can specify a specific chromosome to limit the regions to be
        in that chromosome only.
        """
        if chromosome is None:
            chromosome_lengths = list(self.get_all_chrom_lengths().items())
        else:
            chromosome_lengths = [
                (chromosome, self.get_chrom_length(chromosome)),
            ]

        if region_size == 0:
            yield from [Region(chrom, 1) for chrom, _ in chromosome_lengths]
            return

        for chrom, chrom_len in chromosome_lengths:
            logger.debug(
                "Chromosome '%s' has length %s",
                chrom, chrom_len)
            i = 1
            while i < chrom_len - region_size:
                yield Region(chrom, i, i + region_size - 1)
                i += region_size
            yield Region(chrom, i, None)

    def fetch(
        self, chrom: str, start: int, stop: int | None,
        buffer_size: int = 512,
    ) -> Generator[str, None, None]:
        """
        Yield the nucleotides in a specific region.

        While line feed calculation can be inaccurate because not every fetch
        will start at the start of a line, line feeds add extra characters
        to read and the output is limited by the amount of nucleotides
        expected to be read.
        """
        if chrom not in self.chromosomes:
            logger.warning(
                "chromosome %s not found in %s",
                chrom, self.resource.resource_id)
            return

        chrom_length = self.get_chrom_length(chrom)

        if stop is None:
            length = chrom_length - start + 1
        else:
            length = min(stop, chrom_length) - start + 1

        yield from self._backend.fetch(chrom, start, length, buffer_size)

    def get_sequence(self, chrom: str, start: int, stop: int) -> str:
        """Return sequence of nucleotides from specified chromosome region."""
        return "".join(self.fetch(chrom, start, stop))

    def is_pseudoautosomal(self, chrom: str, pos: int) -> bool:
        """Return true if specified position is pseudoautosomal."""
        def in_any_region(
                chrom: str, pos: int, regions: list[Region]) -> bool:
            return any(reg for reg in regions if reg.isin(chrom, pos))

        pars_regions = self.pars.get(chrom, None)
        if pars_regions:
            return in_any_region(
                chrom, pos, pars_regions)
        return False

    @staticmethod
    def get_schema() -> dict[str, Any]:
        return {
            **get_base_resource_schema(),
            "filename": {"type": "string"},
            "PARS": {"type": "dict", "schema": {
                "X": {"type": "list", "schema": {"type": "string"}},
                "Y": {"type": "list", "schema": {"type": "string"}},
            }},
        }


def build_reference_genome_from_file(filename: str) -> ReferenceGenome:
    """Open a reference genome from a file."""
    dirname = os.path.dirname(filename)
    basename = os.path.basename(filename)
    res = build_local_resource(dirname, {
        "type": "genome",
        "filename": basename,
    })
    return build_reference_genome_from_resource(res)


def build_reference_genome_from_resource(
        resource: GenomicResource) -> ReferenceGenome:
    """Open a reference genome from resource."""
    if resource.get_type() != "genome":
        logger.error(
            "trying to open a resource %s of type "
            "%s as reference genome",
            resource.resource_id, resource.get_type())
        raise ValueError(f"wrong resource type: {resource.resource_id}")

    return ReferenceGenome(resource)


def build_reference_genome_from_resource_id(
    resource_id: str, grr: GenomicResourceRepo | None = None,
) -> ReferenceGenome:
    if grr is None:
        grr = build_genomic_resource_repository()
    return build_reference_genome_from_resource(
        grr.get_resource(resource_id))
