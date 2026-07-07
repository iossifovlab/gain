# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pathlib

import pytest
from gain.gene_scores.gene_scores import build_gene_score_from_resource
from gain.genomic_resources.genomic_scores import PositionScore
from gain.genomic_resources.reference_genome import (
    build_reference_genome_from_resource,
)
from gain.genomic_resources.repository import GenomicResourceProtocolRepo
from gain.genomic_resources.testing.builders import (
    a_gene_score,
    a_grr,
    a_position_score,
    a_reference_genome,
)


def test_bare_default_is_a_readable_minimal_score(
    tmp_path: pathlib.Path,
) -> None:
    res = a_position_score().build_resource(tmp_path)

    assert res.get_type() == "position_score"
    score = PositionScore(res).open()
    assert len(score.get_all_scores()) == 1
    values = score.fetch_scores("1", 10)
    assert values is not None
    assert isinstance(values[0], float)


def test_grr_resource_reads_back_authored_values(
    tmp_path: pathlib.Path,
) -> None:
    repo = (
        a_grr()
        .with_resource(
            "scores/pos",
            a_position_score()
            .with_score("phastCons100way", "float")
            .with_data("""
                chrom  pos_begin  phastCons100way
                1      10         0.02
                1      11         0.03
                1      15         0.46
                2      8          0.01
            """),
        )
        .build_repo(tmp_path)
    )

    assert isinstance(repo, GenomicResourceProtocolRepo)
    score = PositionScore(repo.get_resource("scores/pos")).open()
    assert score.get_all_scores() == ["phastCons100way"]
    assert score.fetch_scores("1", 11) == [0.03]
    assert score.fetch_scores("1", 15) == [0.46]
    assert score.fetch_scores("2", 8) == [0.01]
    assert score.fetch_scores("1", 12) is None


def test_builders_are_immutable_no_cross_variation_leak() -> None:
    base = a_position_score()
    variant_a = base.with_score("aaa", "float")
    variant_b = base.with_score("bbb", "int")

    # The shared base is untouched by either derivation.
    assert len(base.scores) == 0
    assert [s.score_id for s in variant_a.scores] == ["aaa"]
    assert [s.score_id for s in variant_b.scores] == ["bbb"]

    # with_data on a variant does not mutate the others.
    variant_a2 = variant_a.with_data("chrom pos_begin aaa\n1 10 0.5\n")
    assert variant_a.data is None
    assert variant_a2.data is not None
    assert variant_a is not variant_a2


def test_grr_builder_is_immutable() -> None:
    base = a_grr()
    extended = base.with_resource("x", a_position_score())
    assert len(base.resources) == 0
    assert len(extended.resources) == 1
    assert base is not extended


def test_column_name_defaults_to_score_id(
    tmp_path: pathlib.Path,
) -> None:
    # No column_name given: the data column is named after the score id.
    res = (
        a_position_score()
        .with_score("myscore", "float")
        .with_data("""
            chrom  pos_begin  myscore
            1      10         0.7
        """)
        .build_resource(tmp_path)
    )
    score = PositionScore(res).open()
    assert score.get_all_scores() == ["myscore"]
    assert score.fetch_scores("1", 10) == [0.7]


def test_explicit_column_name_override(
    tmp_path: pathlib.Path,
) -> None:
    # column_name differs from the score id; the data uses the column name.
    res = (
        a_position_score()
        .with_score("myscore", "float", column_name="raw_col")
        .with_data("""
            chrom  pos_begin  raw_col
            1      10         0.7
        """)
        .build_resource(tmp_path)
    )
    score = PositionScore(res).open()
    assert score.get_all_scores() == ["myscore"]
    assert score.fetch_scores("1", 10) == [0.7]


def test_data_missing_declared_score_column_raises(
    tmp_path: pathlib.Path,
) -> None:
    builder = (
        a_position_score()
        .with_score("phastCons100way", "float")
        .with_data("""
            chrom  pos_begin  wrong_name
            1      10         0.02
        """)
    )
    with pytest.raises(ValueError, match="phastCons100way") as excinfo:
        builder.build_resource(tmp_path)
    assert "missing" in str(excinfo.value)


