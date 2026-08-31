# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pathlib

import pytest
from gain.genomic_resources.gene_models.gene_models import GeneModels
from gain.genomic_resources.gene_models.gene_models_factory import (
    build_gene_models_from_resource,
)
from gain.genomic_resources.gene_models.parsers import (
    SUPPORTED_GENE_MODELS_FILE_FORMATS,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing.builders import (
    ResourceBuilder,
    a_grr,
    a_reference_genome,
    build_resource_tempdir,
)
from gain.genomic_resources.testing.gene_models_builder import (
    GENE_MODELS_FORMATS,
    ResourceValidationError,
    a_gene_models,
)

#: What the two transcripts a bare builder authors must parse to, in
#: whichever format they were written down.  Stated here rather than
#: taken from one format's parse, so that a renderer cannot agree with
#: another renderer's mistake.  The frames follow
#: ``TranscriptModel.calc_frames``: exon 1 of ``G1`` opens the coding
#: sequence at frame 0 and leaves 7 coding bases, so exon 2 starts at
#: frame 1; ``G2`` is the same run read right-to-left.
EXPECTED_TRANSCRIPTS = {
    "G1": {
        "chrom": "chr1",
        "strand": "+",
        "tx": (11, 45),
        "cds": (14, 40),
        "exons": [(11, 20, 0), (31, 45, 1)],
    },
    "G2": {
        "chrom": "chr1",
        "strand": "-",
        "tx": (101, 135),
        "cds": (104, 130),
        "exons": [(101, 110, 1), (121, 135, 0)],
    },
}

#: The formats whose columns can carry a gene label distinct from the
#: transcript name.  ``ccds`` and ``knowngene`` cannot -- see
#: ``test_the_one_name_formats_label_genes_with_the_transcript_name``.
GENE_LABELLING_FORMATS = [
    "default", "refflat", "refseq", "ucscgenepred", "gtf",
]


def parsed_transcripts(gene_models: GeneModels) -> dict[str, dict]:
    """Reduce parsed models to the facts every format states alike."""
    return {
        gene: {
            "chrom": transcript.chrom,
            "strand": transcript.strand,
            "tx": transcript.tx,
            "cds": transcript.cds,
            "exons": [
                (exon.start, exon.stop, exon.frame)
                for exon in transcript.exons
            ],
        }
        for gene in gene_models.gene_names()
        for transcript in gene_models.gene_models_by_gene_name(gene)
    }


def loaded(resource: GenomicResource) -> GeneModels:
    return build_gene_models_from_resource(resource).load()


def test_a_bare_gene_models_is_readable_with_default_genes(
    tmp_path: pathlib.Path,
) -> None:
    resource = a_gene_models().build_resource(tmp_path)

    assert resource.get_type() == "gene_models"

    gene_models = build_gene_models_from_resource(resource).load()

    assert gene_models.gene_names()
    assert gene_models.transcript_models


def test_the_default_transcripts_read_back_at_their_authored_bounds(
    tmp_path: pathlib.Path,
) -> None:
    """The builder's coordinates are gain's: 1-based, inclusive.

    The refFlat file the bare builder writes is half-open, so this is
    what says the renderer applied that shift and applied it once.
    """
    resource = a_gene_models().build_resource(tmp_path)

    gene_models = build_gene_models_from_resource(resource).load()

    assert sorted(gene_models.gene_names()) == ["G1", "G2"]
    transcript = gene_models.gene_models_by_gene_name("G1")[0]
    assert transcript.chrom == "chr1"
    assert transcript.strand == "+"
    assert transcript.tx == (11, 45)
    assert transcript.cds == (14, 40)
    assert [(exon.start, exon.stop) for exon in transcript.exons] == [
        (11, 20), (31, 45)]


@pytest.mark.parametrize("fileformat", GENE_LABELLING_FORMATS)
def test_every_format_states_the_same_transcripts(
    tmp_path: pathlib.Path, fileformat: str,
) -> None:
    """One authored transcript set, five ways of writing it down.

    Each format is read back through ``GeneModels`` and compared against
    the SAME hand-written expectation -- not against another format's
    parse -- so a renderer that agrees with its own mistake still fails.
    The other two registered formats reach the same expectation under a
    different gene label; see the sibling test.
    """
    resource = (
        a_gene_models().with_format(fileformat).build_resource(tmp_path)
    )

    assert parsed_transcripts(loaded(resource)) == EXPECTED_TRANSCRIPTS


@pytest.mark.parametrize("fileformat", ["ccds", "knowngene"])
def test_the_one_name_formats_label_genes_with_the_transcript_name(
    tmp_path: pathlib.Path, fileformat: str,
) -> None:
    """Two formats have one name column, and it is the gene label.

    A CCDS record's gene and transcript name are the same string, and
    knownGene has no alternate-name column at all -- so the gene the
    builder authored cannot survive being written in either.  The
    coordinates still do, which is what makes them usable at all.
    """
    resource = (
        a_gene_models().with_format(fileformat).build_resource(tmp_path)
    )

    gene_models = loaded(resource)

    assert sorted(gene_models.gene_names()) == ["tx1", "tx2"]
    assert parsed_transcripts(gene_models) == {
        "tx1": EXPECTED_TRANSCRIPTS["G1"],
        "tx2": EXPECTED_TRANSCRIPTS["G2"],
    }


def test_authored_transcripts_replace_the_defaults(
    tmp_path: pathlib.Path,
) -> None:
    resource = (
        a_gene_models()
        .with_transcript(
            "NM_1", gene="ALPHA", chrom="chr2", strand="-",
            exons=[(100, 120), (200, 260)], cds=(110, 250))
        .build_resource(tmp_path)
    )

    gene_models = loaded(resource)

    assert gene_models.gene_names() == ["ALPHA"]
    transcript = gene_models.gene_models_by_gene_name("ALPHA")[0]
    assert transcript.tr_name == "NM_1"
    assert transcript.chrom == "chr2"
    assert transcript.strand == "-"
    assert transcript.tx == (100, 260)
    assert transcript.cds == (110, 250)
    assert [(exon.start, exon.stop) for exon in transcript.exons] == [
        (100, 120), (200, 260)]


def test_transcripts_accumulate_across_calls(
    tmp_path: pathlib.Path,
) -> None:
    resource = (
        a_gene_models()
        .with_transcript("tx_a", gene="ALPHA", exons=[(10, 20)])
        .with_transcript("tx_b", gene="BETA", exons=[(50, 60)])
        .build_resource(tmp_path)
    )

    gene_models = loaded(resource)

    assert sorted(gene_models.gene_names()) == ["ALPHA", "BETA"]


def test_with_no_genes_builds_an_empty_but_readable_resource(
    tmp_path: pathlib.Path,
) -> None:
    resource = a_gene_models().with_no_genes().build_resource(tmp_path)

    assert resource.get_type() == "gene_models"

    gene_models = loaded(resource)

    assert gene_models.gene_names() == []
    assert gene_models.transcript_models == {}


def test_with_no_genes_refuses_a_format_it_cannot_honour(
    tmp_path: pathlib.Path,
) -> None:
    """The empty case is realized as a refFlat header and nothing else.

    Silently ignoring the format a test asked for would be worse than
    saying the two cannot be combined.  Reported when the resource is
    realized, like every other two-authoring-modes conflict here.
    """
    with pytest.raises(ResourceValidationError, match="cannot honour"):
        a_gene_models().with_format("gtf").with_no_genes() \
            .build_resource(tmp_path)


def test_with_no_genes_refuses_authored_transcripts(
    tmp_path: pathlib.Path,
) -> None:
    with pytest.raises(
            ResourceValidationError, match="mutually exclusive"):
        (
            a_gene_models()
            .with_transcript("tx_a", gene="ALPHA", exons=[(10, 20)])
            .with_no_genes()
            .build_resource(tmp_path)
        )


def test_a_conflict_names_the_resource_it_came_from(
    tmp_path: pathlib.Path,
) -> None:
    """Which is why the conflict waits for realize time to be reported.

    ``GRRBuilder`` annotates a ``ResourceValidationError`` raised out of
    ``realize_into`` with the id of the resource that carried it.  A
    ``with_*`` method is called while the builder is still being
    assembled, before any id exists, so raising there would forfeit the
    annotation -- the same reason ``ReferenceGenomeBuilder`` defers its
    own two-authoring-modes conflict.
    """
    repo_builder = (
        a_grr()
        .with_resource("genome", a_reference_genome())
        .with_resource(
            "genes/empty",
            a_gene_models().with_format("gtf").with_no_genes())
    )

    with pytest.raises(ResourceValidationError, match="'genes/empty'"):
        repo_builder.build_repo(tmp_path)


@pytest.mark.parametrize("fileformat", sorted(GENE_MODELS_FORMATS))
def test_the_emitted_config_names_the_format_the_data_is_in(
    tmp_path: pathlib.Path, fileformat: str,
) -> None:
    """The config's ``format:`` is never a fabricated ``"None"``.

    ``setup_gene_models`` interpolates its ``fileformat`` argument into
    the config it writes, so leaving it unset emits the literal string
    ``"None"`` -- a format no parser is registered under.  The builder
    always states one.
    """
    resource = (
        a_gene_models().with_format(fileformat).build_resource(tmp_path)
    )

    config = resource.get_config()

    assert config["format"] == fileformat
    assert config["filename"] == (
        "genes.gtf" if fileformat == "gtf" else "genes.txt")
    assert config["filename"] in resource.get_manifest().names()


def test_builders_are_immutable_no_cross_variation_leak(
    tmp_path: pathlib.Path,
) -> None:
    base = a_gene_models().with_transcript(
        "tx_a", gene="ALPHA", exons=[(10, 20)])

    refflat = base.with_format("refflat")
    gtf = base.with_format("gtf")
    extended = base.with_transcript("tx_b", gene="BETA", exons=[(50, 60)])

    assert base.fileformat == "refflat"
    assert gtf.fileformat == "gtf"
    assert len(base.transcripts) == 1
    assert len(extended.transcripts) == 2
    assert sorted(loaded(
        refflat.build_resource(tmp_path / "refflat")).gene_names()) == [
        "ALPHA"]
    assert sorted(loaded(
        extended.build_resource(tmp_path / "extended")).gene_names()) == [
        "ALPHA", "BETA"]


def test_gene_models_compose_into_a_grr_with_other_resource_types(
    tmp_path: pathlib.Path,
) -> None:
    repo = (
        a_grr()
        .with_resource("genome", a_reference_genome())
        .with_resource("genes", a_gene_models())
        .build_repo(tmp_path)
    )

    assert repo.get_resource("genes").get_type() == "gene_models"
    assert loaded(repo.get_resource("genes")).gene_names() == ["G1", "G2"]


def test_gene_models_meta_reads_back_through_the_resource(
    tmp_path: pathlib.Path,
) -> None:
    resource = (
        a_gene_models()
        .with_meta(summary="two toy genes")
        .with_labels(reference_genome="chr1_genome")
        .build_resource(tmp_path)
    )

    assert resource.get_summary() == "two toy genes"
    assert resource.get_labels() == {"reference_genome": "chr1_genome"}
    assert loaded(resource).gene_names() == ["G1", "G2"]


@pytest.mark.parametrize("fileformat", sorted(GENE_MODELS_FORMATS))
def test_a_transcript_authored_without_a_cds_is_non_coding(
    tmp_path: pathlib.Path, fileformat: str,
) -> None:
    """Every format has its own way of saying "no coding sequence".

    They do not agree on the ``cds`` sentinel they leave behind -- the
    columnar formats place an empty interval at the transcript's end,
    GTF simply never widens the one it starts with -- so what is
    asserted here is the question a caller actually asks.
    """
    resource = (
        a_gene_models()
        .with_format(fileformat)
        .with_transcript("NR_1", exons=[(10, 20), (30, 40)])
        .build_resource(tmp_path)
    )

    transcript = loaded(resource).gene_models_by_gene_name("NR_1")[0]

    assert not transcript.is_coding()
    assert [exon.frame for exon in transcript.exons] == [-1, -1]
    assert [(exon.start, exon.stop) for exon in transcript.exons] == [
        (10, 20), (30, 40)]


@pytest.mark.parametrize("fileformat", ["refflat", "default"])
def test_a_non_coding_transcript_carries_the_ucsc_empty_interval(
    tmp_path: pathlib.Path, fileformat: str,
) -> None:
    """UCSC spells "not coding" as ``cdsStart == cdsEnd`` at the end.

    Read back through the half-open shift that is a ``cds`` starting one
    past where it ends.  Pinned because any empty interval reads as
    non-coding, so a renderer could drift to a different one -- and
    write a file UCSC's own tools would read as a 1-base CDS -- without
    ``is_coding()`` ever noticing.
    """
    resource = (
        a_gene_models()
        .with_format(fileformat)
        .with_transcript("NR_1", exons=[(10, 20), (30, 40)])
        .build_resource(tmp_path)
    )

    transcript = loaded(resource).gene_models_by_gene_name("NR_1")[0]

    assert transcript.cds == (41, 40)


@pytest.mark.parametrize(
    ("fileformat", "attributes"),
    [
        ("refflat", {}),
        ("knowngene", {"proteinID": "tx1", "alignID": "tx1"}),
        ("refseq", {
            "#bin": "0", "score": "0", "exonCount": "2",
            "cdsStartStat": "cmpl", "cdsEndStat": "cmpl",
            "exonFrames": "0,1"}),
        ("ucscgenepred", {
            "score": "0", "name2": "G1", "cdsStartStat": "cmpl",
            "cdsEndStat": "cmpl", "exonFrames": "0,1"}),
        ("gtf", {
            "gene_id": "G1", "transcript_id": "tx1", "gene_name": "G1"}),
    ],
)
def test_the_filler_columns_reach_the_model_as_attributes(
    tmp_path: pathlib.Path, fileformat: str, attributes: dict[str, str],
) -> None:
    """What a format carries beyond the coordinates is not nothing.

    Each format copies a different set of its columns into
    ``TranscriptModel.attributes`` -- refFlat none, refSeq six of them,
    genePredExt a different five -- so what the builder writes into the
    filler columns is observable, and a test migrating between formats
    will see it change.
    """
    resource = (
        a_gene_models().with_format(fileformat).build_resource(tmp_path)
    )

    transcript = loaded(resource).transcript_models["tx1_1"] \
        if fileformat != "gtf" \
        else loaded(resource).transcript_models["tx1"]

    assert transcript.attributes == attributes


def test_a_transcript_with_no_exons_is_refused() -> None:
    """A transcript is its exons: with none there is nothing to write.

    GTF drops such a transcript on read and the columnar formats write a
    record with empty bound columns that the reader refuses, so the
    builder refuses first and names the transcript.
    """
    with pytest.raises(ResourceValidationError, match="NR_1"):
        a_gene_models().with_transcript("NR_1", exons=[])


def test_a_repeated_transcript_name_is_refused() -> None:
    """The same name twice is three different resources, by format.

    refFlat suffixes the two records apart into ``t_1``/``t_2``, gain's
    own format keys by the name and silently keeps only the last, and
    GTF raises while being read.  A builder that promises one transcript
    set written seven ways cannot offer that, so it refuses up front.
    """
    with pytest.raises(ResourceValidationError, match="NM_1"):
        (
            a_gene_models()
            .with_transcript("NM_1", gene="ALPHA", exons=[(10, 20)])
            .with_transcript("NM_1", gene="BETA", exons=[(50, 60)])
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("gene", "A B"),
        ("gene", "EMPTYGENE"),
        ("gene", "A||B"),
        ("chrom", "chr 1"),
        ("tr_name", "NM 1"),
    ],
)
def test_a_label_the_tab_conversion_would_rewrite_is_refused(
    field: str, value: str,
) -> None:
    """The ``setup_*`` helpers give three substrings a meaning of their own.

    Whitespace becomes a column separator -- so an authored gene of
    ``"A B"`` shifted the whole record and read back as the gene ``B``
    -- while ``||`` is rewritten to a space and ``EMPTY`` to a dot.  All
    three corrupted the label silently, with no error anywhere.
    """
    labels = {"tr_name": "NM_1", "gene": "ALPHA", "chrom": "chr1"}
    labels[field] = value

    with pytest.raises(ResourceValidationError, match=r"NM_1|NM 1"):
        a_gene_models().with_transcript(
            labels["tr_name"], gene=labels["gene"],
            chrom=labels["chrom"], exons=[(10, 20)])


