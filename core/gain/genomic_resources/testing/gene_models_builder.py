"""Fluent, immutable test-data builder for ``gene_models`` resources.

A sibling of :mod:`gain.genomic_resources.testing.builders` for the same
reason :mod:`.data_frame_builder` and :mod:`.ann_data_builder` are ones:
``builders`` is already past pylint's ``max-module-lines`` and carries a
``too-many-lines`` suppression to say so, so a new builder there would
be growing a module that is over the limit rather than finding room in
one.  (Both siblings describe ``builders`` as sitting AT the ceiling;
that stopped being true when the suppression went in.)  The
dependency runs ONE WAY -- this
module imports the shared single-realize seam from ``builders`` and
``builders`` does not import back -- so ``a_gene_models`` is imported from
here.

The axis this builder exists to vary is the INTERCHANGE FORMAT.  A
``gene_models`` resource names its format in the config, and each of the
seven formats gain parses spells the same transcript differently -- in
different columns, in different coordinate conventions, and in the
columnar family across two different half-open bounds.  A test that wants
"the same genes, read through another format" therefore had to hand-roll a
second file and a second config and keep the two in step by eye.  Here the
transcripts are authored ONCE, in gain's own coordinates, and
:meth:`GeneModelsBuilder.with_format` decides how they are written down;
the emitted config always names the format the data was actually rendered
in, so the two cannot drift.

Coordinates are gain's throughout the builder's interface: 1-based and
inclusive on both ends, the convention :class:`Exon` documents and every
parser converts to.  The half-open shift the UCSC-derived formats need is
applied by the renderer, not by the test author.

Like the other builders here, this exposes NO expected ``GeneModels``: a
test states what it expects the parse to be.  Handing back a model built
from the same records would check the builder against itself on exactly
the axis a gene-models test varies.
"""
from __future__ import annotations

import dataclasses
import pathlib
from collections.abc import Callable
from itertools import starmap

