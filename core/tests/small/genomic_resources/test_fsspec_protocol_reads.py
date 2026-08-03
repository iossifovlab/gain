# pylint: disable=W0621,C0114,C0116,W0212,W0613
import os
import pathlib
from collections.abc import Generator
from typing import Any

import pysam
import pytest
import pytest_mock
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    RepositoryProtocol,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
    build_http_test_protocol,
    build_s3_test_protocol,
    setup_directories,
    setup_tabix,
    setup_vcf,
)
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_vcf_info_score,
)

pytestmark = [pytest.mark.grr_full, pytest.mark.grr_http]


@pytest.fixture
def tabix_fsspec_proto(
    content_fixture: dict[str, Any],
    tmp_path: pathlib.Path,
    grr_scheme: str,
    mocker: pytest_mock.MockerFixture,
) -> Generator[RepositoryProtocol, None, None]:

    root_path = tmp_path

    setup_directories(root_path, content_fixture)
    setup_tabix(
        root_path / "one" / "test.txt.gz",
        """
            #chrom  pos_begin  pos_end    c1
            1      1          10         1.0
            2      1          10         2.0
            2      11         20         2.5
            3      1          10         3.0
            3      11         20         3.5
        """,
        seq_col=0, start_col=1, end_col=2)

    setup_vcf(
        root_path / "one" / "in.vcf.gz",
        """
        ##fileformat=VCFv4.2
        ##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
        ##contig=<ID=foo>
        ##contig=<ID=bar>
        #CHROM POS ID REF ALT QUAL FILTER INFO FORMAT m1
        foo    10  .  T   G     .    .      .    GT   0/1
        foo    13  .  T   G     .    .      .    GT   0/1
        bar    15  .  T   G     .    .      .    GT   1/1
        bar    16  .  T   G     .    .      .    GT   0/1
        """)
    scheme = grr_scheme
    if scheme == "file":
        yield build_filesystem_test_protocol(root_path)
        return
    if scheme == "http":
        with build_http_test_protocol(root_path) as proto:
            yield proto
        return
    if scheme == "s3":
        mocker.patch.dict(os.environ, {
            "AWS_SECRET_ACCESS_KEY": "minioadmin",
            "AWS_ACCESS_KEY_ID": "minioadmin",
        })

        with build_s3_test_protocol(root_path) as proto:
            yield proto
        return

    raise ValueError(f"unexpected protocol scheme: <{scheme}>")


@pytest.fixture(scope="module")
def tabix_fsspec_proto_utf8(
        tmp_path_factory: pytest.TempPathFactory) -> RepositoryProtocol:
    root_path = tmp_path_factory.mktemp("tabix_fsspec_proto_utf8")
    setup_directories(root_path, {
        "one": {
            GR_CONF_FILE_NAME: "",
        },
    })
    setup_tabix(
        root_path / "one" / "test.txt.gz",
        """
            #chrëm  pos_bëgin pos_ënd    ë1
            1      1          10         1.0
            2      1          10         2.0
            2      11         20         2.5
            3      1          10         3.0
            3      11         20         3.5
        """,
        seq_col=0, start_col=1, end_col=2)
    setup_vcf(
        root_path / "one" / "in.vcf.gz",
        """
        ##fileformat=VCFv4.2
        ##FORMAT=<ID=GT,Number=1,Type=String,Description="Gënééééééotype">
        ##contig=<ID=foo>
        ##contig=<ID=bar>
        #CHROM POS ID REF ALT QUAL FILTER INFO FORMAT m1
        foo    10  .  T   G     .    .      .    GT   0/1
        foo    13  .  T   G     .    .      .    GT   0/1
        bar    15  .  T   G     .    .      .    GT   1/1
        bar    16  .  T   G     .    .      .    GT   0/1
        """)
    return build_filesystem_test_protocol(root_path)


@pytest.mark.grr_tabix
def test_get_all_resources(tabix_fsspec_proto: RepositoryProtocol) -> None:
    proto = tabix_fsspec_proto
    resources = list(proto.get_all_resources())
    assert len(resources) == 5, resources


