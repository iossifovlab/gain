# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pathlib
import textwrap

import pytest
from gain.genomic_resources.cli import _create_contents_db, cli_manage
from gain.genomic_resources.repository import (
    GenomicResourceProtocolRepo,
    GenomicResourceRepo,
    parse_gr_id_version_token,
    parse_resource_id_version,
)
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
    convert_to_tab_separated,
    setup_directories,
)


@pytest.mark.parametrize(
    "token,gr_id,version", [
        ("gene_models(1.0)", "gene_models", (1, 0)),
        ("gene_models(0)", "gene_models", (0,)),
        ("gene_models", "gene_models", (0,)),
    ],
)
def test_parse_gr_id_version_token(
        token: str,
        gr_id: str,
        version: tuple[int, int],
) -> None:
    parsed_gr_id, parsed_version = parse_gr_id_version_token(token)
    assert parsed_gr_id == gr_id
    assert parsed_version == version


@pytest.mark.parametrize(
    "token,resource_id,version", [
        ("gene_models(1.0)", "gene_models", (1, 0)),
        ("gene_models(0)", "gene_models", (0,)),
        ("gene_models", "gene_models", None),
    ],
)
def test_parse_resource_id_version(
        token: str,
        resource_id: str,
        version: tuple[int, ...] | None,
) -> None:
    parsed_resource_id, parsed_version = parse_resource_id_version(token)
    assert parsed_resource_id == resource_id
    assert parsed_version == version