def test_a_cds_bound_in_an_intron_is_refused() -> None:
    """GTF states the CDS through its exons; the others state it outright.

    An intronic bound therefore read back as ``(30, 40)`` in GTF and
    ``(24, 40)`` everywhere else -- the same authored transcript meaning
    two different things depending on the format knob.
    """
    with pytest.raises(ResourceValidationError, match="outside its exons"):
        a_gene_models().with_transcript(
            "NM_1", exons=[(10, 20), (30, 45)], cds=(24, 40))


def test_a_cds_outside_the_transcript_is_refused() -> None:
    with pytest.raises(ResourceValidationError, match="outside its exons"):
        a_gene_models().with_transcript(
            "NM_1", exons=[(10, 20)], cds=(12, 99))


def test_an_empty_cds_interval_is_refused() -> None:
    with pytest.raises(ResourceValidationError, match="empty cds"):
        a_gene_models().with_transcript(
            "NM_1", exons=[(10, 20)], cds=(18, 12))


def test_exons_out_of_order_are_refused() -> None:
    """The frames are computed by walking the exons in the given order."""
    with pytest.raises(ResourceValidationError, match="ascending"):
        a_gene_models().with_transcript(
            "NM_1", exons=[(30, 40), (10, 20)])


def test_overlapping_exons_are_refused() -> None:
    with pytest.raises(ResourceValidationError, match="ascending"):
        a_gene_models().with_transcript(
            "NM_1", exons=[(10, 30), (20, 40)])


