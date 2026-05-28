from __future__ import annotations

import argparse
import gc
import gzip
import itertools
import logging
import os
import sys
import traceback
from collections.abc import Iterable, Sequence
from contextlib import chdir, closing
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, TextIO, cast

from pysam import TabixFile, tabix_compress, tabix_index

from gain import __version__
from gain.annotation.annotate_utils import (
    add_common_annotation_arguments,
    add_input_files_to_task_graph,
    build_cli_genomic_context,
    cache_pipeline_resources,
    check_resource_locality,
    get_grr_from_context,
    get_pipeline_from_context,
    handle_default_args,
    produce_partfile_paths,
    produce_regions,
    stringify,
)
from gain.annotation.annotation_config import (
    RawAnnotatorsConfig,
    RawPipelineConfig,
)
from gain.annotation.annotation_factory import (
    build_annotation_pipeline,
    load_pipeline_from_file,
)
from gain.annotation.annotation_pipeline import (
    AnnotationPipeline,
    Attribute,
    ReannotationPipeline,
)
from gain.annotation.processing_pipeline import (
    Annotation,
    AnnotationPipelineAnnotatablesBatchFilter,
    AnnotationPipelineAnnotatablesFilter,
    AnnotationsWithSource,
    DeleteAttributesFromAWSBatchFilter,
    DeleteAttributesFromAWSFilter,
)
from gain.annotation.record_to_annotatable import (
    RECORD_TO_ANNOTATABLE_CONFIGURATION,
    DaeAlleleRecordToAnnotatable,
    RecordToCNVAllele,
    RecordToPosition,
    RecordToRegion,
    RecordToVcfAllele,
    add_record_to_annotable_arguments,
    build_record_to_annotatable,
)
from gain.genomic_resources.reference_genome import (
    ReferenceGenome,
    build_reference_genome_from_resource_id,
)
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)
from gain.task_graph.cli_tools import TaskGraphCli
from gain.task_graph.graph import TaskGraph
from gain.utils.fs_utils import (
    is_compressed_filename,
    strip_compression_suffix,
    tabix_index_filename,
)
from gain.utils.processing_pipeline import Filter, PipelineProcessor, Source
from gain.utils.regions import Region
from gain.utils.verbosity_configuration import VerbosityConfiguration

logger = logging.getLogger("annotate_tabular")


class _CSVSource(Source):
    """Source for delimiter-separated values files."""

    def __init__(
        self,
        path: str,
        ref_genome: ReferenceGenome | None,
        columns_args: dict[str, str],
        input_separator: str,
    ):
        self.path = path
        self.ref_genome = ref_genome
        self.columns_args = columns_args
        self.source_file: TextIO | TabixFile
        self.input_separator = input_separator
        self.header: list[str] = self._extract_header()

    def __enter__(self) -> _CSVSource:
        index_filename = (
            tabix_index_filename(self.path)
            if is_compressed_filename(self.path) else None
        )
        if index_filename is not None:
            self.source_file = TabixFile(self.path, index=index_filename)
        elif is_compressed_filename(self.path):
            self.source_file = gzip.open(self.path, "rt")
            self.source_file.readline()  # Skip header line
        else:
            self.source_file = open(self.path, "rt")
            self.source_file.readline()  # Skip header line
        if self.ref_genome is not None:
            self.ref_genome.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        if exc_type is not None:
            logger.error(
                "exception during annotation: %s, %s, %s",
                exc_type, exc_value, traceback.format_tb(exc_tb))

        self.source_file.close()

        if self.ref_genome is not None:
            self.ref_genome.close()

        return exc_type is None

    def _extract_header(self) -> list[str]:
        if is_compressed_filename(self.path):
            with gzip.open(self.path, "rt") as in_file_raw:
                raw_header = in_file_raw.readline()
        else:
            with open(self.path, "rt") as in_file_raw:
                raw_header = in_file_raw.readline()

        return [
            c.strip("#")
            for c in raw_header.strip("\r\n").split(self.input_separator)
        ]

    def _get_line_iterator(self, region: Region | None) -> Iterable:
        if not isinstance(self.source_file, TabixFile):
            return self.source_file
        if region is None:
            return self.source_file.fetch()  # type: ignore
        assert region.start is not None
        return self.source_file.fetch(  # type: ignore
            region.chrom, region.start - 1, region.stop)

    def fetch(
        self, region: Region | None = None,
    ) -> Iterable[AnnotationsWithSource]:
        line_iterator = self._get_line_iterator(region)
        record_to_annotatable = build_record_to_annotatable(
            self.columns_args, set(self.header),
            ref_genome=self.ref_genome)

        reg_start = region.start if region and region.start is not None else 1
        errors = []
        for lnum, line in enumerate(line_iterator):
            try:
                columns = line.strip("\n\r").split(self.input_separator)
                record = dict(zip(self.header, columns, strict=True))
                annotatable = record_to_annotatable.build(record)
                if annotatable.position < reg_start:
                    continue
                yield AnnotationsWithSource(
                    record, [Annotation(annotatable, dict(record))],
                )
            except Exception as ex:  # pylint: disable=broad-except
                logger.exception(
                    "unexpected input data format at line: %s", line)
                errors.append((
                    lnum, line,
                    "".join(traceback.format_exception(ex)), str(ex)))
                if len(errors) >= 10:
                    break
        if len(errors) > 0:
            for _lnum, line, error, message in errors:
                logger.error("line: %s", line)
                logger.error("\t%s", message)
                logger.error("\t%s", error)
            lnum, line, error, message = errors[0]
            raise ValueError(
                f"errors occured during reading of CSV file starting at "
                f"line: {line.strip()}: {message}")


