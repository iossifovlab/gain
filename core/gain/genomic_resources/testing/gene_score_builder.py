"""Fluent, immutable test-data builder for ``gene_score`` resources.

A sibling of :mod:`gain.genomic_resources.testing.builders`.  The
dependency runs one way: this module imports the shared single-realize
seam from ``builders``, and ``builders`` does not import back.  So
``a_gene_score`` is imported from here.
"""
from __future__ import annotations

import dataclasses
import gzip
import pathlib
import textwrap
from typing import Any

from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.repository_ids import GR_CONF_FILE_NAME
from gain.genomic_resources.testing import (
    convert_to_tab_separated,
    setup_directories,
)
from gain.genomic_resources.testing.builders import (
    _build_single_resource,
)
from gain.genomic_resources.testing.extra_files import ExtraFilesMixin
from gain.genomic_resources.testing.resource_meta import (
    MetaMixin,
)
from gain.genomic_resources.testing.score_builder_helpers import (
    _validate_data_header,
    _validate_score_specs,
)
from gain.genomic_resources.testing.score_specs import (
    ResourceValidationError,
    ScoreSpec,
    append_score,
    render_score_specs_yaml,
    scores_or_default,
    set_histogram,
)

_GENE_DATA_FILENAME = "data.tsv"


_DEFAULT_GENE_COLUMN = "gene"


@dataclasses.dataclass(frozen=True)
class GeneScoreBuilder(ExtraFilesMixin, MetaMixin):
    """Immutable builder for a single ``gene_score`` resource.

    Built on the shared score-declaration base (:class:`ScoreSpec`): scores
    are declared with :meth:`with_score` (``column_name`` defaults to the
    score id) and validated for duplicate ids / column names exactly like a
    position score.  The gene→value table is authored with :meth:`with_data`
    as a whitespace block whose header must be ``{gene_column}`` plus each
    declared score's ``column_name``.

    Realizes as a PLAIN (non-gzipped) tab-separated ``data.tsv`` table with a
    top-level ``filename:`` config -- mirroring the simplest working
    ``gene_score`` fixture.  A bare builder realizes a valid minimal readable
    gene score: one ``float`` score and a few gene rows, with NO histogram
    (the numeric default histogram is auto-built when the score is read).
    """

    scores: tuple[ScoreSpec, ...] = ()
    data: str | None = None
    gene_column: str = _DEFAULT_GENE_COLUMN
    gzipped: bool = False

    def with_score(
        self, score_id: str, value_type: str = "float", *,
        column_name: str | None = None, desc: str | None = None,
    ) -> GeneScoreBuilder:
        """Declare a gene score; ``column_name`` defaults to ``score_id``."""
        return dataclasses.replace(
            self,
            scores=append_score(
                self.scores, score_id, value_type,
                column_name=column_name, desc=desc),
        )

    def with_histogram(
        self, histogram: dict[str, Any], *, score_id: str | None = None,
    ) -> GeneScoreBuilder:
        """Attach a histogram block to a declared score.

        With ``score_id`` omitted the histogram is attached to the
        most-recently-declared score; passing ``score_id`` targets that
        score.  Omitted by default: a numeric score relies on the resource's
        auto-built default histogram, so no ``histogram:`` block is emitted
        unless one is declared here.
        """
        return dataclasses.replace(
            self,
            scores=set_histogram(
                self.scores, histogram, score_id=score_id),
        )

    def with_gene_column(self, name: str) -> GeneScoreBuilder:
        """Set the gene-id column name (default ``"gene"``)."""
        return dataclasses.replace(self, gene_column=name)

    def with_gzip(self) -> GeneScoreBuilder:
        """Realize the gene table gzipped (``.tsv.gz``) instead of plain.

        The default is a plain ``.tsv`` table; ``with_gzip`` gzips the TSV to
        ``data.tsv.gz`` and points ``filename:`` at it.  The resource reads
        back identically to the plain form.
        """
        return dataclasses.replace(self, gzipped=True)

    def with_data(self, data: str) -> GeneScoreBuilder:
        """Author the gene→value table as a whitespace-separated block.

        Validated at the header level only: it must contain the gene column
        plus each declared score's ``column_name``; a missing declared column
        or an undeclared extra column raises ``ResourceValidationError``.
        """
        return dataclasses.replace(self, data=data)

    def realize_into(self, resource_dir: pathlib.Path) -> None:
        """Write this gene-score resource into ``resource_dir``.

        Raises a ``ResourceValidationError`` on invalid content;
        ``GRRBuilder`` annotates it with the resource id.
        """
        setup_directories(resource_dir, _build_gene_score_content(self))
        self.realize_files_into(resource_dir)

    def build_resource(
        self, tmp_path: pathlib.Path,
    ) -> GenomicResource:
        """Realize this single resource (repo id ``""``) into ``tmp_path``."""
        return _build_single_resource(self, tmp_path)


def _effective_gene_data(builder: GeneScoreBuilder) -> str:
    if builder.data is not None:
        return builder.data
    return """
        gene  score
        G1    0.1
        G2    0.2
        G3    0.3
    """


def _build_gene_score_content(
    builder: GeneScoreBuilder,
) -> dict[str, Any]:
    """Build the pure filesystem content dict for one gene-score resource.

    Validation raises a ``ResourceValidationError``; the caller
    (``GRRBuilder``) annotates it with the resource id, so messages here
    stay id-free.
    """
    scores = scores_or_default(builder.scores)
    data = _effective_gene_data(builder)
    _validate_score_specs(scores)
    colliding = [
        spec.score_id for spec in scores
        if spec.column_name == builder.gene_column
    ]
    if colliding:
        raise ResourceValidationError(
            f"score(s) {sorted(colliding)} declare column_name "
            f"{builder.gene_column!r}, which is the gene column; a score "
            f"cannot read from the gene column -- give it a distinct "
            f"column_name")
    _validate_data_header(
        data, scores, base_required=(builder.gene_column,))

    filename = (
        f"{_GENE_DATA_FILENAME}.gz" if builder.gzipped
        else _GENE_DATA_FILENAME)
    config = textwrap.dedent(f"""\
        type: gene_score
        filename: {filename}
        """)
    if builder.gene_column != _DEFAULT_GENE_COLUMN:
        config += f"gene_column: {builder.gene_column}\n"
    config += "scores:\n" + render_score_specs_yaml(scores)
    config += builder.render_meta()
    tsv = convert_to_tab_separated(data)
    table_content: str | bytes = (
        gzip.compress(tsv.encode()) if builder.gzipped else tsv)
    return {
        GR_CONF_FILE_NAME: config,
        filename: table_content,
    }


def a_gene_score() -> GeneScoreBuilder:
    """Return an immutable gene-score builder."""
    return GeneScoreBuilder()
