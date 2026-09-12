# pylint: disable=W0621,C0116,W0613
"""How long the url an s3 GRR hands pysam stays valid.

The signature's lifetime is the handle's lifetime (ADR 0023, gain#1398).
It is pinned at the filesystem's ``sign`` seam -- the ``expiration``
handed to s3fs is what the url's expiry is made from -- rather than by
outliving a signature, which a unit test cannot afford to wait for.
"""
import inspect
import os
import pathlib
from collections.abc import Generator

import pytest
import pytest_mock
from gain.genomic_resources.fsspec_protocol import (
    S3_PRESIGN_EXPIRATION_SECONDS,
    FsspecReadWriteProtocol,
)
from gain.genomic_resources.testing import build_s3_test_protocol
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_position_score,
)

# S3 refuses a SigV4 presigned url that would live longer than seven days.
_SIGV4_MAX_EXPIRATION_SECONDS = 7 * 24 * 60 * 60


@pytest.fixture
def s3_tabix_proto(
    s3_enabled: None,
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[FsspecReadWriteProtocol, None, None]:
    """An s3 GRR carrying one tabix-backed position score.

    The ``chdir`` keeps the run's cwd clean: htslib caches the index it
    downloads under its basename in the current directory.
    """
    monkeypatch.chdir(tmp_path)
    (
        a_grr()
        .with_resource("res", a_position_score().with_tabix())
        .build_repo(tmp_path)
    )
    mocker.patch.dict(os.environ, {
        "AWS_SECRET_ACCESS_KEY": "minioadmin",
        "AWS_ACCESS_KEY_ID": "minioadmin",
    })
    with build_s3_test_protocol(tmp_path) as proto:
        yield proto


def test_s3_presign_lifetime_does_not_exceed_what_sigv4_accepts() -> None:
    assert S3_PRESIGN_EXPIRATION_SECONDS <= _SIGV4_MAX_EXPIRATION_SECONDS


def test_tabix_open_on_s3_presigns_data_and_index_for_the_full_lifetime(
    s3_tabix_proto: FsspecReadWriteProtocol,
    mocker: pytest_mock.MockerFixture,
) -> None:
    proto = s3_tabix_proto
    res = proto.get_resource("res")
    signature = inspect.signature(proto.filesystem.sign)
    sign = mocker.spy(proto.filesystem, "sign")

    proto.open_tabix_file(res, "data.txt.gz").close()

    # Bound through the real signature: keyword or positional, a dropped
    # ``expiration`` reads as s3fs's default rather than as ``None``.
    signed = {}
    for call in sign.call_args_list:
        bound = signature.bind(*call.args, **call.kwargs)
        bound.apply_defaults()
        path = bound.arguments["path"].rsplit("/", 1)[-1]
        signed[path] = bound.arguments["expiration"]
    assert signed == {
        "data.txt.gz": S3_PRESIGN_EXPIRATION_SECONDS,
        "data.txt.gz.tbi": S3_PRESIGN_EXPIRATION_SECONDS,
    }
