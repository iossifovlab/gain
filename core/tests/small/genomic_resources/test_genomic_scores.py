# pylint: disable=W0621,C0114,C0116,W0212,W0613,C0415
import gc
import pathlib
import textwrap
import weakref
from collections.abc import Callable
from typing import Any, cast

import pysam
import pytest
from gain.genomic_resources.fsspec_protocol import (
    FsspecReadWriteProtocol,
    build_fsspec_protocol,
)
from gain.genomic_resources.genomic_position_table import (
    VCFGenomicPositionTable,
)
from gain.genomic_resources.genomic_position_table.line import (
    BigWigLine,
    Line,
)
from gain.genomic_resources.genomic_position_table.record import PAYLOAD
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    GenomicScore,
    RecordScoreLine,
    ScoreLine,
    ScoreLineBase,
    VCFScoreLine,
    _ScoreDef,
    build_score_from_resource,
    build_score_from_resource_id,
)
from gain.genomic_resources.histogram import (
    CategoricalHistogramConfig,
    NullHistogram,
    NullHistogramConfig,
    NumberHistogram,
    NumberHistogramConfig,
)
from gain.genomic_resources.implementations.genomic_scores_impl import (
    build_score_implementation_from_resource,
)
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GenomicResource,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_repository,
    build_filesystem_test_resource,
    build_inmemory_test_repository,
    build_inmemory_test_resource,
    convert_to_tab_separated,
    setup_bigwig,
    setup_directories,
    setup_genome,
    setup_tabix,
    setup_vcf,
)
from gain.task_graph.graph import TaskGraph


def build_simple_position_score_resource(
        extra_files: dict[str, str] | None = None) -> GenomicResource:
    base_content = {
        GR_CONF_FILE_NAME: textwrap.dedent("""
            type: position_score
            table:
                filename: data.mem
            scores:
                - id: score
                  type: float
                  name: score
        """),
        "data.mem": convert_to_tab_separated("""
            chrom pos_begin pos_end score
            1     10        10      0.1
            1     20        20      0.2
        """),
    }
    if extra_files:
        base_content.update(extra_files)
    return build_inmemory_test_resource(base_content)


@pytest.fixture
def vcf_score(tmp_path: pathlib.Path) -> AlleleScore:
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": textwrap.dedent("""
                type: allele_score
                table:
                    filename: data.vcf.gz
                    format: vcf_info
            """),
        })
    setup_vcf(
        tmp_path / "data.vcf.gz",
        textwrap.dedent("""
##fileformat=VCFv4.1
##INFO=<ID=A,Number=1,Type=Integer,Description="Score A">
##INFO=<ID=B,Number=.,Type=Integer,Description="Score B">
##INFO=<ID=C,Number=R,Type=String,Description="Score C">
##INFO=<ID=D,Number=A,Type=String,Description="Score D">
#CHROM POS ID REF ALT QUAL FILTER  INFO
chr1   2   .  A   .   .    .       A=0;B=01,02,03;C=c01
chr1   5   .  A   T   .    .       A=1;B=11,12,13;C=c11,c12;D=d11
chr1   15   .  A   T,G   .    .       A=2;B=21,22;C=c21,c22,c23;D=d21,d22
chr1   30   .  A   T,G,C   .    .      A=3;B=31;C=c31,c32,c33,c34;D=d31,d32,d33
    """),
    )
    res = build_filesystem_test_resource(tmp_path)
    score = build_score_from_resource(res)
    return cast(AlleleScore, score)


def test_scoreline_init() -> None:
    # ScoreLine wraps the remaining line adapters.  The VCF backend is no longer
    # one of them: it yields records, and its INFO lookup lives in VCFScoreLine.
    raw_line = ("chr1", 1, 10, 0.123)
    assert ScoreLine(Line(raw_line), {})
    assert ScoreLine(BigWigLine(raw_line), {})


def test_default_annotation_pre_normalize_validates() -> None:
    res: GenomicResource = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
              - id: phastCons100way
                type: float
                desc: "The phastCons computed over the tree of 100 \
                       verterbrate species"
                name: s1
            default_annotation:
              attributes:
                - phastCons100way""",
        "data.mem": """
            chrom  pos_begin  s1
            1      10         0.02
            1      11         0.03
            1      15         0.46
            2      8          0.01
            """,
    })
    assert res is not None
    assert res.get_type() == "position_score"


def test_default_annotation_auto_includes_all_scores() -> None:
    res: GenomicResource = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
                - id: score1
                  type: float
                  name: score1
                - id: score2
                  type: float
                  name: score2
        """,
        "data.mem": """
            chrom  pos_begin  score1  score2
            1      10         0.1     0.2
        """,
    })

    score = build_score_from_resource(res)
    score.open()

    attributes = score.get_default_annotation_attributes()
    assert attributes == [
        {"source": "score1", "name": "score1"},
        {"source": "score2", "name": "score2"},
    ]

    assert score.get_default_annotation_attribute("score1") == "score1"
    assert score.get_default_annotation_attribute("score2") == "score2"
    assert score.get_default_annotation_attribute("missing") is None


def test_default_annotation_custom_names() -> None:
    res: GenomicResource = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
                - id: score1
                  type: float
                  name: score1
                - id: score2
                  type: float
                  name: score2
            default_annotation:
                - source: score1
                  name: primary_score
                - source: score1
                  name: secondary_score
                - source: score2
        """,
        "data.mem": """
            chrom  pos_begin  score1  score2
            1      10         0.1     0.2
        """,
    })

    score = build_score_from_resource(res)
    score.open()

    attributes = score.get_default_annotation_attributes()
    assert attributes == [
        {"source": "score1", "name": "primary_score"},
        {"source": "score1", "name": "secondary_score"},
        {"source": "score2"},
    ]

    assert (
        score.get_default_annotation_attribute("score1")
        == "primary_score,secondary_score"
    )
    assert score.get_default_annotation_attribute("score2") == "score2"
    assert score.get_default_annotation_attribute("score3") is None


def test_vcf_tables_autogenerate_scoredefs(vcf_score: AlleleScore) -> None:
    assert isinstance(vcf_score.table, VCFGenomicPositionTable)
    assert set(vcf_score.score_definitions.keys()) == {"A", "B", "C", "D"}
    assert vcf_score.score_definitions["A"].desc == "Score A"
    assert vcf_score.score_definitions["A"].value_parser is None


