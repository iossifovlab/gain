"""What a multi-valued VCF INFO field DECLARES as its type (gain#1259).

A field whose ``##INFO`` header gives it a ``Number`` outside
``_SCALAR_VALUED_NUMBERS`` reads as ``|``-joined TEXT -- the join is the
``converter`` the header-derived definition installs, and gain#1233 made that
converter survive a stated ``type:``.  What the definition DECLARED did not
follow: ``##INFO=<ID=MANY,Number=.,Type=Integer>`` announced ``int`` and read
``'1|2'``.

That is not a cosmetic mislabel.  The declared type picks the histogram --
``build_default_histogram_conf`` answers ``int``/``float`` with a NUMBER
histogram -- and the auto-ranging scan behind it calls ``np.isnan`` on every
value, so the joined text aborted the whole statistics build with
``TypeError: ufunc 'isnan' not supported for the input types``, leaving the
resource with no statistics and no info page.

The contract these tests hold: a field the header declares multi-valued
declares ``str``, the type its joined value actually has, whatever its
``Type=`` says and whatever a ``scores:`` entry states.  The shapes that
reach a parser as a scalar are untouched -- they really do hold their
declared type.
"""
import json
import pathlib
import textwrap

import pytest
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    build_score_from_resource,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_resource,
    setup_directories,
    setup_vcf,
)

# Every ``Number`` shape the type decision distinguishes.  The scalar four
# (``0``, ``1``, ``A``, ``R``) carry a value on the first row and the joined
# shapes carry theirs on the second, so that neither data line runs past the
# line length limit; no test here reads a value, so neither row is named.
# ``Type=`` varies within the joined shapes because it is exactly what must
# stop mattering for them.
#
# This fixture is a near-copy of the one in ``test_vcf_typed_multivalued_score``
# and deliberately differs in two places: ``TWO`` is ``Type=Float`` here (there
# it is ``Integer``), so that the fixed-arity shape is not typed the same as
# ``MANY`` and a fix keyed on one cannot pass for the other; and that file's
# ``PAIR`` (``Number=2,Type=String``) is dropped, because a fixed-arity STRING
# field declares ``str`` on both sides of this change and so cannot show it.
# The copy exists because ``a_vcf_info_score()`` cannot emit a ``scores:``
# block at all -- gain#1290 is about giving it one and consolidating the four
# hand-rolled helpers that work around it.
_VCF = textwrap.dedent("""
##fileformat=VCFv4.1
##INFO=<ID=RV,Number=0,Type=Flag,Description="a flag">
##INFO=<ID=CNT,Number=1,Type=Integer,Description="a count">
##INFO=<ID=PA,Number=A,Type=Integer,Description="one per ALT allele">
##INFO=<ID=PR,Number=R,Type=Float,Description="one per allele, REF first">
##INFO=<ID=MANY,Number=.,Type=Integer,Description="an unbounded list">
##INFO=<ID=FMANY,Number=.,Type=Float,Description="unbounded decimals">
##INFO=<ID=TAGS,Number=.,Type=String,Description="unbounded text">
##INFO=<ID=TWO,Number=2,Type=Float,Description="exactly two decimals">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1 5 . A T,G . . RV;CNT=3;PA=7,8;PR=1.5,2.5,3.5
chr1 6 . A T . . MANY=1,2;FMANY=1.5,2.5;TAGS=x,y;TWO=1.5,2.5
""")

#: The genotype-arity shape, alone and never read.  ``Number=G`` belongs to
#: the same "not a scalar" side of the decision as the fields above, but pysam
#: refuses to hand over an INFO value declared that way at all -- reading one
#: raises ``ValueError: genotype is only valid as a format field`` -- so a
#: resource carrying it cannot have its statistics built for reasons that have
#: nothing to do with gain#1259.  Its DECLARATION is still built from the
#: header like any other, which is the part this file is about, so it is
#: pinned here on its own rather than in the fixture every other test reads.
_PERGT_VCF = textwrap.dedent("""
##fileformat=VCFv4.1
##INFO=<ID=PERGT,Number=G,Type=Integer,Description="one per genotype">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1 5 . A T . . PERGT=1,2
""")

#: Every field, with the ``type:`` its own ``##INFO`` line declares -- what an
#: author documenting the file naturally writes.
_HEADER_TYPES = {
    "RV": "bool", "CNT": "int", "PA": "int", "PR": "float",
    "MANY": "int", "FMANY": "float", "TAGS": "str",
    "TWO": "float",
}

