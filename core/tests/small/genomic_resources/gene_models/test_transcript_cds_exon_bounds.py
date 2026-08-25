# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""A CDS is not guaranteed to lie inside the transcript's exons.

Metazoan mitochondrial genes carry an *incomplete stop codon*: the mRNA ends
in ``T`` or ``TA`` and polyadenylation completes the codon. Annotators
therefore emit a ``stop_codon`` that runs 1-2 bp past the terminal exon, so a
perfectly well-annotated coding transcript can have ``cds[1] >
exons[-1].stop`` (plus strand) or ``cds[0] < exons[0].start`` (minus strand).

Every CDS-derived quantity must be computed against the CDS *intersected with
the exon set*, which makes such a transcript indistinguishable from the same
transcript whose CDS is flush with its terminal exon -- the overrunning bases
are not transcribed, so they cannot contribute a frame, a region or a length.

See iossifovlab/gain#866.
"""

from collections.abc import Callable
from io import StringIO
from itertools import starmap

import pytest
from gain.genomic_resources.gene_models.parsers import (
    parse_ccds_gene_models_format,
    parse_gtf_gene_models_format,
    parse_known_gene_models_format,
    parse_ref_flat_gene_models_format,
    parse_ref_seq_gene_models_format,
    parse_ucscgenepred_models_format,
)
from gain.genomic_resources.gene_models.serialization import transcript_to_gtf
from gain.genomic_resources.gene_models.transcript_models import (
    Exon,
    TranscriptModel,
)
from gain.genomic_resources.testing import convert_to_tab_separated
from gain.utils.regions import BedRegion

THREE_EXONS = [(100, 200), (300, 400), (500, 600)]


def build_transcript(
    strand: str,
    cds: tuple[int, int],
    exons: list[tuple[int, int]],
) -> TranscriptModel:
    return TranscriptModel(
        gene="gene",
        tr_id="transcript",
        tr_name="transcript",
        chrom="chrM",
        strand=strand,
        tx=(exons[0][0], exons[-1][1]),
        cds=cds,
        exons=list(starmap(Exon, exons)),
    )


def test_calc_frames_when_cds_overruns_terminal_exon_on_plus_strand() -> None:
    # FBtr0100857 (mt:ND2), dmel-all-r6.68.gtf.gz: the stop codon runs
    # 2 bp past the single exon.
    overrun = build_transcript("+", (240, 1265), [(240, 1263)])
    flush = build_transcript("+", (240, 1263), [(240, 1263)])

    assert overrun.calc_frames() == flush.calc_frames()


def test_calc_frames_when_cds_overruns_terminal_exon_on_minus_strand() -> None:
    # FBtr0433501 (mt:ND5): on the minus strand the stop codon sits at the
    # low coordinate, so the overrun is below the first exon.
    overrun = build_transcript("-", (6407, 8125), [(6409, 8125)])
    flush = build_transcript("-", (6409, 8125), [(6409, 8125)])

    assert overrun.calc_frames() == flush.calc_frames()


def test_utr3_regions_when_cds_overruns_terminal_exon_on_plus_strand() -> None:
    overrun = build_transcript("+", (240, 1265), [(240, 1263)])
    flush = build_transcript("+", (240, 1263), [(240, 1263)])

    assert overrun.utr3_regions() == flush.utr3_regions()


def test_utr5_regions_when_cds_overruns_terminal_exon_on_minus_strand(
) -> None:
    # The mirror of the plus-strand case: on the minus strand the 5' end of
    # the CDS is the high coordinate, so a start codon annotated past the
    # last exon overruns upwards.
    overrun = build_transcript(
        "-", (100, 605), THREE_EXONS)
    flush = build_transcript("-", (100, 600), THREE_EXONS)

    assert overrun.utr5_regions() == flush.utr5_regions()
    assert overrun.calc_frames() == flush.calc_frames()


def test_cds_regions_stay_inside_the_exons_when_the_cds_overruns() -> None:
    # FBtr0433501 (mt:ND5). This arm never raised -- it silently reported a
    # coding region starting 2 bp before the only exon.
    overrun = build_transcript("-", (6407, 8125), [(6409, 8125)])

    assert overrun.cds_regions() == [
        BedRegion(chrom="chrM", start=6409, stop=8125),
    ]


@pytest.mark.parametrize(
    ("cds", "exon", "expected_len"),
    [
        pytest.param((6407, 8125), (6409, 8125), 1717, id="FBtr0433501"),
        pytest.param((8205, 9545), (8207, 9545), 1339, id="FBtr0433500"),
    ],
)
def test_cds_len_counts_only_exonic_coding_bases(
    cds: tuple[int, int],
    exon: tuple[int, int],
    expected_len: int,
) -> None:
    overrun = build_transcript("-", cds, [exon])

    assert overrun.cds_len() == expected_len


@pytest.mark.parametrize("strand", ["+", "-"])
def test_a_cds_entirely_outside_the_exons_translates_nothing(
    strand: str,
) -> None:
    # Degenerate annotation: the CDS misses the exon set completely, so the
    # intersection is empty and there is nothing to translate. Clamping such
    # a CDS would invert it, so it is reported as non-coding instead.
    transcript = build_transcript(strand, (5, 8), [(10, 20)])

    assert transcript.is_coding() is False
    assert transcript.calc_frames() == [-1]
    assert not transcript.cds_regions()
    assert transcript.cds_len() == 0
    assert not transcript.utr5_regions()
    assert not transcript.utr3_regions()


@pytest.mark.parametrize("strand", ["+", "-"])
def test_clamped_cds_uses_the_same_coding_test_as_an_annotated_one(
    strand: str,
) -> None:
    # cds (5, 10) against exon (10, 20) overlaps by a single base, so it is
    # the flush equivalent of an annotated cds (10, 10) -- which the model
    # already reports as non-coding. The two must not disagree.
    clamped = build_transcript(strand, (5, 10), [(10, 20)])
    annotated = build_transcript(strand, (10, 10), [(10, 20)])

    assert clamped.is_coding() == annotated.is_coding()
    assert clamped.calc_frames() == annotated.calc_frames()
    assert clamped.cds_regions() == annotated.cds_regions()
    assert clamped.utr5_regions() == annotated.utr5_regions()
    assert clamped.utr3_regions() == annotated.utr3_regions()
    # ... and the whole class agrees it is not coding
    assert annotated.is_coding() is False
    assert annotated.calc_frames() == [-1]


# (strand, annotated cds, flush-equivalent cds, exons). The flush CDS is
# written out rather than derived, so the oracle does not restate the
# implementation it is checking.
OVERRUN_TRANSCRIPTS = [
    pytest.param("+", (240, 1265), (240, 1263), [(240, 1263)],
                 id="FBtr0100857-mt:ND2"),
    pytest.param("+", (3083, 3769), (3083, 3767), [(3083, 3767)],
                 id="FBtr0100863-mt:CoII"),
    pytest.param("-", (6407, 8125), (6409, 8125), [(6409, 8125)],
                 id="FBtr0433501-mt:ND5"),
    pytest.param("-", (8205, 9545), (8207, 9545), [(8207, 9545)],
                 id="FBtr0433500-mt:ND4"),
    # The mitochondrial transcripts only exercise (+, above the last exon)
    # and (-, below the first); these are the other two corners.
    pytest.param("+", (95, 400), (100, 400), THREE_EXONS,
                 id="plus-overruns-below-the-first-exon"),
    pytest.param("-", (100, 605), (100, 600), THREE_EXONS,
                 id="minus-overruns-above-the-last-exon"),
]


@pytest.mark.parametrize(
    ("strand", "cds", "flush_cds", "exons"), OVERRUN_TRANSCRIPTS)
def test_an_overrunning_cds_matches_the_flush_transcript(
    strand: str,
    cds: tuple[int, int],
    flush_cds: tuple[int, int],
    exons: list[tuple[int, int]],
) -> None:
    # The whole contract in one property: bases annotated outside the exons
    # change nothing, so every derived quantity matches the flush twin.
    overrun = build_transcript(strand, cds, exons)
    flush = build_transcript(strand, flush_cds, exons)

    assert overrun.calc_frames() == flush.calc_frames()
    assert overrun.cds_regions() == flush.cds_regions()
    assert overrun.cds_len() == flush.cds_len()
    assert overrun.utr5_regions() == flush.utr5_regions()
    assert overrun.utr3_regions() == flush.utr3_regions()


@pytest.mark.parametrize(
    ("strand", "cds", "expected_frames"),
    [
        pytest.param("+", (100, 600), [0, 2, 1], id="plus-spans-every-exon"),
        pytest.param("-", (100, 600), [1, 2, 0], id="minus-spans-every-exon"),
        pytest.param("+", (150, 550), [0, 0, 2], id="plus-both-utrs"),
        pytest.param("-", (150, 550), [2, 0, 0], id="minus-both-utrs"),
        pytest.param("+", (310, 390), [-1, 0, -1], id="plus-interior-exon"),
        pytest.param("-", (310, 390), [-1, 0, -1], id="minus-interior-exon"),
        pytest.param("+", (601, 99), [-1, -1, -1], id="plus-non-coding"),
        pytest.param("-", (601, 99), [-1, -1, -1], id="minus-non-coding"),
    ],
)
def test_frames_of_a_well_formed_transcript_are_unchanged(
    strand: str,
    cds: tuple[int, int],
    expected_frames: list[int],
) -> None:
    # A CDS that already lies inside the exons must be clamped to itself.
    # These are the values gain produced before the clamp existed.
    transcript = build_transcript(strand, cds, THREE_EXONS)

    assert transcript.calc_frames() == expected_frames


@pytest.mark.parametrize("strand", ["+", "-"])
def test_cds_regions_of_a_well_formed_transcript_are_unchanged(
    strand: str,
) -> None:
    transcript = build_transcript(strand, (150, 550), THREE_EXONS)

    assert transcript.cds_regions() == [
        BedRegion(chrom="chrM", start=150, stop=200),
        BedRegion(chrom="chrM", start=300, stop=400),
        BedRegion(chrom="chrM", start=500, stop=550),
    ]
    assert transcript.cds_len() == 203


@pytest.mark.parametrize(
    ("strand", "expected_utr5", "expected_utr3"),
    [
        pytest.param("+", [(100, 149)], [(551, 600)], id="plus"),
        pytest.param("-", [(551, 600)], [(100, 149)], id="minus"),
    ],
)
def test_utrs_of_a_well_formed_transcript_are_unchanged(
    strand: str,
    expected_utr5: list[tuple[int, int]],
    expected_utr3: list[tuple[int, int]],
) -> None:
    transcript = build_transcript(strand, (150, 550), THREE_EXONS)

    assert transcript.utr5_regions() == [
        BedRegion(chrom="chrM", start=start, stop=stop)
        for start, stop in expected_utr5
    ]
    assert transcript.utr3_regions() == [
        BedRegion(chrom="chrM", start=start, stop=stop)
        for start, stop in expected_utr3
    ]


# The exon is chrM:240-1263 and the CDS runs to 1265 -- mt:ND2's incomplete
# stop codon. Tabular formats are 0-based half-open on the left, so the
# start coordinates are one less than the 1-based model coordinates.
# refSeq and CCDS share a column layout, so they are fed the same bytes.
REFSEQ_STYLE_MT_ND2 = (
    "#bin name chrom strand txStart txEnd cdsStart cdsEnd exonCount"
    " exonStarts exonEnds score name2 cdsStartStat cdsEndStat exonFrames\n"
    "585 FBtr0100857 chrM + 239 1263 239 1265 1 239 1263 0 mt:ND2"
    " cmpl incmpl 0"
)

MT_ND2_BY_FORMAT = [
    pytest.param(
        parse_ref_flat_gene_models_format,
        "#geneName name chrom strand txStart txEnd cdsStart cdsEnd"
        " exonCount exonStarts exonEnds\n"
        "mt:ND2 FBtr0100857 chrM + 239 1263 239 1265 1 239 1263",
        id="refflat",
    ),
    pytest.param(parse_ref_seq_gene_models_format, REFSEQ_STYLE_MT_ND2,
                 id="refseq"),
    pytest.param(parse_ccds_gene_models_format, REFSEQ_STYLE_MT_ND2,
                 id="ccds"),
    pytest.param(
        parse_known_gene_models_format,
        "name chrom strand txStart txEnd cdsStart cdsEnd exonCount"
        " exonStarts exonEnds proteinID alignID\n"
        "FBtr0100857 chrM + 239 1263 239 1265 1 239 1263 P12345 Q5T123",
        id="knowngene",
    ),
    pytest.param(
        parse_ucscgenepred_models_format,
        "name chrom strand txStart txEnd cdsStart cdsEnd exonCount"
        " exonStarts exonEnds\n"
        "FBtr0100857 chrM + 239 1263 239 1265 1 239 1263",
        id="ucscgenepred",
    ),
]


@pytest.mark.parametrize(("parser", "content"), MT_ND2_BY_FORMAT)
def test_every_tabular_parser_loads_an_incomplete_stop_codon(
    parser: Callable[[StringIO], dict[str, TranscriptModel] | None],
    content: str,
) -> None:
    # Each parser calls update_frames() on what it builds, so before the
    # clamp existed this raised while reading the file.
    result = parser(StringIO(convert_to_tab_separated(content)))

    assert result is not None
    transcript = next(iter(result.values()))
    assert transcript.cds == (240, 1265)
    assert [exon.frame for exon in transcript.exons] == [0]


def test_the_gtf_parser_loads_an_incomplete_stop_codon() -> None:
    attrs = 'gene_id "FBgn0013675"; transcript_id "FBtr0100857";'
    result = parse_gtf_gene_models_format(StringIO(
        f"chrM\tFlyBase\ttranscript\t240\t1263\t.\t+\t.\t{attrs}\n"
        f"chrM\tFlyBase\texon\t240\t1263\t.\t+\t.\t{attrs}\n"
        f"chrM\tFlyBase\tstart_codon\t240\t242\t.\t+\t0\t{attrs}\n"
        f"chrM\tFlyBase\tCDS\t240\t1262\t.\t+\t0\t{attrs}\n"
        f"chrM\tFlyBase\tstop_codon\t1263\t1265\t.\t+\t0\t{attrs}\n"))

    assert result is not None
    transcript = next(iter(result.values()))
    assert transcript.cds == (240, 1265)
    assert transcript.cds[1] > transcript.exons[-1].stop
    assert transcript.cds_regions() == [
        BedRegion(chrom="chrM", start=240, stop=1263),
    ]
    assert [exon.frame for exon in transcript.exons] == [0]


def test_a_cds_that_misses_the_exons_is_not_coding() -> None:
    # is_coding() must agree with the CDS-derived methods: consumers rely
    # on a coding transcript having at least one CDS region, and index
    # into cds_regions() without checking.
    transcript = build_transcript("+", (200, 220), [(240, 1263)])

    assert transcript.is_coding() is False


def test_gtf_export_of_a_cds_that_misses_the_exons_does_not_crash() -> None:
    content = convert_to_tab_separated(
        "#geneName name chrom strand txStart txEnd cdsStart cdsEnd"
        " exonCount exonStarts exonEnds\n"
        "g t chrM + 239 1263 200 220 1 239 1263")
    result = parse_ref_flat_gene_models_format(StringIO(content))
    assert result is not None
    transcript = next(iter(result.values()))

    assert transcript_to_gtf(transcript) is not None


def test_all_regions_with_splice_extension_matches_the_flush_transcript(
) -> None:
    # all_regions() decides where to extend by comparing exons against the
    # CDS, so it needs the same exonic bounds as every other CDS-derived
    # method; otherwise an overrun transcript gets an extension the flush
    # one does not.
    overrun = build_transcript("+", (97, 201), [(100, 200)])
    flush = build_transcript("+", (100, 200), [(100, 200)])

    assert overrun.all_regions(ss_extend=3) == flush.all_regions(ss_extend=3)


@pytest.mark.parametrize("strand", ["+", "-"])
def test_a_transcript_without_exons_translates_nothing(strand: str) -> None:
    # The GTF parser builds the model on the transcript feature and only
    # appends exons afterwards, so a record carrying no exon rows reaches
    # every one of these methods with an empty exon list.
    transcript = TranscriptModel(
        gene="gene", tr_id="transcript", tr_name="transcript", chrom="chrM",
        strand=strand, tx=(240, 1263), cds=(240, 1265), exons=[])

    assert transcript.is_coding() is False
    assert transcript.calc_frames() == []
    assert not transcript.cds_regions()
    assert transcript.cds_len() == 0
    assert not transcript.utr5_regions()
    assert not transcript.utr3_regions()