class _CSVBatchSource(Source):
    """Batched source for delimiter-separated values files."""

    def __init__(
        self,
        path: str,
        ref_genome: ReferenceGenome | None,
        columns_args: dict[str, str],
        input_separator: str,
        batch_size: int,
    ):
        self.source = _CSVSource(
            path, ref_genome, columns_args, input_separator)
        self.header = self.source.header
        self.batch_size = batch_size

    def __enter__(self) -> _CSVBatchSource:
        self.source.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        if exc_type is not None:
            logger.error(
                "exception during annotation: %s, %s, %s",
                exc_type, exc_value, traceback.format_tb(exc_tb))

        self.source.__exit__(exc_type, exc_value, exc_tb)

        return exc_type is None

    def fetch(
        self, region: Region | None = None,
    ) -> Iterable[Sequence[AnnotationsWithSource]]:
        records = self.source.fetch(region)
        while batch := tuple(itertools.islice(records, self.batch_size)):
            yield batch


class _CSVWriter(Filter):
    """Writes delimiter-separated values to a file."""

    def __init__(
        self,
        path: str,
        separator: str,
        header: _CSVHeader,
    ) -> None:
        self.path = path
        self.separator = separator
        self.header = header
        self.out_file: Any

    def __enter__(self) -> _CSVWriter:
        self.out_file = open(self.path, "w")
        header_row = self.separator.join([
            *self.header.input_header,
            *self.header.annotation_header,
        ])
        self.out_file.write(f"{header_row}\n")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        if exc_type is not None:
            logger.error(
                "exception during annotation: %s, %s, %s",
                exc_type, exc_value, traceback.format_tb(exc_tb))

        self.out_file.close()

        return exc_type is None

    def filter(self, data: AnnotationsWithSource) -> None:
        context = data.annotations[0].context
        source = data.source
        source_result = {
            col: source[col]
            for col in self.header.input_header
        }
        annotation_result = {
            col: context[col]
            for col in self.header.annotation_header
        }
        self.out_file.write(
            self.separator.join(
                stringify(val)
                for val in [
                    *source_result.values(), *annotation_result.values()]))

        self.out_file.write("\n")


class _CSVBatchWriter(Filter):
    """Writes delimiter-separated values to a file in batches."""

    def __init__(
        self,
        path: str,
        separator: str,
        header: _CSVHeader,
    ) -> None:
        self.writer = _CSVWriter(path, separator, header)

    def __enter__(self) -> _CSVBatchWriter:
        self.writer.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        if exc_type is not None:
            logger.error(
                "exception during annotation: %s, %s, %s",
                exc_type, exc_value, traceback.format_tb(exc_tb))

        self.writer.__exit__(exc_type, exc_value, exc_tb)

        return exc_type is None

    def filter(self, data: Sequence[AnnotationsWithSource]) -> None:
        for record in data:
            self.writer.filter(record)


