"""Prepare a tabular file for parallel annotation.

Sorts a (possibly gzip-compressed) columnar file by genomic coordinates and
produces a bgzip-compressed, tabix-indexed output that ``annotate_tabular``
can fan out across regions.

The same ``--col-*`` options as ``annotate_tabular`` select which input
columns carry chromosome / position / etc., and the same
``RecordToAnnotable`` lookup is reused to derive the sort and tabix keys.
"""
from __future__ import annotations

import argparse
import gzip
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from pysam import BGZFile, tabix_index

import gain.logging as logging
from gain import __version__
from gain.annotation.annotatable import (
    Annotatable,
    CNVAllele,
    Position,
    Region,
    VCFAllele,
)
from gain.annotation.record_to_annotatable import (
    RECORD_TO_ANNOTATABLE_CONFIGURATION,
    RecordToAnnotable,
    RecordToCNVAllele,
    RecordToPosition,
    RecordToRegion,
    RecordToVcfAllele,
    add_record_to_annotable_arguments,
    build_record_to_annotatable,
)
from gain.genomic_resources.genomic_context import (
    context_providers_init,
    get_genomic_context,
)
from gain.genomic_resources.reference_genome import ReferenceGenome
from gain.utils.fs_utils import is_compressed_filename
from gain.utils.verbosity_configuration import VerbosityConfiguration

logger = logging.getLogger("prepare_tabular")

# tabix requires tab-separated body. We accept other input separators
# (commas for csv inputs) but always emit tab-separated output.
_OUTPUT_SEPARATOR = "\t"


_DIRECT_R2A_TYPES = (
    RecordToPosition, RecordToRegion, RecordToVcfAllele, RecordToCNVAllele,
)


def _detect_input_separator(input_path: str) -> str:
    """Pick a default column separator based on the input filename."""
    name = input_path.lower().removesuffix(".gz")
    if name.endswith(".csv"):
        return ","
    return "\t"


def _check_no_output_separator_in_cells(cols: list[str]) -> None:
    for c in cols:
        if _OUTPUT_SEPARATOR in c:
            raise ValueError(
                f"input cell contains a tab character which would "
                f"break the tab-separated output: {c!r}")


@dataclass
class _SortPlan:
    """Plan for sorting a tabular file.

    ``output_header`` is the header of the produced file (input header
    optionally extended with injected columns).

    ``sort_keys`` describes how to sort the *output* body rows: a list of
    ``(1-based column index in output, kind)`` tuples, where kind is
    ``"n"`` for numeric and ``""`` for lex. The chrom column is encoded
    via ``chrom_col_idx`` (0-based in output) — when a chromosome-order
    rank prefix is in use, ``sort_keys`` excludes the chrom entry
    (the rank already encodes it).

    ``tabix_seq_col``, ``tabix_start_col``, ``tabix_end_col`` are 0-based
    column indexes in the produced file (pysam's tabix_index expects 0-based).

    ``inject``, if set, computes the values to append to each input row.
    """
    output_header: list[str]
    chrom_col_idx: int
    sort_keys: list[tuple[int, str]]
    tabix_seq_col: int
    tabix_start_col: int
    tabix_end_col: int
    inject: Callable[[dict[str, str]], list[str]] | None = None
    injected_count: int = 0
    expected_annotatable_type: type | None = None


def _build_direct_sort_plan(
    r2a: RecordToAnnotable, header: list[str],
) -> _SortPlan:
    """Build a sort plan for an R2A whose chrom/pos are direct columns."""
    if isinstance(r2a, RecordToPosition):
        chrom_idx = header.index(r2a.chrom_column)
        pos_idx = header.index(r2a.pos_column)
        return _SortPlan(
            output_header=list(header),
            chrom_col_idx=chrom_idx,
            sort_keys=[(pos_idx + 1, "n")],
            tabix_seq_col=chrom_idx,
            tabix_start_col=pos_idx,
            tabix_end_col=pos_idx,
        )
    if isinstance(r2a, RecordToVcfAllele):
        chrom_idx = header.index(r2a.chrom_col)
        pos_idx = header.index(r2a.pos_col)
        ref_idx = header.index(r2a.ref_col)
        alt_idx = header.index(r2a.alt_col)
        return _SortPlan(
            output_header=list(header),
            chrom_col_idx=chrom_idx,
            sort_keys=[
                (pos_idx + 1, "n"),
                (ref_idx + 1, ""),
                (alt_idx + 1, ""),
            ],
            tabix_seq_col=chrom_idx,
            tabix_start_col=pos_idx,
            tabix_end_col=pos_idx,
        )
    if isinstance(r2a, (RecordToRegion, RecordToCNVAllele)):
        chrom_idx = header.index(r2a.chrom_col)
        beg_idx = header.index(r2a.pos_beg_col)
        end_idx = header.index(r2a.pos_end_col)
        return _SortPlan(
            output_header=list(header),
            chrom_col_idx=chrom_idx,
            sort_keys=[(beg_idx + 1, "n"), (end_idx + 1, "n")],
            tabix_seq_col=chrom_idx,
            tabix_start_col=beg_idx,
            tabix_end_col=end_idx,
        )
    raise TypeError(
        f"unsupported direct record-to-annotatable type: {type(r2a).__name__}")