@pytest.mark.grr_tabix
def test_open_raw_file_read_three_a(
        tabix_fsspec_proto: RepositoryProtocol) -> None:
    # Given
    proto = tabix_fsspec_proto
    res = proto.get_resource("three")

    # When
    with proto.open_raw_file(res, "sub1/a.txt") as infile:
        content = infile.read()

    # Then
    assert content == "a"


@pytest.mark.grr_tabix
def test_open_raw_file_read_one_compressed(
        tabix_fsspec_proto: RepositoryProtocol) -> None:
    # Given
    proto = tabix_fsspec_proto
    res = proto.get_resource("one")

    # When
    with proto.open_raw_file(
            res, "test.txt.gz", compression="gzip") as infile:
        header = infile.readline()

    # Then
    assert header == "#chrom\tpos_begin\tpos_end\tc1\n"


@pytest.mark.grr_tabix
def test_open_raw_file_seek(tabix_fsspec_proto: RepositoryProtocol) -> None:
    # Given
    proto = tabix_fsspec_proto
    res = proto.get_resource("xxxxx-genome")

    # When
    with proto.open_raw_file(
            res, "chr.fa") as infile:

        infile.seek(7)
        sequence = infile.read(10)

    # Then
    assert sequence == "NNACCCAAAC"


@pytest.mark.grr_tabix
def test_open_tabix_file_contigs(
        tabix_fsspec_proto: RepositoryProtocol) -> None:
    # Given
    proto = tabix_fsspec_proto
    res = proto.get_resource("one")

    # When
    with proto.open_tabix_file(res, "test.txt.gz") as tabix:
        contigs = tabix.contigs

    # Then
    assert contigs == ["1", "2", "3"]


@pytest.mark.grr_tabix
def test_open_tabix_file_fetch_all(
        tabix_fsspec_proto: RepositoryProtocol) -> None:
    # Given
    proto = tabix_fsspec_proto
    res = proto.get_resource("one")

    # When
    lines = []
    with proto.open_tabix_file(res, "test.txt.gz") as tabix:
        lines = list(tabix.fetch())

    # Then
    assert len(lines) == 5


@pytest.mark.grr_tabix
def test_open_tabix_file_fetch_region(
        tabix_fsspec_proto: RepositoryProtocol) -> None:
    # Given
    proto = tabix_fsspec_proto
    res = proto.get_resource("one")

    # When
    lines = []
    with proto.open_tabix_file(res, "test.txt.gz") as tabix:
        lines = list(tabix.fetch("3"))

    # Then
    assert [tuple(r) for r in lines] == [
        ("3", "1", "10", "3.0"), ("3", "11", "20", "3.5")]


@pytest.mark.grr_tabix
def test_open_vcf_file_contigs(
        tabix_fsspec_proto: RepositoryProtocol) -> None:
    # Given
    proto = tabix_fsspec_proto
    res = proto.get_resource("one")
    # When
    with proto.open_vcf_file(res, "in.vcf.gz") as vcf:
        contigs = list(vcf.header.contigs)

    # Then
    assert contigs == ["foo", "bar"]


@pytest.mark.grr_tabix
def test_open_vcf_file_fetch_all(
        tabix_fsspec_proto: RepositoryProtocol) -> None:
    # Given
    proto = tabix_fsspec_proto
    res = proto.get_resource("one")

    # When
    lines = []
    with proto.open_vcf_file(res, "in.vcf.gz") as vcf:
        lines = list(vcf.fetch())

    # Then
    assert len(lines) == 4


@pytest.mark.grr_tabix
def test_open_vcf_file_fetch_region(
    tabix_fsspec_proto: RepositoryProtocol,
) -> None:
    # Given
    proto = tabix_fsspec_proto
    res = proto.get_resource("one")

    # When
    lines = []
    with proto.open_vcf_file(res, "in.vcf.gz") as vcf:
        lines = list(vcf.fetch("foo"))

    # Then
    assert len(lines) == 2


@pytest.mark.grr_tabix
def test_open_utf8_tabix_file(
        tabix_fsspec_proto_utf8: RepositoryProtocol) -> None:
    proto = tabix_fsspec_proto_utf8
    res = proto.get_resource("one")

    with proto.open_tabix_file(res, "test.txt.gz") as tabix:
        print(tabix.contigs)

    with proto.open_tabix_file(res, "in.vcf.gz") as vcf:
        print(vcf.contigs)


