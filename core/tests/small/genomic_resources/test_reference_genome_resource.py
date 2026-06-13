# pylint: disable=W0621,C0114,C0116,W0212,W0613

import os
import pathlib
import textwrap
from collections.abc import Generator
from typing import Any

import pytest
import pytest_mock
from gain.genomic_resources.fsspec_protocol import build_local_resource
from gain.genomic_resources.implementations import reference_genome_impl
from gain.genomic_resources.implementations.reference_genome_impl import (
    ReferenceGenomeImplementation,
)
from gain.genomic_resources.reference_genome import (
    build_reference_genome_from_file,
    build_reference_genome_from_resource,
    build_reference_genome_from_resource_id,
    reference_genome_files,
)
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GenomicResourceRepo,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_resource,
    build_http_test_protocol,
    build_inmemory_test_repository,
    build_s3_test_protocol,
    setup_directories,
    setup_genome,
    setup_genome_bgz,
)
from gain.utils.regions import Region


@pytest.fixture
def genome_repo() -> GenomicResourceRepo:
    return build_inmemory_test_repository({
        "gosho_genome": {
            GR_CONF_FILE_NAME: """
                type: genome
                filename: chr.fa
            """,
            "chr.fa": textwrap.dedent("""
                >pesho
                NNACCCAAAC
                GGGCCTTCCN
                NNNA
                >gosho
                NNAACCGGTT
                TTGGCCAANN"""),
            "chr.fa.fai": "pesho\t24\t8\t10\t11\ngosho\t20\t42\t10\t11",
        },
    })


@pytest.fixture
def genome_fixture(tmp_path: pathlib.Path) -> pathlib.Path:
    root_path = tmp_path / "genome"
    setup_directories(root_path, {
        "genomic_resource.yaml": "{type: genome, filename: chr.fa}",
    })
    setup_genome(root_path / "chr.fa", textwrap.dedent("""
            >pesho
            NNACCCAAAC
            GGGCCTTCCN
            NNNA
            >gosho
            NNAACCGGTT
            TTGGCCAANN
    """))
    return root_path


def test_basic_sequence_resource_file(genome_fixture: pathlib.Path) -> None:
    res = build_filesystem_test_resource(genome_fixture)
    reference_genome = build_reference_genome_from_resource(res)
    with reference_genome.open() as ref:
        assert len(ref.get_all_chrom_lengths()) == 2

        assert ref.get_chrom_length("pesho") == 24
        assert ref.get_sequence("pesho", 1, 12) == "NNACCCAAACGG"

        assert ref.get_chrom_length("gosho") == 20
        assert ref.get_sequence("gosho", 11, 20) == "TTGGCCAANN"


def test_basic_sequence_resource_http(genome_fixture: pathlib.Path) -> None:
    with build_http_test_protocol(genome_fixture) as proto:
        res = proto.get_resource("")
        reference_genome = build_reference_genome_from_resource(res)
        with reference_genome.open() as ref:
            assert len(ref.get_all_chrom_lengths()) == 2

            assert ref.get_chrom_length("pesho") == 24
            assert ref.get_sequence("pesho", 1, 12) == "NNACCCAAACGG"

            assert ref.get_chrom_length("gosho") == 20
            assert ref.get_sequence("gosho", 11, 20) == "TTGGCCAANN"


def test_filesystem_genomic_sequence(genome_fixture: pathlib.Path) -> None:
    reference_genome = build_reference_genome_from_file(
        str(genome_fixture / "chr.fa"))

    assert reference_genome is not None
    with reference_genome.open() as ref:
        assert len(ref.get_all_chrom_lengths()) == 2

        assert ref.get_chrom_length("pesho") == 24
        assert ref.get_sequence("pesho", 1, 12) == "NNACCCAAACGG"

        assert ref.get_chrom_length("gosho") == 20
        assert ref.get_sequence("gosho", 11, 20) == "TTGGCCAANN"


