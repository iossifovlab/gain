# pylint: disable=C0114,C0116
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from gain.utils.processing_pipeline import Filter, PipelineProcessor, Source
from gain.utils.regions import Region


class _RecordingSource(Source):
    """Yield two items for each fetch and record the region of the fetch."""

    def __init__(self) -> None:
        self.fetched_regions: list[Region | None] = []

    def fetch(self, region: Region | None = None) -> Iterable[Any]:
        self.fetched_regions.append(region)
        label = "all" if region is None else str(region)
        return [f"{label}#1", f"{label}#2"]


class _RecordingFilter(Filter):
    """Record each input and pass on the input with a suffix."""

    def __init__(self, suffix: str) -> None:
        self.suffix = suffix
        self.inputs: list[Any] = []

    def __exit__(self, *args: object) -> None:
        return None

    def filter(self, data: Any) -> Any:
        self.inputs.append(data)
        return f"{data}{self.suffix}"


def _processor() -> tuple[
    PipelineProcessor, _RecordingSource, _RecordingFilter, _RecordingFilter,
]:
    source = _RecordingSource()
    first = _RecordingFilter("+a")
    second = _RecordingFilter("+b")
    return PipelineProcessor(source, [first, second]), source, first, second


def test_process_without_regions_fetches_the_whole_source_once() -> None:
    processor, source, _first, _second = _processor()

    processor.process()

    assert source.fetched_regions == [None]


def test_process_fetches_each_region_in_order() -> None:
    processor, source, _first, _second = _processor()
    regions = [Region("chr2", 1, 10), Region("chr1", 5, 7)]

    processor.process(regions)

    assert source.fetched_regions == regions


def test_process_with_an_empty_region_list_fetches_nothing() -> None:
    processor, source, first, _second = _processor()

    processor.process([])

    assert not source.fetched_regions
    assert not first.inputs


def test_process_passes_each_item_through_the_filters_in_order() -> None:
    processor, _source, first, second = _processor()

    processor.process([Region("chr1", 1, 2), Region("chr3", 4, 5)])

    assert first.inputs == [
        "chr1:1-2#1", "chr1:1-2#2", "chr3:4-5#1", "chr3:4-5#2"]
    assert second.inputs == [
        "chr1:1-2#1+a", "chr1:1-2#2+a", "chr3:4-5#1+a", "chr3:4-5#2+a"]