# ---------------------------------------------------------------------------
# open_vcf_file — index handling
# ---------------------------------------------------------------------------

VCF_CONTENT = """\
##fileformat=VCFv4.2
##INFO=<ID=AF,Number=A,Type=Float,Description="Allele frequency">
##contig=<ID=1>
#CHROM POS ID REF ALT QUAL FILTER INFO
1      10  .  T   G     .    .    AF=0.1
1      20  .  A   C     .    .    AF=0.2
"""


@pytest.fixture
def vcf_proto_with_index(tmp_path: pathlib.Path) -> RepositoryProtocol:
    """Filesystem protocol where the VCF has a .tbi index."""
    setup_directories(tmp_path, {"res": {GR_CONF_FILE_NAME: ""}})
    setup_vcf(tmp_path / "res" / "data.vcf.gz", VCF_CONTENT)
    return build_filesystem_test_protocol(tmp_path)


@pytest.fixture
def vcf_proto_without_index(tmp_path: pathlib.Path) -> RepositoryProtocol:
    """Filesystem protocol where the VCF has NO .tbi index."""
    setup_directories(tmp_path, {"res": {GR_CONF_FILE_NAME: ""}})
    setup_vcf(tmp_path / "res" / "data.vcf.gz", VCF_CONTENT)
    (tmp_path / "res" / "data.vcf.gz.tbi").unlink()
    return build_filesystem_test_protocol(tmp_path)


