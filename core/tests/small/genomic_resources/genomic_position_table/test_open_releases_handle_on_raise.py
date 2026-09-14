"""A table's ``open()`` releases the handle it acquired when its setup raises.

Every file-backed backend acquires its native handle first and then does the
setup that can refuse the table -- resolving columns, building the chromosome
mapping, constructing the parser.  Nothing above ``open()`` has been told the
table is open when that setup raises, so no caller will ever ``close()`` it:
the handle is ``open()``'s own to release (gain#627).

Three properties, on each of the three backends:

- a raise from the setup leaves the acquired handle closed;
- a ``BaseException`` (a dask cancellation) does too, and propagates as the
  same object;
- a release that itself fails does not replace the refusal the caller is
  owed.

The raises are injected: the subject is the handle lifecycle around a raise,
not the rule that raised, and no builder knob makes a VCF or bigWig table's
own setup refuse it.  The two in-tree fixtures where a *real* refusal is
reachable -- the tabix chromosome-mapping tests in
test_genomic_position_table.py -- assert on their handle in place.  The
handle itself comes from wrapping the resource's ``open_*_file``: a closed
table with a live handle is exactly the leak, and nothing functional tells it
apart from a released one.
"""
# pylint: disable=C0116
import asyncio
import logging
import pathlib
from typing import Any