@dataclass
class _CSVHeader:
    input_header: list[str]
    annotation_header: list[str]


def _build_new_header(
    input_header: list[str],
    annotation_attributes: list[Attribute],
    attributes_to_delete: Sequence[str],
) -> _CSVHeader:
    result = list(input_header)
    for attr_name in attributes_to_delete:
        if attr_name in result:
            result.remove(attr_name)
    annotation_header = [
        attr.name for attr in annotation_attributes if not attr.internal
    ]
    return _CSVHeader(
        result,
        annotation_header,
    )


def _build_sequential(
    input_path: str,
    pipeline: AnnotationPipeline,
    output_path: str,
    args: dict[str, Any],
    reference_genome: ReferenceGenome | None,
    attributes_to_delete: Sequence[str],
) -> PipelineProcessor:
    source = _CSVSource(
        input_path,
        reference_genome,
        args["columns_args"],
        args["input_separator"],
    )
    filters: list[Filter] = []
    new_header = _build_new_header(
        source.header, pipeline.get_attributes(), attributes_to_delete)
    filters.extend([
        DeleteAttributesFromAWSFilter(attributes_to_delete),
        AnnotationPipelineAnnotatablesFilter(pipeline),
        _CSVWriter(output_path, args["output_separator"], new_header),
    ])
    return PipelineProcessor(source, filters)


def _build_batched(
    input_path: str,
    pipeline: AnnotationPipeline,
    output_path: str,
    args: dict[str, Any],
    reference_genome: ReferenceGenome | None,
    attributes_to_delete: Sequence[str],
) -> PipelineProcessor:
    source = _CSVBatchSource(
        input_path,
        reference_genome,
        args["columns_args"],
        args["input_separator"],
        args["batch_size"],
    )
    filters: list[Filter] = []
    new_header = _build_new_header(
        source.header, pipeline.get_attributes(), attributes_to_delete)
    filters.extend([
        DeleteAttributesFromAWSBatchFilter(attributes_to_delete),
        AnnotationPipelineAnnotatablesBatchFilter(pipeline),
        _CSVBatchWriter(output_path, args["output_separator"], new_header),
    ])
    return PipelineProcessor(source, filters)


def _annotate_csv(
    output_path: str,
    pipeline_config: RawAnnotatorsConfig,
    grr_definition: dict,
    reference_genome_resource_id: str | None,
    region: Region | None,
    args: dict[str, Any],
) -> None:
    """Annotate a CSV file using a processing pipeline."""

    build_cli_genomic_context(args)
    grr = build_genomic_resource_repository(definition=grr_definition)

    pipeline_previous = None
    if args["reannotate"]:
        pipeline_previous = load_pipeline_from_file(args["reannotate"], grr)

    ref_genome = None
    if reference_genome_resource_id is not None:
        ref_genome = build_reference_genome_from_resource_id(
            reference_genome_resource_id, grr)

    pipeline = build_annotation_pipeline(
        pipeline_config, grr,
        allow_repeated_attributes=args["allow_repeated_attributes"],
        work_dir=Path(args["work_dir"]),
    )

    attributes_to_delete = []

    if pipeline_previous:
        pipeline = ReannotationPipeline(
            pipeline, pipeline_previous,
            full_reannotation=args["full_reannotation"])
        attributes_to_delete = pipeline.deleted_attributes

    _annotate_tabular_helper(
        input_path=args["input"],
        pipeline=pipeline,
        output_path=output_path,
        args=args,
        reference_genome=ref_genome,
        region=region,
        attributes_to_delete=attributes_to_delete,
    )


def _concat(
    partfile_paths: list[str],
    output_path: str,
    keep_parts: bool,  # noqa: FBT001
) -> None:
    """Concatenate multiple CSV files into a single CSV file *in order*."""
    # Get any header from the partfiles, they should all be equal
    # and usable as a final output header
    with open(partfile_paths[0], "r") as partfile:
        header = partfile.readline().strip()

    with open(output_path, "w") as outfile:
        outfile.write(header)

        for path in partfile_paths:
            with open(path, "r") as partfile:
                partfile.readline()  # skip header
                for line in partfile:
                    outfile.write("\n")
                    outfile.write(line.strip("\r\n"))

        outfile.write("\n")

    if not keep_parts:
        for partfile_path in partfile_paths:
            os.remove(partfile_path)