#: The shapes pysam hands over whole, so that a ``|``-join makes their value.
_JOINED_FIELDS = ["MANY", "FMANY", "TAGS", "TWO"]

#: The shapes that reach a parser as a single value.
_SCALAR_FIELDS = ["RV", "CNT", "PA", "PR"]

#: A ``scores:`` block naming every field and restating the header's own type.
_TYPED_BLOCK = "scores:\n" + "".join(
    f"- id: {field}\n  name: {field}\n  type: {value_type}\n"
    for field, value_type in _HEADER_TYPES.items())

#: The same block with no ``type:`` at all -- the gain#1221 shape.
_UNTYPED_BLOCK = "scores:\n" + "".join(
    f"- id: {field}\n  name: {field}\n" for field in _HEADER_TYPES)

#: One joined field typed with a number, and one scalar field typed against
#: its header -- the two entries the tests below reach for repeatedly.  Named
#: rather than written inline each time so that the field and the type it
#: states stay paired in one place.
_MANY_TYPED_INT = textwrap.dedent("""
    scores:
    - id: MANY
      name: MANY
      type: int
""")

_CNT_TYPED_FLOAT = textwrap.dedent("""
    scores:
    - id: CNT
      name: CNT
      type: float
""")

#: The joined field under an EXPLICIT number histogram -- gain#1285's shape --
#: alongside a scalar field carrying nothing but its header type.  The scalar
#: is what makes the test able to say that refusing ONE score's histogram does
#: not cost the resource the rest of its statistics; without it the block
#: names only MANY, since a ``scores:`` block filters.
_MANY_NUMBER_HIST_WITH_CNT = textwrap.dedent("""
    scores:
    - id: MANY
      name: MANY
      type: int
      histogram:
        type: number
        number_of_bins: 4
    - id: CNT
      name: CNT
      type: int
""")

#: A stable fragment of the override report, so the silence assertions test
#: for THAT line rather than for a quiet log -- opening a resource emits
#: other warnings, and a test that demanded none would fail for the wrong
#: reason the moment one was added.
_JOINED_TEXT_REPORT = "reads '|'-joined text"

#: The three ways a resource can reach a field's definition.  Named so a
#: failure says which route drifted.
_ROUTES = [
    pytest.param("", id="header-only"),
    pytest.param(_UNTYPED_BLOCK, id="named-without-type"),
    pytest.param(_TYPED_BLOCK, id="named-restating-header-type"),
]


def _vcf_score(tmp_path: pathlib.Path, scores_block: str = "") -> AlleleScore:
    """An opened allele score over ``_VCF``, with the ``scores:`` block given.

    Hand-rolled rather than built with ``a_vcf_info_score()``, which emits no
    ``scores:`` block -- and whether the block can override the declared type
    is half of what these tests are about.  An empty ``scores_block`` is the
    header-only resource.
    """
    setup_directories(tmp_path, {
        "genomic_resource.yaml": textwrap.dedent("""
            type: allele_score
            table:
                filename: data.vcf.gz
        """) + scores_block,
    })
    setup_vcf(tmp_path / "data.vcf.gz", _VCF)
    score = build_score_from_resource(build_filesystem_test_resource(tmp_path))
    assert isinstance(score, AlleleScore)
    return score.open()


def _repaired_vcf_resource(
    tmp_path: pathlib.Path, scores_block: str = "",
) -> pathlib.Path:
    """Realize ``_VCF`` as a one-resource GRR and ``repo-repair`` it.

    The ``_vcf_score`` twin for the tests that need the statistics BUILT
    rather than the definitions read: same resource, same ``scores:`` block
    composition, but under a ``repo/`` dir the CLI can be pointed at.
    Returns the resource directory, so a caller reads its ``statistics/``.
    """
    repo = tmp_path / "repo"
    resource = repo / "vcf_score"
    setup_directories(resource, {
        "genomic_resource.yaml": textwrap.dedent("""
            type: allele_score
            table:
                filename: data.vcf.gz
        """) + scores_block,
    })
    setup_vcf(resource / "data.vcf.gz", _VCF)

    cli_manage(["repo-repair", "-R", str(repo), "-j", "1"])
    return resource


def _declared(score: AlleleScore, field: str) -> str | None:
    """What the score DEFINITION says ``field`` holds.

    The definition's own ``value_type`` -- not the Python type of a value read
    through it.  The two are the same question only when the definition tells
    the truth, and the whole of gain#1259 is that for a joined field it did
    not.
    """
    return score.score_definitions[field].value_type