def test_with_no_genes_refuses_the_same_contradictions_either_way_round(
    tmp_path: pathlib.Path,
) -> None:
    """Order must not decide whether a combination is allowed.

    The empty case is realized as a refFlat header, so pairing it with
    refFlat is no contradiction at all and is accepted whichever way
    round it is written; pairing it with any other format is refused
    whichever way round it is written.
    """
    empty = a_gene_models().with_no_genes()

    assert loaded(
        empty.with_format("refflat").build_resource(tmp_path / "a"),
    ).gene_names() == []
    assert loaded(
        a_gene_models().with_format("refflat").with_no_genes()
        .build_resource(tmp_path / "b"),
    ).gene_names() == []

    with pytest.raises(ResourceValidationError, match="cannot honour"):
        empty.with_format("gtf").build_resource(tmp_path / "c")
    with pytest.raises(ResourceValidationError, match="cannot honour"):
        a_gene_models().with_format("gtf").with_no_genes() \
            .build_resource(tmp_path / "d")
    with pytest.raises(
            ResourceValidationError, match="mutually exclusive"):
        empty.with_transcript("tx_a", exons=[(10, 20)]) \
            .build_resource(tmp_path / "e")


def test_an_unknown_format_is_reported_as_unknown_even_when_empty(
) -> None:
    """The nearer guard must not hide the reason the caller needs."""
    with pytest.raises(ResourceValidationError, match="unknown gene models"):
        a_gene_models().with_no_genes().with_format("bogus")


