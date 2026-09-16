"""A ``Number=G`` INFO field is refused where definitions are built (gain#1258).

``Number=G`` is a legal INFO arity -- one value per genotype -- and a header
declaring one parses fine, in pysam and in gain.  What pysam will not do is
READ such a field: ``info.get(key)`` on a row that carries it raises
``ValueError: genotype is only valid as a format field``.  That lookup is a
pure function of ``(record, score_def)``, deliberately outside the ``try``
that guards the parse, so the error escaped the score read uncaught -- from a
resource that had opened without complaint, on the first row carrying the
field, naming neither the resource nor the field.

The contract these tests hold: the shape is visible in the header, so a field
declared ``Number=G`` is refused where the definitions are BUILT, by the same
:class:`MalformedResourceError` gain#1336 raises for a contradictory
``type:`` -- and only when that field becomes a definition.  A ``scores:``
block that leaves it out reads the rest of the file exactly as before.
"""
import pathlib
import textwrap

import pytest
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.resource_errors import MalformedResourceError
from gain.genomic_resources.testing.builders import (
    VcfInfoScoreBuilder,
    a_grr,
    a_vcf_info_score,
)

from tests.small.genomic_resources.conftest import named_allele_score

#: One per-genotype field beside one ordinary scalar, and a row that CARRIES
#: the per-genotype value: that row is the one pysam refuses to read, so any
#: test that reads through the resource proves the field is never looked up.
_VCF = textwrap.dedent("""
##fileformat=VCFv4.1
##INFO=<ID=PERGT,Number=G,Type=Integer,Description="one per genotype">
##INFO=<ID=CNT,Number=1,Type=Integer,Description="a scalar">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1 5 . A T . . CNT=7
chr1 6 . A T . . PERGT=1,2,3;CNT=8
""")

_RESOURCE_ID = "a_per_genotype_vcf"


def _vcf() -> VcfInfoScoreBuilder:
    """A resource over ``_VCF`` with no ``scores:`` block.

    The report's shape, and the base every block below is stated on.
    """
    return a_vcf_info_score().with_data(_VCF)


def test_a_header_only_resource_with_a_per_genotype_field_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    """The report's shape: no ``scores:`` block, so every header field is a
    definition, ``PERGT`` among them.

    The fetch sits inside the ``raises`` so the failure names the defect:
    without the refusal the build succeeds and the row carrying ``PERGT``
    dies in pysam with a bare ``ValueError`` that is not a
    ``MalformedResourceError`` and names neither resource nor field.
    """
    with pytest.raises(MalformedResourceError, match=_RESOURCE_ID) as excinfo:
        named_allele_score(
            _vcf(), tmp_path, _RESOURCE_ID,
        ).open().fetch_allele_scores("chr1", 6, "A", "T")

    assert "PERGT" in str(excinfo.value)


def test_the_refusal_says_what_was_declared_and_how_to_fix_it(
    tmp_path: pathlib.Path,
) -> None:
    """A refusal the author can act on: the declared shape, why it cannot be
    read, and the two edits that resolve it -- leave the field out of a
    ``scores:`` block, or change the header.
    """
    with pytest.raises(MalformedResourceError) as excinfo:
        named_allele_score(_vcf(), tmp_path, _RESOURCE_ID)

    message = str(excinfo.value)
    assert "Number=G" in message
    assert "pysam" in message
    assert "'scores:' block that leaves it out" in message
    assert "change the header" in message


#: A ``scores:`` entry naming the per-genotype field, with and without a
#: stated type.  The typed one is the shape gain#1336 refuses for the TYPE
#: ("state 'type: str'") -- advice that would send the author to an edit
#: which cannot make the field readable.
_NAMING_PERGT = pytest.mark.parametrize("builder", [
    pytest.param(_vcf().with_score("PERGT"), id="untyped"),
    pytest.param(_vcf().with_score("PERGT", "int"), id="typed-int"),
])


@_NAMING_PERGT
def test_a_scores_entry_naming_the_field_is_refused_for_its_arity(
    tmp_path: pathlib.Path, builder: VcfInfoScoreBuilder,
) -> None:
    """The config route: an entry naming ``PERGT`` makes it a definition.

    The arity refusal comes FIRST.  An entry stating ``type: int`` over a
    joined field is also a contradiction gain#1336 refuses, but its advice
    -- state ``str`` or drop the line -- leaves a field pysam cannot read;
    the refusal the author sees has to be the one whose fix works.
    """
    with pytest.raises(MalformedResourceError, match="PERGT") as excinfo:
        named_allele_score(builder, tmp_path, _RESOURCE_ID)

    message = str(excinfo.value)
    assert "Number=G" in message
    assert "type: str" not in message


#: The fix the refusal recommends: name what you want, leave ``PERGT`` out.
_OMITTING_PERGT = _vcf().with_score("CNT")


def test_a_scores_block_omitting_the_field_reads_the_rest(
    tmp_path: pathlib.Path,
) -> None:
    """The refusal is about DEFINITIONS, not the header.

    With ``merge_vcf_scores`` unset the ``scores:`` block is a filter, so no
    definition of ``PERGT`` is built and nothing ever looks it up -- which
    the row that CARRIES it proves: pysam would refuse that lookup, and the
    read of ``CNT`` on the same row goes through untouched.
    """
    score = named_allele_score(_OMITTING_PERGT, tmp_path, _RESOURCE_ID).open()

    assert score.fetch_allele_scores("chr1", 6, "A", "T") == {"CNT": 8}


def test_merging_the_header_back_in_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    """``merge_vcf_scores: true`` turns the same block from a filter into an
    override: every header field the block does not name is merged in as
    the header defined it, ``PERGT`` included -- so it is a definition
    again, and refused again.
    """
    with pytest.raises(MalformedResourceError, match="PERGT"):
        named_allele_score(
            _OMITTING_PERGT.with_merge_vcf_scores(), tmp_path, _RESOURCE_ID)


def test_repo_repair_names_the_refused_resource_and_builds_the_rest(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """Where the refusal is felt: the statistics build.

    Before it, ``repo-repair`` opened the header-only resource, planned its
    region tasks and died in one of them with pysam's bare error.  Now the
    resource is refused before any task is planned, reported by name as one
    attributed line rather than an unexpected internal error, and the
    repository's other resource -- whose block omits the field -- still
    builds its statistics, reading through the row that carries ``PERGT``.
    """
    # Realized only -- no manifests, no index -- so ``repo-repair`` starts
    # from the raw directory it would find on a fresh checkout.
    repo = tmp_path / "repo"
    (
        a_grr()
        .with_resource("refused", _vcf())
        .with_resource("agreeing", _OMITTING_PERGT)
        .realize_all(repo)
    )

    with caplog.at_level("ERROR"), pytest.raises(SystemExit):
        cli_manage(["repo-repair", "-R", str(repo), "-j", "1"])

    assert not (repo / "refused" / "statistics").exists(), (
        "the refused resource ran its statistics tasks"
    )
    assert (repo / "agreeing" / "statistics" / "histogram_CNT.json").exists(), (
        "the valid resource lost its statistics; one refused resource must "
        "not cost the rest of the repository its build"
    )
    reports = [
        r.getMessage() for r in caplog.records
        if "Number=G" in r.getMessage()]
    assert len(reports) == 1, "the refusal is reported once, not per task"
    assert "<refused>" in reports[0]
    assert "unexpected internal error" not in caplog.text