def test_a_stated_type_does_not_restore_the_numeric_declaration(
    tmp_path: pathlib.Path,
) -> None:
    """The field from the report, under an author restating its own type.

    gain#1233 already stopped a stated ``type:`` from displacing the join,
    so this entry reads ``'1|2'`` -- but it went on DECLARING ``int``,
    because the type still followed the config.  A resource cannot talk its
    way back into a number histogram over joined text.

    The route matrix below covers this field through ``_TYPED_BLOCK`` too.
    This one is kept because that block is DERIVED from ``_HEADER_TYPES``:
    edit that mapping and the matrix would stop exercising ``type: int`` on
    ``MANY`` without any test going red.  Here the pairing is written out,
    so it cannot drift.
    """
    score = _vcf_score(tmp_path, _MANY_TYPED_INT)

    assert _declared(score, "MANY") == "str"


@pytest.mark.parametrize("scores_block", _ROUTES)
@pytest.mark.parametrize("field", _JOINED_FIELDS)
def test_every_joined_shape_declares_str_on_every_route(
    tmp_path: pathlib.Path, field: str, scores_block: str,
) -> None:
    """The rule itself, over each joined shape and each way in.

    ``Number=.`` is not the only shape that joins: pysam decodes ANY arity
    above one to a tuple, so a fixed ``Number=2`` joins exactly as the
    unbounded shapes do.  A fix keyed on ``Number == "."`` would leave
    ``TWO`` declaring a number and still aborting the statistics build.

    ``TAGS`` (``Number=.``/``Type=String``) is in the list as the shape that
    must NOT move: it already declared ``str``, and it is the only
    multi-valued shape deployed resources actually carry.
    """
    score = _vcf_score(tmp_path, scores_block)

    assert _declared(score, field) == "str"


@pytest.mark.parametrize("scores_block", _ROUTES)
def test_the_scalar_shapes_keep_the_type_they_declare(
    tmp_path: pathlib.Path, scores_block: str,
) -> None:
    """The fence: the narrowing must not reach a shape that holds its type.

    ``Number`` of ``0``, ``1``, ``A`` and ``R`` reach the parser as a single
    value -- a per-allele field is indexed down to one element first -- so
    each really does hold what it declares, and a blanket ``str`` would be
    the same lie in the other direction.  Restating the header's type leaves
    them where they were, which is what makes the three routes agree here.
    """
    score = _vcf_score(tmp_path, scores_block)

    assert [_declared(score, field) for field in _SCALAR_FIELDS] == [
        _HEADER_TYPES[field] for field in _SCALAR_FIELDS]


def test_a_scalar_field_still_takes_a_config_type_the_header_denies(
    tmp_path: pathlib.Path,
) -> None:
    """A stated ``type:`` still OVERRIDES on a scalar -- it is not ignored.

    Restating the header's own type cannot show this: the two candidates
    agree, so the assertion would hold whichever won.  ``CNT`` is
    ``Type=Integer`` in the file and ``float`` in the config, and the config
    is what a reader must see -- otherwise this fix has quietly taken the
    override away from the shapes it belongs to.
    """
    score = _vcf_score(tmp_path, _CNT_TYPED_FLOAT)

    assert _declared(score, "CNT") == "float"


