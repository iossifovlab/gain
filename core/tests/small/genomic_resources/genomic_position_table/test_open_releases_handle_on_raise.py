"""A table's ``open()`` closes the handle it acquired when its setup raises.

The structural half -- every field a failed open established is released, on
every backend, for ``Exception`` and ``BaseException`` alike -- is asked in
test_table_lifetime.py by
``test_a_failed_open_releases_what_it_had_established``; the reasoning is in
``GenomicPositionTable._releasing_on_raise`` (gain#627).
What that sweep cannot see is the handle itself: a field set to ``None`` says
nothing about whether ``close()`` reached the file underneath.  So this module
gets hold of the handle ``open()`` acquired, by spying on the resource's
``open_*_file``, and asserts on it directly.

The raises are injected: the subject is the handle lifecycle around a raise,
not the rule that raised.  The one in-tree fixture where a *real* tabix
refusal is reachable after the acquire
(``test_invalid_chrom_mapping_file_with_tabix``) asserts on its handle in
place.
"""
# pylint: disable=C0116
import logging
import pathlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pytest
import pytest_mock
from gain.genomic_resources.genomic_position_table import (
    build_genomic_position_table,
)
from gain.genomic_resources.genomic_position_table.table import (
    GenomicPositionTable,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.resource_errors import MalformedResourceError
from gain.genomic_resources.testing.builders import (
    a_bigwig_score,
    a_position_score,
    a_vcf_info_score,
)


class _HandleProxy:
    """Forward everything to the real handle, except the given overrides.

    ``__iter__`` is forwarded explicitly because the in-memory backend reads
    its stream with a ``for``, and dunder lookup bypasses ``__getattr__``.
    """

    def __init__(self, handle: Any, **overrides: Any) -> None:
        self._handle = handle
        self.__dict__.update(overrides)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._handle, name)

    def __iter__(self) -> Iterator[Any]:
        return iter(self._handle)


class _HandleWhoseCloseFails(_HandleProxy):
    """``close()`` releases the real handle, then raises.

    ``hts_close`` can fail after the file is gone -- this package documents
    the sibling ``VariantFile.close()`` doing exactly that -- and a proxy is
    the only way to make a Cython handle do it on demand.
    """

    def close(self) -> None:
        self._handle.close()
        raise OSError("release failed")


def _wrap_handles(
    resource: GenomicResource, method: str,
    monkeypatch: pytest.MonkeyPatch, wrap: Callable[[Any], Any],
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


def _bigwig_is_closed(handle: Any) -> bool:
    # A pyBigWig handle has no ``closed`` flag; a released one refuses every
    # call instead.
    try:
        handle.chroms()
    except RuntimeError as error:
        return "not opened" in str(error)
    return False


@dataclass(frozen=True)
class _Backend:
    """How one backend acquires its handle, and how a closed one looks."""

    build: Callable[[pathlib.Path], GenomicResource]
    open_method: str
    handle_field: str
    is_closed: Callable[[Any], bool] = lambda handle: bool(handle.closed)


def _tabular(tmp_path: pathlib.Path, *, tabix: bool) -> GenomicResource:
    builder = a_position_score().with_score("c2", "float").with_data("""
        chrom  pos_begin  pos_end  c2
        chr1   10         12       3.14
    """)
    if tabix:
        builder = builder.with_tabix()
    return builder.build_resource(tmp_path)


def _vcf(tmp_path: pathlib.Path) -> GenomicResource:
    return a_vcf_info_score().with_data("""
##fileformat=VCFv4.1
##INFO=<ID=scoreA,Number=1,Type=Float,Description="score A">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   10  .  A   T   .    .      scoreA=0.1
""").build_resource(tmp_path)


def _bigwig(tmp_path: pathlib.Path) -> GenomicResource:
    return (
        a_bigwig_score()
        .with_score("bw", "float")
        .with_data("chr1  0  10  0.11")
        .with_chrom_lens({"chr1": 1000})
        .build_resource(tmp_path)
    )


_BIGWIG = _Backend(_bigwig, "open_bigwig_file", "_bw_file", _bigwig_is_closed)

_BACKENDS = [
    pytest.param(
        _Backend(lambda p: _tabular(p, tabix=False),
                 "open_raw_file", "str_stream"),
        id="inmemory"),
    pytest.param(
        _Backend(lambda p: _tabular(p, tabix=True),
                 "open_tabix_file", "pysam_file"),
        id="tabix"),
    pytest.param(
        _Backend(_vcf, "open_vcf_file", "pysam_file"),
        id="vcf"),
    pytest.param(_BIGWIG, id="bigwig"),
]


def _table(
    backend: _Backend, tmp_path: pathlib.Path,
) -> tuple[GenomicResource, GenomicPositionTable]:
    resource = backend.build(tmp_path)
    assert resource.config is not None
    return resource, build_genomic_position_table(
        resource, resource.config["table"])


@pytest.mark.parametrize("backend", _BACKENDS)
def test_a_raise_after_the_acquire_closes_the_handle(
    backend: _Backend, tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
) -> None:
    resource, table = _table(backend, tmp_path)
    acquire = mocker.spy(resource, backend.open_method)
    refusal = ValueError("The chromosome mapping collides")
    mocker.patch.object(table, "_build_chrom_mapping", side_effect=refusal)

    with pytest.raises(ValueError) as raised:
        table.open()

    assert raised.value is refusal
    assert acquire.call_count == 1
    assert backend.is_closed(acquire.spy_return)
    assert getattr(table, backend.handle_field) is None


@pytest.mark.parametrize("backend", _BACKENDS)
def test_a_failing_release_does_not_replace_the_refusal(
    backend: _Backend, tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The caller must see the refusal -- the one line ``grr_manage``
    # attributes to the resource -- not the ``OSError`` from releasing the
    # handle on the way out; the failed release is logged instead.
    resource, table = _table(backend, tmp_path)
    handles = _wrap_handles(
        resource, backend.open_method, monkeypatch, _HandleWhoseCloseFails)
    refusal = MalformedResourceError("is malformed")
    mocker.patch.object(table, "_set_core_column_keys", side_effect=refusal)

    with (
        caplog.at_level(logging.WARNING),
        pytest.raises(MalformedResourceError) as raised,
    ):
        table.open()

    assert raised.value is refusal
    assert backend.is_closed(handles[0])
    assert resource.get_full_id() in caplog.text
    assert "release failed" in caplog.text


def test_bigwig_raise_from_the_chroms_read_closes_the_handle(
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The contig read is the first thing after the acquire, and it is a call
    # on the handle itself -- so the raise is injected there, through the
    # handle, rather than at a table method.
    resource, table = _table(_BIGWIG, tmp_path)
    refusal = RuntimeError("corrupt chromosome tree")
    handles = _wrap_handles(
        resource, _BIGWIG.open_method, monkeypatch,
        lambda handle: _HandleProxy(
            handle, chroms=mocker.Mock(side_effect=refusal)))

    with pytest.raises(RuntimeError) as raised:
        table.open()

    assert raised.value is refusal
    assert _BIGWIG.is_closed(handles[0])
    assert getattr(table, _BIGWIG.handle_field) is None