def test_data_with_undeclared_extra_column_raises(
    tmp_path: pathlib.Path,
) -> None:
    builder = (
        a_position_score()
        .with_score("phastCons100way", "float")
        .with_data("""
            chrom  pos_begin  phastCons100way  bonus
            1      10         0.02             9.9
        """)
    )
    with pytest.raises(ValueError, match="bonus") as excinfo:
        builder.build_resource(tmp_path)
    assert "undeclared" in str(excinfo.value)


def test_validation_error_names_resource_id(
    tmp_path: pathlib.Path,
) -> None:
    builder = (
        a_position_score()
        .with_score("sc", "float")
        .with_data("chrom pos_begin extra\n1 10 0.1\n")
    )
    with pytest.raises(ValueError, match="scores/broken"):
        a_grr().with_resource("scores/broken", builder).build_repo(tmp_path)


def test_hash_prefixed_header_raises(
    tmp_path: pathlib.Path,
) -> None:
    # The builder owns the data format; a conventional '#'-prefixed header
    # is rejected explicitly instead of being silently skipped.
    builder = (
        a_position_score()
        .with_score("sc", "float")
        .with_data("""
            #chrom  pos_begin  sc
            1       10         0.1
        """)
    )
    with pytest.raises(ValueError, match="must not start with '#'"):
        builder.build_resource(tmp_path)


def test_hash_prefixed_header_names_resource_id(
    tmp_path: pathlib.Path,
) -> None:
    builder = (
        a_position_score()
        .with_score("sc", "float")
        .with_data("#chrom pos_begin sc\n1 10 0.1\n")
    )
    with pytest.raises(ValueError, match="scores/hashed"):
        a_grr().with_resource("scores/hashed", builder).build_repo(tmp_path)


def test_duplicate_column_name_across_scores_raises(
    tmp_path: pathlib.Path,
) -> None:
    # Two scores mapped to the same column_name must not silently collapse.
    builder = (
        a_position_score()
        .with_score("sc1", "float", column_name="shared")
        .with_score("sc2", "float", column_name="shared")
        .with_data("chrom pos_begin shared\n1 10 0.1\n")
    )
    with pytest.raises(ValueError, match="shared") as excinfo:
        builder.build_resource(tmp_path)
    assert "column_name" in str(excinfo.value)


def test_duplicate_score_id_raises(
    tmp_path: pathlib.Path,
) -> None:
    # The same score id declared twice must not silently collapse.  The two
    # scores map to DISTINCT column_names, so the duplicate-column_name check
    # cannot fire -- only the duplicate-id check can -- which isolates it.
    builder = (
        a_position_score()
        .with_score("sc", "float", column_name="a")
        .with_score("sc", "float", column_name="b")
        .with_data("chrom pos_begin a b\n1 10 0.1 0.2\n")
    )
    with pytest.raises(ValueError, match="sc") as excinfo:
        builder.build_resource(tmp_path)
    assert "duplicate" in str(excinfo.value).lower()


def test_range_rows_with_pos_end(
    tmp_path: pathlib.Path,
) -> None:
    # pos_end is an allowed optional position column for range rows.
    res = (
        a_position_score()
        .with_score("sc", "float")
        .with_data("""
            chrom  pos_begin  pos_end  sc
            1      10         15       0.02
            1      17         19       0.03
        """)
        .build_resource(tmp_path)
    )
    score = PositionScore(res).open()
    assert score.table is not None
    assert score.table.pos_end_key == 2
    assert score.fetch_scores("1", 12) == [0.02]
    assert score.fetch_scores("1", 18) == [0.03]


def test_realized_table_is_plain_txt(
    tmp_path: pathlib.Path,
) -> None:
    # The position table realizes as a plain .txt file (no tabix/.gz on
    # the table itself).
    a_position_score().build_resource(tmp_path)
    assert (tmp_path / "data.txt").is_file()
    assert not (tmp_path / "data.txt.gz").exists()
    assert not list(tmp_path.glob("data.txt*.tbi"))


def test_multiple_scores_in_one_resource(
    tmp_path: pathlib.Path,
) -> None:
    res = (
        a_position_score()
        .with_score("s_float", "float")
        .with_score("s_int", "int")
        .with_data("""
            chrom  pos_begin  s_float  s_int
            1      10         0.02     5
        """)
        .build_resource(tmp_path)
    )
    score = PositionScore(res).open()
    assert score.get_all_scores() == ["s_float", "s_int"]
    assert score.fetch_scores("1", 10) == [0.02, 5]


