"""A ``scores:`` entry naming no ``##INFO`` field is refused (gain#1489).

A VCF score is its INFO key: the ``id`` of a ``scores:`` entry over a VCF
table is looked up in the header, and column addressing is not overridable.
An ``id`` the header does not declare used to escape that lookup as a bare
``KeyError`` -- from one of two indexers, depending on ``merge_vcf_scores``,
neither naming the resource nor saying the ``scores:`` block was at fault --
and ``KeyError`` is not a fault ``repo-repair`` attributes to a resource, so
the run reported an unexpected internal error with a traceback.

The contract these tests hold: the mismatch is visible where the definitions
are BUILT, so it is refused there, by the same :class:`MalformedResourceError`
the ``type:`` contradiction (gain#1336) and the ``Number=G`` arity
(gain#1258) raise -- ahead of both, because neither can be asked of a field
the header does not have.
"""
import pathlib
import textwrap

import pytest
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    build_score_from_resource,
)
from gain.genomic_resources.resource_errors import MalformedResourceError
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
    setup_directories,
    setup_vcf,
)

#: Two declared fields, one per-genotype (for the ordering test) and one
#: ordinary scalar; the config under test names a third the header lacks.
_VCF = textwrap.dedent("""
##fileformat=VCFv4.1
##INFO=<ID=CNT,Number=1,Type=Integer,Description="a scalar">
##INFO=<ID=PERGT,Number=G,Type=Integer,Description="one per genotype">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1 5 . A T . . CNT=7
""")

_RESOURCE_ID = "a_vcf_naming_an_unknown_field"


def _vcf_resource(
    repo: pathlib.Path, scores_block: str = "",
    resource_id: str = _RESOURCE_ID,
) -> pathlib.Path:
    """Write one allele-score resource over ``_VCF`` into ``repo``.

    Realized under a repository directory rather than straight into
    ``tmp_path`` so the resource has an id -- the refusal has to name it,
    and ``build_filesystem_test_resource`` hands back the id ``""``.
    Hand-rolled like the gain#1258 sibling's because ``a_vcf_info_score()``
    has no ``merge_vcf_scores`` knob, and that flag is what these tests vary.
    """
    resource_dir = repo / resource_id
    setup_directories(resource_dir, {
        "genomic_resource.yaml": textwrap.dedent("""
            type: allele_score
            table:
                filename: data.vcf.gz
        """) + scores_block,
    })
    setup_vcf(resource_dir / "data.vcf.gz", _VCF)
    return resource_dir


def _build(repo: pathlib.Path, resource_id: str = _RESOURCE_ID) -> AlleleScore:
    proto = build_filesystem_test_protocol(repo)
    score = build_score_from_resource(proto.get_resource(resource_id))
    assert isinstance(score, AlleleScore)
    return score


#: The shape under test: an entry whose ``id`` no ``##INFO`` line declares.
_NAMING_NOPE = textwrap.dedent("""
    scores:
    - id: NOPE
      name: NOPE
""")


def test_an_entry_naming_no_info_field_is_refused_by_name(
    tmp_path: pathlib.Path,
) -> None:
    """The refusal carries the address a reader needs -- which resource,
    which score -- and says what the header does declare, so the author can
    see the typo without opening the file.
    """
    _vcf_resource(tmp_path, _NAMING_NOPE)

    with pytest.raises(MalformedResourceError) as excinfo:
        _build(tmp_path)

    message = str(excinfo.value)
    assert _RESOURCE_ID in message
    assert "'NOPE'" in message
    assert "##INFO" in message
    assert "CNT" in message
    assert "PERGT" in message


def test_merging_the_header_in_does_not_excuse_the_entry(
    tmp_path: pathlib.Path,
) -> None:
    """``merge_vcf_scores: true`` turns the block from a filter into an
    override of header fields, and there is no ``NOPE`` to override: the
    entry is as wrong as in filter mode, and the refusal has to be the
    same one.  A check that ran only on the filter path would let this
    resource through to the override loop and its own bare ``KeyError``.
    """
    _vcf_resource(tmp_path, "merge_vcf_scores: true\n" + _NAMING_NOPE)

    with pytest.raises(MalformedResourceError, match="'NOPE'"):
        _build(tmp_path)


def test_the_unknown_id_is_refused_before_the_arity_of_a_declared_one(
    tmp_path: pathlib.Path,
) -> None:
    """Ordering, the mirror of "arity precedes type" (gain#1258): a block
    naming an undeclared id AND the per-genotype field is refused for the
    id.  Neither the arity nor the type rule can be asked of a field the
    header does not have, so the unknown id is checked first, whatever
    else the block gets wrong.
    """
    _vcf_resource(tmp_path, textwrap.dedent("""
        scores:
        - id: PERGT
          name: PERGT
        - id: NOPE
          name: NOPE
    """))

    with pytest.raises(MalformedResourceError, match="'NOPE'") as excinfo:
        _build(tmp_path)

    assert "Number=G" not in str(excinfo.value)


def test_repo_repair_names_the_refused_resource_and_builds_the_rest(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """Where the refusal is felt: the statistics build.

    A bare ``KeyError`` is not in the tier ``report_resource_failure``
    attributes to a resource, so ``repo-repair`` used to log an unexpected
    internal error with a traceback.  Now the resource is refused before any
    task is planned, reported by name as one attributed line, and the
    repository's other resource -- whose block names a declared field --
    still builds its statistics.
    """
    repo = tmp_path / "repo"
    bad = _vcf_resource(repo, _NAMING_NOPE, resource_id="refused")
    good = _vcf_resource(repo, textwrap.dedent("""
        scores:
        - id: CNT
          name: CNT
    """), resource_id="agreeing")

    with caplog.at_level("ERROR"), pytest.raises(SystemExit):
        cli_manage(["repo-repair", "-R", str(repo), "-j", "1"])

    assert not (bad / "statistics").exists(), (
        "the refused resource ran its statistics tasks"
    )
    assert (good / "statistics" / "histogram_CNT.json").exists(), (
        "the valid resource lost its statistics; one refused resource must "
        "not cost the rest of the repository its build"
    )
    reports = [
        r.getMessage() for r in caplog.records
        if "'NOPE'" in r.getMessage()]
    assert len(reports) == 1, "the refusal is reported once, not per task"
    assert "<refused>" in reports[0]
    assert "unexpected internal error" not in caplog.text