import pysam
import pytest
from gain.genomic_resources.genomic_position_table import (
    build_genomic_position_table,
)
from gain.genomic_resources.genomic_position_table.table_bigwig import (
    BigWigTable,
)
from gain.genomic_resources.genomic_position_table.table_tabix import (
    TabixGenomicPositionTable,
)
from gain.genomic_resources.genomic_position_table.table_vcf import (
    VCFGenomicPositionTable,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.resource_errors import MalformedResourceError
from gain.genomic_resources.testing.builders import (
    a_bigwig_score,
    a_grr,
    a_position_score,
    a_vcf_info_score,
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


class _HandleProxy:
    """Forward everything to the real handle; subclasses override one call."""

    def __init__(self, handle: Any) -> None:
        self._handle = handle

    def __getattr__(self, name: str) -> Any:
        return getattr(self._handle, name)


class _HandleWhoseCloseFails(_HandleProxy):
    """``close()`` releases the real handle, then raises.

    ``hts_close`` can fail after the file is gone -- this package documents
    the sibling ``VariantFile.close()`` doing exactly that -- and a proxy is
    the only way to make a Cython handle do it on demand.
    """

    def close(self) -> None:
        self._handle.close()
        raise OSError("hts_close failed")


class _BigWigHandleWhoseChromsFail(_HandleProxy):
    """``chroms()`` -- the first thing the bigWig ``open()`` reads -- raises."""

    def __init__(self, handle: Any, error: BaseException) -> None:
        super().__init__(handle)
        self._error = error

    def chroms(self) -> dict[str, int]:
        raise self._error


def _wrap_handles(
    resource: GenomicResource, method: str,
    monkeypatch: pytest.MonkeyPatch, wrap: Any,
) -> list[Any]:
    """Hand ``open()`` ``wrap(handle)`` in place of each handle; record both."""
    handles: list[Any] = []
    original = getattr(resource, method)

    def wrapping(*args: Any, **kwargs: Any) -> Any:
        handle = original(*args, **kwargs)
        handles.append(handle)
        return wrap(handle)

    monkeypatch.setattr(resource, method, wrapping)
    return handles


def _raise(error: BaseException) -> Any:
    def raiser(*_args: Any, **_kwargs: Any) -> None:
        raise error
    return raiser


def _assert_bigwig_handle_closed(handle: Any) -> None:
    # A pyBigWig handle has no ``closed`` flag; a released one refuses every
    # call instead.
    with pytest.raises(RuntimeError, match="not opened"):
        handle.chroms()


# --- fixtures: one well-formed table per backend ---------------------------


def _tabix_table(
    tmp_path: pathlib.Path,
) -> tuple[GenomicResource, TabixGenomicPositionTable]:
    repo = (
        a_grr()
        .with_resource(
            "scores/tabix",
            a_position_score()
            .with_score("c2", "float")
            .with_tabix()
            .with_data("""
                chrom  pos_begin  pos_end  c2
                chr1   10         12       3.14
            """),
        )
        .build_repo(tmp_path)
    )
    resource = repo.get_resource("scores/tabix")
    assert resource.config is not None
    table = build_genomic_position_table(resource, resource.config["table"])
    assert isinstance(table, TabixGenomicPositionTable)
    return resource, table


def _vcf_table(
    tmp_path: pathlib.Path,
) -> tuple[GenomicResource, VCFGenomicPositionTable]:
    repo = (
        a_grr()
        .with_resource(
            "scores/vcf",
            a_vcf_info_score().with_data("""
##fileformat=VCFv4.1
##INFO=<ID=scoreA,Number=1,Type=Float,Description="score A">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   10  .  A   T   .    .      scoreA=0.1
"""),
        )
        .build_repo(tmp_path)
    )
    resource = repo.get_resource("scores/vcf")
    assert resource.config is not None
    table = build_genomic_position_table(resource, resource.config["table"])
    assert isinstance(table, VCFGenomicPositionTable)
    return resource, table


def _bigwig_table(
    tmp_path: pathlib.Path,
) -> tuple[GenomicResource, BigWigTable]:
    repo = (
        a_grr()
        .with_resource(
            "scores/bw",
            a_bigwig_score()
            .with_score("bw", "float")
            .with_data("chr1  0  10  0.11")
            .with_chrom_lens({"chr1": 1000}),
        )
        .build_repo(tmp_path)
    )
    resource = repo.get_resource("scores/bw")
    assert resource.config is not None
    table = build_genomic_position_table(resource, resource.config["table"])
    assert isinstance(table, BigWigTable)
    return resource, table


# --- tabix -----------------------------------------------------------------


def test_tabix_raise_from_chrom_mapping_leaves_no_open_handle(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource, table = _tabix_table(tmp_path)
    handles = _spy_handles(resource, "open_tabix_file", monkeypatch)
    refusal = ValueError("The chromosome mapping collides")
    monkeypatch.setattr(table, "_build_chrom_mapping", _raise(refusal))

    with pytest.raises(ValueError) as raised:
        table.open()

    assert raised.value is refusal
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
    resource, table = _tabix_table(tmp_path)
    handles = _spy_handles(resource, "open_tabix_file", monkeypatch)
    cancelled = asyncio.CancelledError()
    monkeypatch.setattr(table, "_set_core_column_keys", _raise(cancelled))

    with pytest.raises(asyncio.CancelledError) as raised:
        table.open()

    assert raised.value is cancelled
    assert len(handles) == 1
    assert handles[0].closed
    assert table.pysam_file is None


def test_tabix_failing_close_does_not_replace_the_refusal(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The caller must see the refusal -- the one line ``grr_manage``
    # attributes to the resource -- not the ``OSError`` from releasing the
    # handle on the way out, with the refusal demoted to its ``__context__``.
    # The failed release is not silent, though: it is logged.
    resource, table = _tabix_table(tmp_path)
    handles = _wrap_handles(
        resource, "open_tabix_file", monkeypatch, _HandleWhoseCloseFails)
    refusal = MalformedResourceError("<scores/tabix> is malformed")
    monkeypatch.setattr(table, "_set_core_column_keys", _raise(refusal))

    with (
        caplog.at_level(logging.WARNING),
        pytest.raises(MalformedResourceError) as raised,
    ):
        table.open()

    assert raised.value is refusal
    assert handles[0].closed
    assert "scores/tabix" in caplog.text
    assert "hts_close failed" in caplog.text


# --- vcf -------------------------------------------------------------------


def test_vcf_raise_from_chrom_mapping_leaves_no_open_handle(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource, table = _vcf_table(tmp_path)
    handles = _spy_handles(resource, "open_vcf_file", monkeypatch)
    refusal = ValueError("The chromosome mapping collides")
    monkeypatch.setattr(table, "_build_chrom_mapping", _raise(refusal))

    with pytest.raises(ValueError) as raised:
        table.open()

    assert raised.value is refusal
    assert len(handles) == 1
    assert isinstance(handles[0], pysam.VariantFile)
    assert handles[0].closed
    assert table.pysam_file is None


def test_vcf_cancellation_during_setup_leaves_no_open_handle(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource, table = _vcf_table(tmp_path)
    handles = _spy_handles(resource, "open_vcf_file", monkeypatch)
    cancelled = asyncio.CancelledError()
    monkeypatch.setattr(table, "_set_core_column_keys", _raise(cancelled))

    with pytest.raises(asyncio.CancelledError) as raised:
        table.open()

    assert raised.value is cancelled
    assert len(handles) == 1
    assert handles[0].closed
    assert table.pysam_file is None


def test_vcf_failing_close_does_not_replace_the_refusal(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource, table = _vcf_table(tmp_path)
    handles = _wrap_handles(
        resource, "open_vcf_file", monkeypatch, _HandleWhoseCloseFails)
    refusal = MalformedResourceError("<scores/vcf> is malformed")
    monkeypatch.setattr(table, "_set_core_column_keys", _raise(refusal))

    with pytest.raises(MalformedResourceError) as raised:
        table.open()

    assert raised.value is refusal
    assert handles[0].closed


# --- bigwig ----------------------------------------------------------------


def test_bigwig_raise_from_the_chroms_read_leaves_no_open_handle(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The contig read is the first thing after the acquire, and it is a call
    # on the handle itself -- so the raise is injected there, through the
    # handle, rather than at a table method.
    resource, table = _bigwig_table(tmp_path)
    refusal = RuntimeError("corrupt chromosome tree")
    handles = _wrap_handles(
        resource, "open_bigwig_file", monkeypatch,
        lambda handle: _BigWigHandleWhoseChromsFail(handle, refusal))

    with pytest.raises(RuntimeError) as raised:
        table.open()

    assert raised.value is refusal
    assert len(handles) == 1
    _assert_bigwig_handle_closed(handles[0])
    assert table._bw_file is None
    assert not table.chroms


def test_bigwig_raise_from_chrom_mapping_leaves_no_open_handle(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource, table = _bigwig_table(tmp_path)
    handles = _spy_handles(resource, "open_bigwig_file", monkeypatch)
    refusal = ValueError("The chromosome mapping collides")
    monkeypatch.setattr(table, "_build_chrom_mapping", _raise(refusal))

    with pytest.raises(ValueError) as raised:
        table.open()

    assert raised.value is refusal
    assert len(handles) == 1
    _assert_bigwig_handle_closed(handles[0])
    assert table._bw_file is None
    assert not table.chroms


def test_bigwig_cancellation_during_setup_leaves_no_open_handle(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource, table = _bigwig_table(tmp_path)
    handles = _spy_handles(resource, "open_bigwig_file", monkeypatch)
    cancelled = asyncio.CancelledError()
    monkeypatch.setattr(table, "_set_core_column_keys", _raise(cancelled))

    with pytest.raises(asyncio.CancelledError) as raised:
        table.open()

    assert raised.value is cancelled
    assert len(handles) == 1
    _assert_bigwig_handle_closed(handles[0])
    assert table._bw_file is None


def test_bigwig_failing_close_does_not_replace_the_refusal(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource, table = _bigwig_table(tmp_path)
    handles = _wrap_handles(
        resource, "open_bigwig_file", monkeypatch, _HandleWhoseCloseFails)
    refusal = MalformedResourceError("<scores/bw> is malformed")
    monkeypatch.setattr(table, "_set_core_column_keys", _raise(refusal))

    with pytest.raises(MalformedResourceError) as raised:
        table.open()

    assert raised.value is refusal
    _assert_bigwig_handle_closed(handles[0])
