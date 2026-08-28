# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""A transcript that ends the parse with no exons is dropped.

``GTF_EXONLESS_TRANSCRIPT_FEATURES`` skips two spellings up front, but
that skip is keyed on the feature name and runs before any child record
is seen -- so a transcript of any other spelling that simply turns out
to have no ``exon`` records still reached the models with an empty exon
list. Serializing one to the default format joined its three exon
columns over nothing and wrote three blank cells, which since gain#929
are a hard parse error: gain wrote a file it could not read back
(gain#965).

The drop happens once the whole file has been read, because that is the
first point at which "this transcript has no exons" is known.
"""

import logging
import pathlib
from collections.abc import Callable

import pytest
from gain.genomic_resources.gene_models import parsers
from gain.genomic_resources.gene_models.gene_models import GeneModels

from tests.small.genomic_resources.gene_models.conftest import (
    build_from_content,
)


def _record(feature: str, start: int, end: int, attributes: str) -> str:
    return "\t".join([
        "chr17", "test", feature, str(start), str(end), ".", "+", ".",
        attributes,
    ])


TR1 = 'gene_id "G1"; transcript_id "TR1";'
TR2 = 'gene_id "G2"; transcript_id "TR2";'


def test_a_transcript_with_no_exon_records_yields_no_transcript_model(
    gtf_gene_models: Callable[..., GeneModels],
) -> None:
    gene_models = gtf_gene_models(
        _record("transcript", 500, 800, TR2),
    )

    assert gene_models.transcript_models == {}


def test_the_drop_takes_only_the_transcript_that_has_no_exons(
    gtf_gene_models: Callable[..., GeneModels],
) -> None:
    """A neighbour in the same file keeps its exons.

    The drop is a filter over the whole assembled mapping, so the guard
    that matters is that it does not over-reach: naming the surviving
    transcript's exons here means a drop that emptied the mapping, or
    one that discarded a transcript's exons on the way past, fails.
    """
    gene_models = gtf_gene_models(
        _record("transcript", 100, 200, TR1),
        _record("exon", 100, 150, TR1),
        _record("exon", 180, 200, TR1),
        _record("transcript", 500, 800, TR2),
    )

    assert list(gene_models.transcript_models) == ["TR1"]
    transcript = gene_models.transcript_models["TR1"]
    assert [(e.start, e.stop) for e in transcript.exons] == [
        (100, 150), (180, 200),
    ]


def test_dropping_a_transcript_warns_naming_it_and_its_chromosome(
    gtf_gene_models: Callable[..., GeneModels],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A silent drop would lose a record without saying so.

    The drop is data loss, however well-founded: a GRR ``build.sh`` run
    has to be able to say which transcripts its source file lost. Both
    identifiers the parse has are named, because the transcript id alone
    does not locate the record in a multi-chromosome source.
    """
    with caplog.at_level(logging.WARNING):
        gtf_gene_models(
            _record("transcript", 500, 800, TR2),
        )

    messages = [
        record.getMessage() for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    assert any(
        "TR2" in message and "chr17" in message for message in messages
    ), messages


def test_a_transcript_whose_exons_fall_past_the_inference_prefix_survives(
    tmp_path: pathlib.Path,
) -> None:
    """The drop must not run while parsing a prefix of the file.

    Format inference parses only the first `INFERENCE_SAMPLE_ROWS`
    records, so "this transcript has no exons" is not yet knowable
    there: a feature-sorted GTF -- every `transcript` record, then every
    `exon` record -- introduces transcripts in the prefix whose exons
    are all past it. Dropping on that evidence emptied the sample,
    inference concluded the file matched no format at all, and a
    well-formed GTF stopped loading unless its format was declared.
    """
    count = parsers.INFERENCE_SAMPLE_ROWS + 10

    def records(feature: str) -> list[str]:
        # One list per feature, from one body: each transcript has to be
        # matched by an exon carrying the same id and bounds, and two
        # comprehensions that merely look alike do not enforce that.
        return [
            _record(feature, 100 + i * 1000, 500 + i * 1000,
                    f'gene_id "G{i}"; transcript_id "T{i}";')
            for i in range(count)
        ]

    gene_models = build_from_content(
        tmp_path, "genes.gtf",
        "\n".join(records("transcript") + records("exon")) + "\n",
    ).load()

    assert len(gene_models.transcript_models) == count