def test_local_genomic_sequence(genome_fixture: pathlib.Path) -> None:

    res = build_local_resource(str(genome_fixture), {
        "type": "genome",
        "filename": "chr.fa",
    })
    assert res is not None

    reference_genome = build_reference_genome_from_resource(res)
    with reference_genome.open() as ref:
        assert len(ref.get_all_chrom_lengths()) == 2

        assert ref.get_chrom_length("pesho") == 24
        assert ref.get_sequence("pesho", 1, 12) == "NNACCCAAACGG"

        assert ref.get_chrom_length("gosho") == 20
        assert ref.get_sequence("gosho", 11, 20) == "TTGGCCAANN"


@pytest.fixture
def bgz_genome_fixture(tmp_path: pathlib.Path) -> pathlib.Path:
    root_path = tmp_path / "bgz_genome"
    setup_directories(root_path, {
        "genomic_resource.yaml": "{type: genome, filename: chr.fa.gz}",
    })
    setup_genome_bgz(root_path / "chr.fa.gz", textwrap.dedent("""
            >pesho
            NNACCCAAAC
            GGGCCTTCCN
            NNNA
            >gosho
            NNAACCGGTT
            TTGGCCAANN
    """))
    return root_path


def test_bgz_sequence_resource_file(bgz_genome_fixture: pathlib.Path) -> None:
    res = build_filesystem_test_resource(bgz_genome_fixture)
    reference_genome = build_reference_genome_from_resource(res)
    with reference_genome.open() as ref:
        assert ref.get_chrom_length("pesho") == 24
        assert ref.get_sequence("pesho", 1, 12) == "NNACCCAAACGG"


def test_bgz_matches_plain_fa(tmp_path: pathlib.Path) -> None:
    content = textwrap.dedent("""
        >pesho
        NNACCCaaaC
        GGGCCTTCCN
        NNNA
        >gosho
        NNAACCGGTT
        TTGGCCAANN
    """)
    plain = setup_genome(tmp_path / "plain" / "chr.fa", content)
    bgz = setup_genome_bgz(tmp_path / "bgz" / "chr.fa.gz", content)
    regions = [
        ("pesho", 1, 12),
        ("pesho", 5, 10),   # spans the lowercase 'aaa' -> must upper-case
        ("pesho", 20, 24),  # end of chromosome
        ("gosho", 1, 20),   # whole chromosome
        ("gosho", 11, 20),
    ]
    for chrom, start, stop in regions:
        assert bgz.get_sequence(chrom, start, stop) \
            == plain.get_sequence(chrom, start, stop), (chrom, start, stop)


def test_bgz_fetch_small_buffer(bgz_genome_fixture: pathlib.Path) -> None:
    res = build_filesystem_test_resource(bgz_genome_fixture)
    with build_reference_genome_from_resource(res).open() as ref:
        full = ref.get_sequence("pesho", 1, 24)
        # the whole chromosome fetched in tiny 3bp windows must reassemble
        chunked = "".join(ref.fetch("pesho", 1, 24, buffer_size=3))
        assert chunked == full
        assert len(chunked) == 24


def test_bgz_fetch_to_end_of_chromosome(
        bgz_genome_fixture: pathlib.Path) -> None:
    res = build_filesystem_test_resource(bgz_genome_fixture)
    with build_reference_genome_from_resource(res).open() as ref:
        # stop=None means "to the end of the chromosome"
        assert "".join(ref.fetch("gosho", 11, None)) == "TTGGCCAANN"


def test_bgz_fetch_unknown_chromosome_yields_nothing(
        bgz_genome_fixture: pathlib.Path) -> None:
    res = build_filesystem_test_resource(bgz_genome_fixture)
    with build_reference_genome_from_resource(res).open() as ref:
        assert not list(ref.fetch("nonexistent", 1, 10))


def test_bgz_missing_gzi_raises_actionable_error(
        bgz_genome_fixture: pathlib.Path) -> None:
    (bgz_genome_fixture / "chr.fa.gz.gzi").unlink()
    res = build_filesystem_test_resource(bgz_genome_fixture)
    reference_genome = build_reference_genome_from_resource(res)
    with pytest.raises(ValueError, match=r"\.gzi"):
        reference_genome.open()