def test_vcf_tables_can_override_autogenerated_scoredefs(
        tmp_path: pathlib.Path) -> None:
    root_path = tmp_path
    setup_directories(
        root_path / "grr",
        {
            "tmp": {
                "genomic_resource.yaml": textwrap.dedent("""
                    type: allele_score
                    table:
                        filename: data.vcf.gz
                    scores:
                    - id: A
                      name: A
                      type: float
                      desc: Score A, but overriden
                """),
            },
        },
    )
    setup_vcf(
        root_path / "grr" / "tmp" / "data.vcf.gz",
        textwrap.dedent("""
##fileformat=VCFv4.1
##INFO=<ID=A,Number=1,Type=Integer,Description="Score A">
##INFO=<ID=B,Number=1,Type=Float,Description="Score B">
#CHROM POS ID REF ALT QUAL FILTER  INFO
chr1   5   .  A   T   .    .       A=1;B=2.5
    """))
    proto = build_fsspec_protocol("testing", str(root_path / "grr"))
    score = build_score_from_resource(proto.get_resource("tmp"))
    assert isinstance(score.table, VCFGenomicPositionTable)
    assert set(score.score_definitions.keys()) == {"A"}
    assert score.score_definitions["A"].desc == "Score A, but overriden"
    assert score.score_definitions["A"].value_parser is float


def test_vcf_tables_merge_vcf_scores(tmp_path: pathlib.Path) -> None:
    root_path = tmp_path
    setup_directories(
        root_path / "grr",
        {
            "merge": {
                "genomic_resource.yaml": textwrap.dedent("""
                    type: allele_score
                    merge_vcf_scores: true
                    table:
                        filename: data.vcf.gz
                    scores:
                    - id: A
                      name: A
                      type: float
                      desc: Score A, but overriden
                """),
            },
        },
    )
    setup_vcf(
        root_path / "grr" / "merge" / "data.vcf.gz",
        textwrap.dedent("""
##fileformat=VCFv4.1
##INFO=<ID=A,Number=1,Type=Integer,Description="Score A">
##INFO=<ID=B,Number=1,Type=Float,Description="Score B">
#CHROM POS ID REF ALT QUAL FILTER  INFO
chr1   5   .  A   T   .    .       A=1;B=2.5
        """),
    )
    proto = build_fsspec_protocol("testing", str(root_path / "grr"))
    score = build_score_from_resource(proto.get_resource("merge"))
    assert isinstance(score.table, VCFGenomicPositionTable)
    assert set(score.score_definitions.keys()) == {"A", "B"}
    assert score.score_definitions["A"].desc == "Score A, but overriden"
    assert score.score_definitions["A"].value_parser is float
    assert score.score_definitions["B"].desc == "Score B"
    assert score.score_definitions["B"].value_parser is None


def test_score_definition_via_index_headerless_tabix(
        tmp_path: pathlib.Path) -> None:
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": """
                type: position_score
                table:
                  filename: data.txt.gz
                  format: tabix
                  header_mode: none
                  chrom:
                    index: 0
                  pos_begin:
                    index: 1
                  pos_end:
                    index: 2
                scores:
                - id: piscore
                  index: 3
                  type: float
            """,
        })
    setup_tabix(
        tmp_path / "data.txt.gz",
        "1     10        12       3.14",
        seq_col=0, start_col=1, end_col=2)
    res = build_filesystem_test_resource(tmp_path)
    score = build_score_from_resource(res)
    score.open()
    score_line = next(score.fetch_lines("1", 10, 12))
    assert len(score.score_definitions) == 1
    assert "piscore" in score.score_definitions
    assert score_line.get_available_scores() == ("piscore",)
    assert score_line.get_score("piscore") == 3.14


def test_score_definition_list_header_tabix(tmp_path: pathlib.Path) -> None:
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": """
                type: allele_score
                table:
                    filename: data.txt.gz
                    format: tabix
                    header_mode: list
                    header:
                    - chrom
                    - start
                    - stop
                    - reference
                    - alt
                    - score
                    pos_begin:
                      name: start
                    pos_end:
                      name: stop
                    reference:
                      name: reference
                    alternative:
                      name: alt
                scores:
                - id: piscore
                  name: score
                  type: float
            """,
        })
    setup_tabix(
        tmp_path / "data.txt.gz",
        "1     10        12         A         G     3.14",
        seq_col=0, start_col=1, end_col=2)
    res = build_filesystem_test_resource(tmp_path)
    score = build_score_from_resource(res)
    score.open()
    score_line = next(score.fetch_lines("1", 10, 12))
    assert len(score.score_definitions) == 1
    assert "piscore" in score.score_definitions
    assert score_line.get_available_scores() == ("piscore",)
    assert score_line.chrom == "1"
    assert score_line.pos_begin == 10
    assert score_line.pos_end == 12
    assert score_line.ref == "A"
    assert score_line.alt == "G"
    assert score_line.get_score("piscore") == 3.14


def test_forbid_column_names_in_scores_when_no_header_configured() -> None:
    res = build_inmemory_test_resource({
        "genomic_resource.yaml": """
            type: position_score
            table:
                header_mode: none
                filename: data.mem
                chrom:
                    index: 0
                pos_begin:
                    index: 1
            scores:
            - id: c2
              name: this_doesnt_make_sense
              type: float""",
        "data.mem": convert_to_tab_separated("""
            1   10  12  3.14
        """),
    })
    with pytest.raises(AssertionError) as excinfo:
        build_score_from_resource(res).open()
    assert str(excinfo.value) == ("Cannot configure score columns by name"
                                  " when header_mode is 'none'!")


def test_raise_error_when_missing_column_name_in_header() -> None:
    res = build_inmemory_test_resource({
        "genomic_resource.yaml": """
            type: position_score
            table:
                filename: data.mem
                pos_begin:
                    name: pos2
            scores:
            - id: c2
              name: this_doesnt_exist_in_header
              type: float""",
        "data.mem": convert_to_tab_separated(
            """
            chrom pos pos2 c2
            1     10  12   3.14
            """),
    })
    with pytest.raises(AssertionError):
        build_score_from_resource(res).open()


def test_raise_error_when_missing_column_name_in_header_as_list() -> None:
    res = build_inmemory_test_resource({
        "genomic_resource.yaml": """
            type: position_score
            table:
                header_mode: list
                header: ["chrom", "pos", "pos2", "score"]
                filename: data.mem
                pos_begin:
                    name: pos2
            scores:
            - id: c2
              name: this_doesnt_exist_in_header
              type: float""",
        "data.mem": convert_to_tab_separated("""
            1   10  12  3.14
        """),
    })
    with pytest.raises(AssertionError):
        build_score_from_resource(res).open()


def test_vcf_check_for_missing_score_columns(tmp_path: pathlib.Path) -> None:
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": textwrap.dedent("""
                type: allele_score
                table:
                  filename: data.vcf.gz
                scores:
                - id: A
                  name: NO_SUCH_SCORE_IN_HEADER
                  type: float
            """),
        })
    setup_vcf(
        tmp_path / "data.vcf.gz",
        textwrap.dedent("""
