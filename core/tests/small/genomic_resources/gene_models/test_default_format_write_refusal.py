"""A model the default format cannot express leaves no file behind.

Why refusing before the `open` is the whole point, and what a
mid-write refusal leaves on disk instead, is set out on
`_check_default_format_can_express`. These tests pin the part of it
that is observable only from outside: what the filesystem holds after
a refusal (gain#978).

That is why they drive the public `save_as_default_gene_models`
against a real path. The round-trip suite covers the same two defects
through the private writer and a `StringIO`, so it can say that the
write was refused but never that nothing was left behind.

The two branches of the public function open the file by different
calls, and a guard in only one of them would be a half-fix, so
everything here runs against both.
"""

import pathlib
from collections.abc import Callable

import pytest
from gain.genomic_resources.gene_models.gene_models import GeneModels
from gain.genomic_resources.gene_models.gene_models_factory import (
    build_gene_models_from_resource,
)
from gain.genomic_resources.gene_models.serialization import (
    save_as_default_gene_models,
)
from gain.genomic_resources.gene_models.transcript_models import (
    Exon,
    TranscriptModel,
)
from gain.genomic_resources.testing import build_inmemory_test_resource

from tests.small.genomic_resources.gene_models.columnar_formats import REFSEQ


def drop_the_exons(transcript: TranscriptModel) -> None:
    """All three exon columns join over `exons`, so all three go blank."""
    transcript.exons = []


def drop_the_frames(transcript: TranscriptModel) -> None:
    """An `Exon` built without a frame holds `None`, not `-1`."""
    transcript.exons = [Exon(exon.start, exon.stop)
                        for exon in transcript.exons]


#: The two model shapes the format has no spelling for, each with the
#: part of the refusal that says which one was met.
DEFECTS = [
    pytest.param(drop_the_exons, "no exons", id="exonless-transcript"),
    pytest.param(drop_the_frames, "exonFrames", id="frameless-exon"),
]

Defect = Callable[[TranscriptModel], None]


def models_with(defect: Defect) -> tuple[GeneModels, TranscriptModel]:
    """A single-record model carrying `defect`, and the record itself.

    Built by parsing and then damaged, so that everything the writer
    touches other than the field under test is what a parser produces.
    """
    gene_models = build_gene_models_from_resource(build_inmemory_test_resource(
        content={
            "genomic_resource.yaml":
                "{type: gene_models, filename: genes.txt, format: refseq}",
            "genes.txt": REFSEQ.good_row(),
        }))
    gene_models.load()
    [tr_id] = gene_models.transcript_models
    transcript = gene_models.transcript_models[tr_id]
    defect(transcript)
    return gene_models, transcript


#: What every test asks for, and what the two branches actually write
#: it to -- the gzip one appends `.gz` to a name that lacks it. Spelled
#: out rather than derived, so that a test cannot follow production
#: into a renaming it was supposed to catch and sit green watching a
#: path nothing writes.
ASKED_FOR = "gene_models.txt"
WRITTEN_TO = {True: "gene_models.txt.gz", False: "gene_models.txt"}


@pytest.mark.parametrize(("defect", "names_the_defect"), DEFECTS)
def test_a_model_the_format_cannot_express_is_refused(
    defect: Defect,
    names_the_defect: str,
    tmp_path: pathlib.Path,
) -> None:
    """The refusal says which record it is about, and which column.

    A transcript with no exons writes a blank cell the read side
    refuses (gain#929), and because format inference runs on load the
    operator would otherwise see "can't infer gene models file format"
    with the real cause buried in the formats-tried ledger. Naming the
    record here is what keeps the cause at the point it is known.
    """
    gene_models, transcript = models_with(defect)

    with pytest.raises(ValueError, match=names_the_defect) as error:
        save_as_default_gene_models(
            gene_models, str(tmp_path / ASKED_FOR), gzipped=False)

    assert transcript.tr_id in str(error.value)
    assert transcript.chrom in str(error.value)


@pytest.mark.parametrize("gzipped", [True, False])
@pytest.mark.parametrize(("defect", "names_the_defect"), DEFECTS)
def test_the_refusal_creates_no_output_file(
    defect: Defect,
    names_the_defect: str,
    tmp_path: pathlib.Path,
    *,
    gzipped: bool,
) -> None:
    """Nothing on disk -- the property a mid-write refusal cannot have.

    Both open branches create and truncate, so a refusal raised from
    inside the write loop leaves a header plus every record up to the
    offender. That file loads, so a caller ignoring the exit code gets
    data loss dressed as success.

    The directory is asserted empty rather than the expected path
    absent: the gzip branch renames what it was given, so "no file
    under the name production picked" is the weaker claim of the two.
    """
    gene_models, _ = models_with(defect)

    with pytest.raises(ValueError, match=names_the_defect):
        save_as_default_gene_models(
            gene_models, str(tmp_path / ASKED_FOR), gzipped=gzipped)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("gzipped", [True, False])
@pytest.mark.parametrize(("defect", "names_the_defect"), DEFECTS)
def test_the_refusal_leaves_a_file_already_there_untouched(
    defect: Defect,
    names_the_defect: str,
    tmp_path: pathlib.Path,
    *,
    gzipped: bool,
) -> None:
    """Rewriting a published file that fails is not allowed to lose it.

    "Creates no file" and "truncates no file" are different properties
    of the same `open`, and the second is the one that costs data: the
    caller re-serializing over last release's file would be left with
    neither the new models nor the old ones.
    """
    gene_models, _ = models_with(defect)
    target = tmp_path / WRITTEN_TO[gzipped]
    target.write_bytes(b"the file that was already there")

    with pytest.raises(ValueError, match=names_the_defect):
        save_as_default_gene_models(
            gene_models, str(tmp_path / ASKED_FOR), gzipped=gzipped)

    assert target.read_bytes() == b"the file that was already there"