def test_bgz_on_inmemory_protocol_raises() -> None:
    repo = build_inmemory_test_repository({
        "bgz_genome": {
            GR_CONF_FILE_NAME: """
                type: genome
                filename: chr.fa.gz
            """,
            "chr.fa.gz": "ignored",
            "chr.fa.gz.fai": "pesho\t24\t8\t10\t11",
            "chr.fa.gz.gzi": "ignored",
        },
    })
    res = repo.get_resource("bgz_genome")
    reference_genome = build_reference_genome_from_resource(res)
    with pytest.raises(OSError, match="not supported"):
        reference_genome.open()


@pytest.fixture
def bgz_remote_genome(
    tmp_path: pathlib.Path,
    grr_scheme: str,
    mocker: pytest_mock.MockerFixture,
) -> Generator[Any, None, None]:
    mocker.patch.dict(os.environ, {
        "AWS_SECRET_ACCESS_KEY": "minioadmin",
        "AWS_ACCESS_KEY_ID": "minioadmin",
    })
    root = tmp_path / "bgz_genome"
    setup_genome_bgz(root / "chr.fa.gz", _SOFT_MASKED_GENOME)

    if grr_scheme == "http":
        with build_http_test_protocol(root) as proto:
            yield proto.get_resource("")
    elif grr_scheme == "s3":
        with build_s3_test_protocol(root) as proto:
            yield proto.get_resource("")
    else:  # file
        yield build_filesystem_test_resource(root)


# grr_tabix parametrizes over file/s3/http (pysam cannot read inmemory)
@pytest.mark.grr_tabix
def test_bgz_genome_over_remote_protocol(bgz_remote_genome: Any) -> None:
    with build_reference_genome_from_resource(bgz_remote_genome).open() as ref:
        assert ref.get_chrom_length("pesho") == 24
        assert ref.get_sequence("pesho", 1, 12) == "NNACCCAAACGG"
        assert ref.get_sequence("gosho", 11, 20) == "TTGGCCAANN"


def test_chromosome_statistic_basic(genome_fixture: pathlib.Path) -> None:
    res = build_filesystem_test_resource(genome_fixture)
    stat = ReferenceGenomeImplementation._do_chrom_statistic(
        res, "pesho", 1, None,
    )

    assert stat.length == 24

    assert stat.nucleotide_counts["A"] == 5
    assert stat.nucleotide_counts["C"] == 8
    assert stat.nucleotide_counts["G"] == 3
    assert stat.nucleotide_counts["T"] == 2
    assert stat.nucleotide_counts["N"] == 6
    total_nucleotides = stat.length

    assert stat.nucleotide_pair_counts["AA"] == 2
    assert stat.nucleotide_pair_counts["AG"] == 0
    assert stat.nucleotide_pair_counts["AC"] == 2
    assert stat.nucleotide_pair_counts["AT"] == 0
    assert stat.nucleotide_pair_counts["GA"] == 0
    assert stat.nucleotide_pair_counts["GG"] == 2
    assert stat.nucleotide_pair_counts["GC"] == 1
    assert stat.nucleotide_pair_counts["GT"] == 0
    assert stat.nucleotide_pair_counts["CA"] == 1
    assert stat.nucleotide_pair_counts["CG"] == 1
    assert stat.nucleotide_pair_counts["CC"] == 4
    assert stat.nucleotide_pair_counts["CT"] == 1
    assert stat.nucleotide_pair_counts["TA"] == 0
    assert stat.nucleotide_pair_counts["TG"] == 0
    assert stat.nucleotide_pair_counts["TC"] == 1
    assert stat.nucleotide_pair_counts["TT"] == 1
    total_pairs = sum(stat.nucleotide_pair_counts.values())

    for pair, count in stat.nucleotide_pair_counts.items():
        assert stat.bi_nucleotide_distribution[pair] == \
            pytest.approx(count / total_pairs * 100)

    for nuc, count in stat.nucleotide_counts.items():
        assert stat.nucleotide_distribution[nuc] == \
            pytest.approx(count / total_nucleotides * 100)

    stat.finish()

    print(stat.serialize())
    print(stat.bi_nucleotide_distribution)
    print(stat.nucleotide_distribution)

    ReferenceGenomeImplementation._save_chrom_statistic(res, "pesho", stat)

    assert os.path.exists(os.path.join(
        genome_fixture,
        "statistics",
        "pesho_statistic.yaml",
    ))