def _read_header(filepath: str, separator: str = "\t") -> list[str]:
    """Extract header from columns file."""
    if is_compressed_filename(filepath):
        file = gzip.open(filepath, "rt")  # noqa: SIM115
    else:
        file = open(filepath, "r")  # noqa: SIM115
    with file:
        header = file.readline()
    return [c.strip() for c in header.split(separator)]


def _count_tabular_rows(input_path: str, limit: int) -> int:
    """Count data rows (excluding the header), short-circuiting at limit."""
    opener = gzip.open if is_compressed_filename(input_path) else open
    count = 0
    with opener(input_path, "rt") as in_file:
        in_file.readline()  # skip header
        for _ in in_file:
            count += 1
            if count >= limit:
                break
    return count


def _tabix_compress(filepath: str, output_path: str | None = None) -> None:
    """Produce a tabix-compressed version of the given variants file."""
    if output_path is None:
        output_path = f"{filepath}.gz"
    tabix_compress(filepath, output_path, force=True)
    if os.path.exists(filepath):
        os.remove(filepath)


def _tabix_index(filepath: str, args: dict | None = None) -> None:
    """Produce a tabix index file for the given variants file."""
    header = _read_header(filepath)
    line_skip = 0 if header[0].startswith("#") else 1
    header = [c.strip("#") for c in header]
    record_to_annotatable = build_record_to_annotatable(
        args if args is not None else {},
        set(header),
    )
    if isinstance(record_to_annotatable, (RecordToRegion,
                                          RecordToCNVAllele)):
        seq_col = header.index(record_to_annotatable.chrom_col)
        start_col = header.index(record_to_annotatable.pos_beg_col)
        end_col = header.index(record_to_annotatable.pos_end_col)
    elif isinstance(record_to_annotatable, RecordToVcfAllele):
        seq_col = header.index(record_to_annotatable.chrom_col)
        start_col = header.index(record_to_annotatable.pos_col)
        end_col = start_col
    elif isinstance(
            record_to_annotatable,
            (RecordToPosition, DaeAlleleRecordToAnnotatable)):
        seq_col = header.index(record_to_annotatable.chrom_column)
        start_col = header.index(record_to_annotatable.pos_column)
        end_col = start_col
    else:
        raise TypeError(
            "Could not generate tabix index: record"
            f" {type(record_to_annotatable)} is of unsupported type.")
    logger.info(
        "producing tabix index for '%s': "
        "tabix_index(%s, seq_col=%s, start_col=%s, end_col=%s, "
        "line_skip=%s, force=True)",
        filepath, filepath, seq_col, start_col, end_col, line_skip)
    try:
        tabix_index(filepath,
                    seq_col=seq_col,
                    start_col=start_col,
                    end_col=end_col,
                    line_skip=line_skip,
                    force=True)
    except Exception:  # pylint: disable=broad-except
        logger.exception("failed to create tabix index for '%s'", filepath)
        raise