_INJECTED_NAMES_BY_TYPE: dict[type, list[str]] = {
    Position: ["chrom", "pos"],
    Region: ["chrom", "pos_beg", "pos_end"],
    VCFAllele: ["chrom", "pos", "ref", "alt"],
    CNVAllele: ["chrom", "pos_beg", "pos_end", "cnv_type"],
}


def _injected_values_for(ann: Annotatable) -> list[str]:
    if isinstance(ann, VCFAllele):
        return [ann.chrom, str(ann.position), ann.reference, ann.alternative]
    if isinstance(ann, CNVAllele):
        return [ann.chrom, str(ann.position), str(ann.end_position),
                ann.type.name]
    if isinstance(ann, Region):
        return [ann.chrom, str(ann.position), str(ann.end_position)]
    if isinstance(ann, Position):
        return [ann.chrom, str(ann.position)]
    raise ValueError(f"unsupported annotatable type: {type(ann).__name__}")


def _build_indirect_sort_plan(
    r2a: RecordToAnnotable,
    header: list[str],
    first_row: dict[str, str],
) -> _SortPlan:
    """Build a sort plan for an R2A that requires computing the annotatable."""
    sample_ann = r2a.build(first_row)
    ann_type = type(sample_ann)
    if ann_type not in _INJECTED_NAMES_BY_TYPE:
        raise ValueError(
            f"unsupported annotatable type produced by "
            f"{type(r2a).__name__}: {ann_type.__name__}")

    inj_names = _INJECTED_NAMES_BY_TYPE[ann_type]
    collisions = [n for n in inj_names if n in header]
    if collisions:
        raise ValueError(
            f"cannot inject sort columns {collisions} into a file that "
            f"already has columns with those names; rename the input "
            f"columns or use a record-to-annotatable layout that uses "
            f"them directly")

    output_header = [*header, *inj_names]
    n_orig = len(header)
    inj_idx = {name: n_orig + i for i, name in enumerate(inj_names)}
    chrom_col_idx = inj_idx["chrom"]

    if ann_type is Position:
        sort_keys = [(inj_idx["pos"] + 1, "n")]
        tabix = (chrom_col_idx, inj_idx["pos"], inj_idx["pos"])
    elif ann_type is Region:
        sort_keys = [(inj_idx["pos_beg"] + 1, "n"),
                     (inj_idx["pos_end"] + 1, "n")]
        tabix = (chrom_col_idx, inj_idx["pos_beg"], inj_idx["pos_end"])
    elif ann_type is VCFAllele:
        sort_keys = [(inj_idx["pos"] + 1, "n"),
                     (inj_idx["ref"] + 1, ""),
                     (inj_idx["alt"] + 1, "")]
        tabix = (chrom_col_idx, inj_idx["pos"], inj_idx["pos"])
    else:  # CNVAllele
        sort_keys = [(inj_idx["pos_beg"] + 1, "n"),
                     (inj_idx["pos_end"] + 1, "n")]
        tabix = (chrom_col_idx, inj_idx["pos_beg"], inj_idx["pos_end"])

    def inject(record: dict[str, str]) -> list[str]:
        ann = r2a.build(record)
        if not isinstance(ann, ann_type):
            raise TypeError(
                f"non-uniform annotatable types in input: expected "
                f"{ann_type.__name__}, got {type(ann).__name__} "
                f"for record {record}")
        return _injected_values_for(ann)

    return _SortPlan(
        output_header=output_header,
        chrom_col_idx=chrom_col_idx,
        sort_keys=sort_keys,
        tabix_seq_col=tabix[0],
        tabix_start_col=tabix[1],
        tabix_end_col=tabix[2],
        inject=inject,
        injected_count=len(inj_names),
        expected_annotatable_type=ann_type,
    )