def test_bare_reference_genome_is_readable_minimal(
    tmp_path: pathlib.Path,
) -> None:
    res = a_reference_genome().build_resource(tmp_path)

    assert res.get_type() == "genome"
    with build_reference_genome_from_resource(res).open() as ref:
        assert "1" in ref.get_all_chrom_lengths()
        seq = ref.get_sequence("1", 1, ref.get_chrom_length("1"))
        assert set(seq) <= set("ACGTN")
        assert len(seq) >= 12


def test_reference_genome_with_fasta_reads_back_exact_bases(
    tmp_path: pathlib.Path,
) -> None:
    res = (
        a_reference_genome()
        .with_fasta(">1\nACGTACGTAC\nTTGGCCAANN")
        .build_resource(tmp_path)
    )
    with build_reference_genome_from_resource(res).open() as ref:
        assert ref.get_chrom_length("1") == 20
        assert ref.get_sequence("1", 1, 20) == "ACGTACGTACTTGGCCAANN"


def test_reference_genome_with_chromosome_reads_back(
    tmp_path: pathlib.Path,
) -> None:
    res = (
        a_reference_genome()
        .with_chromosome("chrA", "ACGTACGTAC")
        .with_chromosome("chrB", "TTTTGGGGCC")
        .build_resource(tmp_path)
    )
    with build_reference_genome_from_resource(res).open() as ref:
        assert ref.get_all_chrom_lengths() == {"chrA": 10, "chrB": 10}
        assert ref.get_sequence("chrA", 1, 10) == "ACGTACGTAC"
        assert ref.get_sequence("chrB", 1, 10) == "TTTTGGGGCC"


def test_reference_genome_default_is_bgzipped(
    tmp_path: pathlib.Path,
) -> None:
    a_reference_genome().with_chromosome("1", "ACGTACGTAC").build_resource(
        tmp_path)
    assert (tmp_path / "chr.fa.gz").is_file()
    assert (tmp_path / "chr.fa.gz.fai").is_file()
    assert (tmp_path / "chr.fa.gz.gzi").is_file()
    assert not (tmp_path / "chr.fa").exists()


def test_reference_genome_as_plain_realizes_plain_fa(
    tmp_path: pathlib.Path,
) -> None:
    res = (
        a_reference_genome()
        .as_plain()
        .with_chromosome("1", "ACGTACGTAC")
        .build_resource(tmp_path)
    )
    assert (tmp_path / "chr.fa").is_file()
    assert (tmp_path / "chr.fa.fai").is_file()
    assert not (tmp_path / "chr.fa.gz").exists()
    with build_reference_genome_from_resource(res).open() as ref:
        assert ref.get_sequence("1", 1, 10) == "ACGTACGTAC"


def test_reference_genome_line_width_controls_wrapping(
    tmp_path: pathlib.Path,
) -> None:
    seq = "ACGT" * 10  # 40 bases
    (
        a_reference_genome()
        .as_plain()
        .with_line_width(8)
        .with_chromosome("1", seq)
        .build_resource(tmp_path)
    )
    fasta_lines = (tmp_path / "chr.fa").read_text().splitlines()
    seq_lines = [ln for ln in fasta_lines if not ln.startswith(">")]
    assert seq_lines[0] == "ACGTACGT"
    assert all(len(ln) <= 8 for ln in seq_lines)
    assert "".join(seq_lines) == seq


def test_reference_genome_builder_is_immutable() -> None:
    base = a_reference_genome()
    extended = base.with_chromosome("1", "ACGT")
    plain = base.as_plain()
    assert base.chromosomes == ()
    assert base.bgzip is True
    assert extended.chromosomes == (("1", "ACGT"),)
    assert plain.bgzip is False
    assert base is not extended


def test_reference_genome_builder_no_cross_variation_leak(
    tmp_path: pathlib.Path,
) -> None:
    # From one shared base, two siblings set DIFFERENT chromosomes and are
    # BOTH realized; each genome must read back ONLY its own chromosome.
    # If the builder accumulated chromosomes into a shared mutable list
    # (append in place) instead of a fresh tuple per with_chromosome, both
    # siblings would carry both chromosomes and this would fail.
    base = a_reference_genome()
    sibling_a = base.with_chromosome("1", "ACGT")
    sibling_b = base.with_chromosome("2", "TTTT")

    res_a = sibling_a.build_resource(tmp_path / "a")
    res_b = sibling_b.build_resource(tmp_path / "b")

    with build_reference_genome_from_resource(res_a).open() as ref_a:
        assert set(ref_a.get_all_chrom_lengths()) == {"1"}
        assert ref_a.get_sequence("1", 1, 4) == "ACGT"
    with build_reference_genome_from_resource(res_b).open() as ref_b:
        assert set(ref_b.get_all_chrom_lengths()) == {"2"}
        assert ref_b.get_sequence("2", 1, 4) == "TTTT"