from gain.genomic_resources.gene_models.transcript_models import (
    Exon,
    TranscriptModel,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing import (
    setup_empty_gene_models,
    setup_gene_models,
)
from gain.genomic_resources.testing.builders import _build_single_resource
from gain.genomic_resources.testing.resource_meta import MetaMixin
from gain.genomic_resources.testing.score_specs import (
    ResourceValidationError,
)

#: How one format is written down: authored transcripts in, file text
#: out.  One per entry of ``_FORMATS``.
_Renderer = Callable[[tuple["TranscriptSpec", ...]], str]


@dataclasses.dataclass(frozen=True)
class _FormatSpec:
    """What one interchange format needs in order to be realized."""

    render: _Renderer
    filename: str


#: What a builder writes when no format is asked for.  refFlat is the
#: format the hand-rolled gene-models fixtures in ``core/tests`` are
#: overwhelmingly written in, so it is the least surprising default.
_DEFAULT_FORMAT = "refflat"

#: The format ``setup_empty_gene_models`` writes its header in, and so
#: the only one the empty case can be realized in.
_EMPTY_FORMAT = "refflat"

#: The chromosome an authored transcript lands on unless it says
#: otherwise -- the same one the default transcripts use, so that a
#: builder mixing the two still describes one chromosome.
_DEFAULT_CHROM = "chr1"

#: A well-formed statement of "no attributes" for gain's own format --
#: see :func:`_render_default` for why it is not simply an empty cell.
_EMPTY_ATTRIBUTES = ";"


@dataclasses.dataclass(frozen=True)
class TranscriptSpec:
    """One authored transcript, in gain's 1-based inclusive coordinates.

    ``cds`` is the coding interval; ``None`` means a non-coding
    transcript, which each format spells its own way.
    """

    tr_name: str
    gene: str
    chrom: str
    strand: str
    exons: tuple[tuple[int, int], ...]
    cds: tuple[int, int] | None

    @property
    def tx(self) -> tuple[int, int]:
        """The transcript bounds -- the span of its exons."""
        return (self.exons[0][0], self.exons[-1][1])


#: The transcripts a bare builder realizes: one two-exon coding transcript
#: per strand, on one chromosome, with the coding interval starting and
#: ending inside an exon so that every exon frame is exercised.
_DEFAULT_TRANSCRIPTS = (
    TranscriptSpec(
        tr_name="tx1", gene="G1", chrom="chr1", strand="+",
        exons=((11, 20), (31, 45)), cds=(14, 40)),
    TranscriptSpec(
        tr_name="tx2", gene="G2", chrom="chr1", strand="-",
        exons=((101, 110), (121, 135)), cds=(104, 130)),
)


@dataclasses.dataclass(frozen=True)
class GeneModelsBuilder(MetaMixin):
    """Immutable builder for a single ``gene_models`` resource."""

    transcripts: tuple[TranscriptSpec, ...] = ()
    fileformat: str = _DEFAULT_FORMAT
    no_genes: bool = False

    def with_transcript(
        self, tr_name: str, *,
        exons: list[tuple[int, int]],
        gene: str | None = None,
        chrom: str = _DEFAULT_CHROM,
        strand: str = "+",
        cds: tuple[int, int] | None = None,
    ) -> GeneModelsBuilder:
        """Author one transcript, replacing the default transcripts.

        Coordinates -- ``exons`` and ``cds`` alike -- are gain's: 1-based
        and inclusive at both ends, whatever format they end up written
        in.  ``gene`` defaults to the transcript name, and ``cds``
        omitted means a non-coding transcript.
        """
        _validate_transcript(
            tr_name, gene, chrom,
            tuple(exons), cds,
            already_named=tuple(
                spec.tr_name for spec in self.transcripts),
        )
        return dataclasses.replace(
            self,
            transcripts=(*self.transcripts, TranscriptSpec(
                tr_name=tr_name,
                gene=tr_name if gene is None else gene,
                chrom=chrom,
                strand=strand,
                exons=tuple(exons),
                cds=cds,
            )),
        )

    def with_format(self, fileformat: str) -> GeneModelsBuilder:
        """Select the interchange format the transcripts are written in.

        An unknown name is refused here, where the caller named it.  A
        format that clashes with :meth:`with_no_genes` is not: that is a
        conflict between two authoring modes, and it is reported when
        the resource is realized -- see :meth:`_effective_content`.
        """
        if fileformat not in _FORMATS:
            raise ResourceValidationError(
                f"unknown gene models format {fileformat!r}; "
                f"expected one of {sorted(_FORMATS)}")
        return dataclasses.replace(self, fileformat=fileformat)

    def with_no_genes(self) -> GeneModelsBuilder:
        """Build a resource whose gene models are empty.

        The second authoring mode, and mutually exclusive with the
        first: realized by ``setup_empty_gene_models``, which writes a
        refFlat header line and no records.  It therefore combines with
        neither an authored transcript nor another format, and says so
        when the resource is realized rather than ignoring one.
        """
        return dataclasses.replace(self, no_genes=True)

    def realize_into(self, resource_dir: pathlib.Path) -> None:
        """Write this gene-models resource into ``resource_dir``.

        Raises a ``ResourceValidationError`` on two authoring modes at
        once; ``GRRBuilder`` annotates it with the resource id.
        """
        filename, content = self._effective_content()
        if content is None:
            setup_empty_gene_models(resource_dir / filename)
        else:
            setup_gene_models(
                resource_dir / filename, content,
                fileformat=self.fileformat,
            )
        self.append_meta_into(resource_dir)

    def _effective_content(self) -> tuple[str, str | None]:
        """The filename and file text to realize; ``None`` for the empty case.

        Where the two authoring modes are reconciled, for the reason
        ``ReferenceGenomeBuilder._effective_fasta`` reconciles its own
        pair here rather than in the ``with_*`` methods: raised from
        ``realize_into``, the error passes through ``GRRBuilder``, which
        annotates it with the resource id that carries the conflict.  A
        ``with_*`` method cannot be reached through that seam -- the
        builder is still being assembled -- so an eager raise would
        forfeit the annotation.
        """
        if not self.no_genes:
            transcripts = self.transcripts or _DEFAULT_TRANSCRIPTS
            spec = _FORMATS[self.fileformat]
            return spec.filename, spec.render(transcripts)
        if self.transcripts:
            raise ResourceValidationError(
                "with_no_genes and with_transcript are mutually "
                "exclusive; set only one authoring mode")
        if self.fileformat != _EMPTY_FORMAT:
            raise ResourceValidationError(
                f"with_no_genes realizes a {_EMPTY_FORMAT} header and "
                f"cannot honour format {self.fileformat!r}")
        return _FORMATS[_EMPTY_FORMAT].filename, None

    def build_resource(
        self, tmp_path: pathlib.Path,
    ) -> GenomicResource:
        """Realize this single resource (repo id ``""``) into ``tmp_path``."""
        return _build_single_resource(self, tmp_path)


#: The genePred core: the ten columns every UCSC-derived format here is
#: built out of, and the only ones carrying a transcript's coordinates.
#: The four formats below differ from each other by what they put around
#: this, and by nothing else.
_GENEPRED_CORE_COLUMNS = (
    "name", "chrom", "strand", "txStart", "txEnd", "cdsStart", "cdsEnd",
    "exonCount", "exonStarts", "exonEnds",
)

#: What genePredExt adds to the core, and what refSeq/CCDS carry at the
#: same place: an annotation block that every parser copies verbatim into
#: the transcript's attributes.
_GENEPRED_EXT_COLUMNS = (
    "score", "name2", "cdsStartStat", "cdsEndStat", "exonFrames",
)


def _genepred_core_cells(spec: TranscriptSpec) -> tuple[str, ...]:
    """Render one transcript into the ten genePred core columns.

    This is where the half-open shift lives: UCSC's start columns --
    ``txStart``, ``cdsStart`` and each ``exonStarts`` entry -- are
    0-based, while the end columns are the inclusive end read as-is.
    """
    tx_start, tx_end = spec.tx
    cds_start, cds_end = _coding_bounds(spec)
    return (
        spec.tr_name, spec.chrom, spec.strand,
        str(tx_start - 1), str(tx_end),
        str(cds_start - 1), str(cds_end),
        str(len(spec.exons)),
        ",".join(str(start - 1) for start, _ in spec.exons),
        ",".join(str(stop) for _, stop in spec.exons),
    )


def _genepred_ext_cells(spec: TranscriptSpec) -> tuple[str, ...]:
    """Render the genePredExt annotation block.

    Written identically for refSeq, CCDS and genePredExt, which all
    carry these five columns -- but read back differently, because each
    layout names its own attribute columns.  ``name2`` is the one cell
    here that is not deterministic filler, and what it means depends on
    the layout reading it: genePredExt takes the gene label from it AND
    copies it into the attributes, refSeq takes the gene label from it
    and does not, and CCDS -- reading the same file -- takes its gene
    label from the transcript name and never looks at this column at
    all.  ``exonFrames`` is recomputed by every reader.
    """
    return (
        "0", spec.gene, "cmpl", "cmpl", _exon_frames(spec),
    )


def _render_columnar(
    header: tuple[str, ...],
    cells: Callable[[TranscriptSpec], tuple[str, ...]],
    transcripts: tuple[TranscriptSpec, ...],
) -> str:
    """Render a UCSC-derived table: one header line, one line per record."""
    lines = ["\t".join(header)]
    lines.extend("\t".join(cells(spec)) for spec in transcripts)
    return "\n".join(lines)


def _render_refflat(transcripts: tuple[TranscriptSpec, ...]) -> str:
    """Render transcripts as a refFlat table -- genePred plus a gene name."""
    return _render_columnar(
        ("#geneName", *_GENEPRED_CORE_COLUMNS),
        lambda spec: (spec.gene, *_genepred_core_cells(spec)),
        transcripts,
    )


def _render_refseq(transcripts: tuple[TranscriptSpec, ...]) -> str:
    """Render transcripts as a refSeq table.

    Also the CCDS renderer: the two formats declare the same sixteen
    columns and differ only in which of them the gene label is read from,
    so the file is the same and the config is what tells them apart.
    """
    return _render_columnar(
        ("#bin", *_GENEPRED_CORE_COLUMNS, *_GENEPRED_EXT_COLUMNS),
        lambda spec: (
            "0", *_genepred_core_cells(spec), *_genepred_ext_cells(spec)),
        transcripts,
    )


def _render_knowngene(transcripts: tuple[TranscriptSpec, ...]) -> str:
    """Render transcripts as a knownGene table -- the core plus two ids."""
    return _render_columnar(
        (*_GENEPRED_CORE_COLUMNS, "proteinID", "alignID"),
        lambda spec: (
            *_genepred_core_cells(spec), spec.tr_name, spec.tr_name),
        transcripts,
    )


def _render_ucscgenepred(transcripts: tuple[TranscriptSpec, ...]) -> str:
    """Render transcripts as a UCSC genePredExt table.

    The wide of the format's two accepted widths, because the narrow one
    has no alternate-name column at all and so cannot state a gene label
    distinct from the transcript name.
    """
    return _render_columnar(
        (*_GENEPRED_CORE_COLUMNS, *_GENEPRED_EXT_COLUMNS),
        lambda spec: (
            *_genepred_core_cells(spec), *_genepred_ext_cells(spec)),
        transcripts,
    )


def _render_default(transcripts: tuple[TranscriptSpec, ...]) -> str:
    """Render transcripts in gain's own gene-models format.

    Read by column name and already in gain's coordinates, so nothing is
    shifted here.  Two columns are worth naming.  ``exonFrames`` is
    shared with the wide columnar layouts, but this is the only format
    that READS it back out of the file rather than recomputing it, which
    is what makes deriving it correctly here load-bearing -- see
    :func:`_exon_frames`.  ``atts`` is this format's alone, and is
    written as a lone separator: an empty cell would be swallowed by the
    tab conversion the ``setup_*`` helpers apply, while ``";"`` is a
    well-formed statement of no attributes that survives it.
    """
    lines = [
        "\t".join((
            "chr", "trID", "trOrigId", "gene", "strand", "tsBeg", "txEnd",
            "cdsStart", "cdsEnd", "exonStarts", "exonEnds", "exonFrames",
            "atts")),
    ]
    for spec in transcripts:
        tx_start, tx_end = spec.tx
        cds_start, cds_end = _coding_bounds(spec)
        lines.append("\t".join((
            spec.chrom, spec.tr_name, spec.tr_name, spec.gene, spec.strand,
            str(tx_start), str(tx_end),
            str(cds_start), str(cds_end),
            ",".join(str(start) for start, _ in spec.exons),
            ",".join(str(stop) for _, stop in spec.exons),
            _exon_frames(spec),
            _EMPTY_ATTRIBUTES,
        )))
    return "\n".join(lines)


def _render_gtf(transcripts: tuple[TranscriptSpec, ...]) -> str:
    """Render transcripts as GTF.

    The one format that spreads a transcript over several records: a
    ``transcript`` record carrying the bounds, an ``exon`` record each,
    and a ``CDS`` record per coding stretch of an exon.  Coordinates are
    already gain's -- GTF is 1-based and inclusive -- and the ``phase``
    column is left unstated because the reader derives the frames.

    Spaces inside the attributes column are written as ``||``: the
    ``setup_*`` helpers collapse whitespace runs to TABs, and ``||`` is
    the escape they restore a literal space from.
    """
    lines = []
    for spec in transcripts:
        tx_start, tx_end = spec.tx
        attributes = "||".join((
            f'gene_id||"{spec.gene}";',
            f'transcript_id||"{spec.tr_name}";',
            f'gene_name||"{spec.gene}";',
        ))
        lines.append(_gtf_record(
            spec, "transcript", tx_start, tx_end, attributes))
        for start, stop in spec.exons:
            lines.append(_gtf_record(spec, "exon", start, stop, attributes))
        if spec.cds is None:
            continue
        cds_start, cds_end = spec.cds
        for start, stop in spec.exons:
            coding_start, coding_stop = (
                max(start, cds_start), min(stop, cds_end))
            if coding_start > coding_stop:
                continue
            lines.append(_gtf_record(
                spec, "CDS", coding_start, coding_stop, attributes))
    return "\n".join(lines)


def _gtf_record(
    spec: TranscriptSpec, feature: str, start: int, stop: int,
    attributes: str,
) -> str:
    """Render one GTF record of ``spec``."""
    return "\t".join((
        spec.chrom, "gain_test", feature, str(start), str(stop), ".",
        spec.strand, ".", attributes,
    ))


def _validate_transcript(
    tr_name: str, gene: str | None, chrom: str,
    exons: tuple[tuple[int, int], ...],
    cds: tuple[int, int] | None,
    already_named: tuple[str, ...],
) -> None:
    """Refuse a transcript the seven formats could not state alike."""
    for field, value in (
            ("tr_name", tr_name), ("gene", gene), ("chrom", chrom)):
        if value is not None:
            _validate_writable_text(tr_name, field, value)
    if tr_name in already_named:
        raise ResourceValidationError(
            f"transcript {tr_name!r} is authored twice; the formats do "
            f"not agree on what that means -- refFlat suffixes the two "
            f"records apart, gain's own format keeps only the last, and "
            f"GTF refuses the file -- so give them distinct names")
    _validate_exons(tr_name, exons)
    _validate_cds(tr_name, exons, cds)


#: Substrings the ``setup_*`` helpers give a meaning of their own:
#: ``convert_to_tab_separated`` collapses whitespace runs to TABs, then
#: rewrites ``||`` as a space and ``EMPTY`` as a dot.  A label carrying
#: any of them is not written down as itself.
_UNWRITABLE_SUBSTRINGS = ("||", "EMPTY")


def _validate_writable_text(tr_name: str, field: str, value: str) -> None:
    """Refuse a label the tab conversion would not write down as itself.

    A space in a label does not merely look wrong on the way out: the
    conversion turns it into a column separator, so the record silently
    gains a column and every field after it shifts.
    """
    if not value:
        raise ResourceValidationError(
            f"transcript {tr_name!r} has an empty {field}")
    if value.split() != [value]:
        raise ResourceValidationError(
            f"transcript {tr_name!r} has a {field} of {value!r} carrying "
            f"whitespace; the tab conversion would write it as a column "
            f"separator and shift the whole record")
    for substring in _UNWRITABLE_SUBSTRINGS:
        if substring in value:
            raise ResourceValidationError(
                f"transcript {tr_name!r} has a {field} of {value!r} "
                f"carrying {substring!r}, which the tab conversion "
                f"rewrites; it would not be read back as itself")


def _validate_exons(
    tr_name: str, exons: tuple[tuple[int, int], ...],
) -> None:
    """Refuse an exon set no format could state.

    Ascending order is not a formality: the reading frames are computed
    by walking the exons in the order they are given, and every format
    here writes them in that order, so an unsorted set would be written
    out and read back as a different transcript than the one authored.
    """
    if not exons:
        raise ResourceValidationError(
            f"transcript {tr_name!r} has no exons; a transcript is "
            f"written down as its exons, so it needs at least one")
    bounds = [bound for exon in exons for bound in exon]
    if bounds != sorted(bounds):
        raise ResourceValidationError(
            f"transcript {tr_name!r} has exons {list(exons)} that are not "
            f"in ascending, non-overlapping order")


def _validate_cds(
    tr_name: str, exons: tuple[tuple[int, int], ...],
    cds: tuple[int, int] | None,
) -> None:
    """Refuse a coding interval the formats would not agree on.

    GTF states the CDS only through per-exon ``CDS`` records, so a bound
    lying in an intron -- or outside the transcript -- is read back as
    the nearest exon edge there, while the columnar formats and gain's
    own carry the authored number.  Rather than let the same transcript
    mean two things, the builder asks for a coding interval that both
    bounds fall inside an exon of.
    """
    if cds is None:
        return
    cds_start, cds_end = cds
    if cds_start > cds_end:
        raise ResourceValidationError(
            f"transcript {tr_name!r} has an empty cds {cds}; omit cds "
            f"altogether for a non-coding transcript")
    for bound in cds:
        if not any(start <= bound <= stop for start, stop in exons):
            raise ResourceValidationError(
                f"transcript {tr_name!r} has a cds bound {bound} outside "
                f"its exons {list(exons)}; GTF can only state a coding "
                f"interval through its exons, so the formats would "
                f"disagree on where the cds is")


def _coding_bounds(spec: TranscriptSpec) -> tuple[int, int]:
    """The coding interval to write, non-coding included.

    A non-coding transcript is spelled the way UCSC spells it -- an empty
    coding interval placed at the transcript's end -- which every format
    here writes the same way and every parser reads back as a ``cds``
    whose start is past its end.
    """
    if spec.cds is not None:
        return spec.cds
    tx_end = spec.tx[1]
    return (tx_end + 1, tx_end)


def _exon_frames(spec: TranscriptSpec) -> str:
    """Pack the exon reading frames for the formats that carry them.

    The frames are DERIVED, not authored: they are a function of the
    coding interval, the exons and the strand, and gain computes them
    with ``TranscriptModel.update_frames`` for every format that does not
    carry them.  Deriving them the same way here is what lets a
    ``default``-format file -- the one format read straight out of the
    column -- agree with what the same transcripts parse to in any
    other format.
    """
    cds_start, cds_end = _coding_bounds(spec)
    transcript_model = TranscriptModel(
        gene=spec.gene,
        tr_id=spec.tr_name,
        tr_name=spec.tr_name,
        chrom=spec.chrom,
        strand=spec.strand,
        tx=spec.tx,
        cds=(cds_start, cds_end),
        exons=list(starmap(Exon, spec.exons)),
    )
    transcript_model.update_frames()
    return ",".join(str(exon.frame) for exon in transcript_model.exons)


#: The realized filename shared by the six tab-separated formats, which
#: published GRR resources spell ``.txt`` whichever of them they are in.
_TABLE_FILENAME = "genes.txt"


#: Everything that differs between one interchange format and the next:
#: how a transcript is written down, and what the file is called.  One
#: entry per format, so a format cannot be offered without a renderer
#: behind it, nor acquire an extension of its own without saying so
#: here.
#:
#: The keys are the format names a ``gene_models`` config may carry --
#: spelled out here rather than read from ``gene_models.parsers``, so
#: that a format renamed or dropped there fails these tests loudly
#: instead of silently narrowing what the builder covers.  The same
#: choice, for the same reason, is made by
#: ``tests/small/genomic_resources/gene_models/columnar_formats.py``,
#: which spells the parsers' column layouts out a third time.
_FORMATS: dict[str, _FormatSpec] = {
    "default": _FormatSpec(_render_default, _TABLE_FILENAME),
    "refflat": _FormatSpec(_render_refflat, _TABLE_FILENAME),
    "refseq": _FormatSpec(_render_refseq, _TABLE_FILENAME),
    "ccds": _FormatSpec(_render_refseq, _TABLE_FILENAME),
    "knowngene": _FormatSpec(_render_knowngene, _TABLE_FILENAME),
    "ucscgenepred": _FormatSpec(_render_ucscgenepred, _TABLE_FILENAME),
    "gtf": _FormatSpec(_render_gtf, "genes.gtf"),
}


#: Every interchange format this builder can write, for a test that wants
#: to run over all of them.
GENE_MODELS_FORMATS: frozenset[str] = frozenset(_FORMATS)


def a_gene_models() -> GeneModelsBuilder:
    """Return an immutable gene-models builder."""
    return GeneModelsBuilder()
