"""Fluent, immutable test-data builder for ``data_frame`` resources.

A sibling of :mod:`gain.genomic_resources.testing.builders` rather than a
member of it: that module was already within nine lines of its size ceiling
before this builder existed, so the next builder to be added had to live
somewhere.  The dependency runs ONE WAY -- this module imports the shared
single-realize seam from ``builders``, and ``builders`` does not import back
-- so ``a_data_frame`` is imported from here, not from ``builders`` with the
other factories.
"""
from __future__ import annotations

import csv
import dataclasses
import io
import pathlib
from typing import Any

import pandas as pd
import yaml

from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GenomicResource,
)
from gain.genomic_resources.testing import (
    convert_to_tab_separated,
    setup_directories,
)
from gain.genomic_resources.testing.builders import _build_single_resource
from gain.genomic_resources.testing.score_specs import (
    ResourceValidationError,
)

_DATA_FRAME_FORMATS = ("csv", "tsv", "excel")

# The realized filename per format.  ``file:`` points at it, and the
# extension is what a reader browsing the resource expects to find.
_DATA_FRAME_FILENAMES = {
    "csv": "data.csv",
    "tsv": "data.tsv",
    "excel": "data.xlsx",
}

_DEFAULT_DATA_FRAME_DATA = """
    gene  score  label
    G1    0.1    alpha
    G2    0.2    beta
    G3    0.3    gamma
"""


@dataclasses.dataclass(frozen=True)
class DataFrameBuilder:
    """Immutable builder for a single ``data_frame`` resource.

    A ``data_frame`` config declares no columns at all -- only ``file``,
    ``format`` and a ``parameters`` passthrough -- so unlike the score
    builders there is nothing here for a data header to be validated
    against.  What this builder buys instead is the FORMAT axis: one
    authored table realized as csv, tsv or xlsx.  That is the axis
    ``data_frame`` tests vary, and hand-rolling an xlsx fixture per test
    is what makes them tedious otherwise.

    Two authoring modes:

    * :meth:`with_data` -- a whitespace-separated block, normalized by
      ``convert_to_tab_separated`` and rendered into the target format.
    * :meth:`with_raw_content` -- verbatim file content, text or bytes.
      Not a luxury: the ``parameters:`` passthrough (``skiprows``,
      ``comment``, ``na_values``, quoted separators) describes file
      shapes a whitespace block cannot express, and a compressed table
      has no whitespace-block spelling at all.

    A bare builder realizes a valid minimal readable csv resource.

    Deliberately exposes NO expected DataFrame.  Realizing xlsx forces
    this builder to parse the authored block with pandas, and handing
    that frame back as a test's assertion oracle would be circular on
    exactly the separator and dtype axes a ``data_frame`` test varies --
    builder and loader would have to be wrong in the same way for the
    test to stay green, which is precisely the shape of gain#434's
    tsv-parsed-as-csv bug.  Tests state their expectations independently.
    """

    data: str | None = None
    raw_content: str | bytes | None = None
    file_format: str = "csv"
    declared_format: str | None = None
    omit_format_key: bool = False
    filename: str | None = None
    parameters: dict[str, Any] | None = None
    omit_file_key: bool = False

    def with_data(self, data: str) -> DataFrameBuilder:
        """Author the table as a whitespace-separated block.

        The block is normalized by ``convert_to_tab_separated`` (so
        ``||`` becomes a space and ``EMPTY`` a dot) and then rendered
        into whatever :meth:`with_format` selected.
        """
        return dataclasses.replace(self, data=data)

    def with_raw_content(self, content: str | bytes) -> DataFrameBuilder:
        """Write ``content`` to the data file verbatim.

        The escape hatch for file shapes a whitespace block cannot
        express -- comment lines, leading junk rows, quoted separators,
        explicit NA markers -- i.e. everything the ``parameters:``
        passthrough exists to handle.  ``bytes`` for a table the loader
        is meant to decompress, paired with :meth:`with_file` to give it
        a name pandas can infer the compression from.  Mutually
        exclusive with :meth:`with_data`, and unavailable for ``excel``
        (:meth:`with_data` renders the workbook).
        """
        return dataclasses.replace(self, raw_content=content)

    def with_format(self, file_format: str) -> DataFrameBuilder:
        """Select the realized format AND the declared ``format:`` key.

        One of ``csv``, ``tsv``, ``excel``; the filename follows
        (``data.csv`` / ``data.tsv`` / ``data.xlsx``) unless
        :meth:`with_file` overrides it.  To declare a format that does
        NOT match what is on disk -- an unknown format, or a mismatch --
        use :meth:`with_declared_format`.
        """
        if file_format not in _DATA_FRAME_FORMATS:
            raise ResourceValidationError(
                f"unknown data_frame format {file_format!r}; the builder "
                f"can realize {list(_DATA_FRAME_FORMATS)}. To DECLARE an "
                f"unrealizable format, use with_declared_format")
        return dataclasses.replace(self, file_format=file_format)

    def with_declared_format(self, file_format: str) -> DataFrameBuilder:
        """Override the config's ``format:`` only, leaving realization.

        Unvalidated on purpose: this is how a test builds a resource
        declaring an unknown format, or one whose declared format
        disagrees with the bytes on disk.
        """
        return dataclasses.replace(self, declared_format=file_format)

    def with_file(self, filename: str) -> DataFrameBuilder:
        """Override the realized filename (default: per format)."""
        return dataclasses.replace(self, filename=filename)

    def with_parameters(
        self, parameters: dict[str, Any],
    ) -> DataFrameBuilder:
        """Emit a ``parameters:`` block passed through to the reader."""
        return dataclasses.replace(self, parameters=dict(parameters))

    def without_file_key(self) -> DataFrameBuilder:
        """Omit ``file:`` from the config, keeping the data file.

        Realizes the gain#434 misconfiguration the loader rejects, in the
        spirit of ``with_missing_header_mode``: the resource is complete
        on disk but does not say which file to read.
        """
        return dataclasses.replace(self, omit_file_key=True)

    def without_format_key(self) -> DataFrameBuilder:
        """Omit ``format:``, exercising the loader's ``csv`` default."""
        return dataclasses.replace(self, omit_format_key=True)

    def realize_into(self, resource_dir: pathlib.Path) -> None:
        """Write this data_frame resource into ``resource_dir``.

        Raises a ``ResourceValidationError`` on invalid content;
        ``GRRBuilder`` annotates it with the resource id.
        """
        setup_directories(resource_dir, _build_data_frame_content(self))

    def build_resource(
        self, tmp_path: pathlib.Path,
    ) -> GenomicResource:
        """Realize this single resource (repo id ``""``) into ``tmp_path``."""
        return _build_single_resource(self, tmp_path)


