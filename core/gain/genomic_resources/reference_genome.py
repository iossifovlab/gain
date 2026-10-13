from __future__ import annotations

import os
from collections.abc import Generator
from types import TracebackType
from typing import IO, Any, cast

from gain import logging
from gain.genomic_resources import GenomicResource
from gain.genomic_resources.fsspec_protocol import build_local_resource
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)
from gain.genomic_resources.resource_implementation import (
    ResourceConfigValidationMixin,
    get_base_resource_schema,
    get_required_filename_schema,
)
from gain.genomic_resources.resource_types import warn_retired_config_key
from gain.utils.fs_utils import COMPRESSED_EXTENSIONS, endswith_ci
from gain.utils.regions import Region, check_region_size

logger = logging.getLogger(__name__)


def genome_index_file(config: dict[str, Any]) -> str:
    """Return the FASTA ``.fai`` index name for a genome config.

    The optional ``index_file`` key overrides the default
    ``<filename>.fai``.  The three consumers that read a genome resource
    from its config -- :func:`reference_genome_files`, the index parsing
    and the sequence backend -- resolve the name through here, so that a
    resource is listed, hashed, parsed and opened against one and the
    same index.

    Resolution is by name only: the file is not checked for existence and
    is not looked up in the resource manifest.
    """
    return cast("str", config.get("index_file", f"{config['filename']}.fai"))


def reference_genome_files(config: dict[str, Any]) -> set[str]:
    """Return all files a reference-genome resource consists of.

    The set always contains the genome FASTA and its ``.fai`` index
    (honoring the optional ``index_file`` config key). For a bgzipped
    genome (filename ending in ``.gz``/``.bgz``, in any case) htslib
    random access also needs the ``.gzi`` BGZF index, so it is included
    as well.
    """
    file_name = config["filename"]
    files = {file_name, genome_index_file(config)}
    if endswith_ci(file_name, COMPRESSED_EXTENSIONS):
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

    def __init__(self, index_filename: str) -> None:
        self._fasta: Any = None
        self._index_filename = index_filename

    def open(self, resource: GenomicResource, filename: str) -> None:
        self._fasta = resource.open_fasta_file(
            filename, self._index_filename)

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
            yield from self._fasta.fetch(chrom, pos - 1, win_end).upper()
            pos = win_end + 1