_SOFT_MASKED_GENOME = textwrap.dedent("""
    >pesho
    NNACCCaaaC
    GGGCCTTCCN
    NNNA
    >gosho
    NNAACCGGTT
    TTGGCCAANN
""")


def _setup_plain_and_bgz_resources(
        tmp_path: pathlib.Path, content: str,
) -> tuple[Any, Any]:
    setup_genome(tmp_path / "plain" / "chr.fa", content)
    setup_genome_bgz(tmp_path / "bgz" / "chr.fa.gz", content)
    return (
        build_filesystem_test_resource(tmp_path / "plain"),
        build_filesystem_test_resource(tmp_path / "bgz"),
    )


def test_bgz_chromosome_statistic_parity(tmp_path: pathlib.Path) -> None:
    plain_res, bgz_res = _setup_plain_and_bgz_resources(
        tmp_path, _SOFT_MASKED_GENOME)

    for chrom in ("pesho", "gosho"):
        plain = ReferenceGenomeImplementation._do_chrom_statistic(
            plain_res, chrom, 1, None)
        bgz = ReferenceGenomeImplementation._do_chrom_statistic(
            bgz_res, chrom, 1, None)
        assert bgz.length == plain.length
        assert bgz.nucleotide_counts == plain.nucleotide_counts
        assert bgz.nucleotide_pair_counts == plain.nucleotide_pair_counts


def test_bgz_chromosome_statistic_chunked(
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch) -> None:
    plain_res, bgz_res = _setup_plain_and_bgz_resources(
        tmp_path, _SOFT_MASKED_GENOME)

    # force the whole-chromosome stats fetch to span many tiny windows so the
    # chunk-boundary handling in the bgz path is actually exercised
    monkeypatch.setattr(
        reference_genome_impl,
        "CHROMOSOME_STATISTIC_FETCH_BUFFER_SIZE", 3)

    for chrom in ("pesho", "gosho"):
        plain = ReferenceGenomeImplementation._do_chrom_statistic(
            plain_res, chrom, 1, None)
        bgz = ReferenceGenomeImplementation._do_chrom_statistic(
            bgz_res, chrom, 1, None)
        assert bgz.nucleotide_counts == plain.nucleotide_counts
        assert bgz.nucleotide_pair_counts == plain.nucleotide_pair_counts


def test_reference_genome_fetch(genome_fixture: pathlib.Path) -> None:
    res = build_filesystem_test_resource(genome_fixture)
    impl = ReferenceGenomeImplementation(res)

    with impl.reference_genome.open():
        result = list(impl.reference_genome.fetch("pesho", 1, 10))

        assert result == [
            "N",
            "N",
            "A",
            "C",
            "C",
            "C",
            "A",
            "A",
            "A",
            "C",
        ]

        result = list(impl.reference_genome.fetch("pesho", 18, 36))

        assert result == [
            "C",
            "C",
            "N",
            "N",
            "N",
            "N",
            "A",
        ]

        result = list(impl.reference_genome.fetch("pesho", 1, None))

        assert result == [
            "N",
            "N",
            "A",
            "C",
            "C",
            "C",
            "A",
            "A",
            "A",
            "C",
            "G",
            "G",
            "G",
            "C",
            "C",
            "T",
            "T",
            "C",
            "C",
            "N",
            "N",
            "N",
            "N",
            "A",
        ]


