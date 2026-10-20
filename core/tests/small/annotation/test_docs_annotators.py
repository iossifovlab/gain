"""Pin the offline claims of the Annotators chapter of the docs.

The tests run the code that the chapter describes and assert the results
that the chapter states. They read no ``.rst`` file. They pin claims of
these pages under ``docs/source/development/``:

- ``annotators/annotatables.rst``: the ``to_dict`` keys of each
  annotatable, the matching order of ``build_record_to_annotatable`` and
  the examples of the page.
- ``annotators/writing_an_annotator.rst`` and
  ``annotators/pipelines.rst``: the refusals of
  ``ParamsUsageMonitor.get_number`` and ``get_integer``.
- ``annotators/pipelines.rst``: the ``str`` ``work_dir`` refusal and
  ``allow_repeated_attributes``.
- ``annotators/attributes.rst``: ``AnnotatorBase.resolve_input_gene_list``.
"""
import pathlib
from typing import Any

import pytest
from gain import grr
from gain.annotation.annotatable import (
    Annotatable,
    CNVAllele,
    Position,
    Region,
    VCFAllele,
)
from gain.annotation.annotation_config import (
    AnnotationConfigurationError,
    AnnotatorInfo,
    ParamsUsageMonitor,
)
from gain.annotation.annotation_factory import (
    build_annotation_pipeline,
    register_annotator_factory,
)
from gain.annotation.annotation_pipeline import (
    AnnotationPipeline,
    Annotator,
    AttributeSpec,
)
from gain.annotation.annotator_base import AnnotatedValues, AnnotatorBase
from gain.annotation.record_to_annotatable import (
    MalformedRecordError,
    RecordToCNVAllele,
    RecordToPosition,
    RecordToRegion,
    RecordToVcfAllele,
    build_annotatable_from_dict,
    build_record_to_annotatable,
)
from gain.genomic_resources.repository import GenomicResourceRepo

# The stub annotators register under type names unique to this module,
# because the registration is global to the process.
GENE_LIST_STUB = "docs_annotators_gene_list_stub"
OBJECT_STUB = "docs_annotators_object_stub"


class _GenesStub(AnnotatorBase):
    """Emit one attribute ``genes`` of the attribute type given."""

    attribute_type = "attribute"

    def get_attribute_specs(self) -> dict[str, AttributeSpec]:
        return {
            "genes": AttributeSpec(
                source="genes",
                value_type="object",
                description="The genes of the variant",
                attribute_type=self.attribute_type,
            ),
        }

    def _do_annotate(
        self, annotatable: Annotatable, context: dict[str, Any],
    ) -> AnnotatedValues:
        return self._every(None)


class _GeneListStub(_GenesStub):
    attribute_type = "gene_list"


class _ObjectStub(_GenesStub):
    attribute_type = "object"


def _build_gene_list_stub(
    pipeline: AnnotationPipeline, info: AnnotatorInfo,
) -> Annotator:
    return _GeneListStub(pipeline, info)


def _build_object_stub(
    pipeline: AnnotationPipeline, info: AnnotatorInfo,
) -> Annotator:
    return _ObjectStub(pipeline, info)


register_annotator_factory(GENE_LIST_STUB, _build_gene_list_stub)
register_annotator_factory(OBJECT_STUB, _build_object_stub)