def test_reference_genome_fasta_and_chromosome_are_exclusive(
    tmp_path: pathlib.Path,
) -> None:
    builder = (
        a_reference_genome()
        .with_fasta(">1\nACGT")
        .with_chromosome("1", "ACGT")
    )
    with pytest.raises(ValueError, match="mutually exclusive"):
        builder.build_resource(tmp_path)


def test_grr_duplicate_resource_id_raises() -> None:
    # Declaring the same resource id twice would silently last-win (both
    # realize into the same dir); reject it at the call site naming the id.
    grr = a_grr().with_resource("scores/g", a_position_score())
    with pytest.raises(ValueError, match="scores/g") as excinfo:
        grr.with_resource("scores/g", a_position_score())
    assert "duplicate" in str(excinfo.value).lower()


def test_build_repo_passes_through_non_validation_value_error(
    tmp_path: pathlib.Path,
) -> None:
    # build_repo annotates only its OWN validation errors with the resource
    # id.  A plain ValueError raised during realize (not a builder
    # validation error) must pass through un-relabeled -- no
    # "resource '<id>':" prefix.
    class _Exploding:
        def realize_into(self, resource_dir: pathlib.Path) -> None:
            raise ValueError("boom from realize")

    with pytest.raises(ValueError, match="boom from realize") as excinfo:
        a_grr().with_resource("scores/x", _Exploding()).build_repo(tmp_path)
    assert "scores/x" not in str(excinfo.value)
    assert "resource" not in str(excinfo.value)


def test_reference_genome_empty_chromosome_sequence_raises() -> None:
    # An empty (or whitespace-only) sequence would fail deep inside pysam
    # faidx with a cryptic SamtoolsError; fail fast at the call site with a
    # clear ValueError naming the chromosome.
    with pytest.raises(ValueError, match="chromosome '1'") as excinfo:
        a_reference_genome().with_chromosome("1", "")
    assert "non-empty" in str(excinfo.value)

    with pytest.raises(ValueError, match="chromosome 'chrX'"):
        a_reference_genome().with_chromosome("chrX", "   ")


def test_reference_genome_empty_fasta_raises() -> None:
    # An empty (or whitespace-only) FASTA would fail deep inside pysam
    # faidx with a cryptic SamtoolsError ("Could not build fai index");
    # fail fast at the call site with a clear ValueError, mirroring the
    # with_chromosome empty-sequence guard.
    with pytest.raises(ValueError, match="non-empty") as excinfo:
        a_reference_genome().with_fasta("")
    assert "FASTA" in str(excinfo.value)

    with pytest.raises(ValueError, match="non-empty"):
        a_reference_genome().with_fasta("   \n\t  ")


def test_grr_mixes_genome_and_position_score(
    tmp_path: pathlib.Path,
) -> None:
    repo = (
        a_grr()
        .with_resource(
            "genomes/g", a_reference_genome().with_chromosome(
                "1", "ACGTACGTAC"))
        .with_resource(
            "scores/pos",
            a_position_score()
            .with_score("sc", "float")
            .with_data("chrom pos_begin sc\n1 10 0.5\n"),
        )
        .build_repo(tmp_path)
    )
    genome_res = repo.get_resource("genomes/g")
    assert genome_res.get_type() == "genome"
    with build_reference_genome_from_resource(genome_res).open() as ref:
        assert ref.get_sequence("1", 1, 10) == "ACGTACGTAC"

    score = PositionScore(repo.get_resource("scores/pos")).open()
    assert score.fetch_scores("1", 10) == [0.5]


# ---------------------------------------------------------------------------
# gene-score builder
# ---------------------------------------------------------------------------