def test_reference_genome_fetch_corner_case(
        genome_fixture: pathlib.Path) -> None:
    res = build_filesystem_test_resource(genome_fixture)
    reference_genome = build_reference_genome_from_resource(res)

    with reference_genome.open():
        result = list(reference_genome.fetch("pesho", 1, 9))
        assert result == [
            "N",
            "N",
            "A",
            "C",
            "C",
            "C",
            "A",
            "A",
            "A",
        ]


def test_reference_genome_fetch_small_buffer(
        genome_fixture: pathlib.Path) -> None:
    res = build_filesystem_test_resource(genome_fixture)
    reference_genome = build_reference_genome_from_resource(res)

    with reference_genome.open():
        result = list(reference_genome.fetch("pesho", 1, 9, 1))
        assert result == [
            "N",
            "N",
            "A",
            "C",
            "C",
            "C",
            "A",
            "A",
            "A",
        ]


def test_build_reference_genome_from_resource_id(
    genome_repo: GenomicResourceRepo,
) -> None:
    reference_genome = build_reference_genome_from_resource_id(
        "gosho_genome", genome_repo)
    with reference_genome.open() as ref:
        assert len(ref.get_all_chrom_lengths()) == 2
        assert ref.get_chrom_length("pesho") == 24
        assert ref.get_chrom_length("gosho") == 20


def test_reference_genome_files_plain_fasta() -> None:
    assert reference_genome_files({"filename": "chr.fa"}) == {
        "chr.fa", "chr.fa.fai",
    }


def test_reference_genome_files_gz_includes_gzi() -> None:
    assert reference_genome_files({"filename": "chr.fa.gz"}) == {
        "chr.fa.gz", "chr.fa.gz.fai", "chr.fa.gz.gzi",
    }


def test_reference_genome_files_bgz_includes_gzi() -> None:
    assert reference_genome_files({"filename": "chr.fa.bgz"}) == {
        "chr.fa.bgz", "chr.fa.bgz.fai", "chr.fa.bgz.gzi",
    }


def test_reference_genome_files_honors_custom_index_file() -> None:
    assert reference_genome_files({
        "filename": "chr.fa",
        "index_file": "custom.fai",
    }) == {"chr.fa", "custom.fai"}


def test_reference_genome_files_custom_index_gzi_derives_from_filename(
) -> None:
    # With a custom index_file AND a bgzipped genome, the .gzi must derive
    # from the genome filename, never from the custom index name.
    assert reference_genome_files({
        "filename": "chr.fa.gz",
        "index_file": "custom.fai",
    }) == {"chr.fa.gz", "custom.fai", "chr.fa.gz.gzi"}


def test_implementation_files_includes_gzi_for_bgzipped_genome(
        bgz_genome_fixture: pathlib.Path) -> None:
    res = build_filesystem_test_resource(bgz_genome_fixture)
    impl = ReferenceGenomeImplementation(res)
    assert impl.files == {
        "chr.fa.gz", "chr.fa.gz.fai", "chr.fa.gz.gzi",
    }


def test_implementation_files_plain_fasta(
        genome_fixture: pathlib.Path) -> None:
    res = build_filesystem_test_resource(genome_fixture)
    impl = ReferenceGenomeImplementation(res)
    assert impl.files == {"chr.fa", "chr.fa.fai"}


def test_reference_genome_split_into_regions(
    genome_fixture: pathlib.Path,
) -> None:
    res = build_filesystem_test_resource(genome_fixture)
    reference_genome = build_reference_genome_from_resource(res)

    with reference_genome.open():
        regions = list(reference_genome.split_into_regions(0))
        assert regions == [
            Region("pesho", 1),
            Region("gosho", 1),
        ]

        regions = list(reference_genome.split_into_regions(10))
        assert regions == [
            Region("pesho", 1, 10),
            Region("pesho", 11, 20),
            Region("pesho", 21),
            Region("gosho", 1, 10),
            Region("gosho", 11),
        ]

        regions = list(reference_genome.split_into_regions(0, "gosho"))
        assert regions == [
            Region("gosho", 1),
        ]

        regions = list(reference_genome.split_into_regions(10, "gosho"))
        assert regions == [
            Region("gosho", 1, 10),
            Region("gosho", 11),
        ]
