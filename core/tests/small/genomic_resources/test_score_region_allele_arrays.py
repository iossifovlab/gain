"""``AlleleScore.fetch_region_allele_arrays`` and its capability query.

The bulk column-array region read that also carries the ref/alt cells
(gain#780), so a caller scanning a whole region for allele *content* --
the allele statistics of gain#777 -- does not have to build a ``Record``
per row to see the nucleotides.

A separate read rather than a flag on
``GenomicScore.fetch_region_value_arrays``: the shared batch is a fixed
three-element tuple with existing consumers, reference and alternative
belong to the kind that has them, and ADR 0008 turned down a mode flag on
the read path.
"""
# pylint: disable=C0116,W0212,W0621
import pathlib

import numpy as np
import pytest
from gain.genomic_resources.genomic_position_table.record import ALT, REF
from gain.genomic_resources.genomic_scores import AlleleScore
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing.builders import (
    a_bigwig_score,
    a_grr,
    a_vcf_info_score,
    an_allele_score,
)

_VCF_ALLELE_DATA = "\n".join([
    "##fileformat=VCFv4.1",
    '##INFO=<ID=scoreA,Number=1,Type=Float,Description="score A">',
    "#CHROM POS ID REF ALT QUAL FILTER INFO",
    "chr1   10  .  A   T   .    .      scoreA=0.1",
    "",
])


def _vcf_allele_score(tmp_path: pathlib.Path) -> GenomicResource:
    return a_vcf_info_score().with_data(_VCF_ALLELE_DATA).build_resource(
        tmp_path)