@pytest.fixture(name="repo")
def fixture_repo(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    """An empty directory repository."""
    return grr.build_genomic_resource_repository({
        "id": "empty",
        "type": "directory",
        "directory": str(tmp_path / "grr"),
    })


# Claim 1: the keys that `to_dict` writes ("The interval").
@pytest.mark.parametrize(("annotatable", "keys"), [
    (Position("chr1", 5), {"type", "chrom", "pos"}),
    (Region("chr1", 5, 10), {"type", "chrom", "pos_begin", "pos_end"}),
    (CNVAllele("chr1", 5, 10, CNVAllele.Type.LARGE_DELETION),
     {"type", "chrom", "pos_begin", "pos_end"}),
    (VCFAllele("chr1", 5, "A", "T"), {"type", "chrom", "pos", "ref", "alt"}),
], ids=["Position", "Region", "CNVAllele", "VCFAllele"])
def test_to_dict_writes_the_documented_keys(
    annotatable: Annotatable, keys: set[str],
) -> None:
    """Each annotatable writes exactly the keys that the page lists."""
    result = annotatable.to_dict()

    assert set(result) == keys
    assert result["type"] == annotatable.type.name


def test_to_dict_region_keys_are_not_the_record_column_names() -> None:
    """``to_dict`` writes ``pos_begin``; the record column is ``pos_beg``."""
    result = Region("chr1", 5, 10).to_dict()

    assert result["pos_begin"] == 5
    assert "pos_beg" not in result


# Claim 2: the first matching column set wins (the page's matching order).
@pytest.mark.parametrize(("columns", "expected"), [
    ({"chrom", "pos_beg", "pos_end", "cnv_type"}, RecordToCNVAllele),
    ({"chrom", "pos_beg", "pos_end"}, RecordToRegion),
    ({"chrom", "pos", "ref", "alt"}, RecordToVcfAllele),
    ({"chrom", "pos"}, RecordToPosition),
], ids=["cnv-not-region", "region", "vcf-not-position", "position"])
def test_record_matching_picks_the_first_matching_column_set(
    columns: set[str], expected: type,
) -> None:
    """A wider column set wins over the narrower set that it contains."""
    result = build_record_to_annotatable({}, columns)

    assert isinstance(result, expected)


def test_record_matching_builds_a_cnv_allele_from_cnv_columns() -> None:
    """The CNV columns build a ``CNVAllele``, not a ``Region``."""
    record = {"chrom": "chr1", "pos_beg": "5", "pos_end": "10",
              "cnv_type": "deletion"}

    result = build_annotatable_from_dict(record)

    assert isinstance(result, CNVAllele)
    assert (result.chrom, result.pos, result.pos_end) == ("chr1", 5, 10)


def test_record_matching_follows_the_renamed_columns() -> None:
    """The page's rename example builds ``Position("chr1", 5)``."""
    builder = build_record_to_annotatable(
        {"col_chrom": "CHROM"}, {"CHROM", "pos"})

    result = builder.build({"CHROM": "chr1", "pos": "5"})

    assert result == Position("chr1", 5)


def test_build_annotatable_from_dict_builds_the_page_vcf_allele() -> None:
    """The page's dictionary example builds a ``VCFAllele``."""
    result = build_annotatable_from_dict(
        {"chrom": "chr1", "pos": "11796321", "ref": "G", "alt": "A"})

    assert isinstance(result, VCFAllele)
    assert (result.chrom, result.pos, result.ref, result.alt) == (
        "chr1", 11796321, "G", "A")


def test_record_matching_refuses_columns_that_match_no_set() -> None:
    """No matching column set raises ``MalformedRecordError``."""
    with pytest.raises(MalformedRecordError) as error:
        build_record_to_annotatable({}, {"chrom", "start"})

    assert isinstance(error.value, ValueError)


# Claim 3: the refusals of `get_number` and `get_integer`.
@pytest.mark.parametrize("accessor", ["get_number", "get_integer"])
@pytest.mark.parametrize("value", [0, 11], ids=["below", "above"])
def test_number_accessors_refuse_a_value_out_of_range(
    accessor: str, value: int,
) -> None:
    """A value below ``minimum`` or above ``maximum`` is refused."""
    params = ParamsUsageMonitor({"length": value})
    read = getattr(params, accessor)

    with pytest.raises(AnnotationConfigurationError):
        read("length", default=5, minimum=1, maximum=10)


@pytest.mark.parametrize("accessor", ["get_number", "get_integer"])
def test_number_accessors_refuse_a_value_that_is_not_a_number(
    accessor: str,
) -> None:
    """A value that is not a number is refused."""
    params = ParamsUsageMonitor({"length": "long"})
    read = getattr(params, accessor)

    with pytest.raises(AnnotationConfigurationError):
        read("length", default=5)


def test_get_integer_refuses_a_fractional_value() -> None:
    """``get_integer`` refuses a number that is not whole."""
    params = ParamsUsageMonitor({"length": 2.5})

    with pytest.raises(AnnotationConfigurationError):
        params.get_integer("length", default=5)


@pytest.mark.parametrize("accessor", ["get_number", "get_integer"])
def test_number_accessors_return_the_default_for_an_absent_key(
    accessor: str,
) -> None:
    """An absent key returns ``default``."""
    params = ParamsUsageMonitor({})
    read = getattr(params, accessor)

    result = read("length", default=5, minimum=1, maximum=10)

    assert result == 5


# Claim 4: a `str` `work_dir` is refused once there is an annotator.
def test_build_annotation_pipeline_refuses_a_str_work_dir(
    repo: GenomicResourceRepo, tmp_path: pathlib.Path,
) -> None:
    """A ``str`` ``work_dir`` raises ``TypeError`` for one annotator."""
    with pytest.raises(TypeError):
        build_annotation_pipeline(
            [{GENE_LIST_STUB: {}}], repo,
            work_dir=str(tmp_path / "work"),  # type: ignore[arg-type]
        )


def test_build_annotation_pipeline_accepts_a_str_work_dir_without_annotators(
    repo: GenomicResourceRepo, tmp_path: pathlib.Path,
) -> None:
    """A ``str`` ``work_dir`` raises nothing when there is no annotator."""
    pipeline = build_annotation_pipeline(
        [], repo,
        work_dir=str(tmp_path / "work"),  # type: ignore[arg-type]
    )

    assert not pipeline.annotators


# Claim 5: `allow_repeated_attributes` renames every colliding attribute.
def test_allow_repeated_attributes_renames_every_colliding_attribute(
    repo: GenomicResourceRepo, tmp_path: pathlib.Path,
) -> None:
    """Both attributes get the annotator id, the first one too."""
    pipeline = build_annotation_pipeline(
        [{GENE_LIST_STUB: {}}, {GENE_LIST_STUB: {}}], repo,
        allow_repeated_attributes=True, work_dir=tmp_path / "work")

    names = [attr.name for attr in pipeline.get_attributes()]

    assert names == ["genes_A0", "genes_A1"]


def test_repeated_attributes_are_refused_by_default(
    repo: GenomicResourceRepo, tmp_path: pathlib.Path,
) -> None:
    """Without the option, a repeated attribute is refused."""
    with pytest.raises(AnnotationConfigurationError):
        build_annotation_pipeline(
            [{GENE_LIST_STUB: {}}, {GENE_LIST_STUB: {}}], repo,
            work_dir=tmp_path / "work")


# Claim 6: `resolve_input_gene_list` wants a `gene_list` attribute.
def _gene_list_reader(attribute_name: str) -> AnnotatorInfo:
    return AnnotatorInfo(
        "gene_score_annotator", [], {"input_gene_list": attribute_name})


def test_resolve_input_gene_list_resolves_a_gene_list_attribute(
    repo: GenomicResourceRepo, tmp_path: pathlib.Path,
) -> None:
    """An upstream ``gene_list`` attribute resolves to its name."""
    pipeline = build_annotation_pipeline(
        [{GENE_LIST_STUB: {}}], repo, work_dir=tmp_path / "work")

    result = AnnotatorBase.resolve_input_gene_list(
        pipeline, _gene_list_reader("genes"))

    assert result == "genes"


def test_resolve_input_gene_list_refuses_a_missing_attribute(
    repo: GenomicResourceRepo, tmp_path: pathlib.Path,
) -> None:
    """A name that no upstream attribute has raises ``ValueError``."""
    pipeline = build_annotation_pipeline(
        [{GENE_LIST_STUB: {}}], repo, work_dir=tmp_path / "work")

    with pytest.raises(ValueError, match="has not been defined"):
        AnnotatorBase.resolve_input_gene_list(
            pipeline, _gene_list_reader("missing"))


def test_resolve_input_gene_list_refuses_an_object_attribute(
    repo: GenomicResourceRepo, tmp_path: pathlib.Path,
) -> None:
    """An upstream attribute of type ``object`` raises ``ValueError``."""
    pipeline = build_annotation_pipeline(
        [{OBJECT_STUB: {}}], repo, work_dir=tmp_path / "work")

    with pytest.raises(ValueError, match="expected to be of type gene_list"):
        AnnotatorBase.resolve_input_gene_list(
            pipeline, _gene_list_reader("genes"))