def _add_tasks_tabixed(
    args: dict[str, Any],
    task_graph: TaskGraph,
    output_path: str,
    pipeline_config: RawPipelineConfig,
    grr_definition: dict[str, Any],
    ref_genome_id: str | None,
) -> None:
    # output_path carries the final compression suffix (.gz/.bgz); annotate
    # into the uncompressed working file, then compress to the final name.
    # Without a suffix, working_path would equal output_path and the compress
    # task would tabix_compress(out, out, force=True), truncating it in place.
    assert is_compressed_filename(output_path), (
        f"_add_tasks_tabixed: output_path must carry a compression suffix, "
        f"got {output_path!r}")
    working_path = strip_compression_suffix(output_path)
    with closing(
        TabixFile(
            args["input"], index=tabix_index_filename(args["input"])),
    ) as pysam_file:
        regions = produce_regions(pysam_file, args["region_size"])
    file_paths = produce_partfile_paths(
        args["input"], regions, args["work_dir"])

    annotation_tasks = []
    for region, path in zip(regions, file_paths, strict=True):
        annotation_tasks.append(
            task_graph.create_task(
                f"part-{str(region).replace(':', '-')}",
                _annotate_csv,
                args=[
                    path,
                    pipeline_config,
                    grr_definition,
                    ref_genome_id,
                    region,
                    args,
                ],
                deps=[],
                intermediate_output_files=[path],
            ),
        )

    concat_task = task_graph.create_task(
        "concat",
        _concat,
        args=[file_paths, working_path, args["keep_parts"]],
        deps=annotation_tasks,
        input_files=file_paths,
        intermediate_output_files=[working_path],
    )

    compress_task = task_graph.create_task(
        "tabix_compress",
        _tabix_compress,
        args=[working_path, output_path],
        deps=[concat_task],
        input_files=[working_path],
        output_files=[output_path],
    )

    task_graph.create_task(
        "tabix_index",
        _tabix_index,
        args=[output_path, args["columns_args"]],
        deps=[compress_task],
        input_files=[output_path],
        output_files=[f"{output_path}.tbi"],
    )


def _add_tasks_plaintext(
    args: dict[str, Any],
    task_graph: TaskGraph,
    output_path: str,
    pipeline_config: RawPipelineConfig,
    grr_definition: dict[str, Any],
    ref_genome_id: str | None,
) -> None:
    if is_compressed_filename(output_path):
        working_path = strip_compression_suffix(output_path)
        annotate_task = task_graph.create_task(
            "annotate_all",
            _annotate_csv,
            args=[
                working_path,
                pipeline_config,
                grr_definition,
                ref_genome_id,
                None,
                args,
            ],
            deps=[],
            intermediate_output_files=[working_path],
        )
        task_graph.create_task(
            "tabix_compress",
            _tabix_compress,
            args=[working_path, output_path],
            deps=[annotate_task],
            output_files=[output_path],
        )
    else:
        task_graph.create_task(
            "annotate_all",
            _annotate_csv,
            args=[
                output_path,
                pipeline_config,
                grr_definition,
                ref_genome_id,
                None,
                args,
            ],
            deps=[],
            output_files=[output_path],
        )


