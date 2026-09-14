"""A table's ``open()`` releases the handle it acquired when its setup raises.

Every file-backed backend acquires its native handle first and then does the
setup that can refuse the table -- resolving columns, building the chromosome
mapping, constructing the parser.  Nothing above ``open()`` has been told the
table is open when that setup raises, so no caller will ever ``close()`` it:
the handle is ``open()``'s own to release (gain#627).

Each test spies on the resource's ``open_*_file`` to get hold of the handle
``open()`` acquired, then asserts on that handle -- a closed table with a live
handle is exactly the leak, and nothing functional tells it apart from a
released one.
"""
# pylint: disable=C0116
import asyncio
import pathlib
from typing import Any

import pysam
import pytest
from gain.genomic_resources.genomic_position_table import (
    build_genomic_position_table,
)
from gain.genomic_resources.genomic_position_table.table_tabix import (
    TabixGenomicPositionTable,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.resource_errors import MalformedResourceError
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_position_score,
)


def _spy_handles(
    resource: GenomicResource, method: str,
    monkeypatch: pytest.MonkeyPatch,
) -> list[Any]:
    """Record every handle ``resource.<method>`` returns."""
    handles: list[Any] = []
    original = getattr(resource, method)

    def spy(*args: Any, **kwargs: Any) -> Any:
        handle = original(*args, **kwargs)
        handles.append(handle)
        return handle

    monkeypatch.setattr(resource, method, spy)
    return handles


class _HandleWhoseCloseFails:
    """A handle proxy whose ``close()`` releases the real handle, then raises.

    ``hts_close`` can fail after the file is gone -- this package documents
    the sibling ``VariantFile.close()`` doing exactly that -- and a proxy is
    the only way to make a Cython handle do it on demand.
    """

    def __init__(self, handle: Any) -> None:
        self._handle = handle

    def close(self) -> None:
        self._handle.close()
        raise OSError("hts_close failed")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._handle, name)


def _make_close_fail(
    resource: GenomicResource, method: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every handle ``resource.<method>`` returns raises on ``close()``."""
    original = getattr(resource, method)

    def failing(*args: Any, **kwargs: Any) -> Any:
        return _HandleWhoseCloseFails(original(*args, **kwargs))

    monkeypatch.setattr(resource, method, failing)


def _colliding_tabix_table(
    tmp_path: pathlib.Path,
) -> tuple[GenomicResource, TabixGenomicPositionTable]:
    """A tabix table whose chromosome mapping is refused at ``open()``.

    ``del_prefix: chr`` over a file mixing ``chr1`` and ``1`` collides, which
    ``_build_chrom_mapping`` refuses -- after the handle is acquired.
    """
    repo = (
        a_grr()
        .with_resource(
            "scores/collide",
            a_position_score()
            .with_score("c2", "float")
            .with_chrom_mapping(del_prefix="chr")
            .with_tabix()
            .with_data("""
                chrom  pos_begin  pos_end  c2
                chr1   10         12       3.14
                1      11         11       4.14
            """),
        )
        .build_repo(tmp_path)
    )
    resource = repo.get_resource("scores/collide")
    assert resource.config is not None
    table = build_genomic_position_table(resource, resource.config["table"])
    assert isinstance(table, TabixGenomicPositionTable)
    return resource, table


def test_tabix_chrom_mapping_refusal_leaves_no_open_handle(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource, table = _colliding_tabix_table(tmp_path)
    handles = _spy_handles(resource, "open_tabix_file", monkeypatch)

    with pytest.raises(ValueError, match="The chromosome mapping"):
        table.open()

    assert len(handles) == 1
    assert isinstance(handles[0], pysam.TabixFile)
    assert handles[0].closed
    assert table.pysam_file is None


def test_tabix_cancellation_during_setup_leaves_no_open_handle(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ``CancelledError`` is a ``BaseException``: it is how a dask-cancelled
    # statistics scan tears an open down, and an ``except Exception`` guard
    # does not see it.
    resource, table = _colliding_tabix_table(tmp_path)
    handles = _spy_handles(resource, "open_tabix_file", monkeypatch)
    cancelled = asyncio.CancelledError()

    def cancel() -> None:
        raise cancelled

    monkeypatch.setattr(table, "_set_core_column_keys", cancel)

    with pytest.raises(asyncio.CancelledError) as raised:
        table.open()

    assert raised.value is cancelled
    assert len(handles) == 1
    assert handles[0].closed
    assert table.pysam_file is None


def test_tabix_failing_close_does_not_replace_the_refusal(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The caller must see the refusal -- the one line ``grr_manage``
    # attributes to the resource -- not the ``OSError`` from releasing the
    # handle on the way out, with the refusal demoted to its ``__context__``.
    resource, table = _colliding_tabix_table(tmp_path)
    _make_close_fail(resource, "open_tabix_file", monkeypatch)
    refusal = MalformedResourceError("<scores/collide> is malformed")

    def refuse() -> None:
        raise refusal

    monkeypatch.setattr(table, "_set_core_column_keys", refuse)

    with pytest.raises(MalformedResourceError) as raised:
        table.open()

    assert raised.value is refusal