def test_a_stated_type_the_join_cannot_produce_is_reported(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """An overridden ``type:`` is not silently discarded.

    The author of this resource believes ``MANY`` reads integers; every
    consumer will be told ``str``.  Ignoring a stated type without a word is
    how the misconception survives to be reported again, so the report names
    the field, what it declared and what it will actually hold.
    """
    with caplog.at_level("WARNING"):
        _vcf_score(tmp_path, _MANY_TYPED_INT)

    assert "MANY" in caplog.text
    assert "type: int" in caplog.text
    assert _JOINED_TEXT_REPORT in caplog.text


def test_stating_str_on_a_joined_field_is_not_reported(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """The deployed shape, which must stay quiet.

    ``type: str`` on a ``Number=.``/``Type=String`` field is what ClinVar
    writes twenty-odd times and dbSNP writes for ``CAF``/``TOPMED`` -- and
    it is exactly right: ``str`` is what the join produces.  Nothing is
    being overridden, so warning here would put a line per field per open
    against every deployed VCF resource, which is how a real report gets
    tuned out.
    """
    with caplog.at_level("WARNING"):
        _vcf_score(tmp_path, textwrap.dedent("""
            scores:
            - id: TAGS
              name: TAGS
              type: str
        """))

    assert _JOINED_TEXT_REPORT not in caplog.text


def test_a_scalar_field_with_an_overriding_type_is_not_reported(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """A stated type that WINS is not an overridden one.

    ``CNT`` is ``Type=Integer`` in the file and ``float`` in the config, and
    the config takes effect.  The report is for a type that was discarded,
    not for every type that disagrees with the header.
    """
    with caplog.at_level("WARNING"):
        _vcf_score(tmp_path, _CNT_TYPED_FLOAT)

    assert _JOINED_TEXT_REPORT not in caplog.text


def test_the_genotype_arity_shape_declares_str_too(
    tmp_path: pathlib.Path,
) -> None:
    """``Number=G`` is on the joined side of the set, and declares with it.

    It gets its own resource because pysam will not READ an INFO field
    declared per-genotype -- see :data:`_PERGT_VCF` -- so it cannot sit in
    the fixture the other tests read values through.  The declaration is
    built from the header alone, and that is what is checked: a shape that
    is not one of the scalar four must not take its element ``Type=`` as its
    own, whether or not a value ever arrives.
    """
    setup_directories(tmp_path, {
        "genomic_resource.yaml": textwrap.dedent("""
            type: allele_score
            table:
                filename: data.vcf.gz
        """),
    })
    setup_vcf(tmp_path / "data.vcf.gz", _PERGT_VCF)
    score = build_score_from_resource(build_filesystem_test_resource(tmp_path))

    assert score.score_definitions["PERGT"].value_type == "str"


def test_a_resource_with_a_joined_field_builds_its_statistics(
    tmp_path: pathlib.Path,
) -> None:
    """The defect's actual cost, and the only test here that shows it.

    A declared type is not a label: ``build_default_histogram_conf`` answers
    ``int``/``float`` with a NUMBER histogram, and the auto-ranging min/max
    scan in front of one calls ``np.isnan`` on every value it sees.  Handed
    ``'1|2'`` that raises ``TypeError: ufunc 'isnan' not supported for the
    input types``, which nothing on the way out catches: the task fails, the
    resource ends with no statistics and no info page, and the run reports an
    inconsistent GRR.

    So the fix is asserted where it is felt -- a full ``repo-repair`` over a
    resource carrying every joined shape -- and not only on the declared
    type, which an equality assertion could keep passing while the build
    stayed broken.  The histogram is asserted too: reaching ``str`` by
    declaring the field unusable (a null histogram) would build just as
    quietly, and it is the joined values themselves that have to be counted.
    """
    resource = _repaired_vcf_resource(tmp_path)

    histogram = json.loads(
        (resource / "statistics" / "histogram_MANY.json").read_text())
    assert histogram["config"]["type"] == "categorical"
    assert histogram["values"] == {"1|2": 1}


def test_an_explicit_number_histogram_on_a_joined_field_still_builds(
    tmp_path: pathlib.Path,
) -> None:
    """gain#1285: the crash gain#1259 left reachable through the CONFIG.

    Declaring ``str`` fixed the DEFAULT histogram for a joined field, and a
    resource that configures ``histogram: {type: number}`` explicitly walks
    straight back onto the min/max scan that ``np.isnan`` aborts -- the
    stated ``type:`` is discarded either way, so gain#1259's advice does not
    reach this.  It is the histogram config, not the type, that selects the
    crashing path.

    Asserted through a full ``repo-repair``, like its gain#1259 sibling
    above, because the cost was never the exception itself: the resource
    ended with NO statistics at all.  So the scalar field's histogram is
    what the assertion turns on -- one un-histogrammable score must not take
    the rest of the resource down with it -- while the joined field's own
    histogram is suppressed, exactly as a deliberately-disabled one is.
    """
    resource = _repaired_vcf_resource(tmp_path, _MANY_NUMBER_HIST_WITH_CNT)

    statistics = resource / "statistics"
    assert not (statistics / "histogram_MANY.json").exists(), (
        "a number histogram over joined text was built rather than refused"
    )
    scalar = json.loads((statistics / "histogram_CNT.json").read_text())
    assert scalar["config"]["type"] == "number", (
        "the scalar field lost its histogram too; one score configured with "
        "a histogram it cannot feed must not cost the resource the rest"
    )