def test_bare_gene_score_is_readable_minimal(
    tmp_path: pathlib.Path,
) -> None:
    # A bare gene score (one float score, no histogram) must realize and
    # read back through GeneScore with working get_min/get_max -- the
    # numeric default histogram is auto-built by gene_scores.py.
    res = a_gene_score().build_resource(tmp_path)

    assert res.get_type() == "gene_score"
    gene_score = build_gene_score_from_resource(res)
    assert len(gene_score.get_all_scores()) == 1
    score_id = gene_score.get_all_scores()[0]
    assert gene_score.get_min(score_id) == pytest.approx(
        gene_score.get_min(score_id))
    assert gene_score.get_max(score_id) >= gene_score.get_min(score_id)


def test_gene_score_reads_back_authored_values(
    tmp_path: pathlib.Path,
) -> None:
    res = (
        a_gene_score()
        .with_score("pli", "float")
        .with_data("""
            gene   pli
            GENE1  0.5
            GENE2  0.9
            GENE3  0.1
        """)
        .build_resource(tmp_path)
    )
    gene_score = build_gene_score_from_resource(res)
    assert gene_score.get_all_scores() == ["pli"]
    assert gene_score.get_gene_value("pli", "GENE1") == pytest.approx(0.5)
    assert gene_score.get_gene_value("pli", "GENE2") == pytest.approx(0.9)
    assert gene_score.get_min("pli") == pytest.approx(0.1)
    assert gene_score.get_max("pli") == pytest.approx(0.9)


def test_gene_score_desc_reads_back(
    tmp_path: pathlib.Path,
) -> None:
    # A plain desc is emitted and read back on the score's ScoreDef.
    res = (
        a_gene_score()
        .with_score("pli", "float", desc="pLI probability")
        .with_data("""
            gene   pli
            GENE1  0.5
        """)
        .build_resource(tmp_path)
    )
    gene_score = build_gene_score_from_resource(res)
    assert gene_score.score_definitions["pli"].description == (
        "pLI probability")


def test_gene_score_desc_with_colon_renders_valid_yaml(
    tmp_path: pathlib.Path,
) -> None:
    # A desc containing a YAML-special character (a colon) must not corrupt
    # the emitted config: it must parse AND read back verbatim.
    desc = "pLI: prob of intolerance"
    res = (
        a_gene_score()
        .with_score("pli", "float", desc=desc)
        .with_data("""
            gene   pli
            GENE1  0.5
        """)
        .build_resource(tmp_path)
    )
    config = res.get_config()
    assert config is not None
    assert config["scores"][0]["desc"] == desc
    gene_score = build_gene_score_from_resource(res)
    assert gene_score.score_definitions["pli"].description == desc


def test_gene_score_column_name_defaults_to_id(
    tmp_path: pathlib.Path,
) -> None:
    res = (
        a_gene_score()
        .with_score("pli")
        .with_data("""
            gene   pli
            GENE1  0.5
        """)
        .build_resource(tmp_path)
    )
    gene_score = build_gene_score_from_resource(res)
    assert gene_score.get_all_scores() == ["pli"]
    assert gene_score.get_gene_value("pli", "GENE1") == pytest.approx(0.5)


def test_gene_score_explicit_column_name_override(
    tmp_path: pathlib.Path,
) -> None:
    res = (
        a_gene_score()
        .with_score("pli", column_name="raw_pli")
        .with_data("""
            gene   raw_pli
            GENE1  0.5
        """)
        .build_resource(tmp_path)
    )
    gene_score = build_gene_score_from_resource(res)
    assert gene_score.get_all_scores() == ["pli"]
    assert gene_score.get_gene_value("pli", "GENE1") == pytest.approx(0.5)


def test_gene_score_custom_gene_column(
    tmp_path: pathlib.Path,
) -> None:
    res = (
        a_gene_score()
        .with_gene_column("symbol")
        .with_score("pli", "float")
        .with_data("""
            symbol  pli
            GENE1   0.5
            GENE2   0.9
        """)
        .build_resource(tmp_path)
    )
    gene_score = build_gene_score_from_resource(res)
    assert gene_score.get_gene_value("pli", "GENE1") == pytest.approx(0.5)
    assert gene_score.get_gene_value("pli", "GENE2") == pytest.approx(0.9)


def test_gene_score_missing_declared_column_raises(
    tmp_path: pathlib.Path,
) -> None:
    builder = (
        a_gene_score()
        .with_score("pli", "float")
        .with_data("""
            gene   wrong_name
            GENE1  0.5
        """)
    )
    with pytest.raises(ValueError, match="pli") as excinfo:
        builder.build_resource(tmp_path)
    assert "missing" in str(excinfo.value)