##fileformat=VCFv4.1
##INFO=<ID=A,Number=1,Type=Integer,Description="Score A">
#CHROM POS ID REF ALT QUAL FILTER  INFO
chr1   5   .  A   T   .    .       A=1
    """))
    res = build_filesystem_test_resource(tmp_path)
    with pytest.raises(AssertionError):
        build_score_from_resource(res).open()


def test_line_score_value_parsing(tmp_path: pathlib.Path) -> None:
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": """
                type: position_score
                table:
                  filename: data.txt.gz
                  format: tabix
                scores:
                - id: c2
                  name: c2
                  type: float
            """,
        })
    setup_tabix(
        tmp_path / "data.txt.gz",
        """
        #chrom  pos_begin  pos_end    c2
        1     10        12       3.14
        1     15        20       4.14
        1     21        30       5.14
        """, seq_col=0, start_col=1, end_col=2)
    res = build_filesystem_test_resource(tmp_path)
    score = build_score_from_resource(res)
    score.open()
    result = [line.get_score("c2") for line in score.fetch_lines("1", 10, 30)]
    assert result == [3.14, 4.14, 5.14]


def test_genomic_score_chrom_mapping(tmp_path: pathlib.Path) -> None:
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": """
                type: position_score
                table:
                  filename: data.txt.gz
                  chrom_mapping:
                    add_prefix: chr
                  format: tabix
                scores:
                - id: c2
                  name: c2
                  type: float
            """,
        })
    setup_tabix(
        tmp_path / "data.txt.gz",
        """
        #chrom  pos_begin  pos_end    c2
        1     10        12       3.14
        1     15        20       4.14
        1     21        30       5.14
        """, seq_col=0, start_col=1, end_col=2)
    res = build_filesystem_test_resource(tmp_path)
    impl = build_score_implementation_from_resource(res)
    score = impl.score
    score.open()
    result = impl._get_chrom_regions(1_000_000)
    assert result[0].chrom == "chr1"


def test_genomic_score_chrom_mapping_with_genome(
        tmp_path: pathlib.Path) -> None:
    setup_directories(
        tmp_path, {
            "one": {
                "genomic_resource.yaml": """
                    type: position_score
                    table:
                      filename: data.txt.gz
                      chrom_mapping:
                        add_prefix: chr
                      format: tabix
                    scores:
                    - id: c2
                      name: c2
                      type: float
                    meta:
                      labels:
                        reference_genome: two
                """,
            },
            "two": {
                "genomic_resource.yaml": "{type: genome, filename: chr.fa}",
            },
        })
    setup_tabix(
        tmp_path / "one" / "data.txt.gz",
        """
        #chrom  pos_begin  pos_end    c2
        1     10        12       3.14
        1     15        20       4.14
        1     21        30       5.14
        """, seq_col=0, start_col=1, end_col=2)
    setup_genome(tmp_path / "two" / "chr.fa", textwrap.dedent("""
            >chr1
            NNACCCAAAC
            GGGCCTTCCN
            GGGCCTTCCN
            GGGCCTTCCN
            GGGCCTTCCN
    """))
    repo = build_filesystem_test_repository(tmp_path)
    res = repo.get_resource("one")
    impl = build_score_implementation_from_resource(res)
    score = impl.score
    score.open()
    result = impl._get_chrom_regions(1_000_000)
    assert result[0].chrom == "chr1"


def test_line_score_na_values(tmp_path: pathlib.Path) -> None:
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": """
                type: position_score
                table:
                  filename: data.txt.gz
                  format: tabix
                scores:
                - id: c2
                  name: c2
                  type: float
                  na_values:
                  - "4.14"
                  - "5.14"
            """,
        })
    setup_tabix(
        tmp_path / "data.txt.gz",
        """
        #chrom  pos_begin  pos_end    c2
        1     10        12       3.14
        1     15        20       4.14
        1     21        30       5.14
        """, seq_col=0, start_col=1, end_col=2)
    res = build_filesystem_test_resource(tmp_path)

    score = build_score_from_resource(res)
    score.open()
    result = [line.get_score("c2") for line in score.fetch_lines("1", 10, 30)]
    assert result == [3.14, None, None]


def test_line_get_available_score_columns(vcf_score: AlleleScore) -> None:
    vcf_score.open()
    score_line = next(vcf_score.fetch_lines("chr1", 2, 30))
    assert set(score_line.get_available_scores()) == {"A", "B", "C", "D"}


def test_vcf_tuple_scores_autoconcat_to_string(vcf_score: AlleleScore) -> None:
    vcf_score.open()
    results = tuple(
        (r.chrom, r.pos_begin, r.pos_end, r.get_score("B"))
        for r in vcf_score.fetch_lines("chr1", 2, 30)
    )
    assert results == (
        ("chr1", 2, 2, "1|2|3"),
        ("chr1", 5, 5, "11|12|13"),
        ("chr1", 15, 15, "21|22"),
        ("chr1", 15, 15, "21|22"),
        ("chr1", 30, 30, "31"),
        ("chr1", 30, 30, "31"),
        ("chr1", 30, 30, "31"),
    )


class _CountingHeaderMetadata:
    """The header's INFO metadata, counting the per-KEY lookups made on it.

    ``metadata.get(key)`` is not free either: it builds a fresh pysam
    ``VariantMetadata`` for the key.  Unlike the two proxies above that cost is
    per-SCORE, not per-line, so it is the one a wide table pays most often --
    which makes *whether a score reads it at all* worth pinning.
    """

    def __init__(self, meta: pysam.VariantHeaderMetadata) -> None:
        self._meta = meta
        self.gets = 0

    def get(self, key: str) -> Any:
        self.gets += 1
        return self._meta.get(key)


class _CountingHeader:
    """A variant header whose INFO metadata counts its lookups."""

    def __init__(self, header: pysam.VariantHeader) -> None:
        self.info = _CountingHeaderMetadata(header.info)


class _CountingVariant:
    """A pysam variant record that counts the proxy reads made through it.

    ``variant.info`` and ``variant.header`` do not return a cached attribute:
    pysam builds a **fresh** proxy object on every access (``v.info is v.info``
    is ``False``, ~85ns each).  So the number of times a score line touches
    them is a per-line cost that is otherwise invisible -- this proxy makes it
    observable, over a real variant record with real INFO data behind it.

    The header it hands back counts one level deeper: how many *keys* were
    looked up in its INFO metadata (``meta_gets``).
    """

    def __init__(self, variant: pysam.VariantRecord) -> None:
        self._variant = variant
        self._header = _CountingHeader(variant.header)
        self.info_reads = 0
        self.header_reads = 0

    @property
    def info(self) -> pysam.VariantRecordInfo:
        self.info_reads += 1
        return self._variant.info

    @property
    def header(self) -> _CountingHeader:
        self.header_reads += 1
        return self._header

    @property
    def meta_gets(self) -> int:
        return self._header.info.gets


def _count_line(
    line: ScoreLineBase, score_defs: dict[str, _ScoreDef],
) -> tuple[VCFScoreLine, _CountingVariant]:
    """Rebuild a VCF score line over a counting stand-in for its variant."""
    assert isinstance(line, VCFScoreLine)
    variant, allele_index = line.record[PAYLOAD]
    counting = _CountingVariant(variant)
    record = (*line.record[:PAYLOAD], (counting, allele_index))
    return VCFScoreLine(record, score_defs), counting


def test_vcf_score_line_reads_the_pysam_proxies_once_per_line(
    vcf_score: AlleleScore,
) -> None:
    """The INFO proxy and its header metadata are read ONCE per line.

    Both ``variant.info`` and ``variant.header.info`` allocate a fresh pysam
    proxy on every access, so obtaining them per *score* would put ~170ns of
    pure re-allocation on every score of every line -- a per-line cost that
    grows with the width of the table.  Measured, the un-hoisted version was
    *slower than pre-#237 master* from 20 scores on; with the hoist the
    migration wins at every width.  They are properties of the LINE, not of a
    score, so they are obtained once and reused for every score read.

    They are still obtained **lazily**, on the first score read: a line whose
    scores are never read (an allele filtered out by REF/ALT in
    ``AlleleScore.fetch_scores``) must pay nothing for them.
    """
    with vcf_score.open():
        # A real, multi-allelic line -- the record whose INFO the counting
        # proxy stands in front of is the genuine pysam one.
        line = next(iter(vcf_score.fetch_lines("chr1", 30, 30)))
        counted_line, counting = _count_line(
            line, vcf_score.score_definitions)

        # Nothing is read before a score is asked for.
        assert (counting.info_reads, counting.header_reads) == (0, 0)

        score_defs = list(vcf_score.score_definitions.values())
        assert len(score_defs) == 4
        values = counted_line.get_values(score_defs)

        # ...and the four scores read the two proxies exactly once between
        # them -- not once each.
        assert counting.info_reads == 1
        assert counting.header_reads == 1

        # The values are the same ones the real line reads: the hoist is a
        # cost change, not a semantic one.
        assert values == line.get_values(score_defs)
        assert values == [3, "31", "c32", "d31"]


def test_vcf_score_line_reads_the_info_metadata_only_for_a_tuple_value(
    vcf_score: AlleleScore,
) -> None:
    """The INFO metadata is looked up ONLY when the value is a tuple.

    ``self._info_meta.get(key)`` builds a fresh pysam ``VariantMetadata`` for
    the key, and unlike the two per-line proxies that cost is paid per SCORE --
    so a 50-score table pays it 50 times a line.  The only thing the metadata is
    ever used for is deciding, for a **tuple** value, which element of it this
    allele reads (Number=A / Number=R / Number='.').  A ``Number=1`` field --
    the shape of essentially every score-bearing INFO field -- decodes to a
    scalar, so its metadata is allocated and thrown away unread.

    So the lookup belongs *inside* the tuple branch, and this test says so in
    the only terms that survive a refactor: how many keys the line looks up in
    the header metadata.  (Measured over a 3000-row VCF: 0.80x at 20 scores,
    0.78x at 50.)  This is a cost change only -- what each number case returns
    is pinned by the three tests below, unchanged.
    """
    with vcf_score.open():
        line = next(iter(vcf_score.fetch_lines("chr1", 30, 30)))
        counted_line, counting = _count_line(
            line, vcf_score.score_definitions)

        # A -- Number=1, so the value is a scalar and its number cannot
        # change which value this allele reads.  The metadata is never asked.
        assert counted_line.get_score("A") == 3
        assert counting.meta_gets == 0

        # C -- Number=R, a tuple: the number is what says the reference sits
        # at offset 0, so the metadata IS read, once, for this key.
        assert counted_line.get_score("C") == "c32"
        assert counting.meta_gets == 1

    # An absent key still answers None -- and does so without touching the
    # metadata and without raising.  (The no-ALT record at chr1:2 carries no
    # D at all.)
    with vcf_score.open():
        absent = next(iter(vcf_score.fetch_lines("chr1", 2, 2)))
        absent_line, absent_counting = _count_line(
            absent, vcf_score.score_definitions)

        assert absent_line.get_score("D") is None
        assert absent_counting.meta_gets == 0


class _HeaderlessVariant:
    """A variant whose INFO reads fine but whose header raises.

    The cheapest thing that can fail *half way through* resolving a VCF score
    line's per-line state: ``variant.info`` hands a value back, ``variant.header
    .info`` does not.  A real one of these is a corrupt or truncated VCF; here
    it only has to raise where pysam would.
    """

    def __init__(self, info: dict[str, Any]) -> None:
        self.info = info

    @property
    def header(self) -> pysam.VariantHeader:
        raise RuntimeError("no header on this variant")


def test_vcf_score_line_that_fails_to_resolve_reports_the_same_error(
    vcf_score: AlleleScore,
) -> None:
    """A line that fails to resolve reports the SAME failure on a re-read.

    ``_info`` doubles as the "per-line state already resolved" flag, and the
    state it guards is written across three statements.  Set the flag FIRST and
    a raise from either of the other two strands the line half-initialised:
    flagged as resolved, with a null ``_info_meta`` behind it.  The next read of
    that same line then skips the resolve block and dies on the null --
    ``AttributeError: 'NoneType' object has no attribute 'get'`` -- which says
    nothing about the corrupt record that actually broke it.

    So the flag is written LAST.  A line that failed to resolve is simply still
    unresolved, and reading it again re-runs the resolve and re-reports the real
    error.  (Only the tuple branch reads the metadata now, so the value below is
    a tuple -- that is the path that would meet the null.)

    No in-tree caller catches an exception out of a score read and then re-reads
    the same line, so this is latent today.  It is pinned rather than argued
    because it is what makes ``_info_meta``'s non-optional type -- and the
    ``type: ignore`` on its null initialiser -- sound instead of merely
    asserted: nothing can observe that null.
    """
    with vcf_score.open():
        variant = _HeaderlessVariant({"D": ("d11",)})
        record = ("chr1", 5, 5, "A", "T", (variant, 0))
        line = VCFScoreLine(record, vcf_score.score_definitions)

        for _ in range(2):
            with pytest.raises(
                    RuntimeError, match="no header on this variant"):
                line.get_score("D")


def test_vcf_score_line_selects_info_values_by_allele_index(
    vcf_score: AlleleScore,
) -> None:
    """The INFO lookup, per allele -- the whole reason VCF has a score line.

    A VCF score is an INFO field, and which value of it a record reads depends
    on the field's declared **number** and on the record's allele index:

    * ``D`` is **Number=A** -- one value per ALT allele: each record reads the
      value at its own allele index.  The variant at chr1:2 has no ALT at all
      and no D in its INFO -- an absent key yields ``None``, it does not raise.
    * ``C`` is **Number=R** -- one value per allele *including the reference*,
      at offset 0: an ALT allele reads at ``allele_index + 1``, and the
      no-ALT record at chr1:2 (null allele index) reads the **reference** value.
    * ``A`` is Number=1 -- one value for the whole variant, allele or not.

    Every allele of a multi-allelic variant appears, one record each.
    """
    vcf_score.open()
    with vcf_score:
        lines = list(vcf_score.fetch_lines("chr1", 2, 30))
        assert all(type(line) is VCFScoreLine for line in lines)

        results = [
            (line.chrom, line.pos_begin, line.alt,
             line.get_score("A"), line.get_score("C"), line.get_score("D"))
            for line in lines
        ]

        # chrom pos alt A(Number=1) C(Number=R) D(Number=A)
        assert results == [
            # no ALT -> null allele index -> C reads the REFERENCE value, and
            # the absent D yields None rather than raising
            ("chr1", 2, None, 0, "c01", None),
            ("chr1", 5, "T", 1, "c12", "d11"),
            ("chr1", 15, "T", 2, "c22", "d21"),
            ("chr1", 15, "G", 2, "c23", "d22"),
            ("chr1", 30, "T", 3, "c32", "d31"),
            ("chr1", 30, "G", 3, "c33", "d32"),
            ("chr1", 30, "C", 3, "c34", "d33"),
        ]


def test_vcf_score_line_hands_back_a_number_a_field_raw_when_alt_is_absent(
    tmp_path: pathlib.Path,
) -> None:
    """A **Number=A** field on a record with no ALT is handed back RAW.

    A record whose ALT is absent ('.') yields one record with a **null** allele
    index -- there is no ALT allele to index by.  A Number=A field declares one
    value *per ALT allele*, so on such a record there is no per-allele value to
    select, and the INFO lookup hands the raw pysam tuple back untouched.  Read
    through a configured ``str`` score that is exactly what you see: the tuple's
    ``repr``, ``"('d01',)"`` -- not ``"d01"``, and not ``None``.

    **This is odd, and it is pinned deliberately, bug-for-bug.**  It is the
    behaviour of the pre-record VCF backend, byte-for-byte, and preserving it is
    the point: #237 is a cost change, not a semantic one.  Anyone who wants to
    *change* it (to the sole value, or to ``None``) is making a separate,
    deliberate decision, and this test is what will tell them they are.

    The branch this pins is a **crash guard**.  Drop the ``allele_index is not
    None`` half of the Number=A test in ``VCFScoreLine._get_raw`` and this
    record indexes a tuple with ``None`` -- ``TypeError: tuple indices must be
    integers or slices, not NoneType``.  Nothing else in the suite covers it:
    the ``vcf_score`` fixture's own no-ALT row happens to omit its Number=A
    field, so the raw-tuple path is never taken there.

    The score is *configured* (``type: str``) rather than autogenerated for the
    same reason as the Number='.' test above: an autogenerated def would parse
    through a converter and could mask what the lookup actually returned.
    """
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": textwrap.dedent("""
                type: allele_score
                table:
                    filename: data.vcf.gz
                    format: vcf_info
                scores:
                - id: D
                  name: D
                  type: str
            """),
        })
    setup_vcf(
        tmp_path / "data.vcf.gz",
        textwrap.dedent("""