def _open_text(path: str) -> TextIO:
    if is_compressed_filename(path):
        return gzip.open(path, "rt")
    return open(path, "rt")


def _read_header(path: str, separator: str) -> list[str]:
    with _open_text(path) as f:
        raw = f.readline()
    return [c.strip("#") for c in raw.rstrip("\r\n").split(separator)]


def _read_first_data_row(
    path: str, separator: str, header: list[str],
) -> dict[str, str] | None:
    with _open_text(path) as f:
        f.readline()  # skip header
        for line in f:
            stripped = line.rstrip("\r\n")
            if not stripped:
                continue
            cols = stripped.split(separator)
            return dict(zip(header, cols, strict=False))
    return None


def _build_sort_cmd(
    plan: _SortPlan,
    *,
    rank_prefix: bool,
    separator: str,
    work_dir: str,
    threads: int | None,
    buffer: str | None,
) -> list[str]:
    """Construct the GNU/BSD ``sort`` invocation.

    Column indexes in plan.sort_keys are 1-based in the *output* layout.
    When ``rank_prefix`` is on, every line passed to sort has an extra
    leading column (the rank), so we shift sort_keys by +1 and add the
    rank as the primary numeric key.
    """
    cmd = ["sort", "-t", separator, "-T", work_dir]
    if threads is not None:
        cmd.append(f"--parallel={threads}")
    if buffer is not None:
        cmd.extend(["-S", buffer])

    if rank_prefix:
        cmd.append("-k1,1n")
        for col, kind in plan.sort_keys:
            shifted = col + 1
            cmd.append(f"-k{shifted},{shifted}{kind}")
    else:
        chrom_col_1b = plan.chrom_col_idx + 1
        cmd.append(f"-k{chrom_col_1b},{chrom_col_1b}")
        for col, kind in plan.sort_keys:
            cmd.append(f"-k{col},{col}{kind}")
    return cmd


def _iter_processed_rows(
    *,
    input_path: str,
    plan: _SortPlan,
    input_separator: str,
    chrom_rank: dict[str, int] | None,
    unknown_chroms: dict[str, int],
) -> Iterable[bytes]:
    """Yield body rows as utf-8 bytes, tab-joined.

    Performs injection of computed columns and (optionally) prepends a
    numeric chromosome-order rank column. ``unknown_chroms`` is mutated
    in place with counts of chromosome names not present in
    ``chrom_rank``.
    """
    rank_prefix = chrom_rank is not None
    unknown_rank = len(chrom_rank) if chrom_rank is not None else 0
    sep = _OUTPUT_SEPARATOR

    with _open_text(input_path) as f_in:
        f_in.readline()  # skip header
        for line in f_in:
            stripped = line.rstrip("\r\n")
            if not stripped:
                continue
            cols = stripped.split(input_separator)
            _check_no_output_separator_in_cells(cols)
            if plan.inject is not None:
                record = dict(zip(plan.output_header, cols, strict=False))
                cols = [*cols, *plan.inject(record)]
            if rank_prefix:
                assert chrom_rank is not None
                chrom = cols[plan.chrom_col_idx]
                rank = chrom_rank.get(chrom)
                if rank is None:
                    unknown_chroms[chrom] = unknown_chroms.get(chrom, 0) + 1
                    rank = unknown_rank
                yield (f"{rank}{sep}" + sep.join(cols) + "\n").encode()
            else:
                yield (sep.join(cols) + "\n").encode()