def test_gene_score_undeclared_extra_column_raises(
    tmp_path: pathlib.Path,
) -> None:
    builder = (
        a_gene_score()
        .with_score("pli", "float")
        .with_data("""
            gene   pli   bonus
            GENE1  0.5   9.9
        """)
    )
    with pytest.raises(ValueError, match="bonus") as excinfo:
        builder.build_resource(tmp_path)
    assert "undeclared" in str(excinfo.value)


def test_gene_score_validation_error_names_resource_id(
    tmp_path: pathlib.Path,
) -> None:
    builder = (
        a_gene_score()
        .with_score("pli", "float")
        .with_data("gene extra\nGENE1 0.1\n")
    )
    with pytest.raises(ValueError, match="genes/broken"):
        a_grr().with_resource("genes/broken", builder).build_repo(tmp_path)


def test_gene_score_column_name_colliding_with_gene_column_raises(
    tmp_path: pathlib.Path,
) -> None:
    # A score whose column_name equals the gene column passes header
    # validation but dies cryptically at read ("could not convert string to
    # float"); fail fast naming the collision.
    builder = (
        a_gene_score()
        .with_score("g", "float", column_name="gene")
        .with_data("""
            gene
            G1
        """)
    )
    with pytest.raises(ValueError, match="gene") as excinfo:
        builder.build_resource(tmp_path)
    assert "gene column" in str(excinfo.value)


def test_gene_score_realizes_plain_tsv(
    tmp_path: pathlib.Path,
) -> None:
    a_gene_score().build_resource(tmp_path)
    assert (tmp_path / "data.tsv").is_file()
    assert not (tmp_path / "data.tsv.gz").exists()
    assert not list(tmp_path.glob("data.tsv*.tbi"))


def test_gene_score_multiple_scores_in_one_resource(
    tmp_path: pathlib.Path,
) -> None:
    res = (
        a_gene_score()
        .with_score("pli", "float")
        .with_score("mis_z", "float")
        .with_data("""
            gene   pli   mis_z
            GENE1  0.5   1.2
            GENE2  0.9   2.4
        """)
        .build_resource(tmp_path)
    )
    gene_score = build_gene_score_from_resource(res)
    assert set(gene_score.get_all_scores()) == {"pli", "mis_z"}
    assert gene_score.get_gene_value("pli", "GENE1") == pytest.approx(0.5)
    assert gene_score.get_gene_value("mis_z", "GENE2") == pytest.approx(2.4)


def test_gene_score_omits_histogram_by_default(
    tmp_path: pathlib.Path,
) -> None:
    # A numeric gene score with no declared histogram emits NO histogram
    # block; gene_scores.py auto-builds the default numeric histogram.
    res = a_gene_score().with_score("pli", "float").with_data("""
        gene   pli
        GENE1  0.5
    """).build_resource(tmp_path)
    config = res.get_config()
    assert config is not None
    assert "histogram" not in config["scores"][0]


def test_gene_score_with_histogram_reads_back(
    tmp_path: pathlib.Path,
) -> None:
    # A declared histogram is emitted and parsed: x_log_scale drives the
    # score's x-scale, which is read from hist_conf (not a statistics file).
    res = (
        a_gene_score()
        .with_score("pli", "float")
        .with_histogram({
            "type": "number",
            "number_of_bins": 5,
            "view_range": {"min": 0.0, "max": 1.0},
            "x_min_log": 0.001,
            "x_log_scale": True,
            "y_log_scale": False,
        })
        .with_data("""
            gene   pli
            GENE1  0.001
            GENE2  0.5
            GENE3  1.0
        """)
        .build_resource(tmp_path)
    )
    config = res.get_config()
    assert config is not None
    assert config["scores"][0]["histogram"]["number_of_bins"] == 5
    gene_score = build_gene_score_from_resource(res)
    assert gene_score.get_x_scale("pli") == "log"
    assert gene_score.get_y_scale("pli") == "linear"


