# pylint: disable=C0114,C0116,W0212,W0621
import gc
import pathlib
import textwrap
import weakref

from gain.genomic_resources.implementations.genomic_scores_impl import (
    GenomicScoreImplementation,
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


def _a_chr1_score(genome_id: str | None = None):
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
    GenomicScoreImplementation._do_noregion_histograms(resource)
    return GenomicScoreImplementation(repo.get_resource(resource_id))


def _a_repo_with_genome(
    where: pathlib.Path, chrom_length: int,
) -> GenomicResourceRepo:
    """A chr1 score, labelled with a genome of the given chr1 length.

    The genome id is the same in every repository built here, which is
    the point: it is what two repositories are allowed to share.
    """
    return (
        a_grr()
        .with_resource("scores/one", _a_chr1_score("genomes/shared"))
        .with_resource(
            "genomes/shared",
            a_reference_genome().with_chromosome("chr1", "A" * chrom_length))
        .build_repo(where)
    )


def test_genome_labeled_score_shows_percent_covered(
    tmp_path: pathlib.Path,
) -> None:
    repo = (
        a_grr()
        .with_resource("scores/one", _a_chr1_score("genomes/g776a"))
        .with_resource(
            "genomes/g776a",
            a_reference_genome().with_chromosome("chr1", "A" * 100))
        .build_repo(tmp_path)
    )
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
    wide = _a_repo_with_genome(tmp_path / "wide", 100)
    narrow = _a_repo_with_genome(tmp_path / "narrow", 50)

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
    repo = _a_repo_with_genome(tmp_path, 100)
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


def test_bigwig_score_without_a_label_uses_header_sizes(
    tmp_path: pathlib.Path,
) -> None:
    repo = (
        a_grr()
        .with_resource(
            "scores/bw",
            a_bigwig_score()
            .with_data(
                """
                chr1  4   9   0.1
                chr1  29  33  0.2
                """)
            .with_chrom_lens({"chr1": 100}))
        .build_repo(tmp_path)
    )
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
    repo = (
        a_grr()
        .with_resource("scores/one", _a_chr1_score("genomes/gshort776"))
        .with_resource(
            "genomes/gshort776",
            a_reference_genome().with_chromosome("chr1", "ACGTAC"))
        .build_repo(tmp_path)
    )
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
    repo = (
        a_grr()
        .with_resource("scores/one", _a_chr1_score("genomes/g776s"))
        .with_resource(
            "genomes/g776s",
            a_reference_genome().with_chromosome("chr1", "A" * 100))
        .build_repo(tmp_path)
    )
    impl = _built_impl(repo, "scores/one")

    page = impl.get_statistics_info(repo=repo)

    assert "Filename" in page  # the statistics-file listing rendered


def test_an_already_open_bigwig_score_stays_open(
    tmp_path: pathlib.Path,
) -> None:
    repo = (
        a_grr()
        .with_resource(
            "scores/bw",
            a_bigwig_score()
            .with_data("chr1  4  9  0.1")
            .with_chrom_lens({"chr1": 100}))
        .build_repo(tmp_path)
    )
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
    a_grr().with_resource(
        "scores/one", _a_chr1_score("genomes/gzero776"),
    ).build_repo(tmp_path)
    setup_directories(tmp_path / "genomes" / "gzero776", {
        "genomic_resource.yaml": "{type: genome, filename: genome.fa}",
        "genome.fa": ">chr1\n",
        "genome.fa.fai": "chr1\t0\t6\t60\t61\n",
    })
    repo = build_filesystem_test_repository(tmp_path)
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