def test_gene_models_realize_through_the_shared_resource_seam() -> None:
    """The builder is a ``ResourceBuilder`` like every other one.

    Which is what lets it into ``a_grr().with_resource(...)`` and into
    the tmp-dir realize helpers without a second code path.
    """
    assert isinstance(a_gene_models(), ResourceBuilder)

    with build_resource_tempdir(a_gene_models()) as resource:
        assert loaded(resource).gene_names() == ["G1", "G2"]


def test_the_builder_covers_every_format_gain_parses(
) -> None:
    """A format gain learns to read is a format tests will want to write.

    The two lists are written out independently -- the builder's renderer
    table is not derived from the parser table -- so this is what says
    they still agree.
    """
    assert GENE_MODELS_FORMATS == SUPPORTED_GENE_MODELS_FILE_FORMATS


def test_the_default_gain_format_reads_back_at_the_same_bounds(
    tmp_path: pathlib.Path,
) -> None:
    """gain's own format states the same transcript in its own columns.

    Its coordinates are already gain's -- no half-open shift -- and it
    carries the exon frames in the file rather than deriving them, so
    this is a different renderer reaching the same parse.
    """
    resource = a_gene_models().with_format("default").build_resource(tmp_path)

    gene_models = build_gene_models_from_resource(resource).load()

    transcript = gene_models.gene_models_by_gene_name("G1")[0]
    assert transcript.chrom == "chr1"
    assert transcript.strand == "+"
    assert transcript.tx == (11, 45)
    assert transcript.cds == (14, 40)
    assert [(exon.start, exon.stop) for exon in transcript.exons] == [
        (11, 20), (31, 45)]