def _allele_tabix(tmp_path: pathlib.Path) -> GenomicResource:
    return (
        an_allele_score()
        .with_score("s1", "float")
        .with_data(
            """
            chrom  pos_begin  reference  alternative  s1
            1      10         A          G            0.1
            1      10         A          C            0.2
            1      16         C          T            0.3
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )


def test_allele_score_fetches_ref_alt_arrays_beside_the_scores(
    tmp_path: pathlib.Path,
) -> None:
    resource = _allele_tabix(tmp_path)

    with AlleleScore(resource).open() as score:
        batches = list(
            score.fetch_region_allele_arrays("1", 1, 20, ["s1"]))

    assert len(batches) == 1
    pos_begin, pos_end, cols, reference, alternative = batches[0]
    assert np.array_equal(pos_begin, [10, 10, 16])
    assert np.array_equal(pos_end, [10, 10, 16])
    # The scores are parsed, exactly as the shared bulk read parses them.
    assert cols["s1"].dtype == np.float64
    assert np.array_equal(cols["s1"], [0.1, 0.2, 0.3])
    # The nucleotides are NOT parsed -- they are the cells as stored.
    assert np.array_equal(reference, ["A", "A", "C"])
    assert np.array_equal(alternative, ["G", "C", "T"])


def _allele_shapes_tabix(tmp_path: pathlib.Path) -> GenomicResource:
    """Every allele shape one table can hold, plus a repeated key.

    The duplicate ``(chrom, pos, ref, alt)`` rows at 20 are not damage: a
    per-transcript resource repeats a key as a matter of course, which is why
    the duplicate rule was refused (see
    ``.out-of-scope/duplicate-allele-keys.md``).  Both rows must reach the
    arrays.
    """
    return (
        an_allele_score()
        .with_score("s1", "float")
        .with_data(
            """
            chrom  pos_begin  reference  alternative  s1
            1      10         A          G            0.1
            1      10         A          T            0.2
            1      10         A          C            0.3
            1      14         C          CTT          0.4
            1      18         GCA        G            0.5
            1      20         T          A            0.6
            1      20         T          A            0.7
            1      24         AC         GT           0.8
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )


def test_ref_alt_arrays_equal_the_per_record_read(
    tmp_path: pathlib.Path,
) -> None:
    resource = _allele_shapes_tabix(tmp_path)

    with AlleleScore(resource).open() as score:
        records = list(score.fetch_records("1", 1, 30))
        batches = list(score.fetch_region_allele_arrays("1", 1, 30, ["s1"]))

    from_records = [(rec[REF], rec[ALT]) for rec in records]
    from_arrays = [
        (ref, alt)
        for batch in batches
        for ref, alt in zip(batch.reference, batch.alternative, strict=True)
    ]

    # Element for element -- substitution, insertion, deletion, MNV, the
    # three alternatives at one position and the repeated key alike.
    assert from_arrays == from_records
    assert len(from_arrays) == 8


def _no_key_columns_tabix(tmp_path: pathlib.Path) -> GenomicResource:
    """An allele score whose table declares neither key column.

    A legal shape -- both columns are optional in the allele-score schema --
    and the one this read has nothing to carry for.
    """
    return (
        an_allele_score()
        .with_score("s1", "float")
        .without_key_columns("reference", "alternative")
        .with_data(
            """
            chrom  pos_begin  s1
            1      10         0.1
            1      16         0.3
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )


def test_the_key_columns_are_handed_back_exactly_as_stored(
    tmp_path: pathlib.Path,
) -> None:
    """The nucleotides are raw where the scores beside them are parsed.

    Every row here is a cell some normalisation would touch: soft-masked
    lower case, a mixed-case MNV, and a ``.`` that is the NA sentinel for
    the score column on the very same row.  The score is parsed and the
    sentinel becomes ``nan``; the key columns go through nothing, so a
    ``.`` stays the string it is.  Upper-casing, stripping or sentinel
    handling on either side breaks this.
    """
    resource = (
        an_allele_score()
        .with_score("s1", "float")
        .with_na_values(".")
        .with_data(
            """
            chrom  pos_begin  reference  alternative  s1
            1      10         a          g            0.1
            1      12         aCg        A            .
            1      14         .          T            0.3
            1      16         C          .            0.4
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )

    with AlleleScore(resource).open() as score:
        records = list(score.fetch_records("1", 1, 20))
        batches = list(score.fetch_region_allele_arrays("1", 1, 20, ["s1"]))

    reference = list(batches[0].reference)
    alternative = list(batches[0].alternative)

    assert reference == ["a", "aCg", ".", "C"]
    assert alternative == ["g", "A", "T", "."]
    # And the same cells the record read hands back, not merely equal-looking
    # ones -- the two reads must not drift into two dialects.
    assert reference == [rec[REF] for rec in records]
    assert alternative == [rec[ALT] for rec in records]
    # The score column on those same rows IS parsed: the sentinel is a
    # non-value there, which is exactly the asymmetry under test.
    assert np.isnan(batches[0].values["s1"][1])


def test_ref_alt_arrays_agree_with_the_record_read_across_batch_sizes_fuzz(
    tmp_path: pathlib.Path,
) -> None:
    """The agreement holds however the read is cut into batches.

    The batch-size counterpart of
    ``test_parse_array_agrees_with_parse_value_fuzz``: that one varies the
    array width because numpy dispatches short and long arrays differently,
    and this one varies the batch size because the backend's batching loop
    is where a column could slip against its positions -- a misalignment a
    single-batch fixture cannot see.  ``batch_size`` is a HINT the tabular
    backend honours, so a size below the row count really does produce
    several batches, which the count assertion pins rather than assumes.
    """
    rows = [
        ("A", "G"), ("a", "g"), ("C", "CTT"), ("GCA", "G"), ("AC", "GT"),
        ("T", "A"), ("T", "A"), (".", "T"), ("C", "."), ("aCg", "A"),
        ("N", "A"), ("A", "N"), ("TTTTTTTTTT", "T"), ("G", "GGGGGGGGGG"),
        ("A", "T"), ("C", "G"), ("G", "C"),
    ]
    data = "chrom  pos_begin  reference  alternative  s1\n" + "\n".join(
        f"1  {10 + index * 2}  {ref}  {alt}  0.{index % 10}"
        for index, (ref, alt) in enumerate(rows)
    )
    resource = (
        an_allele_score()
        .with_score("s1", "float")
        .with_data(data)
        .with_tabix()
        .build_resource(tmp_path)
    )

    with AlleleScore(resource).open() as score:
        records = list(score.fetch_records("1", 1, 1000))
        want = [(rec[REF], rec[ALT]) for rec in records]

        assert want == rows

        for batch_size in (1, 2, 3, 7, 8, 16, len(rows)):
            batches = list(score.fetch_region_allele_arrays(
                "1", 1, 1000, ["s1"], batch_size=batch_size))

            # The loop really did run: without this a single fat batch would
            # satisfy every assertion below and test nothing about batching.
            expected_batches = -(-len(rows) // batch_size)
            assert len(batches) == expected_batches, batch_size

            got = [
                (ref, alt)
                for batch in batches
                for ref, alt in zip(
                    batch.reference, batch.alternative, strict=True)
            ]
            assert got == want, batch_size


def test_vcf_backed_allele_score_refuses_the_read(
    tmp_path: pathlib.Path,
) -> None:
    # A VCF table SUBCLASSES the tabix one, so an unguarded call does not
    # fail cleanly -- it trips the inherited method's bare ``assert`` and
    # yields a message-less AssertionError (nothing at all under ``-O``).
    # The refusal names the resource and which of the two rules turned it
    # away, so the caller can act on it.
    score = AlleleScore(_vcf_allele_score(tmp_path))
    assert score.supports_region_allele_arrays(["scoreA"]) is False

    with score.open() as opened, pytest.raises(TypeError) as excinfo:
        opened.fetch_region_allele_arrays("chr1", 1, 100, ["scoreA"])

    message = str(excinfo.value)
    assert "supports_region_allele_arrays" in message
    assert "supports_value_arrays False" in message


def test_a_tabix_table_with_no_key_columns_is_refused_by_the_column_rule(
    tmp_path: pathlib.Path,
) -> None:
    """The key-column rule is about the data, not about a backend class."""
    # Answerable WITHOUT opening: the table is built in ``__init__``, so a
    # caller does not open a resource -- and so its file -- merely to learn
    # that this read is not available for it.
    score = AlleleScore(_no_key_columns_tabix(tmp_path))

    # It is NOT the backend rule that turns this one away -- that one says
    # yes.  Asserting both is what pins which rule fired.
    assert score.supports_region_value_arrays(["s1"]) is True
    assert score.supports_region_allele_arrays(["s1"]) is False

    with score.open() as opened, pytest.raises(TypeError) as excinfo:
        opened.fetch_region_allele_arrays("1", 1, 30, ["s1"])

    message = str(excinfo.value)
    assert "neither a 'reference' nor an 'alternative' column" in message
    # Points at the query to ask instead, rather than only complaining.
    assert "supports_region_allele_arrays" in message
    # No backend named: the same sentence has to serve every table that has
    # no key columns, whatever class implements it.
    assert "Table" not in message


def test_the_refusal_names_the_resource_it_refused(
    tmp_path: pathlib.Path,
) -> None:
    """A caller with several scores has to know WHICH one was turned away.

    Built through ``a_grr()`` rather than ``build_resource()`` on purpose:
    the single-resource form realizes a repo whose resource id is the empty
    string, so ``assert resource_id in message`` would hold for every
    message ever written, including one that names no resource at all.
    """
    repo = (
        a_grr()
        .with_resource(
            "scores/no_key_columns",
            an_allele_score()
            .with_score("s1", "float")
            .without_key_columns("reference", "alternative")
            .with_score_line(chrom="1", pos_begin="10", s1="0.1"),
        )
        .build_repo(tmp_path)
    )

    score = AlleleScore(repo.get_resource("scores/no_key_columns"))

    with score.open() as opened, pytest.raises(TypeError) as excinfo:
        opened.fetch_region_allele_arrays("1", 1, 30, ["s1"])

    assert "scores/no_key_columns" in str(excinfo.value)


def test_a_bigwig_backed_allele_score_is_refused_by_the_column_rule(
    tmp_path: pathlib.Path,
) -> None:
    """The backend the column rule is really aimed at.

    A bigWig serves value arrays perfectly well and has no key columns
    whatsoever, so it must be turned away by the column rule rather than
    the backend one -- and without being named, since what disqualifies it
    is its data and not its class.  Nobody publishes an allele score over a
    bigWig; it is built here because the rule should not depend on that.
    """
    resource = (
        a_bigwig_score()
        .with_resource_type("allele_score")
        .with_score("bw", "float")
        .with_data("""
            chr1  0  2  0.0
            chr1  2  4  2.5
        """)
        .with_chrom_lens({"chr1": 100})
        .build_resource(tmp_path)
    )

    score = AlleleScore(resource)
    assert score.supports_region_value_arrays(["bw"]) is True
    assert score.supports_region_allele_arrays(["bw"]) is False

    with score.open() as opened, pytest.raises(TypeError) as excinfo:
        opened.fetch_region_allele_arrays("chr1", 1, 4, ["bw"])

    message = str(excinfo.value)
    assert "neither a 'reference' nor an 'alternative' column" in message
    assert "BigWig" not in message


def test_reading_from_an_unopened_score_is_refused_at_call_time(
    tmp_path: pathlib.Path,
) -> None:
    score = AlleleScore(_allele_tabix(tmp_path))

    # Not on the first next(): a caller that builds the generator and hands
    # it elsewhere must be told at the mistake, not at some later point.
    with pytest.raises(ValueError, match="is not open"):
        score.fetch_region_allele_arrays("1", 1, 20, ["s1"])


def test_an_unknown_chromosome_is_refused_at_call_time(
    tmp_path: pathlib.Path,
) -> None:
    with AlleleScore(_allele_tabix(tmp_path)).open() as score, \
            pytest.raises(ValueError, match="not among the available"):
        score.fetch_region_allele_arrays("chrNope", 1, 20, ["s1"])


def test_one_declared_key_column_is_served_with_the_other_as_none(
    tmp_path: pathlib.Path,
) -> None:
    # The columns are configured independently, so a table can declare one.
    # Serving it -- rather than refusing the pair -- is what keeps this read
    # and the record read the same answer: the record carries None for the
    # column that is not there, and so does the array.
    resource = (
        an_allele_score()
        .with_score("s1", "float")
        .without_key_columns("reference")
        .with_data(
            """
            chrom  pos_begin  alternative  s1
            1      10         G            0.1
            1      16         T            0.3
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )

    score = AlleleScore(resource)
    assert score.supports_region_allele_arrays(["s1"]) is True

    with score.open() as opened:
        records = list(opened.fetch_records("1", 1, 20))
        batches = list(opened.fetch_region_allele_arrays("1", 1, 20, ["s1"]))

    assert [rec[REF] for rec in records] == [None, None]
    assert list(batches[0].alternative) == [rec[ALT] for rec in records]
    assert list(batches[0].reference) == [None, None]
