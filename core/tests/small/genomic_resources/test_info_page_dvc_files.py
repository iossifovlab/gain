# pylint: disable=W0621,C0114,C0116,W0212,W0613
import hashlib
import pathlib
import textwrap

import pytest
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.fsspec_protocol import FsspecReadWriteProtocol
from gain.genomic_resources.repository import GR_CONF_FILE_NAME
from gain.genomic_resources.repository_factory import (
    build_resource_implementation,
)
from gain.genomic_resources.resource_implementation import (
    InfoImplementationMixin,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
    setup_directories,
)

DATA = "ORIGINAL DATA - trust me\n"
STATS = '{"bins": [1, 2, 3]}\n'


def dvc_sidecar(path: str, content: str) -> str:
    """Render a well-formed ``.dvc`` pointer for ``content`` at ``path``."""
    return textwrap.dedent(f"""
        outs:
        - md5: {hashlib.md5(content.encode("utf8")).hexdigest()}
          size: {len(content.encode("utf8"))}
          path: {path}
    """)  # ruff: ignore[hashlib-insecure-hash-function]


@pytest.fixture
def dvc_resource_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[pathlib.Path, FsspecReadWriteProtocol]:
    """Build a resource whose data and statistics files are DVC-managed."""
    path = tmp_path_factory.mktemp("info_page_dvc_files")
    setup_directories(path, {
        "one": {
            GR_CONF_FILE_NAME: "type: basic\n",
            "data.txt": DATA,
            "data.txt.dvc": dvc_sidecar("data.txt", DATA),
            "statistics": {
                "histogram.json": STATS,
                "histogram.json.dvc": dvc_sidecar("histogram.json", STATS),
            },
        },
    })
    proto = build_filesystem_test_protocol(path)
    return path, proto


def _implementation(
    proto: FsspecReadWriteProtocol,
) -> InfoImplementationMixin:
    impl = build_resource_implementation(proto.get_resource("one"))
    assert isinstance(impl, InfoImplementationMixin)
    return impl


def test_resource_files_table_lists_the_dvc_managed_file(
    dvc_resource_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
) -> None:
    """The data file keeps its row -- and the sidecar's md5 and size."""
    _path, proto = dvc_resource_fixture

    entries = {
        entry.name: entry
        for entry in _implementation(proto).get_template_data()[
            "resource_files"]
    }

    assert "data.txt" in entries
    assert entries["data.txt"].md5 == hashlib.md5(  # ruff: ignore[hashlib-insecure-hash-function]
        DATA.encode("utf8")).hexdigest()


def test_resource_files_table_hides_dvc_sidecars(
    dvc_resource_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
) -> None:
    _path, proto = dvc_resource_fixture

    names = [
        entry.name
        for entry in _implementation(proto).get_template_data()[
            "resource_files"]
    ]

    assert "data.txt.dvc" not in names


def test_statistics_files_table_hides_dvc_sidecars(
    dvc_resource_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
) -> None:
    _path, proto = dvc_resource_fixture

    names = [
        entry.name
        for entry in _implementation(proto).get_statistics_template_data()[
            "statistic_files"]
    ]

    assert "histogram.json" in names, \
        "the statistics table lost the file the sidecar describes"
    assert "histogram.json.dvc" not in names


def test_rendered_info_page_has_no_dvc_link(
    dvc_resource_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
) -> None:
    """Guard the template too: the row must not come back by another route."""
    path, _proto = dvc_resource_fixture

    cli_manage(["resource-info", "-R", str(path), "-r", "one", "-j", "1"])

    page = (path / "one/index.html").read_text()
    assert "data.txt" in page
    assert ".dvc" not in page