class ReferenceGenome(
    ResourceConfigValidationMixin,
):
    """Query the chromosomes and the nucleotides of a reference genome.

    The genome wraps a resource of type ``genome``. The builder returns it
    closed. Call :meth:`open` before :meth:`fetch` or :meth:`get_sequence`.
    The chromosome accessors load the index on demand, so they answer
    without :meth:`open`.

    Attributes:
        resource (GenomicResource): The ``genome`` resource that the object
            wraps.

        config (dict): The validated configuration of the resource.

        pars (dict): The pseudoautosomal regions from the ``PARS`` key of
            the configuration, as a mapping from the chromosome name to a
            list of ``Region``. The mapping is empty when the
            configuration has no ``PARS`` key.
    """

    def __init__(self, resource: GenomicResource):
        self.resource = resource
        if resource.get_type() != "genome":
            raise ValueError(
                f"wrong type of resource passed: {resource.get_type()}")
        self._index: dict[str, Any] = {}
        self._chromosomes: list[str] = []
        self._chromosome_lengths: dict[str, int] = {}

        self.config: dict[str, Any] = self.validate_and_normalize_schema(
            resource.get_config(), resource,
        )
        if "chrom_prefix" in self.config:
            warn_retired_config_key(
                logger, "chrom_prefix",
                found_in=f"Resource '{resource.get_full_id()}'",
                reason="a genome's prefix is derived from its contig names")

        filename = self.config["filename"]
        # Each backend is handed the index it needs at construction: the
        # pysam reader the .fai name to open, the raw-seek reader the
        # parsed index dict it seeks with.
        if endswith_ci(filename, COMPRESSED_EXTENSIONS):
            self._backend: _SequenceBackend = _PysamFastaSequence(
                genome_index_file(self.config))
        else:
            self._backend = _RawSeekSequence(self._index)

        self.pars: dict = self._parse_pars(self.config)

    @property
    def resource_id(self) -> str:
        """The id of the genome resource this object wraps."""
        return self.resource.resource_id

    @staticmethod
    def _parse_pars(config: dict[str, Any]) -> dict:
        if "PARS" not in config:
            return {}

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
        """The chromosome names, in the order of the ``.fai`` index."""
        self._load_genome_index()
        return self._chromosomes

    @property
    def chrom_prefix(self) -> str:
        """The prefix of the chromosome names: ``"chr"`` or ``""``.

        The value is ``"chr"`` when the first chromosome name starts with
        it, and ``""`` otherwise. A ``chrom_prefix`` key in the
        configuration does not change it.
        """
        self._load_genome_index()
        chrom = self._chromosomes[0]
        if chrom.startswith("chr"):
            return "chr"
        return ""

    def _load_genome_index(self) -> None:
        if self._index:
            return
        index_content = self.resource.get_file_content(
            genome_index_file(self.config))
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
        """Close the sequence file and drop the loaded index.

        Closing a genome that is not open does nothing.
        """
        self._backend.close()
        self._index.clear()

    def open(self) -> ReferenceGenome:
        """Open the sequence file of the genome.

        Returns
        -------
        ReferenceGenome
            The genome itself, so that the call chains onto a builder. A
            genome that is already open is returned unchanged.

        Raises
        ------
        FileNotFoundError
            If the index file or the sequence file is missing.
        """
        if self.is_open():
            logger.info(
                "opening already opened reference genome %s",
                self.resource.resource_id)
            return self

        # Parse the index first: it reads the configured .fai by name, so a
        # missing or misnamed index fails here, naming the configured file,
        # rather than deeper inside htslib.
        self._load_genome_index()

        self._backend.open(self.resource, self.config["filename"])

        return self

    def is_open(self) -> bool:
        """Whether :meth:`open` has run and :meth:`close` has not since.

        Returns
        -------
        bool
            ``True`` when the sequence file is open.
        """
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
        """Return the length of a chromosome.

        Parameters
        ----------
        chrom : str
            The name of the chromosome.

        Returns
        -------
        int
            The length of the chromosome in nucleotides.

        Raises
        ------
        ValueError
            If the genome has no chromosome with this name.
        """
        self._load_genome_index()
        if chrom not in self._chromosome_lengths:
            raise ValueError(f"can't find chromosome {chrom}")

        return self._chromosome_lengths[chrom]

    def get_all_chrom_lengths(self) -> dict[str, int]:
        """Return the lengths of all chromosomes.

        Returns
        -------
        dict[str, int]
            A mapping from the chromosome name to its length, in the
            order of the ``.fai`` index. The mapping is the internal one,
            not a copy. Do not change it.
        """
        self._load_genome_index()
        return self._chromosome_lengths

    def split_into_regions(
        self, region_size: int, chromosome: str | None = None,
    ) -> Generator[Region, None, None]:
        """Split the genome into regions of a given size.

        Parameters
        ----------
        region_size : int
            The size of each region in nucleotides. The value ``0`` yields
            one region for each chromosome.
        chromosome : str | None
            The chromosome to split. When it is ``None``, the method
            splits all chromosomes.

        Returns
        -------
        Generator[Region, None, None]
            The regions, chromosome by chromosome. The last region of each
            chromosome has ``stop`` set to ``None``, and it can be shorter
            than ``region_size``.

        Raises
        ------
        ValueError
            If ``region_size`` is negative. The call raises. If
            ``chromosome`` is not a chromosome of the genome, the
            generator raises on the first step.
        """
        # Checked here, not in the generator, so the call itself fails.
        check_region_size(region_size)
        return self._split_into_regions(region_size, chromosome)

    def _split_into_regions(
        self, region_size: int, chromosome: str | None,
    ) -> Generator[Region, None, None]:
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
        """Yield the nucleotides of a region, one character at a time.

        The coordinates are 1-based and both ends are included. The genome
        must be open.

        Parameters
        ----------
        chrom : str
            The name of the chromosome.
        start : int
            The first position of the region.
        stop : int | None
            The last position of the region. A value past the end of the
            chromosome is cut to the end. When it is ``None``, the region
            runs to the end of the chromosome.
        buffer_size : int
            The number of characters that the reader takes from the file
            in one step.

        Returns
        -------
        Generator[str, None, None]
            The nucleotides in upper case. The generator is empty when
            the genome has no chromosome with this name. In that case a
            warning is logged.
        """
        # The count of line feeds is not exact, because a fetch does not
        # always start at the start of a line. The line feeds only add
        # characters to read. The output is limited by the number of
        # nucleotides that the region expects.
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
        """Return the nucleotides of a region as one string.

        The coordinates are 1-based and both ends are included. The genome
        must be open.

        Parameters
        ----------
        chrom : str
            The name of the chromosome.
        start : int
            The first position of the region.
        stop : int
            The last position of the region. A value past the end of the
            chromosome is cut to the end.

        Returns
        -------
        str
            The nucleotides in upper case. The string is empty when the
            genome has no chromosome with this name.
        """
        return "".join(self.fetch(chrom, start, stop))

    def is_pseudoautosomal(self, chrom: str, pos: int) -> bool:
        """Tell whether a position is in a pseudoautosomal region.

        Parameters
        ----------
        chrom : str
            The name of the chromosome.
        pos : int
            The 1-based position.

        Returns
        -------
        bool
            ``True`` when the position is in a region of ``pars``. The
            value is ``False`` for a chromosome without such regions, and
            for every position when the configuration has no ``PARS`` key.
        """
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
        """Return the schema that the config of a genome is checked against.

        Returns
        -------
        dict[str, Any]
            The base resource schema, with the keys ``filename``,
            ``index_file``, ``PARS`` and ``chrom_prefix``.
        """
        # The retired ``chrom_prefix`` key is still accepted, and the
        # constructor announces it, until ``LEGACY_VOCABULARY_REMOVAL_RELEASE``.
        return {
            **get_base_resource_schema(),
            **get_required_filename_schema(),
            # Not nullable: only a MISSING key defaults to
            # `<filename>.fai`, so `index_file: ~` would reach
            # `get_file_content` as None.
            "index_file": {"type": "string"},
            "chrom_prefix": {"type": "string", "nullable": True},
            # `_parse_pars` subscripts both keys, and branches on Y being
            # None for an assembly whose Y carries no PARS.
            "PARS": {"type": "dict", "schema": {
                "X": {
                    "type": "list", "schema": {"type": "string"},
                    "required": True,
                },
                "Y": {
                    "type": "list", "schema": {"type": "string"},
                    "required": True, "nullable": True,
                },
            }},
        }