def test_gene_score_with_histogram_targets_named_score(
    tmp_path: pathlib.Path,
) -> None:
    res = (
        a_gene_score()
        .with_score("pli", "float")
        .with_score("mis_z", "float")
        .with_histogram(
            {"type": "number", "number_of_bins": 3,
             "x_log_scale": True, "y_log_scale": False},
            score_id="pli")
        .with_data("""
            gene   pli   mis_z
            GENE1  0.001 1.0
            GENE2  1.0   2.0
        """)
        .build_resource(tmp_path)
    )
    gene_score = build_gene_score_from_resource(res)
    # only the targeted score got the log histogram
    assert gene_score.get_x_scale("pli") == "log"
    assert gene_score.get_x_scale("mis_z") == "linear"


def test_position_score_with_histogram_emits_block(
    tmp_path: pathlib.Path,
) -> None:
    res = (
        a_position_score()
        .with_score("phastCons", "float")
        .with_histogram({
            "type": "number",
            "number_of_bins": 4,
            "x_log_scale": False,
            "y_log_scale": False,
        })
        .with_data("chrom pos_begin phastCons\n1 10 0.5\n")
        .build_resource(tmp_path)
    )
    config = res.get_config()
    assert config is not None
    assert config["scores"][0]["histogram"]["number_of_bins"] == 4
    # the score still reads back
    score = PositionScore(res).open()
    assert score.fetch_scores("1", 10) == [0.5]


def test_with_histogram_before_any_score_raises() -> None:
    with pytest.raises(ValueError, match="call with_score first"):
        a_gene_score().with_histogram({"type": "number"})
    with pytest.raises(ValueError, match="call with_score first"):
        a_position_score().with_histogram({"type": "number"})


def test_with_histogram_unknown_score_raises() -> None:
    with pytest.raises(ValueError, match="no score 'nope'"):
        (
            a_gene_score()
            .with_score("pli", "float")
            .with_histogram({"type": "number"}, score_id="nope")
        )


def test_gene_score_builder_is_immutable() -> None:
    base = a_gene_score()
    extended = base.with_score("pli", "float")
    with_col = base.with_gene_column("symbol")
    assert base.scores == ()
    assert base.gene_column == "gene"
    assert [s.score_id for s in extended.scores] == ["pli"]
    assert with_col.gene_column == "symbol"
    assert base is not extended


def test_gene_score_builder_no_cross_variation_leak(
    tmp_path: pathlib.Path,
) -> None:
    # From one shared base, two siblings declare DIFFERENT scores and are
    # BOTH realized; each gene score must read back ONLY its own score.
    base = a_gene_score().with_data("""
        gene   sa    sb
        GENE1  0.1   0.9
        GENE2  0.2   0.8
    """)
    sibling_a = base.with_score("sa", "float")
    sibling_b = base.with_score("sb", "float")

    res_a = sibling_a.with_data("""
        gene   sa
        GENE1  0.1
        GENE2  0.2
    """).build_resource(tmp_path / "a")
    res_b = sibling_b.with_data("""
        gene   sb
        GENE1  0.9
        GENE2  0.8
    """).build_resource(tmp_path / "b")

    gs_a = build_gene_score_from_resource(res_a)
    gs_b = build_gene_score_from_resource(res_b)
    assert gs_a.get_all_scores() == ["sa"]
    assert gs_b.get_all_scores() == ["sb"]
    # the shared base is untouched by either derivation
    assert base.scores == ()


def test_grr_mixes_gene_score_position_score_and_genome(
    tmp_path: pathlib.Path,
) -> None:
    repo = (
        a_grr()
        .with_resource(
            "genomes/g",
            a_reference_genome().with_chromosome("1", "ACGTACGTAC"))
        .with_resource(
            "scores/pos",
            a_position_score()
            .with_score("sc", "float")
            .with_data("chrom pos_begin sc\n1 10 0.5\n"))
        .with_resource(
            "genes/pli",
            a_gene_score()
            .with_score("pli", "float")
            .with_data("""
                gene   pli
                GENE1  0.5
                GENE2  0.9
            """))
        .build_repo(tmp_path)
    )
    with build_reference_genome_from_resource(
            repo.get_resource("genomes/g")).open() as ref:
        assert ref.get_sequence("1", 1, 10) == "ACGTACGTAC"

    score = PositionScore(repo.get_resource("scores/pos")).open()
    assert score.fetch_scores("1", 10) == [0.5]

    gene_score = build_gene_score_from_resource(repo.get_resource("genes/pli"))
    assert gene_score.get_gene_value("pli", "GENE2") == pytest.approx(0.9)
