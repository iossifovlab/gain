"""A VCF ``scores:`` entry's column address is its ``id`` (gain#1498).

A VCF score is addressed by its INFO key, which is the entry's ``id``; the
column address a tabular score carries (``column_name:`` or ``column_index:``
and their legacy spellings ``name:``/``index:``) is not the config's to give.
An entry that states one anyway used to be policed at ``open()`` by the
tabular header check -- refused with a bare ``AssertionError`` when the name
was not in the header, refused for stating NO address at all, and silently
accepted when it named some OTHER declared field, which the reader then
ignored.

The contract these tests hold: an address equal to the ``id`` is accepted
(every deployed VCF resource spells ``column_name: <id>``), no address at all
is accepted, and anything else is a contradiction between the config and the
header, refused where the definitions are BUILT by the same
:class:`MalformedResourceError` as an undeclared ``id`` (gain#1489) and a
``type:`` the header contradicts (gain#1336).
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

#: Two declared scalar fields, so an address can name the OTHER one.
_VCF = textwrap.dedent("""
##fileformat=VCFv4.1
##INFO=<ID=A,Number=1,Type=Integer,Description="Score A">
##INFO=<ID=B,Number=1,Type=Integer,Description="Score B">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1 5 . A T . . A=1;B=2
""")

_RESOURCE_ID = "a_vcf_with_an_addressed_score"


def _vcf_resource(
    repo: pathlib.Path, scores_block: str,
    resource_id: str = _RESOURCE_ID,
) -> pathlib.Path:
    """Write one allele-score resource over ``_VCF`` into ``repo``.

    Under a repository directory rather than straight into ``tmp_path`` so
    the resource has an id for the refusal to name (the gain#1489 sibling
    says why).
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


def test_an_address_the_header_lacks_is_refused_where_the_score_is_built(
    tmp_path: pathlib.Path,
) -> None:
    """The reported shape: ``id: A`` addressed as a name no ``##INFO`` line
    declares.  Refused at BUILD, not ``open()``, naming the resource, the
    score and what was stated -- and saying what the score reads instead,
    so the fix (drop the line, or make it the id) reads off the message.
    """
    _vcf_resource(tmp_path, textwrap.dedent("""
        scores:
        - id: A
          column_name: NO_SUCH_SCORE_IN_HEADER
          type: float
    """))

    with pytest.raises(MalformedResourceError) as excinfo:
        _build(tmp_path)

    message = str(excinfo.value)
    assert _RESOURCE_ID in message
    assert "'A'" in message
    assert "NO_SUCH_SCORE_IN_HEADER" in message


def test_an_address_naming_another_declared_field_is_refused_too(
    tmp_path: pathlib.Path,
) -> None:
    """The silent half: ``id: A`` addressed as ``B``, which the header DOES
    declare, used to pass the header check and read ``A`` -- the author's
    ``B`` never consulted.  Being in the header does not make the address
    the score's; only equality with the id does.
    """
    _vcf_resource(tmp_path, textwrap.dedent("""
        scores:
        - id: A
          column_name: B
    """))

    with pytest.raises(MalformedResourceError, match="'B'") as excinfo:
        _build(tmp_path)

    assert "'A'" in str(excinfo.value)


def test_a_column_index_is_refused_because_a_vcf_field_has_none(
    tmp_path: pathlib.Path,
) -> None:
    """An INFO field is not a column; ``index: 0`` (the legacy spelling of
    ``column_index:``) used to be accepted and ignored.  Index 0 on purpose:
    it is the one address a falsy-check would have overlooked.
    """
    _vcf_resource(tmp_path, textwrap.dedent("""
        scores:
        - id: A
          index: 0
    """))

    with pytest.raises(MalformedResourceError, match="column_index") as excinfo:
        _build(tmp_path)

    assert "'A'" in str(excinfo.value)


def test_an_entry_stating_no_address_reads_the_info_field_named_by_its_id(
    tmp_path: pathlib.Path,
) -> None:
    """The address is not the config's to give, so it need not give one.
    ``id:`` alone used to be refused at ``open()`` for "neither an index
    nor a name" -- the tabular rule, asked of a table it does not apply to.
    """
    _vcf_resource(tmp_path, textwrap.dedent("""
        scores:
        - id: A
    """))

    with _build(tmp_path).open() as score:
        values = score.fetch_allele_scores("chr1", 5, "A", "T", ["A"])

    assert values == {"A": 1}


@pytest.mark.parametrize("spelling", ["column_name", "name"])
def test_an_address_equal_to_the_id_is_accepted(
    tmp_path: pathlib.Path, spelling: str,
) -> None:
    """Redundant, not wrong: every deployed VCF resource (ClinVar, dbSNP)
    spells ``column_name: <id>`` on each of its scores, and the legacy
    ``name:`` is the same statement.  Both keep building and reading.
    """
    _vcf_resource(tmp_path, textwrap.dedent(f"""
        scores:
        - id: A
          {spelling}: A
    """))

    with _build(tmp_path).open() as score:
        values = score.fetch_allele_scores("chr1", 5, "A", "T", ["A"])

    assert values == {"A": 1}


def test_an_undeclared_id_is_refused_for_the_id_whatever_its_address_says(
    tmp_path: pathlib.Path,
) -> None:
    """Ordering: an entry that is wrong twice -- an ``id`` no ``##INFO``
    line declares AND an address that is not that id -- is refused for the
    id (gain#1489).  The address rule cannot say what such a score "reads
    instead", because it reads nothing.
    """
    _vcf_resource(tmp_path, textwrap.dedent("""
        scores:
        - id: NOPE
          column_name: A
    """))

    with pytest.raises(MalformedResourceError, match="##INFO") as excinfo:
        _build(tmp_path)

    assert "column_name" not in str(excinfo.value)


def test_repo_repair_names_the_refused_resource_and_builds_the_rest(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """Where the refusal is felt: the statistics build.

    ``AssertionError`` is not in the tier ``report_resource_failure``
    attributes to a resource, so ``repo-repair`` used to log an unexpected
    internal error with a traceback -- and only once the score was OPENED,
    inside a task.  Now the resource is refused before any task is planned,
    reported by name as one attributed line, and the repository's other
    resource -- addressed by its id -- still builds its statistics.
    """
    repo = tmp_path / "repo"
    bad = _vcf_resource(repo, textwrap.dedent("""
        scores:
        - id: A
          column_name: NO_SUCH_SCORE_IN_HEADER
    """), resource_id="refused")
    good = _vcf_resource(repo, textwrap.dedent("""
        scores:
        - id: A
          column_name: A
    """), resource_id="agreeing")

    with caplog.at_level("ERROR"), pytest.raises(SystemExit):
        cli_manage(["repo-repair", "-R", str(repo), "-j", "1"])

    assert not (bad / "statistics").exists(), (
        "the refused resource ran its statistics tasks"
    )
    assert (good / "statistics" / "histogram_A.json").exists(), (
        "the valid resource lost its statistics; one refused resource must "
        "not cost the rest of the repository its build"
    )
    reports = [
        r.getMessage() for r in caplog.records
        if "NO_SUCH_SCORE_IN_HEADER" in r.getMessage()]
    assert len(reports) == 1, "the refusal is reported once, not per task"
    assert "<refused>" in reports[0]
    assert "unexpected internal error" not in caplog.text