def build_reference_genome_from_file(filename: str) -> ReferenceGenome:
    """Build a reference genome from a FASTA file.

    The function wraps the file in a local resource. The genome is closed.

    Parameters
    ----------
    filename : str
        The path of the FASTA file. The index file ``<filename>.fai`` must
        be next to it.

    Returns
    -------
    ReferenceGenome
        The closed genome. Call ``open()`` before any sequence query.
    """
    dirname = os.path.dirname(filename)
    basename = os.path.basename(filename)
    res = build_local_resource(dirname, {
        "type": "genome",
        "filename": basename,
    })
    return build_reference_genome_from_resource(res)


def build_reference_genome_from_resource(
        resource: GenomicResource) -> ReferenceGenome:
    """Build the reference genome of a genomic resource.

    Parameters
    ----------
    resource : GenomicResource
        A resource of type ``genome``.

    Returns
    -------
    ReferenceGenome
        The closed genome. Call ``open()`` before any sequence query.

    Raises
    ------
    ValueError
        If the resource is not of type ``genome``.
    """
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
    """Build the reference genome of the resource with a given id.

    Parameters
    ----------
    resource_id : str
        The id of a resource of type ``genome`` in the repository.
    grr : GenomicResourceRepo | None
        The repository that resolves ``resource_id``. When it is
        ``None``, the function builds the default repository with
        ``build_genomic_resource_repository()``.

    Returns
    -------
    ReferenceGenome
        The closed genome. Call ``open()`` before any sequence query.

    Raises
    ------
    FileNotFoundError
        If the repository has no resource with this id.
    ValueError
        If the resource is not of type ``genome``.
    """
    if grr is None:
        grr = build_genomic_resource_repository()
    return build_reference_genome_from_resource(
        grr.get_resource(resource_id))
