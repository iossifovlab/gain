# pylint: disable=W0621,C0114,C0116,W0212,W0613
import os
import time
from collections.abc import Generator
from urllib.parse import parse_qsl, urlparse

import pytest
import pytest_mock
from gain.utils import fs_utils
from gain.utils.fs_utils import S3_PRESIGN_EXPIRATION_SECONDS
from s3fs import S3FileSystem


@pytest.mark.parametrize("segments, expected", [
    (["/abc", "de"], "/abc/de"),
    (["ab", "c"], "ab/c"),
    (["s3://server/", "path"], "s3://server/path"),
    (["/abc/", "s3://server/", "path"], "s3://server/path"),
    (["s3://server/", "/abc/", "path"], "/abc/path"),
])
def test_join(
    segments: str,
    expected: str,
) -> None:
    assert fs_utils.join(*segments) == expected


@pytest.mark.parametrize("url, expected", [
    ("/filename", "/"),
    ("/dir/filename", "/dir"),
    ("file:///dir/filename", "file:///dir"),
    ("s3://bucket", "s3://"),
    ("s3://bucket/dir/filename", "s3://bucket/dir"),
    ("file", None),  # None signifies cwd
    ("", ""),
    ("/", "/"),
    ("s3://", "s3://"),
])
def test_containing_path(
    url: str,
    expected: str | None,
) -> None:
    expected = expected if expected is not None else os.getcwd()
    assert fs_utils.containing_path(url) == expected


@pytest.mark.parametrize("filename, exists_mockery, expected", [
    ("test", {"test": True, "test.tbi": True}, "test.tbi"),
    ("test", {"test": True, "test.csi": True}, "test.csi"),
    ("test", {"test": True}, None),
])
def test_tabix_index_filename(
    mocker: pytest_mock.MockFixture,
    filename: str,
    exists_mockery: dict[str, bool],
    expected: str | None,
) -> None:
    mocker.patch(
        "gain.utils.fs_utils.exists",
        lambda tf: exists_mockery.get(tf, False))
    assert fs_utils.tabix_index_filename(filename) == expected


def test_tabix_index_filename_file_not_found(
    mocker: pytest_mock.MockFixture,
) -> None:
    mocker.patch(
        "gain.utils.fs_utils.exists",
        return_value=False)
    with pytest.raises(IOError, match="tabix file 'test' not found"):
        fs_utils.tabix_index_filename("test")


@pytest.mark.parametrize("url, expected", [
    ("/filename", "/filename"),
    ("/dir/filename", "/dir/filename"),
    ("file:///dir/filename", "file:///dir/filename"),
    ("s3://bucket", "s3://bucket"),
    ("s3://bucket/dir/filename", "s3://bucket/dir/filename"),
    ("file", "/abs/path/file"),
    ("./file", "/abs/path/file"),
    ("/", "/"),
    ("s3://", "s3://"),
])
def test_abspath(
    url: str,
    expected: str,
    mocker: pytest_mock.MockFixture,
) -> None:
    mocker.patch("os.getcwd", return_value="/abs/path")
    res = fs_utils.abspath(url)
    assert res == expected


def test_copy_folder(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    dest_path = tmp_path_factory.mktemp("dest")

    src_path = tmp_path_factory.mktemp("src")
    (src_path / "a").mkdir(parents=True)
    (src_path / "c").mkdir(parents=True)
    (src_path / "a" / "b.txt").write_text("b")
    (src_path / "c" / "d.txt").write_text("d")

    fs_utils.copy(str(dest_path), str(src_path))

    assert (dest_path / "a" / "b.txt").read_text() == "b"
    assert (dest_path / "c" / "d.txt").read_text() == "d"


@pytest.mark.parametrize("filename, expected", [
    ("data.txt", None),
    ("data.txt.gz", ".gz"),
    ("data.txt.bgz", ".bgz"),
    ("data.vcf.bgz", ".bgz"),
    ("log.gz", ".gz"),
    ("catalog.gz", ".gz"),
])
def test_compression_suffix(filename: str, expected: str | None) -> None:
    assert fs_utils.compression_suffix(filename) == expected


@pytest.mark.parametrize("filename, expected", [
    ("data.txt", "data.txt"),
    ("data.txt.gz", "data.txt"),
    ("data.txt.bgz", "data.txt"),
    ("log.gz", "log"),
    ("catalog.gz", "catalog"),
])
def test_strip_compression_suffix(filename: str, expected: str) -> None:
    assert fs_utils.strip_compression_suffix(filename) == expected


@pytest.mark.parametrize("filename, expected", [
    ("data.txt", False),
    ("data.txt.gz", True),
    ("data.txt.bgz", True),
])
def test_is_compressed_filename(filename: str, expected: bool) -> None:
    assert fs_utils.is_compressed_filename(filename) is expected


def _presigned_lifetime_seconds(url: str, signed_at: int) -> int:
    """How long a presigned s3 ``url`` stays valid, in seconds.

    botocore spells the expiry two ways: SigV2 writes an absolute
    ``Expires`` epoch, SigV4 a relative ``X-Amz-Expires``. Which one a
    filesystem gets is a property of its endpoint, so read either.
    """
    query = dict(parse_qsl(urlparse(url).query))
    if "X-Amz-Expires" in query:
        return int(query["X-Amz-Expires"])
    return int(query["Expires"]) - signed_at


@pytest.fixture
def fresh_s3_filesystem_cache() -> Generator[None, None, None]:
    """Clear fsspec's s3 instance cache around a test.

    ``url_to_fs`` hands out the one instance fsspec built for its kwargs,
    whatever environment an earlier test built it in -- and the one this
    test builds carries its credentials, so it is dropped afterwards too.
    """
    S3FileSystem.clear_instance_cache()
    yield
    S3FileSystem.clear_instance_cache()


# The region decides which signature botocore emits: none is SigV2, a
# regional endpoint is SigV4. Both spellings must carry the lifetime.
@pytest.mark.parametrize("region", [
    pytest.param(None, id="sigv2"),
    pytest.param("eu-central-1", id="sigv4"),
])
def test_sign_presigns_an_s3_url_for_the_full_handle_lifetime(
    fresh_s3_filesystem_cache: None,
    monkeypatch: pytest.MonkeyPatch,
    region: str | None,
) -> None:
    # Presigning is local to botocore -- credentials are all it needs, no
    # endpoint -- so this speaks to the real s3 filesystem. The developer's
    # own ``~/.aws`` is shut out so the region is the one set here.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "minioadmin")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "minioadmin")
    monkeypatch.setenv("AWS_CONFIG_FILE", "/nonexistent")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/nonexistent")
    monkeypatch.delenv("AWS_REGION", raising=False)
    if region is None:
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    else:
        monkeypatch.setenv("AWS_DEFAULT_REGION", region)
    signed_at = int(time.time())

    signed = fs_utils.sign("s3://bucket/dir/data.txt.gz")

    assert ("X-Amz-Expires" in signed) is (region is not None)
    lifetime = _presigned_lifetime_seconds(signed, signed_at)
    assert lifetime == pytest.approx(S3_PRESIGN_EXPIRATION_SECONDS, abs=5)


def test_sign_returns_a_filename_the_filesystem_cannot_sign_as_is() -> None:
    assert fs_utils.sign("/dir/data.txt.gz") == "/dir/data.txt.gz"