def _stream_input_to_bgzip(
    *,
    input_path: str,
    output_path: str,
    plan: _SortPlan,
    input_separator: str,
    chrom_rank: dict[str, int] | None,
    skip_sort: bool,
    work_dir: str,
    threads: int | None,
    buffer: str | None,
) -> None:
    """Stream input through ``sort`` (when needed) and into a bgzip output.

    No intermediate uncompressed files are created. The header is
    written first, followed by the (optionally sorted) body. When a
    chromosome-order rank prefix is in use, it is stripped from each
    line on the way out of ``sort`` before being written to the bgzip
    stream.
    """
    rank_prefix = chrom_rank is not None and not skip_sort
    unknown_chroms: dict[str, int] = {}
    sep_bytes = _OUTPUT_SEPARATOR.encode()
    header_bytes = (
        _OUTPUT_SEPARATOR.join(plan.output_header) + "\n").encode()

    def write_header_and(body_iter: Iterable[bytes]) -> None:
        with BGZFile(output_path, "wb", index=None) as bgz:
            bgz.write(header_bytes)
            for chunk in body_iter:
                bgz.write(chunk)

    try:
        if skip_sort:
            logger.info("--skip-sort set; streaming input straight to bgzip")
            write_header_and(_iter_processed_rows(
                input_path=input_path, plan=plan,
                input_separator=input_separator,
                chrom_rank=None, unknown_chroms=unknown_chroms,
            ))
        else:
            sort_cmd = _build_sort_cmd(
                plan, rank_prefix=rank_prefix,
                separator=_OUTPUT_SEPARATOR,
                work_dir=work_dir, threads=threads, buffer=buffer)
            logger.info("sort command: %s", " ".join(sort_cmd))
            env = {**os.environ, "LC_ALL": "C"}
            # long-lived: stdin/stdout are driven manually below and the
            # process is wait()-ed at the end, so a `with` block won't fit.
            sort_proc = subprocess.Popen(  # pylint: disable=consider-using-with
                sort_cmd,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env,
            )
            assert sort_proc.stdin is not None
            assert sort_proc.stdout is not None
            try:
                # Feed all input to sort. GNU sort drains stdin into its
                # internal --temporary-directory before emitting output,
                # so no deadlock on a synchronous write-then-read loop.
                for row in _iter_processed_rows(
                        input_path=input_path, plan=plan,
                        input_separator=input_separator,
                        chrom_rank=chrom_rank,
                        unknown_chroms=unknown_chroms):
                    sort_proc.stdin.write(row)
            finally:
                sort_proc.stdin.close()

            def stripped_sort_output() -> Iterable[bytes]:
                assert sort_proc.stdout is not None
                if rank_prefix:
                    for line in sort_proc.stdout:
                        idx = line.index(sep_bytes)
                        yield line[idx + len(sep_bytes):]
                else:
                    yield from sort_proc.stdout

            write_header_and(stripped_sort_output())
    except BaseException:
        if os.path.exists(output_path):
            os.remove(output_path)
        raise

    if not skip_sort:
        rc = sort_proc.wait()
        if rc != 0:
            if os.path.exists(output_path):
                os.remove(output_path)
            raise RuntimeError(
                f"native sort failed with exit code {rc}: "
                f"{' '.join(sort_cmd)}")

    if unknown_chroms:
        logger.warning(
            "%d chromosome name(s) not found in the reference genome were "
            "sorted to the end of the file: %s",
            len(unknown_chroms),
            ", ".join(
                f"{c}({n})" for c, n in sorted(unknown_chroms.items())[:10]),
        )


def _default_output_path(input_path: str) -> str:
    """Map ``FFF.<ext>[.gz|.bgz]`` to ``FFF.sorted.tsv.bgz``."""
    p = Path(input_path)
    if p.suffix.lower() in (".gz", ".bgz"):
        p = p.with_suffix("")
    if p.suffix:
        p = p.with_suffix("")
    return str(p) + ".sorted.tsv.bgz"


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Sort and tabix-index a tabular file so that annotate_tabular "
            "can parallelize annotation."),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "input",
        help="Input tabular file (plain text or gzip/bgzip compressed).")
    parser.add_argument(
        "-o", "--output", default=None,
        help=("Output bgzip-compressed file path (must end with .bgz "
              "or .gz). Defaults to <input-stem>.sorted.bgz next to "
              "the input."))
    parser.add_argument(
        "--input-separator", "--in-sep", default=None,
        help=("The column separator in the input. If not specified, "
              "defaults to ',' for .csv/.csv.gz inputs and to a tab "
              "otherwise. The output is always tab-separated."))
    parser.add_argument(
        "--skip-sort", action="store_true",
        help=("Assume the input is already sorted; only bgzip and tabix "
              "index it (still injecting derived chrom/pos columns when "
              "needed)."))
    parser.add_argument(
        "-w", "--work-dir", default=None,
        help=("Directory for temporary files used by the native sort. "
              "Defaults to a fresh temporary directory next to the "
              "output."))
    parser.add_argument(
        "--sort-threads", type=int, default=None,
        help="Threads for native sort (maps to sort's --parallel).")
    parser.add_argument(
        "--sort-buffer", default=None,
        help="Memory buffer for native sort, e.g. 1G (maps to sort's -S).")
    parser.add_argument(
        "--version", action="store_true",
        help="Show the GAIn version and exit.")

    add_record_to_annotable_arguments(parser)
    _add_genomic_context_arguments(parser)
    VerbosityConfiguration.set_arguments(parser)
    return parser