def _render_data_frame_table(builder: DataFrameBuilder) -> str | bytes:
    """Render the authored table into the target format's file content.

    Everything routes through one canonical tab-separated intermediate,
    so the three formats carry the same logical table by construction --
    which is what makes a cross-format equivalence test meaningful
    rather than a restatement.
    """
    if builder.raw_content is not None:
        return builder.raw_content

    data = builder.data if builder.data is not None \
        else _DEFAULT_DATA_FRAME_DATA
    # A block's closing indentation survives ``convert_to_tab_separated`` as
    # an empty line; it would land as a trailing blank record.
    lines = [
        line for line in convert_to_tab_separated(data).split("\n") if line
    ]
    tsv = "".join(f"{line}\n" for line in lines)

    if builder.file_format == "tsv":
        return tsv
    if builder.file_format == "csv":
        # Through ``csv.writer`` rather than a join, so that a value
        # carrying the separator or a quote character is quoted rather
        # than silently shifting the columns -- "the same table by
        # construction" has to hold for those values too.
        text_buffer = io.StringIO(newline="")
        writer = csv.writer(text_buffer, lineterminator="\n")
        writer.writerows(line.split("\t") for line in lines)
        return text_buffer.getvalue()

    frame = pd.read_csv(io.StringIO(tsv), sep="\t")
    buffer = io.BytesIO()
    frame.to_excel(buffer, index=False)
    return buffer.getvalue()


def _build_data_frame_content(
    builder: DataFrameBuilder,
) -> dict[str, Any]:
    """Build the pure filesystem content dict for one data_frame resource.

    Validation raises a ``ResourceValidationError``; the caller
    (``GRRBuilder``) annotates it with the resource id, so messages here
    stay id-free.
    """
    if builder.data is not None and builder.raw_content is not None:
        raise ResourceValidationError(
            "with_data and with_raw_content both set; the table can be "
            "authored as a whitespace block or written verbatim, not both")
    if builder.raw_content is not None and builder.file_format == "excel":
        raise ResourceValidationError(
            "with_raw_content writes bytes through untouched, but an xlsx "
            "is a rendered workbook; author an excel table with with_data")

    filename = builder.filename or _DATA_FRAME_FILENAMES[builder.file_format]

    config = "type: data_frame\n"
    if not builder.omit_file_key:
        config += f"file: {filename}\n"
    if not builder.omit_format_key:
        declared = builder.declared_format or builder.file_format
        config += f"format: {declared}\n"
    if builder.parameters:
        config += yaml.safe_dump(
            {"parameters": builder.parameters},
            default_flow_style=False, sort_keys=False)

    return {
        GR_CONF_FILE_NAME: config,
        filename: _render_data_frame_table(builder),
    }


def a_data_frame() -> DataFrameBuilder:
    """Return an immutable data_frame builder."""
    return DataFrameBuilder()
