# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pathlib
import textwrap

import pytest
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.repository import (
    GenomicResourceRepo,
    GenomicResourceProtocolRepo,
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


@pytest.fixture
def search_grr_fixture(tmp_path: pathlib.Path) -> GenomicResourceProtocolRepo:
    """GRR with three resources covering two types and varied labels.

    scores/cadd      — position_score, reference=hg38, domain=pathogenicity
    scores/phylop    — position_score, reference=hg38, domain=conservation
    annotation/gencode — gene_models,  reference=hg38, domain=annotation

    The fixture uses the CLI to build manifests and the FTS index so that
    search_resources() can be exercised end-to-end.
    """
    setup_directories(
        tmp_path,
        {
            "scores/cadd": {
                "genomic_resource.yaml": textwrap.dedent("""
                    type: position_score
                    meta:
                        description: CADD pathogenicity scores
                        summary: Combined Annotation Dependent Depletion
                        labels:
                            reference: hg38
                            domain: pathogenicity
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
            "scores/phylop": {
                "genomic_resource.yaml": textwrap.dedent("""
                    type: position_score
                    meta:
                        description: PhyloP conservation scores
                        summary: Phylogenetic P-values for conservation
                        labels:
                            reference: hg38
                            domain: conservation
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
            "annotation/gencode": {
                "genomic_resource.yaml": textwrap.dedent("""
                    type: gene_models
                    meta:
                        description: GENCODE gene annotations
                        summary: Comprehensive human gene models
                        labels:
                            reference: hg38
                            domain: annotation
                    file: genes.gtf
                """),
                "genes.gtf": 'chr1\t.\tgene\t1\t1000\t.\t+\t.\tgene_id "TP53";\n',
            },
        },
    )
    cli_manage(["repo-manifest", "-R", str(tmp_path)])
    cli_manage(["repo-build-fts", "-R", str(tmp_path)])
    proto = build_filesystem_test_protocol(tmp_path, repair=False)
    return GenomicResourceProtocolRepo(proto)


def test_search_resources_no_filter(
    search_grr_fixture: GenomicResourceProtocolRepo,
) -> None:
    resources = list(search_grr_fixture.search_resources())
    assert len(resources) == 3


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
    assert resources[0].resource_id == "annotation/gencode"


def test_search_resources_by_term_matches_id(
    search_grr_fixture: GenomicResourceProtocolRepo,
) -> None:
    resources = list(search_grr_fixture.search_resources(search_term="cadd"))
    assert len(resources) == 1
    assert resources[0].resource_id == "scores/cadd"


def test_search_resources_by_term_matches_label_value(
    search_grr_fixture: GenomicResourceProtocolRepo,
) -> None:
    resources = list(
        search_grr_fixture.search_resources(search_term="pathogenicity"),
    )
    assert len(resources) == 1
    assert resources[0].resource_id == "scores/cadd"


def test_search_resources_combined_type_and_term(
    search_grr_fixture: GenomicResourceProtocolRepo,
) -> None:
    resources = list(
        search_grr_fixture.search_resources(
            search_term="hg38",
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