def _add_genomic_context_arguments(
    parser: argparse.ArgumentParser,
) -> None:
    """Register only the GRR + reference-genome flags we need.

    We deliberately skip ``context_providers_add_argparser_arguments``
    because it would also register annotation-pipeline-specific options
    (``pipeline`` positional, ``-ar``) and gene-models options (``-G``)
    that are irrelevant to prepare_tabular.
    """
    parser.add_argument(
        "-g", "--grr-filename", "--grr", default=None,
        help="The GRR configuration file. If absent, the default "
             "GRR repository is used.")
    parser.add_argument(
        "--grr-directory", default=None,
        help="Local GRR directory to use as the repository.")
    parser.add_argument(
        "-R", "--reference-genome-resource-id", "--ref", default=None,
        help="The resource id for the reference genome whose chromosome "
             "order will be used. If absent, chromosomes are sorted "
             "lexicographically.")


def _get_reference_genome(args: dict[str, Any]) -> ReferenceGenome | None:
    context_providers_init(**args)
    context = get_genomic_context()
    return context.get_reference_genome()


def cli(argv: list[str] | None = None) -> None:
    """Entry point for the prepare_tabular tool."""
    if not argv:
        argv = sys.argv[1:]

    parser = _build_argument_parser()
    args = vars(parser.parse_args(argv))

    if args.get("version"):
        print(f"GAIn version: {__version__}")
        sys.exit(0)

    VerbosityConfiguration.set(args)

    input_path = args["input"]
    if not os.path.exists(input_path):
        raise FileNotFoundError(input_path)

    input_separator = args["input_separator"] \
        or _detect_input_separator(input_path)
    logger.info("input separator: %r", input_separator)
    output_path = args["output"] or _default_output_path(input_path)
    if not output_path.endswith((".gz", ".bgz")):
        raise ValueError(
            f"--output must end with .bgz or .gz "
            f"(tabix needs a bgzip file); got: {output_path}")

    output_dir = os.path.dirname(os.path.abspath(output_path)) or "."
    os.makedirs(output_dir, exist_ok=True)

    ref_genome = _get_reference_genome(args)
    chrom_rank: dict[str, int] | None = None
    if ref_genome is not None:
        ref_genome.open()
        chrom_rank = {c: i for i, c in enumerate(ref_genome.chromosomes)}
        logger.info(
            "using reference genome %s for chromosome order (%d chromosomes)",
            ref_genome.resource_id, len(chrom_rank))
    else:
        logger.info(
            "no reference genome in genomic context; "
            "sorting chromosomes lexicographically")

    try:
        header = _read_header(input_path, input_separator)
        columns_args = {
            f"col_{c}": args[f"col_{c}"]
            for cols in RECORD_TO_ANNOTATABLE_CONFIGURATION
            for c in cols
        }
        r2a = build_record_to_annotatable(
            columns_args, set(header), ref_genome)

        if isinstance(r2a, _DIRECT_R2A_TYPES):
            plan = _build_direct_sort_plan(r2a, header)
        else:
            first_row = _read_first_data_row(
                input_path, input_separator, header)
            if first_row is None:
                raise ValueError(
                    f"input file {input_path} has no data rows")
            plan = _build_indirect_sort_plan(r2a, header, first_row)

        provided_work_dir = args.get("work_dir")
        with tempfile.TemporaryDirectory(
                prefix="prepare_tabular_",
                dir=provided_work_dir or output_dir) as work_dir:
            _stream_input_to_bgzip(
                input_path=input_path,
                output_path=output_path,
                plan=plan,
                input_separator=input_separator,
                chrom_rank=chrom_rank,
                skip_sort=args["skip_sort"],
                work_dir=work_dir,
                threads=args.get("sort_threads"),
                buffer=args.get("sort_buffer"),
            )

            logger.info(
                "tabix indexing %s (seq_col=%d, start_col=%d, end_col=%d)",
                output_path,
                plan.tabix_seq_col, plan.tabix_start_col, plan.tabix_end_col)
            tabix_index(
                output_path,
                seq_col=plan.tabix_seq_col,
                start_col=plan.tabix_start_col,
                end_col=plan.tabix_end_col,
                line_skip=1,
                force=True,
            )
    finally:
        if ref_genome is not None:
            ref_genome.close()


if __name__ == "__main__":
    cli()