@pytest.fixture
def vcf_proto_with_csi_index(
    tmp_path: pathlib.Path,
    grr_scheme: str,
    mocker: pytest_mock.MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[RepositoryProtocol, None, None]:
    """Protocol where the VCF's ONLY index is a ``.csi``.

    Parametrized over the schemes like its ``.tbi`` siblings above: the
    resolution is protocol-independent, and the remote schemes are where a
    wrongly-named index costs a round trip rather than a local stat.

    The ``chdir`` keeps the run's cwd clean: on the remote schemes htslib
    caches the index it downloads under its BASENAME in the current
    directory, so without this a run from ``core/`` drops a stray
    ``data.vcf.gz.csi`` into the source tree.
    """
    monkeypatch.chdir(tmp_path)
    setup_directories(tmp_path, {"res": {GR_CONF_FILE_NAME: ""}})
    setup_vcf(tmp_path / "res" / "data.vcf.gz", VCF_CONTENT, csi=True)

    if grr_scheme == "file":
        yield build_filesystem_test_protocol(tmp_path)
        return
    if grr_scheme == "http":
        with build_http_test_protocol(tmp_path) as proto:
            yield proto
        return
    if grr_scheme == "s3":
        mocker.patch.dict(os.environ, {
            "AWS_SECRET_ACCESS_KEY": "minioadmin",
            "AWS_ACCESS_KEY_ID": "minioadmin",
        })
        with build_s3_test_protocol(tmp_path) as proto:
            yield proto
        return

    raise ValueError(f"unexpected protocol scheme: <{grr_scheme}>")


@pytest.mark.grr_tabix
def test_open_vcf_file_resolves_a_csi_index_it_was_not_told_about(
        vcf_proto_with_csi_index: RepositoryProtocol,
        mocker: pytest_mock.MockerFixture) -> None:
    """With no index argument the open must find the ``.csi`` (gain#596).

    Assuming ``{filename}.tbi`` and then opening unindexed because that name
    does not exist is what the tabix sibling stopped doing in gain#430; this
    is the same resolution on the VCF open.

    Observed at the URL seam, deliberately -- and NOT by reading records
    back.  htslib probes for an adjacent index whenever it is handed none,
    and that probe reaches over http and s3 as well as on local disk
    (measured against the docker fixtures): the read succeeds either way, so
    it cannot tell a resolved index from a guessed one.  What the caller
    passes still matters -- the caching protocol fetches the index this
    resolution names, and only that one, into the cache.
    """
    proto = vcf_proto_with_csi_index
    res = proto.get_resource("res")
    spy = mocker.spy(proto, "_get_file_url")

    with proto.open_vcf_file(res, "data.vcf.gz") as vcf:
        assert [record.pos for record in vcf.fetch("1", 9, 10)] == [10]

    index_filenames = [
        call.args[1] for call in spy.call_args_list
        if call.args[1] != "data.vcf.gz"
    ]
    assert index_filenames == ["data.vcf.gz.csi"]


@pytest.mark.grr_tabix
def test_open_vcf_file_with_index_reads_contigs(
        vcf_proto_with_index: RepositoryProtocol) -> None:
    proto = vcf_proto_with_index
    res = proto.get_resource("res")
    with proto.open_vcf_file(res, "data.vcf.gz") as vcf:
        assert list(vcf.header.contigs) == ["1"]


@pytest.mark.grr_tabix
def test_open_vcf_file_with_index_fetches_by_region(
        vcf_proto_with_index: RepositoryProtocol) -> None:
    proto = vcf_proto_with_index
    res = proto.get_resource("res")
    with proto.open_vcf_file(res, "data.vcf.gz") as vcf:
        records = list(vcf.fetch("1"))
    assert len(records) == 2


@pytest.mark.grr_tabix
def test_open_vcf_file_without_index_reads_contigs(
        vcf_proto_without_index: RepositoryProtocol) -> None:
    proto = vcf_proto_without_index
    res = proto.get_resource("res")
    with proto.open_vcf_file(res, "data.vcf.gz") as vcf:
        assert list(vcf.header.contigs) == ["1"]


@pytest.mark.grr_tabix
def test_open_vcf_file_without_index_does_not_build_index_url(
        vcf_proto_without_index: RepositoryProtocol,
        mocker: pytest_mock.MockerFixture) -> None:
    """_get_file_url must not be called for the index when it does not exist.

    This is critical for remote protocols (S3, HTTP) where constructing the
    index URL alone can trigger a network request or presigned-URL generation.
    """
    proto = vcf_proto_without_index
    res = proto.get_resource("res")

    spy = mocker.spy(proto, "_get_file_url")
    with proto.open_vcf_file(res, "data.vcf.gz"):
        pass

    called_filenames = [call.args[1] for call in spy.call_args_list]
    assert "data.vcf.gz" in called_filenames
    assert "data.vcf.gz.tbi" not in called_filenames


def test_open_vcf_file_refuses_an_explicit_index_that_does_not_exist(
        vcf_proto_with_index: RepositoryProtocol) -> None:
    """An index asked for BY NAME must not silently degrade (gain#596).

    The resource carries its default adjacent ``data.vcf.gz.tbi``, so
    dropping the requested index and opening unindexed would leave htslib to
    auto-probe its way to a working file -- and a caller that asked for a
    particular index would never learn that its request did nothing.
    """
    proto = vcf_proto_with_index
    res = proto.get_resource("res")

    with pytest.raises(OSError) as excinfo:
        proto.open_vcf_file(res, "data.vcf.gz", "no-such.tbi")

    assert "no-such.tbi" in str(excinfo.value)


@pytest.fixture
def vcf_proto_with_both_indexes(tmp_path: pathlib.Path) -> RepositoryProtocol:
    """Filesystem protocol where the VCF carries both a ``.csi`` and a ``.tbi``.

    The builder realizes the ``.csi``; the extra ``tabix_index`` call adds a
    ``.tbi`` next to it without disturbing it.
    """
    (
        a_grr()
        .with_resource(
            "res", a_vcf_info_score().with_csi_index().with_data(VCF_CONTENT))
        .build_repo(tmp_path)
    )
    pysam.tabix_index(  # pylint: disable=no-member
        str(tmp_path / "res" / "data.vcf.gz"), preset="vcf", force=True)
    return build_filesystem_test_protocol(tmp_path)


@pytest.mark.grr_tabix
def test_open_vcf_file_prefers_the_tbi_index_when_both_exist(
        vcf_proto_with_both_indexes: RepositoryProtocol,
        mocker: pytest_mock.MockerFixture) -> None:
    """gain#556: resolution order stays ``.tbi`` before ``.csi``."""
    proto = vcf_proto_with_both_indexes
    res = proto.get_resource("res")

    spy = mocker.spy(proto, "_get_file_url")
    with proto.open_vcf_file(res, "data.vcf.gz"):
        pass

    called_filenames = [call.args[1] for call in spy.call_args_list]
    assert "data.vcf.gz.tbi" in called_filenames
    assert "data.vcf.gz.csi" not in called_filenames