def _build_argument_parser() -> argparse.ArgumentParser:
    """Configure argument parser."""
    parser = argparse.ArgumentParser(
        description="Annotate columns",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    add_record_to_annotable_arguments(parser)
    parser.add_argument(
        "--input-separator", "--in-sep", default=None,
        help="The column separator in the input; defaults to a tab, "
             "or a comma when the input filename ends in .csv "
             "(optionally .gz/.bgz compressed)")
    parser.add_argument(
        "--output-separator", "--out-sep", default=None,
        help="The column separator in the output")

    add_common_annotation_arguments(parser)

    return parser


def _adjust_default_input_separator(args: dict[str, Any]) -> dict[str, Any]:
    if args["input_separator"] is not None:
        return args
    suffixes = [s.lower() for s in Path(args["input"]).suffixes]
    if suffixes and suffixes[-1] in (".gz", ".bgz"):
        suffixes = suffixes[:-1]
    if suffixes and suffixes[-1] == ".csv":
        args["input_separator"] = ","
        logger.info(
            "input '%s' has a .csv extension; "
            "defaulting --input-separator to comma", args["input"])
    else:
        args["input_separator"] = "\t"
    return args


def _adjust_default_output_separator(args: dict[str, Any]) -> dict[str, Any]:
    if args["output_separator"] is None:
        args["output_separator"] = args["input_separator"]
    return args


def cli(argv: list[str] | None = None) -> None:
    """Entry point for running the tabular annotation tool."""
    if not argv:
        argv = sys.argv[1:]

    arg_parser = _build_argument_parser()
    args = vars(arg_parser.parse_args(argv))

    if args.get("version"):
        print(f"GAIn version: {__version__}")
        sys.exit(0)

    VerbosityConfiguration.set(args)
    args = handle_default_args(args)
    args = _adjust_default_input_separator(args)
    args = _adjust_default_output_separator(args)

    # Run inside work_dir so that intermediate files created by worker
    # processes (e.g. htslib downloading a remote tabix .tbi index over
    # http) land in work_dir instead of the launch directory. Workers
    # spawned by process_graph inherit this working directory.
    with chdir(args["work_dir"]):
        context = build_cli_genomic_context(args)
        pipeline = get_pipeline_from_context(context)

        grr = get_grr_from_context(context)
        assert grr.definition is not None

        check_resource_locality(
            pipeline,
            lambda limit: _count_tabular_rows(args["input"], limit),
            allow_remote=args["allow_remote_resources"],
        )

        ref_genome = context.get_reference_genome()
        ref_genome_id = ref_genome.resource_id if ref_genome else None

        cache_pipeline_resources(grr, pipeline)

        args["columns_args"] = {
            f"col_{col}": args[f"col_{col}"]
            for cols in RECORD_TO_ANNOTATABLE_CONFIGURATION
            for col in cols
        }

        output_path = args["output"]
        region_size = args["region_size"]

        task_graph = TaskGraph()
        if tabix_index_filename(args["input"]) and region_size > 0:
            _add_tasks_tabixed(
                args,
                task_graph,
                output_path,
                pipeline.raw,
                grr.definition,
                ref_genome_id,
            )
        else:
            logger.info(
                "input %s cannot be split into genomic regions; "
                "forcing sequential execution (-j 1)",
                args["input"])
            args["jobs"] = 1
            _add_tasks_plaintext(
                args,
                task_graph,
                output_path,
                pipeline.raw,
                grr.definition,
                ref_genome_id,
            )

        add_input_files_to_task_graph(args, task_graph)
        TaskGraphCli.process_graph(task_graph, **args)

    pipeline.close()
    if ref_genome is not None:
        ref_genome.close()

    gc.collect()


def _annotate_tabular_helper(
    input_path: str,
    pipeline: AnnotationPipeline,
    output_path: str,
    args: dict[str, Any], *,
    reference_genome: ReferenceGenome | None = None,
    region: Region | None = None,
    attributes_to_delete: Sequence[str] | None = None,
) -> None:
    """Annotate a tabular file using a processing pipeline."""
    attributes_to_delete = attributes_to_delete or []

    filters: list[Filter] = []
    source: Source

    batch_size = cast(int, args.get("batch_size", 0))
    if batch_size <= 0:
        source = _CSVSource(
            input_path,
            reference_genome,
            args["columns_args"],
            args["input_separator"],
        )
        new_header = _build_new_header(
            source.header, pipeline.get_attributes(), attributes_to_delete)
        filters.extend([
            DeleteAttributesFromAWSFilter(attributes_to_delete),
            AnnotationPipelineAnnotatablesFilter(pipeline),
            _CSVWriter(output_path, args["output_separator"], new_header),
        ])
    else:
        source = _CSVBatchSource(
            input_path,
            reference_genome,
            args["columns_args"],
            args["input_separator"],
            args["batch_size"],
        )
        new_header = _build_new_header(
            source.header, pipeline.get_attributes(), attributes_to_delete)
        filters.extend([
            DeleteAttributesFromAWSBatchFilter(attributes_to_delete),
            AnnotationPipelineAnnotatablesBatchFilter(pipeline),
            _CSVBatchWriter(output_path, args["output_separator"], new_header),
        ])

    with PipelineProcessor(source, filters) as processor:
        processor.process_region(region)


def annotate_tabular(
    input_path: str,
    pipeline: AnnotationPipeline,
    output_path: str,
    args: dict[str, Any], *,
    reference_genome: ReferenceGenome | None = None,
    region: Region | None = None,
    attributes_to_delete: Sequence[str] | None = None,
) -> None:
    """Annotate a tabular file using a processing pipeline."""
    temp_output_path = output_path
    if is_compressed_filename(output_path):
        temp_output_path = strip_compression_suffix(output_path)

    _annotate_tabular_helper(
        input_path,
        pipeline,
        temp_output_path,
        args,
        reference_genome=reference_genome,
        region=region,
        attributes_to_delete=attributes_to_delete,
    )
    if is_compressed_filename(output_path):
        # honor the explicit compression suffix (.gz/.bgz)
        _tabix_compress(temp_output_path, output_path)
    elif is_compressed_filename(input_path):
        # uncompressed output name + compressed input: default to .gz
        _tabix_compress(temp_output_path)
