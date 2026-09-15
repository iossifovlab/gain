"""A non-scalar cell costs one score its statistic, not the whole build.

The route that used to reach it (gain#1337): htslib does not enforce a
header's ``Number`` on read, so a ``Number=1`` INFO field whose row carries
two values was handed over by pysam as a TUPLE, and ``Number=1`` is exactly
the shape ``vcf_scores`` gives no ``value_parser``, so the tuple reached the
reducers raw.  That door is shut since gain#1257 -- the read refuses the
row and no backend yields a tuple any more -- so the record stream is stood
in at the read seam every pass composes, the way ``test_min_max_nullify``
stands in a ``complex``.  The backstop is for "any future construction
route, or a parser bug", and a shape nothing produces today is the point.

These sit at the pass seam rather than the reducer's: the reducer tests pin
the wording, these pin that the passes' containment applies to it.
"""
import pathlib
import textwrap

import pytest
from gain.genomic_resources.histogram import (
    HistogramConfig,
    NullHistogram,
    NumberHistogram,
    NumberHistogramConfig,
)
from gain.genomic_resources.implementations.genomic_scores_impl import (
    scan,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.statistics.min_max import (
    MinMaxValue,
    NullMinMaxValue,
)
from gain.genomic_resources.testing.builders import a_vcf_info_score

#: ``SC`` is declared scalar; ``OK`` is the well-formed neighbour whose
#: statistics the build used to lose with it.  The VCF itself is well
#: formed: the tuple is stood in at the record stream below.
_VCF = textwrap.dedent("""
    ##fileformat=VCFv4.2
    ##contig=<ID=chr1,length=1000>
    ##INFO=<ID=SC,Number=1,Type=Float,Description="declared scalar">
    ##INFO=<ID=OK,Number=1,Type=Float,Description="well-formed neighbour">
    #CHROM POS ID REF ALT QUAL FILTER INFO
    chr1 5 . A T . . SC=1.5;OK=0.25
    chr1 6 . A T . . SC=2.5;OK=0.5
    chr1 7 . A T . . SC=4.5;OK=0.75
""")

_REGION = ("chr1", 1, 10)

#: The stream as the passes see it, with the tuple where the read used to
#: leak one.  ``OK``'s values are exact in float32, which is what a VCF
#: ``Float`` is.
_RECORDS = [
    (5, 5, [1.5, 0.25]),
    (6, 6, [(2.5, 3.5), 0.5]),
    (7, 7, [4.5, 0.75]),
]


@pytest.fixture
def resource(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> GenomicResource:
    monkeypatch.setattr(
        scan, "scan_region", lambda *_args, **_kwargs: iter(_RECORDS))
    return a_vcf_info_score().with_data(_VCF).build_resource(tmp_path)


def test_the_min_max_pass_nullifies_the_tuple_score_and_folds_the_rest(
    resource: GenomicResource,
) -> None:
    result = scan.do_min_max(resource, ["SC", "OK"], *_REGION)

    assert isinstance(result["SC"], NullMinMaxValue)
    assert "(2.5, 3.5) (<class 'tuple'>)" in result["SC"].reason
    assert type(result["OK"]) is MinMaxValue
    assert (result["OK"].min, result["OK"].max) == (0.25, 0.75)


def test_the_histogram_pass_nullifies_the_tuple_score_and_bins_the_rest(
    resource: GenomicResource,
) -> None:
    confs: dict[str, HistogramConfig] = {
        "SC": NumberHistogramConfig(view_range=(0, 10), number_of_bins=4),
        "OK": NumberHistogramConfig(view_range=(0, 1), number_of_bins=4),
    }

    result = scan.do_histogram(resource, confs, *_REGION)

    assert isinstance(result["SC"], NullHistogram)
    assert "(2.5, 3.5) (<class 'tuple'>)" in result["SC"].reason
    assert isinstance(result["OK"], NumberHistogram)
    assert result["OK"].bars.sum() == 3