@pytest.fixture
def grr_fixture(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    root_path = tmp_path / "test_local_grr"

    setup_directories(
        root_path,
        {
            "one": {
                "genomic_resource.yaml": textwrap.dedent("""
                        type: position_score
                        table:
                            filename: data.txt
                        scores:
                        - id: score_one
                          type: float
                          name: score
                """),
                "data.txt": convert_to_tab_separated("""
                    chrom  pos_begin  score
                    chr1   4          0.01
                    chr1   54         0.02
                """),
            },
            "one(1.0)": {
                "genomic_resource.yaml": textwrap.dedent("""
                        type: position_score
                        table:
                            filename: data.txt
                        scores:
                        - id: score_one
                          type: float
                          name: score
                """),
                "data.txt": convert_to_tab_separated("""
                    chrom  pos_begin  score
                    chr1   4          0.11
                    chr1   54         0.12
                """),
            },
            "one(1.1)": {
                "genomic_resource.yaml": textwrap.dedent("""
                        type: position_score
                        table:
                            filename: data.txt
                        scores:
                        - id: score_one
                          type: float
                          name: score
                """),
                "data.txt": convert_to_tab_separated("""
                    chrom  pos_begin  score
                    chr1   4          0.11
                    chr1   54         0.12
                """),
            },
        },
    )
    return build_genomic_resource_repository({
        "id": "test_local",
        "type": "directory",
        "directory": str(root_path),
    })


@pytest.mark.parametrize(
    "resource_id_version,expected_version", [
        ("one(1.0)", (1, 0)),
        ("one(1.1)", (1, 1)),
        ("one(0)", (0,)),
        ("one", (1, 1)),
    ],
)
def test_find_resource_with_version(
    grr_fixture: GenomicResourceRepo,
    resource_id_version: str,
    expected_version: tuple[int, int],
) -> None:
    resource = grr_fixture.find_resource(resource_id_version)
    assert resource is not None
    assert resource.resource_id == "one"
    assert resource.version == expected_version


@pytest.fixture(scope="module")
def search_grr_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> GenomicResourceProtocolRepo:
    """GRR with four resources covering three types and varied labels.

    scores/res_a         — position_score, ref=ref_a, domain=domain_a
    scores/res_b         — position_score, ref=ref_a, domain=domain_b
    annotation/res_c     — gene_models,    ref=ref_a, domain=domain_c
    gene_scores/res_d    — gene_score,     ref=ref_a, domain=domain_d
                           score IDs: score1

    The fixture uses the CLI to build manifests and the FTS index so that
    search_resources() can be exercised end-to-end.

    Module-scoped: every test that consumes it only reads via
    search_resources(), so the (relatively expensive) repo build is shared
    across the whole module instead of rebuilt per test.
    """
    tmp_path = tmp_path_factory.mktemp("search_grr")
    setup_directories(
        tmp_path,
        {
            "scores/res_a": {
                "genomic_resource.yaml": textwrap.dedent("""
                    type: position_score
                    meta:
                        description: Example position score A
                        summary: Example summary A
                        labels:
                            ref: ref_a
                            domain: domain_a
                    table:
                        filename: data.txt
                    scores:
                        - id: score
                          type: float
                          name: score
                """),
                "data.txt": convert_to_tab_separated("""
                    chrom  pos_begin  score
                    chr1   100        1.5
                """),
            },
            "scores/res_b": {
                "genomic_resource.yaml": textwrap.dedent("""
                    type: position_score
                    meta:
                        description: Example position score B
                        summary: Example summary B
                        labels:
                            ref: ref_a
                            domain: domain_b
                    table:
                        filename: data.txt
                    scores:
                        - id: score
                          type: float
                          name: score
                """),
                "data.txt": convert_to_tab_separated("""
                    chrom  pos_begin  score
                    chr1   200        0.7
                """),
            },
            "annotation/res_c": {
                "genomic_resource.yaml": textwrap.dedent("""
                    type: gene_models
                    meta:
                        description: Example gene models C
                        summary: Example summary C
                        labels:
                            ref: ref_a
                            domain: domain_c
                    filename: genes.gtf
                """),
                "genes.gtf":
                    'chr1\t.\tgene\t1\t1000\t.\t+\t.\tgene_id "gene1";\n',
            },
            "gene_scores/res_d": {
                "genomic_resource.yaml": textwrap.dedent("""
                    type: gene_score
                    meta:
                        description: Example gene score D
                        labels:
                            ref: ref_a
                            domain: domain_d
                    filename: scores.csv
                    scores:
                        - id: score1
                          type: float
                          column_name: score1
                          desc: example score one description
                          histogram:
                              type: number
                              number_of_bins: 3
                              x_log_scale: false
                              y_log_scale: false
                """),
                "scores.csv": "gene,score1\ngene_a,0.9\ngene_b,0.5\n",
            },
        },
    )
    # search_resources() only needs the FTS index, not the resource
    # statistics/histograms. Build the manifests + content file with
    # repo-manifest and the FTS index directly, skipping the much more
    # expensive statistics TaskGraph that repo-stats would also run.
    cli_manage(["repo-manifest", "-R", str(tmp_path)])
    proto = build_filesystem_test_protocol(tmp_path, repair=False)
    _create_contents_db(proto)
    return GenomicResourceProtocolRepo(proto)


def test_search_resources_no_filter(
    search_grr_fixture: GenomicResourceProtocolRepo,
) -> None:
    resources = list(search_grr_fixture.search_resources())
    assert len(resources) == 4


def test_search_resources_by_type_position_score(
    search_grr_fixture: GenomicResourceProtocolRepo,
) -> None:
    resources = list(
        search_grr_fixture.search_resources(resource_type="position_score"),
    )
    assert len(resources) == 2
    assert all(r.get_type() == "position_score" for r in resources)


def test_search_resources_by_type_gene_models(
    search_grr_fixture: GenomicResourceProtocolRepo,
) -> None:
    resources = list(
        search_grr_fixture.search_resources(resource_type="gene_models"),
    )
    assert len(resources) == 1
    assert resources[0].resource_id == "annotation/res_c"


def test_search_resources_by_term_matches_id(
    search_grr_fixture: GenomicResourceProtocolRepo,
) -> None:
    resources = list(search_grr_fixture.search_resources(search_term="res_a"))
    assert len(resources) == 1
    assert resources[0].resource_id == "scores/res_a"


def test_search_resources_by_term_matches_label_value(
    search_grr_fixture: GenomicResourceProtocolRepo,
) -> None:
    resources = list(
        search_grr_fixture.search_resources(search_term="domain_a"),
    )
    assert len(resources) == 1
    assert resources[0].resource_id == "scores/res_a"


def test_search_resources_combined_type_and_term(
    search_grr_fixture: GenomicResourceProtocolRepo,
) -> None:
    resources = list(
        search_grr_fixture.search_resources(
            search_term="ref_a",
            resource_type="position_score",
        ),
    )
    assert len(resources) == 2
    assert all(r.get_type() == "position_score" for r in resources)


def test_search_resources_no_match(
    search_grr_fixture: GenomicResourceProtocolRepo,
) -> None:
    resources = list(
        search_grr_fixture.search_resources(search_term="xyzzy_no_match"),
    )
    assert len(resources) == 0


def test_search_resources_gene_score_by_score_id(
    search_grr_fixture: GenomicResourceProtocolRepo,
) -> None:
    resources = list(
        search_grr_fixture.search_resources(search_term="score1"),
    )
    assert len(resources) == 1
    assert resources[0].resource_id == "gene_scores/res_d"
