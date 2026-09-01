# pylint: disable=C0114,C0116,W0212,W0621
import gc
import logging
import pathlib
import textwrap
import weakref
from typing import Any

import pytest
from gain.genomic_resources.implementations.genomic_scores_impl import (
    GenomicScoreImplementation,
    scan,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing import (
    build_filesystem_test_repository,
    setup_bigwig,
    setup_directories,
)
from gain.genomic_resources.testing.builders import (
    a_bigwig_score,
    a_grr,
    a_position_score,
    a_reference_genome,
)

from .conftest import UNUSABLE_RESOURCE_ID_LABELS, label_warnings


def _a_chr1_score(genome_id: Any = None):
    builder = (
        a_position_score()
        .with_score("score", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  score
            chr1   5          9        0.1
            chr1   30         33       0.2
            """)
        .with_tabix()
    )
    if genome_id is not None:
        builder = builder.with_labels(reference_genome=genome_id)
    return builder


COVERED = 9  # 5..9 and 30..33


def _built_impl(
    repo: GenomicResourceRepo, resource_id: str,
) -> GenomicScoreImplementation:
    resource = repo.get_resource(resource_id)
    scan.do_noregion_histograms(resource)
    return GenomicScoreImplementation(repo.get_resource(resource_id))


def _a_repo_with_genome(
    where: pathlib.Path,
    genome_id: str,
    **chrom_lengths: int,
) -> GenomicResourceRepo:
    """The chr1 score, labelled with a genome of these contig lengths.

    ``genome_id`` is a parameter rather than a constant because two
    repositories sharing one id -- each defining it differently -- is
    itself a case under test here.  Contigs beyond chr1 are the ones
    the score never touches.
    """
    genome = a_reference_genome()
    for chrom, length in chrom_lengths.items():
        genome = genome.with_chromosome(chrom, "A" * length)
    return (
        a_grr()
        .with_resource("scores/one", _a_chr1_score(genome_id))
        .with_resource(genome_id, genome)
        .build_repo(where)
    )


def _a_repo_with_a_raw_fai(
    where: pathlib.Path,
    genome_id: str,
    fasta: str,
    fai: str,
) -> GenomicResourceRepo:
    """The chr1 score against a genome written as raw FASTA plus index.

    For the genome shapes the builders refuse -- a zero-length contig is
    an empty FASTA record, which pysam's faidx will not synthesize --
    the convention is hand-rolled files.  The five ``.fai`` columns are
    name, length, offset, line bases and line width; only the length is
    what these tests are about, but all five have to be right or the
    genome does not open at all.
    """
    a_grr().with_resource(
        "scores/one", _a_chr1_score(genome_id),
    ).build_repo(where)
    setup_directories(where / pathlib.Path(genome_id), {
        "genomic_resource.yaml": "{type: genome, filename: genome.fa}",
        "genome.fa": fasta,
        "genome.fa.fai": fai,
    })
    return build_filesystem_test_repository(where)


def test_genome_labeled_score_shows_percent_covered(
    tmp_path: pathlib.Path,
) -> None:
    repo = _a_repo_with_genome(tmp_path, "genomes/g776a", chr1=100)
    impl = _built_impl(repo, "scores/one")

    page = impl.get_info(repo=repo)

    assert f">{COVERED}<" in page
    assert page.count(">9.00%<") == 2  # the chr1 row and the global row


def test_two_repos_sharing_a_genome_id_do_not_cross_serve(
    tmp_path: pathlib.Path,
) -> None:
    """Each repository's score is measured against its OWN genome.

    Two repositories are free to define the same genome id with
    different content -- a private repository shadowing a public
    ``genomes/hg38`` is the shape this takes in the wild.  The score is
    identical in both, so only the denominator differs and the rendered
    percentage is what tells the two genomes apart.
    """
    wide = _a_repo_with_genome(tmp_path / "wide", "genomes/shared", chr1=100)
    narrow = _a_repo_with_genome(tmp_path / "narrow", "genomes/shared", chr1=50)

    wide_page = _built_impl(wide, "scores/one").get_info(repo=wide)
    narrow_page = _built_impl(narrow, "scores/one").get_info(repo=narrow)

    assert wide_page.count(">9.00%<") == 2  # 9 of 100
    assert narrow_page.count(">18.00%<") == 2  # 9 of 50


def test_rendering_does_not_pin_the_repository(
    tmp_path: pathlib.Path,
) -> None:
    """A rendered repository stays collectable once its caller lets go.

    The resolved-genome cache is keyed by repository, so it is exactly
    the thing that could hold one forever.  A process that renders
    against many repositories -- a test session is the extreme case --
    must not accumulate them.
    """
    repo = _a_repo_with_genome(tmp_path, "genomes/shared", chr1=100)
    _built_impl(repo, "scores/one").get_info(repo=repo)
    repo_ref = weakref.ref(repo)

    del repo
    gc.collect()

    assert repo_ref() is None


def test_score_without_a_denominator_renders_raw_counts_only(
    tmp_path: pathlib.Path,
) -> None:
    repo = (
        a_grr()
        .with_resource("scores/one", _a_chr1_score())
        .build_repo(tmp_path)
    )
    impl = _built_impl(repo, "scores/one")

    page = impl.get_info(repo=repo)

    assert f">{COVERED}<" in page
    assert "Covered %" not in page
    assert "%<" not in page


#: The bedGraph the bigWig fixtures below carry, covering the same 9
#: positions of chr1 as ``_a_chr1_score`` -- so a bigWig page and a tabix
#: page differ only in the rung their denominator comes from.
_BIGWIG_DATA = """
    chr1  4   9   0.1
    chr1  29  33  0.2
    """


def _a_bigwig_repo(
    where: pathlib.Path,
    chrom_lens: dict[str, int],
    data: str = _BIGWIG_DATA,
) -> GenomicResourceRepo:
    """An unlabelled bigWig score with these header contig sizes."""
    return (
        a_grr()
        .with_resource(
            "scores/bw",
            a_bigwig_score().with_data(data).with_chrom_lens(chrom_lens))
        .build_repo(where)
    )


def test_bigwig_score_without_a_label_uses_header_sizes(
    tmp_path: pathlib.Path,
) -> None:
    repo = _a_bigwig_repo(tmp_path, {"chr1": 100})
    impl = _built_impl(repo, "scores/bw")

    page = impl.get_info(repo=repo)

    assert page.count(">9.00%<") == 2  # the chr1 row and the global row


def test_contig_unknown_to_the_genome_renders_raw_counts_for_it(
    tmp_path: pathlib.Path,
) -> None:
    repo = (
        a_grr()
        .with_resource(
            "scores/two",
            a_position_score()
            .with_score("score", "float")
            .with_data(
                """
                chrom  pos_begin  pos_end  score
                chr1   5          9        0.1
                chr2   1          10       0.2
                """)
            .with_tabix()
            .with_labels(reference_genome="genomes/g776b"))
        .with_resource(
            "genomes/g776b",
            a_reference_genome().with_chromosome("chr1", "A" * 50))
        .build_repo(tmp_path)
    )
    impl = _built_impl(repo, "scores/two")

    page = impl.get_info(repo=repo)

    # chr1 resolves (5/50); chr2 is unknown to the genome, so its row and
    # the global row stay raw -- exactly one percent value on the page.
    assert page.count(">10.00%<") == 1
    assert page.count("%</td>") == 1


def test_a_mislabeled_genome_never_renders_more_than_100_percent(
    tmp_path: pathlib.Path,
) -> None:
    # 9 covered positions against a genome claiming chr1 is 6 long: the
    # denominator is proven wrong, so the row and the global stay raw.
    repo = _a_repo_with_genome(tmp_path, "genomes/gshort776", chr1=6)
    impl = _built_impl(repo, "scores/one")

    page = impl.get_info(repo=repo)

    assert f">{COVERED}<" in page
    assert "Covered %" not in page


def test_statistics_page_still_builds_with_the_repo_kwarg(
    tmp_path: pathlib.Path,
) -> None:
    # The statistics page is a statistics-file listing; it has never
    # rendered the Coverage section (its base template carries no content
    # block).  Pin that consuming the repo kwarg leaves it working.
    repo = _a_repo_with_genome(tmp_path, "genomes/g776s", chr1=100)
    impl = _built_impl(repo, "scores/one")

    page = impl.get_statistics_info(repo=repo)

    assert "Filename" in page  # the statistics-file listing rendered


def test_an_already_open_bigwig_score_stays_open(
    tmp_path: pathlib.Path,
) -> None:
    repo = _a_bigwig_repo(
        tmp_path, {"chr1": 100}, data="chr1  4  9  0.1")
    impl = _built_impl(repo, "scores/bw")
    impl.score.open()

    impl.get_info(repo=repo)

    assert impl.score.is_open()
    impl.score.close()


def test_chrom_mapped_bigwig_resolves_header_sizes_through_the_mapping(
    tmp_path: pathlib.Path,
) -> None:
    # File contigs carry a chr prefix; the table strips it, so the
    # coverage statistic speaks in bare names and the header lookup has
    # to travel back through the mapping.  No builder covers a mapped
    # bigWig -- hand-rolled yaml is the convention for that shape.
    setup_directories(tmp_path / "scores" / "bwmap", {
        "genomic_resource.yaml": textwrap.dedent("""
            type: position_score
            table:
                filename: data.bw
                chrom_mapping:
                    del_prefix: chr
            scores:
            - id: score
              type: float
        """),
    })
    setup_bigwig(
        tmp_path / "scores" / "bwmap" / "data.bw",
        "chr1  4  9  0.1",
        {"chr1": 50})
    repo = build_filesystem_test_repository(tmp_path)
    impl = _built_impl(repo, "scores/bwmap")

    page = impl.get_info(repo=repo)

    assert ">1<" in page  # the mapped contig row is the bare name
    assert page.count(">10.00%<") == 2  # 5 covered / 50, row and global


def test_a_zero_length_genome_contig_degrades_to_raw_counts(
    tmp_path: pathlib.Path,
) -> None:
    # The builders refuse a zero-length chromosome, but a real .fai can
    # carry one (an empty FASTA record) -- hand-roll the genome so the
    # degradation contract is pinned against exactly that shape.
    repo = _a_repo_with_a_raw_fai(
        tmp_path, "genomes/gzero776",
        ">chr1\n", "chr1\t0\t6\t60\t61\n")
    impl = _built_impl(repo, "scores/one")

    page = impl.get_info(repo=repo)

    assert f">{COVERED}<" in page
    assert "Covered %" not in page


def test_a_dangling_genome_label_degrades_to_raw_counts(
    tmp_path: pathlib.Path,
) -> None:
    repo = (
        a_grr()
        .with_resource(
            "scores/one", _a_chr1_score("genomes/not-there-776"))
        .build_repo(tmp_path)
    )
    impl = _built_impl(repo, "scores/one")

    page = impl.get_info(repo=repo)

    assert f">{COVERED}<" in page
    assert "Covered %" not in page


def test_a_label_naming_a_non_genome_resource_degrades_to_raw_counts(
    tmp_path: pathlib.Path,
) -> None:
    repo = (
        a_grr()
        .with_resource("scores/one", _a_chr1_score("scores/other776"))
        .with_resource(
            "scores/other776",
            a_position_score()
            .with_score("other", "float")
            .with_data(
                """
                chrom  pos_begin  other
                chr1   1          0.5
                """))
        .build_repo(tmp_path)
    )
    impl = _built_impl(repo, "scores/one")

    page = impl.get_info(repo=repo)

    assert f">{COVERED}<" in page
    assert "Covered %" not in page


@pytest.mark.parametrize(
    ("value", "reported_as"), UNUSABLE_RESOURCE_ID_LABELS)
def test_an_unusable_genome_label_degrades_to_raw_counts(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    value: Any,
    reported_as: str,
) -> None:
    """A label that cannot name a resource is a raw-counts page, not a crash.

    The two tests above cover the labels that ARE resource ids and fail
    at resolution; these are the ones that are not ids at all.  Read
    unnarrowed they reached the resolution cache as themselves and died
    there with a ``TypeError`` -- the int in the genome regex, the list
    and the dict in the cache dict's own key lookup -- which escapes the
    ``except ValueError`` this method degrades through, failing the page
    build its comment says must not fail (gain#1053).
    """
    repo = (
        a_grr()
        .with_resource("scores/one", _a_chr1_score(value))
        .build_repo(tmp_path)
    )
    impl = _built_impl(repo, "scores/one")

    with caplog.at_level(logging.WARNING):
        page = impl.get_info(repo=repo)

    assert f">{COVERED}<" in page
    assert "Covered %" not in page
    # Scoped to the label's own warnings: this fixture's table omits
    # ``zero_based`` and says so once per open, which is a different
    # message about a different key and not what this pins.
    warnings = label_warnings(caplog)
    assert len(warnings) == 1
    assert "scores/one" in warnings[0]
    assert "reference_genome" in warnings[0]
    assert reported_as in warnings[0]


@pytest.mark.parametrize("builder", [
    pytest.param(_a_chr1_score(), id="no-meta-block-at-all"),
    pytest.param(
        _a_chr1_score().with_labels(domain="score"),
        id="labels-without-the-key"),
    pytest.param(
        _a_chr1_score().with_labels(reference_genome=None),
        id="the-key-an-explicit-yaml-null"),
])
def test_a_score_declaring_no_genome_is_silent(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    builder: Any,
) -> None:
    """Not labelling a score is a supported state, so it is not reported.

    The raw-counts page above is what an unlabelled score is *for*, and
    the production GRRs carry the explicit-null spelling -- warning on
    either would fire on the resources that are already right.  Pinned
    at this seam as well as at the gene-models and liftover ones
    because that is where the warning would be seen.
    """
    repo = (
        a_grr()
        .with_resource("scores/one", builder)
        .build_repo(tmp_path)
    )
    impl = _built_impl(repo, "scores/one")

    with caplog.at_level(logging.WARNING):
        page = impl.get_info(repo=repo)

    assert f">{COVERED}<" in page
    assert label_warnings(caplog) == []


def _a_repo_with_an_untouched_contig(
    where: pathlib.Path,
) -> GenomicResourceRepo:
    """A chr1-only score against a genome that also has chr2.

    The score touches a strict subset of the genome's contigs, which is
    the whole point: chr2 is 300bp of reference the score says nothing
    about, and the global fraction has to count it.
    """
    return _a_repo_with_genome(
        where, "genomes/g1041", chr1=100, chr2=300)


def test_global_fraction_counts_contigs_the_score_never_touches(
    tmp_path: pathlib.Path,
) -> None:
    repo = _a_repo_with_an_untouched_contig(tmp_path)
    impl = _built_impl(repo, "scores/one")

    page = impl.get_info(repo=repo)

    assert page.count(">9.00%<") == 1  # the chr1 row: 9 of 100
    assert ">2.25%<" in page  # the global row: 9 of the genome's 400


def test_untouched_contigs_render_as_one_rollup_row(
    tmp_path: pathlib.Path,
) -> None:
    repo = _a_repo_with_an_untouched_contig(tmp_path)
    impl = _built_impl(repo, "scores/one")

    page = impl.get_info(repo=repo)

    assert "1 contig with no values (300 bp)" in page


def test_bigwig_header_contigs_without_values_join_the_denominator(
    tmp_path: pathlib.Path,
) -> None:
    # The bigWig rung's universe is the header's whole contig list, not
    # only the contigs the statistic touched.  This backend's scan also
    # STORES a zero for chr2 -- so this pins that the roll-up counts a
    # contig by having no values, not by being absent from the file.
    repo = _a_bigwig_repo(tmp_path, {"chr1": 100, "chr2": 300})
    impl = _built_impl(repo, "scores/bw")

    page = impl.get_info(repo=repo)

    assert page.count(">9.00%<") == 1  # the chr1 row
    assert ">2.25%<" in page  # the global row: 9 of the header's 400
    assert "1 contig with no values (300 bp)" in page
    # chr2 IS in this backend's stored counts, at zero.  Absent from the
    # body it is reported once; left there it would be counted twice --
    # a 0.00% row AND a base pair of the roll-up.
    assert ">chr2<" not in page


def test_a_covered_contig_missing_from_the_genome_suppresses_the_rollup(
    tmp_path: pathlib.Path,
) -> None:
    """A mislabeled genome degrades the global -- and the roll-up with it.

    "chr2 has no values" is a claim about the genome being the right
    one, and a covered contig the genome does not list is proof it is
    not.  The uncovered contigs are not reported as fact under a
    denominator already known to be wrong.
    """
    repo = (
        a_grr()
        .with_resource(
            "scores/two",
            a_position_score()
            .with_score("score", "float")
            .with_data(
                """
                chrom  pos_begin  pos_end  score
                chr1   5          9        0.1
                chrX   1          10       0.2
                """)
            .with_tabix()
            .with_labels(reference_genome="genomes/g1041b"))
        .with_resource(
            "genomes/g1041b",
            a_reference_genome()
            .with_chromosome("chr1", "A" * 50)
            .with_chromosome("chr2", "C" * 300))
        .build_repo(tmp_path)
    )
    impl = _built_impl(repo, "scores/two")

    page = impl.get_info(repo=repo)

    assert "contig with no values" not in page
    assert "contigs with no values" not in page


def test_an_implausible_untouched_contig_leaves_the_denominator(
    tmp_path: pathlib.Path,
) -> None:
    # A zero-length .fai record on a contig the score never touched: it
    # cannot bound anything, so it is out of the denominator and out of
    # the roll-up rather than inflating one and being counted by the
    # other.
    repo = _a_repo_with_a_raw_fai(
        tmp_path, "genomes/gz1041",
        ">chr1\n" + "A" * 100 + "\n",
        "chr1\t100\t7\t100\t101\nchr2\t0\t115\t60\t61\n")
    impl = _built_impl(repo, "scores/one")

    page = impl.get_info(repo=repo)

    assert page.count(">9.00%<") == 2  # 9 of chr1's 100, row and global
    assert "with no values" not in page


def test_several_untouched_contigs_roll_up_into_one_row(
    tmp_path: pathlib.Path,
) -> None:
    repo = _a_repo_with_genome(
        tmp_path, "genomes/g1041c", chr1=100, chr2=300, chr3=600)
    impl = _built_impl(repo, "scores/one")

    page = impl.get_info(repo=repo)

    assert "2 contigs with no values (900 bp)" in page
    assert ">0.90%<" in page  # the global row: 9 of the genome's 1000
