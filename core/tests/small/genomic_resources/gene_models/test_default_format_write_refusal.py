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

The third refusal -- a model that is empty because nobody loaded it
(gain#1097) -- is shared with the GTF serializer, and its GTF tests
sit here beside the default-format ones so that the shared message is
pinned in one place.
"""

import pathlib
from collections.abc import Callable
from functools import partial

import pytest
from gain.genomic_resources.gene_models.gene_models import GeneModels
from gain.genomic_resources.gene_models.gene_models_factory import (
    build_gene_models_from_resource,
)
from gain.genomic_resources.gene_models.serialization import (
    gene_models_to_gtf,
    save_as_default_gene_models,
)
from gain.genomic_resources.gene_models.transcript_models import (
    Exon,
    TranscriptModel,
)
from gain.genomic_resources.testing import build_inmemory_test_repository

from tests.small.genomic_resources.gene_models.columnar_formats import REFSEQ

#: The one resource every model here is built from. Named, rather
#: than the root id ``""`` a single-resource builder hands out, so
#: that a refusal can be seen to say which resource it is about.
RESOURCE_ID = "refused/genes"


def models_never_loaded() -> GeneModels:
    """A well-formed single-record resource nobody called ``load()`` on.

    Structurally fine and simply empty: the shape a caller reaches by
    forgetting the load, which the model-shape precondition cannot tell
    from a legitimately empty set (gain#1097).
    """
    grr = build_inmemory_test_repository({
        RESOURCE_ID: {
            "genomic_resource.yaml":
                "{type: gene_models, filename: genes.txt, format: refseq}",
            "genes.txt": REFSEQ.good_row(),
        },
    })
    return build_gene_models_from_resource(grr.get_resource(RESOURCE_ID))


def drop_the_exons(transcript: TranscriptModel) -> None:
    """All three exon columns join over `exons`, so all three go blank."""
    transcript.exons = []


def drop_the_frames(transcript: TranscriptModel) -> None:
    """An `Exon` built without a frame holds `None`, not `-1`."""
    transcript.exons = [Exon(exon.start, exon.stop)
                        for exon in transcript.exons]


def leave_as_parsed(_transcript: TranscriptModel) -> None:
    """The no-defect case: a record exactly as the parser produced it."""


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
    gene_models = models_never_loaded().load()
    [tr_id] = gene_models.transcript_models
    transcript = gene_models.transcript_models[tr_id]
    defect(transcript)
    return gene_models, transcript


def models_damaged_by(defect: Defect) -> GeneModels:
    """`models_with`, for a caller that does not need the record."""
    return models_with(defect)[0]


#: Every model the writer refuses, each with the part of the refusal
#: that says why: the two shapes, and the forgotten load.
REFUSED = [
    pytest.param(partial(models_damaged_by, drop_the_exons), "no exons",
                 id="exonless-transcript"),
    pytest.param(partial(models_damaged_by, drop_the_frames), "exonFrames",
                 id="frameless-exon"),
    pytest.param(models_never_loaded, "never loaded", id="never-loaded"),
]

Refused = Callable[[], GeneModels]


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
@pytest.mark.parametrize(("refused", "names_the_reason"), REFUSED)
def test_the_refusal_creates_no_output_file(
    refused: Refused,
    names_the_reason: str,
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
    gene_models = refused()

    with pytest.raises(ValueError, match=names_the_reason):
        save_as_default_gene_models(
            gene_models, str(tmp_path / ASKED_FOR), gzipped=gzipped)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("gzipped", [True, False])
@pytest.mark.parametrize(("refused", "names_the_reason"), REFUSED)
def test_the_refusal_leaves_a_file_already_there_untouched(
    refused: Refused,
    names_the_reason: str,
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
    gene_models = refused()
    target = tmp_path / WRITTEN_TO[gzipped]
    target.write_bytes(b"the file that was already there")

    with pytest.raises(ValueError, match=names_the_reason):
        save_as_default_gene_models(
            gene_models, str(tmp_path / ASKED_FOR), gzipped=gzipped)

    assert target.read_bytes() == b"the file that was already there"


Serialize = Callable[[GeneModels, pathlib.Path], object]


def to_default_format(
    gene_models: GeneModels, tmp_path: pathlib.Path,
) -> None:
    save_as_default_gene_models(
        gene_models, str(tmp_path / ASKED_FOR), gzipped=False)


def to_gtf(gene_models: GeneModels, _tmp_path: pathlib.Path) -> object:
    return gene_models_to_gtf(gene_models)


#: Both serializers, so the never-loaded refusal is pinned at each
#: entry point with the one spelling they share.
SERIALIZERS = [
    pytest.param(to_default_format, id="default-format"),
    pytest.param(to_gtf, id="gtf"),
]


@pytest.mark.parametrize("serialize", SERIALIZERS)
def test_never_loaded_models_are_refused_naming_the_resource(
    serialize: Serialize,
    tmp_path: pathlib.Path,
) -> None:
    """Forgetting ``load()`` is a caller bug, not a request for a file.

    The refusal says the models were never loaded and which resource
    they are, at the point the cause is known: a header-only file says
    "can't infer gene models file format" on read, with the real cause
    nowhere in it, and an empty GTF string is that file one step
    removed.
    """
    gene_models = models_never_loaded()

    with pytest.raises(ValueError) as error:
        serialize(gene_models, tmp_path)

    assert str(error.value) == (
        f"gene models {RESOURCE_ID} hold no transcripts and were never "
        f"loaded; call load() before serializing them"
    )


def test_populated_models_that_were_never_loaded_are_not_refused(
    tmp_path: pathlib.Path,
) -> None:
    """Holding transcripts is what makes a model serializable, not ``load()``.

    ``join_gene_models`` builds a populated model that was never marked
    loaded, as do fixture builders that fill ``transcript_models`` by
    hand. The refusal is for the forgotten load -- unloaded *and*
    empty -- so a populated model passes through either serializer,
    whichever way it was populated.
    """
    loaded = models_damaged_by(leave_as_parsed)
    joined = GeneModels.join_gene_models(loaded, loaded)
    assert not joined.is_loaded()

    to_default_format(joined, tmp_path)
    gtf = gene_models_to_gtf(joined)

    assert (tmp_path / ASKED_FOR).read_text().count("\n") == 2
    assert "\ttranscript\t" in gtf.getvalue()