##fileformat=VCFv4.1
##INFO=<ID=D,Number=A,Type=String,Description="Score D">
#CHROM POS ID REF ALT QUAL FILTER  INFO
chr1   2   .  A   .   .    .       D=d01
chr1   5   .  A   T   .    .       D=d11
    """),
    )
    res = build_filesystem_test_resource(tmp_path)
    score = cast(AlleleScore, build_score_from_resource(res))

    with score.open():
        lines = list(score.fetch_lines("chr1", 1, 30))
        assert all(type(line) is VCFScoreLine for line in lines)

        results = [
            (line.pos_begin, line.alt, line.get_score("D")) for line in lines
        ]

    assert results == [
        # no ALT -> null allele index -> the RAW tuple, stringified
        (2, None, "('d01',)"),
        # a real ALT allele -> the value AT that allele index
        (5, "T", "d11"),
    ]


def test_a_record_score_lines_record_is_write_once(
    vcf_score: AlleleScore,
) -> None:
    """A record-backed score line's ``record`` is **write-once**.

    Both record-backed score lines memoise something derived from the payload of
    the record they were built over: :class:`VCFScoreLine` hoists the two pysam
    INFO proxies (and the allele index) on its first score read, and
    :class:`RecordScoreLine` binds ``_get_raw`` to its payload's indexer in its
    constructor.  Neither memo has an invalidation hook.

    So a *rebound* ``record`` would produce the most confusing failure there is:
    the core fields (chrom, pos, ref, alt) are read from the slots on every
    access and would report the NEW record, while the scores would still be
    served from the OLD one's payload.  The position says one row, the values
    say another, and nothing raises.

    Rather than pay to detect that on the hot path -- an identity check per
    score read is exactly the per-line cost #237 exists to remove -- the line
    refuses the rebinding at its public surface: ``record`` is a read-only
    property, and that is what this test pins.

    It pins a **guard-rail, not an impossibility**, and the difference matters.
    ``_record`` is an ordinary attribute: ``line._record = other`` still stores,
    and the line then really does report the new record's position with the old
    record's scores, silently.  Nothing short of a per-read check could stop
    that, and that check is the cost this class exists to avoid.  What the
    property does buy is that the stale state cannot be reached through the name
    a caller is meant to use.  A line is built over one record and reads that
    record, and reuse (which #239 may want) has to add memo invalidation
    *deliberately*, with this test to tell it so.
    """
    with vcf_score.open():
        vcf_line = next(iter(vcf_score.fetch_lines("chr1", 30, 30)))
    assert isinstance(vcf_line, VCFScoreLine)

    position_score = build_score_from_resource(
        build_simple_position_score_resource())
    with position_score.open():
        record_line = next(iter(position_score.fetch_lines("1", 10, 10)))
    assert isinstance(record_line, RecordScoreLine)

    # Reading a record is of course fine -- it is only rebinding that is not.
    assert vcf_line.record[PAYLOAD] is not None
    assert record_line.record[PAYLOAD] is not None

    for line in (vcf_line, record_line):
        with pytest.raises(AttributeError):
            line.record = ("chr1", 1, 1, "A", "T", ())  # type: ignore[misc]


def _dies_by_refcount(
    build_line: Callable[[], ScoreLineBase],
) -> bool:
    """Whether a freshly built, fully read score line dies without the GC.

    Builds one line, reads every score off it (so whatever the line memoises
    on its first read is in place), drops the last reference to it with the
    cycle collector **disabled**, and reports whether it was freed anyway.

    A weak reference that has cleared can only mean the object's refcount
    reached zero, so it is exactly the question "is this line in a cycle?" --
    with no dependence on when a GC pass happens to run.
    """
    gc.disable()
    try:
        line = build_line()
        line.get_values(list(line.score_defs.values()))
        ref = weakref.ref(line)
        del line
        return ref() is None
    finally:
        gc.enable()


def test_score_lines_are_freed_without_the_cycle_collector(
    vcf_score: AlleleScore,
) -> None:
    """No score line is part of a reference cycle.

    One score line is built **per line** of a fetch -- that is the whole cost
    #237 is about -- so a line that can only be freed by the cycle collector
    does not merely leak a few bytes: it turns a scan that produced *zero*
    cyclic garbage into one that hands the collector thousands of objects to
    free, and promotes the survivors to gen-1.  What those cycles hold alive
    until then is the payload: a live ``pysam.VariantRecord`` (and the header it
    pins), retained well past its last use instead of being freed by refcount
    the moment the line goes out of scope.

    Measured on a 3000-row VCF (fetch_lines + get_values over every line): with
    the cycle the collector freed 11076/888/0 gen-0/1/2 objects -- ~3.7 per
    line, the line, its instance dict and the bound method -- over 28/2/0
    passes; without it, it freed *nothing*, its 11/1/0 gen-0 passes all empty.
    Count what the collector FREES, not how often it runs: the pass count never
    reaches zero (CPython untracks tuples of immutables, so the gen-0 counter
    creeps even in allocation-balanced code), which is why this test asserts on
    liveness under a disabled collector rather than on a collection count.

    The way to make a per-line object cyclic is to store a bound method **of
    self** on self (``self._x = self._y`` -- self -> bound method -> self), and
    that is exactly what a score line must not do.  Both record-backed lines
    bind their raw-value lookup to something that is *not* self -- the payload's
    indexer -- or reach it as a plain method, which allocates nothing per line
    and refers to nothing.

    Pinned for the whole family, over real fetched lines, so a future backend
    (or #239's rework of the adapters) cannot quietly reintroduce it.
    """
    with vcf_score.open():
        vcf_record = next(
            iter(vcf_score.fetch_lines("chr1", 30, 30))).record
        vcf_defs = vcf_score.score_definitions
        assert _dies_by_refcount(
            lambda: VCFScoreLine(vcf_record, vcf_defs))

    position_score = build_score_from_resource(
        build_simple_position_score_resource())
    with position_score.open():
        record = next(iter(position_score.fetch_lines("1", 10, 10))).record
        defs = position_score.score_definitions
        assert _dies_by_refcount(lambda: RecordScoreLine(record, defs))


def test_vcf_score_line_joins_an_unbounded_string_info_field(
    tmp_path: pathlib.Path,
) -> None:
    """An unbounded string INFO field (Number=., Type=String) joins on '|'.

    This join is **VCF-local** -- it happens in the INFO lookup, on the raw
    value, before any score parser sees it.

    The score is *configured* (``type: str``) rather than autogenerated, and
    that is what makes this test bite.  An autogenerated Number='.' score def
    gets a tuple-joining converter as its value parser, which masks the loss of
    the VCF-local join entirely (both routes then answer ``"c11|c12"``).  A
    configured ``str`` score parses with bare ``str``, so if the lookup handed
    back the raw tuple the score would read ``"('c11', 'c12')"``.
    """
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": textwrap.dedent("""
                type: allele_score
                table:
                    filename: data.vcf.gz
                    format: vcf_info
                scores:
                - id: S
                  name: S
                  type: str
            """),
        })
    setup_vcf(
        tmp_path / "data.vcf.gz",
        textwrap.dedent("""
##fileformat=VCFv4.1
##INFO=<ID=S,Number=.,Type=String,Description="strings">
#CHROM POS ID REF ALT QUAL FILTER  INFO
chr1   5   .  A   T   .    .       S=c11,c12
chr1   9   .  A   T   .    .       S=solo
    """),
    )
    score = cast(
        AlleleScore, build_score_from_resource(
            build_filesystem_test_resource(tmp_path)))
    with score.open():
        assert score.score_definitions["S"].value_parser is str
        assert [
            line.get_score("S") for line in score.fetch_lines("chr1", 5, 9)
        ] == ["c11|c12", "solo"]


def test_vcf_tables_can_select_subset_of_autogenerated_scoredefs(
    tmp_path: pathlib.Path,
) -> None:
    root_path = tmp_path
    setup_directories(
        root_path / "grr",
        {
            "tmp": {
                "genomic_resource.yaml": textwrap.dedent("""
                    type: allele_score
                    table:
                        filename: data.vcf.gz
                    scores:
                    - id: A
                    - id: C
                """),
            },
        },
    )
    setup_vcf(
        root_path / "grr" / "tmp" / "data.vcf.gz",
        textwrap.dedent("""
##fileformat=VCFv4.1
##INFO=<ID=A,Number=1,Type=Integer,Description="Score A">
##INFO=<ID=B,Number=1,Type=Integer,Description="Score B">
##INFO=<ID=C,Number=.,Type=String,Description="Score C">
##INFO=<ID=D,Number=.,Type=String,Description="Score D">
#CHROM POS ID REF ALT QUAL FILTER  INFO
chr1   5   .  A   T   .    .       A=1;C=c11,c12;D=d11
    """))
    proto = build_fsspec_protocol("testing", str(root_path / "grr"))
    score = build_score_from_resource(proto.get_resource("tmp"))
    assert isinstance(score.table, VCFGenomicPositionTable)
    assert set(score.score_definitions.keys()) == {"A", "C"}
    assert score.score_definitions["A"].desc == "Score A"
    assert score.score_definitions["A"].value_type == "int"
    assert score.score_definitions["C"].desc == "Score C"
    assert score.score_definitions["C"].value_type == "str"


def test_score_definition_new_configuration_fields(
    tmp_path: pathlib.Path,
) -> None:
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": """
                type: position_score
                table:
                  filename: data.txt.gz
                  format: tabix
                  header_mode: list
                  header: ["chrom", "pos", "pos2", "score", "score2"]
                  chrom:
                    column_index: 0
                  pos_begin:
                    column_index: 1
                  pos_end:
                    column_name: pos2
                scores:
                - id: piscore
                  column_index: 3
                  type: float
                - id: 2piscore
                  column_name: score2
                  type: float
            """,
        })
    setup_tabix(
        tmp_path / "data.txt.gz",
        "1     10        12       3.14  6.28",
        seq_col=0, start_col=1, end_col=2)
    res = build_filesystem_test_resource(tmp_path)
    score = build_score_from_resource(res)
    score.open()
    assert len(score.score_definitions) == 2
    assert "piscore" in score.score_definitions
    assert "2piscore" in score.score_definitions

    score_line = next(score.fetch_lines("1", 10, 12))
    assert score_line.get_available_scores() == ("piscore", "2piscore")
    assert score_line.get_score("piscore") == 3.14
    assert score_line.get_score("2piscore") == 6.28


def test_score_definition_histograms(
    tmp_path: pathlib.Path,
) -> None:
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": """
                type: position_score
                table:
                  filename: data.txt.gz
                  format: tabix
                  header_mode: list
                  header: ["chrom", "pos", "score1", "score2"]
                  chrom:
                    column_index: 0
                  pos_begin:
                    column_index: 1
                  pos_end:
                    column_index: 1
                scores:
                - id: score1
                  column_index: 2
                  type: str
                - id: score2
                  column_index: 3
                  type: str
                  histogram:
                    type: categorical
            """,
        })
    setup_tabix(
        tmp_path / "data.txt.gz",
        "1  10  aaa  bbb",
        seq_col=0, start_col=1, end_col=1)
    res = build_filesystem_test_resource(tmp_path)
    score = build_score_from_resource(res)
    score.open()
    assert len(score.score_definitions) == 2
    assert "score1" in score.score_definitions
    assert "score2" in score.score_definitions

    score_line = next(score.fetch_lines("1", 10, 10))
    assert score_line.get_available_scores() == ("score1", "score2")
    assert score_line.get_score("score1") == "aaa"
    assert score_line.get_score("score2") == "bbb"

    score1_def = score.score_definitions["score1"]
    assert score1_def.hist_conf is None

    score2_def = score.score_definitions["score2"]
    assert score2_def.hist_conf is not None
    assert isinstance(score2_def.hist_conf, CategoricalHistogramConfig)
    assert score2_def.hist_conf.enforce_type


def test_build_genomic_score_from_resource_id() -> None:
    grr = build_inmemory_test_repository({
        "example_score": {
            GR_CONF_FILE_NAME: """
                type: position_score
                table:
                  filename: data.mem
                scores:
                  - id: s1
                    type: float
                    name: s1
            """,
            "data.mem": """
                chrom  pos_begin  s1
                1      10         0.02
            """,
        }})
    score = build_score_from_resource_id("example_score", grr)
    score.open()
    assert score is not None
    assert list(score.fetch_region_values("1", 10, None, ["s1"])) == [
        (10, 10, [0.02])]


def test_statistics_build_tasks(tmp_path: pathlib.Path) -> None:
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": """
                type: position_score
                table:
                  filename: data.txt.gz
                  format: tabix
                scores:
                - id: dummy
                  name: dummy
                  type: float
            """,
        })
    setup_tabix(
        tmp_path / "data.txt.gz",
        """
        #chrom  pos_begin  pos_end  dummy
        chr1     1         20       3.14
        chr1    21         40       3.15
        chr1    41         60       3.16
        chr2    10         20       4.14
        chr3    10         20       5.14
        """, seq_col=0, start_col=1, end_col=2)
    res = build_filesystem_test_resource(tmp_path)
    impl = build_score_implementation_from_resource(res)

    task_graph = TaskGraph()
    tasks = impl.create_statistics_build_tasks()
    assert len(tasks) == 8
    task_graph.add_tasks(tasks)

    save_task = tasks[-1]
    merge_task = task_graph.get_task_desc(save_task.deps[0])
    calc_tasks = merge_task.deps
    assert len(calc_tasks) == 1  # merge_min_max task

    task_graph = TaskGraph()
    tasks = impl.create_statistics_build_tasks(region_size=20)
    assert len(tasks) == 20

    task_graph.add_tasks(tasks)
    save_task = tasks[-1]
    merge_task = task_graph.get_task_desc(save_task.deps[0])
    calc_tasks = merge_task.deps
    assert len(calc_tasks) == 1  # merge_min_max task

    tasks = impl.create_statistics_build_tasks(region_size=0)
    assert len(tasks) == 1  # merge_min_max task
    noregion_task = tasks[0]
    assert not noregion_task.deps


def test_get_score_range_reads_histogram() -> None:
    histogram = NumberHistogram(NumberHistogramConfig((0.0, 1.0)))
    histogram.min_value = 0.25
    histogram.max_value = 0.75

    res = build_simple_position_score_resource({
        "statistics/histogram_score.json": histogram.serialize(),
    })

    score = build_score_from_resource(res)
    score.open()

    assert score.get_score_range("score") == (0.25, 0.75)


def test_get_score_range_returns_none_for_null_histogram() -> None:
    null_hist = NullHistogram(NullHistogramConfig("disabled"))
    res = build_simple_position_score_resource({
        "statistics/histogram_score.json": null_hist.serialize(),
    })

    score = build_score_from_resource(res)
    score.open()

    assert score.get_score_range("score") is None


def test_get_score_range_unknown_score_raises() -> None:
    score = build_score_from_resource(build_simple_position_score_resource())
    score.open()

    with pytest.raises(ValueError, match="unknown score missing"):
        score.get_score_range("missing")


def test_get_histogram_filename_prefers_yaml_from_manifest() -> None:
    manifest_content = textwrap.dedent(
        """
        - name: genomic_resource.yaml
          size: 0
          md5: ""
        - name: statistics/histogram_score.yaml
          size: 0
          md5: ""
        """)
    yaml_hist = textwrap.dedent(
        """
        config:
          type: null
          reason: disabled
        """)
    res = build_simple_position_score_resource(
        {
            ".MANIFEST": manifest_content,
            "statistics/histogram_score.yaml": yaml_hist,
        },
    )
    score = build_score_from_resource(res)

    assert score.get_histogram_filename("score") == \
        "statistics/histogram_score.yaml"


def test_get_histogram_image_filename_and_url() -> None:
    score = build_score_from_resource(build_simple_position_score_resource())

    assert score.get_histogram_image_filename("score") == \
        "statistics/histogram_score.png"
    url = score.get_histogram_image_url("score")
    assert url is not None
    assert url.endswith("/statistics/histogram_score.png")


def test_get_histogram_image_public_url() -> None:
    res = build_simple_position_score_resource()
    proto = cast(FsspecReadWriteProtocol, res.proto)
    proto.public_url = "https://grr.example.com"
    score = build_score_from_resource(res)

    url = score.get_histogram_image_public_url("score")
    assert url is not None
    # built from the resource's public URL, not the local repo URL
    assert url == (
        f"{res.get_public_url()}/statistics/histogram_score.png"
    )
    assert url.startswith("https://grr.example.com")
    assert url != score.get_histogram_image_url("score")


def test_fetch_region_lines_requires_open() -> None:
    score = build_score_from_resource(build_simple_position_score_resource())

    region_iter = score._fetch_region_lines("1", 10, 10)
    with pytest.raises(ValueError, match="is not open"):
        next(region_iter)


def test_fetch_region_lines_checks_available_chromosomes() -> None:
    score = build_score_from_resource(build_simple_position_score_resource())
    score.open()

    with pytest.raises(ValueError, match="not among the available"):
        next(score._fetch_region_lines("2", 10, 10))


def test_line_to_begin_end_validates_order() -> None:
    bad_line = ScoreLine(
        Line(("1", "20", "10")),
        {},
    )

    with pytest.raises(OSError, match="has a region"):
        GenomicScore._line_to_begin_end(bad_line)


def test_default_annotation_requires_list() -> None:
    res = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: textwrap.dedent("""
            type: position_score
            table:
                filename: data.mem
            scores:
                - id: score
                  type: float
                  name: score
            default_annotation:
                attributes:
                    - score
        """),
        "data.mem": convert_to_tab_separated("""
            chrom pos_begin pos_end score
            1     10        10      0.1
        """),
    })
    score = build_score_from_resource(res)
    score.open()

    with pytest.raises(TypeError, match="default_annotation"):
        score.get_default_annotation_attributes()


_BIGWIG_DATA = textwrap.dedent("""
    chr1  0   10   0.1
    chr1  10  20   0.2
    chr1  20  30   0.3
    chr2  0   10   0.4
    chr2  10  20   0.5
""")
_BIGWIG_CHROM_LENS = {"chr1": 1000, "chr2": 2000}


def _build_bigwig_score_dir(root_path: pathlib.Path) -> None:
    setup_directories(
        root_path,
        {
            "grr.yaml": textwrap.dedent(f"""
                id: test_grr
                type: directory
                directory: {root_path!s}
            """),
            "bw_score": {
                "genomic_resource.yaml": textwrap.dedent("""
                    type: position_score
                    table:
                        filename: data.bw
                    scores:
                    - id: score
                      type: float
                      index: 3
                """),
            },
        },
    )
    setup_bigwig(
        root_path / "bw_score" / "data.bw", _BIGWIG_DATA, _BIGWIG_CHROM_LENS)


@pytest.fixture(scope="module")
def bigwig_position_score(
    tmp_path_factory: pytest.TempPathFactory,
) -> GenomicScore:
    root_path = tmp_path_factory.mktemp("bigwig_score")
    _build_bigwig_score_dir(root_path)
    grr = build_filesystem_test_repository(root_path)
    score = build_score_from_resource(grr.get_resource("bw_score"))
    score.open()
    return score


def test_bigwig_position_score_opens_without_header_mode(
    tmp_path: pathlib.Path,
) -> None:
    # Regression: BigWig score must open even when header_mode is not set.
    _build_bigwig_score_dir(tmp_path)
    grr = build_filesystem_test_repository(tmp_path)
    score = build_score_from_resource(grr.get_resource("bw_score"))
    score.open()
    score.close()


def test_bigwig_position_score_get_all_chromosomes(
    bigwig_position_score: GenomicScore,
) -> None:
    assert bigwig_position_score.get_all_chromosomes() == ["chr1", "chr2"]


def test_bigwig_position_score_get_all_scores(
    bigwig_position_score: GenomicScore,
) -> None:
    assert bigwig_position_score.get_all_scores() == ["score"]


def test_bigwig_position_score_fetch_lines(
    bigwig_position_score: GenomicScore,
) -> None:
    # BigWig [0,10) → GAIn [1,10]; [10,20) → [11,20]
    lines = list(bigwig_position_score.fetch_lines("chr1", 1, 15))
    assert len(lines) == 2
    assert lines[0].get_score("score") == pytest.approx(0.1)
    assert lines[1].get_score("score") == pytest.approx(0.2)


def test_bigwig_position_score_fetch_region_values(
    bigwig_position_score: GenomicScore,
) -> None:
    result = list(
        bigwig_position_score.fetch_region_values("chr1", 1, 20, ["score"]),
    )
    assert len(result) == 2
    assert result[0][2] is not None
    assert result[0][2][0] == pytest.approx(0.1)
    assert result[1][2] is not None
    assert result[1][2][0] == pytest.approx(0.2)


def test_bigwig_position_score_fetch_scores_at_position(
    bigwig_position_score: GenomicScore,
) -> None:
    from gain.genomic_resources.genomic_scores import PositionScore
    ps = cast(PositionScore, bigwig_position_score)
    result = ps.fetch_scores("chr1", 5)
    assert result is not None
    assert result[0] == pytest.approx(0.1)

    result = ps.fetch_scores("chr1", 15)
    assert result is not None
    assert result[0] == pytest.approx(0.2)


def test_bigwig_position_score_fetch_scores_agg(
    bigwig_position_score: GenomicScore,
) -> None:
    from gain.genomic_resources.genomic_scores import PositionScore
    ps = cast(PositionScore, bigwig_position_score)
    # Positions 1-20: [1,10] → 0.1, [11,20] → 0.2; mean = 0.15
    aggs = ps.fetch_scores_agg("chr1", 1, 20)
    assert len(aggs) == 1
    assert aggs[0].get_final() == pytest.approx(0.15)


def test_bigwig_position_score_multi_chrom(
    bigwig_position_score: GenomicScore,
) -> None:
    lines_chr2 = list(bigwig_position_score.fetch_lines("chr2", 1, 20))
    assert len(lines_chr2) == 2
    assert lines_chr2[0].get_score("score") == pytest.approx(0.4)
    assert lines_chr2[1].get_score("score") == pytest.approx(0.5)
