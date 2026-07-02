# pylint: disable=W0621,C0114,C0116,W0212,W0613
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from django.test import Client
from django.utils import timezone
from gain.annotation.annotation_config import Attribute
from gain.annotation.annotation_pipeline import Annotator, AttributeSpec
from gain.genomic_resources.aggregators import CountAggregator, MaxAggregator
from gain.genomic_resources.repository import GenomicResourceRepo
from pytest_mock import MockerFixture

from web_annotation.models import AlleleQuery, User, UserQuota
from web_annotation.pipeline_cache import LRUPipelineCache
from web_annotation.single_allele_annotation.views import SingleAnnotation


class DummyResource:
    """Dummy genomic resource."""
    def __init__(self, resource_id: str) -> None:
        self.resource_id = resource_id

    def get_type(self) -> str:
        return "gene_score"


class DummyRepo:
    """Dummy GRR."""
    def __init__(self, resource: DummyResource) -> None:
        self._resource = resource

    def get_resource(self, resource_id: str) -> DummyResource:
        assert resource_id == self._resource.resource_id
        return self._resource


class DummyPipeline:
    """Dummy pipeline."""
    def __init__(self) -> None:
        self.repository = DummyRepo(DummyResource("test"))
        self.annotators: list = []
        self.preamble = ""
        self.raw = ""

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def annotate(self, *args: Any, **kwargs: Any) -> dict:
        return {"test": 1}


def test_build_attribute_description_with_histogram(
    mocker: MockerFixture,
    test_grr: GenomicResourceRepo,
) -> None:
    view = SingleAnnotation()
    resource = DummyResource("dummy_resource")
    view._grr = cast(GenomicResourceRepo, DummyRepo(resource))

    attribute_info = Attribute(
        "attr_name",
        "score_id",
        internal=False,
        spec=AttributeSpec(
            source="score_id",
            value_type="str",
            description="desc",
        ),
    )

    result = {"attr_name": 123}
    annotator = SimpleNamespace(resource_ids={"dummy_resource"})

    histogram_mock = mocker.patch(
        "web_annotation.single_allele_annotation.views.has_histogram",
        return_value=True,
    )
    help_mock = mocker.patch.object(
        view,
        "generate_annotator_help",
        return_value="help",
    )

    description = view._build_attribute_description(
        result,
        cast(Annotator, annotator),
        attribute_info,
    )

    histogram_mock.assert_called_once_with(resource, "score_id")
    help_mock.assert_called_once_with(annotator, attribute_info)

    assert description["name"] == "attr_name"
    assert description["description"] == "desc"
    assert description["help"] == "help"
    assert description["source"] == "score_id"
    assert description["type"] == "str"
    expected_histogram = "histograms/dummy_resource?score_id=score_id"
    assert description["result"]["histogram"] == expected_histogram
    assert description["result"]["value"] == 123


def _make_attribute_info(
    aggregator_instance: Any = None,
    value_type: str = "float",
) -> Any:
    spec = SimpleNamespace(
        value_type=value_type,
        attribute_type="attribute",
        supports_aggregation=True,
    )
    return SimpleNamespace(
        name="score",
        source="score_id",
        description="",
        aggregator_instance=aggregator_instance,
        aggregator=None,
        spec=spec,
        get_value_type=lambda _aggregated: value_type,
    )


def test_preserves_domain_is_true_without_aggregation(
    mocker: MockerFixture,
) -> None:
    view = SingleAnnotation()
    view._grr = cast(GenomicResourceRepo, DummyRepo(DummyResource("dummy")))
    mocker.patch(
        "web_annotation.single_allele_annotation.views.has_histogram",
        return_value=False,
    )
    mocker.patch.object(view, "generate_annotator_help", return_value=None)

    attr = _make_attribute_info(aggregator_instance=None)
    annotator = SimpleNamespace(resource_ids=set())

    result = view._build_attribute_description(
        {"score": 0.5}, cast(Annotator, annotator), cast(Attribute, attr),
    )
    assert result["preserves_domain"] is True


def test_preserves_domain_is_true_with_domain_preserving_aggregator(
    mocker: MockerFixture,
) -> None:
    view = SingleAnnotation()
    view._grr = cast(GenomicResourceRepo, DummyRepo(DummyResource("dummy")))
    mocker.patch(
        "web_annotation.single_allele_annotation.views.has_histogram",
        return_value=False,
    )
    mocker.patch.object(view, "generate_annotator_help", return_value=None)

    attr = _make_attribute_info(aggregator_instance=MaxAggregator())
    annotator = SimpleNamespace(resource_ids=set())

    result = view._build_attribute_description(
        {"score": 0.5}, cast(Annotator, annotator), cast(Attribute, attr),
    )
    assert result["preserves_domain"] is True


def test_preserves_domain_is_false_with_non_preserving_aggregator(
    mocker: MockerFixture,
) -> None:
    view = SingleAnnotation()
    view._grr = cast(GenomicResourceRepo, DummyRepo(DummyResource("dummy")))
    mocker.patch(
        "web_annotation.single_allele_annotation.views.has_histogram",
        return_value=False,
    )
    mocker.patch.object(view, "generate_annotator_help", return_value=None)

    attr = _make_attribute_info(aggregator_instance=CountAggregator())
    annotator = SimpleNamespace(resource_ids=set())

    result = view._build_attribute_description(
        {"score": 0.5}, cast(Annotator, annotator), cast(Attribute, attr),
    )
    assert result["preserves_domain"] is False


def test_build_attribute_description_stringifies_non_mapping_objects(
    mocker: MockerFixture,
) -> None:
    view = SingleAnnotation()
    resource = DummyResource("dummy_resource")
    view._grr = cast(GenomicResourceRepo, DummyRepo(resource))

    attribute_info = Attribute(
        "attr_name",
        "score_id",
        internal=False,
        spec=AttributeSpec(
            source="score_id",
            value_type="object",
            description="",
        ),
    )

    result = {"attr_name": 456}
    annotator = SimpleNamespace(resource_ids={"dummy_resource"})

    mocker.patch(
        "web_annotation.single_allele_annotation.views.has_histogram",
        return_value=False,
    )
    mocker.patch.object(view, "generate_annotator_help", return_value=None)

    description = view._build_attribute_description(
        result,
        cast(Annotator, annotator),
        attribute_info,
    )

    assert description["result"]["histogram"] is None
    assert description["result"]["value"] == "456"


@pytest.mark.asyncio
async def test_use_of_thread_safe_pipelines(
    mocker: MockerFixture,
    test_grr: GenomicResourceRepo,
) -> None:
    view = SingleAnnotation()
    custom_cache = LRUPipelineCache(test_grr, 16)
    custom_cache.put_pipeline("dummy", "")
    thread_safe_dummy = custom_cache.get_pipeline("dummy")
    assert thread_safe_dummy is not None
    thread_safe_dummy.lock = MagicMock()
    request_data = MagicMock()
    request_data.data = {
        "pipeline_id": "dummy",
        "annotatable": {
            "chrom": "1",
            "pos": 12345,
            "ref": "A",
            "alt": "T",
        },
    }
    request_data.user = MagicMock()
    request_data.user.is_authenticated = False
    request_data.user.as_owner = request_data.user
    pipeline_mock = MagicMock()
    pipeline_mock.table_id.return_value = "dummy"
    request_data.user.get_pipeline.return_value = pipeline_mock
    pipeline_mock.owner = request_data.user
    mocker.patch(
        "web_annotation.single_allele_annotation"
        ".views.SingleAnnotation.lru_cache",
        new=custom_cache,
    )
    assert thread_safe_dummy.lock.__enter__.call_count == 0
    await view.post(request_data)
    assert thread_safe_dummy.lock.__enter__.call_count == 1
    await view.post(request_data)
    await view.post(request_data)
    assert thread_safe_dummy.lock.__enter__.call_count == 3


@pytest.mark.asyncio
async def test_single_annotation_returns_429_when_quota_exceeded(
    mocker: MockerFixture,
    test_grr: GenomicResourceRepo,
) -> None:
    view = SingleAnnotation()
    custom_cache = LRUPipelineCache(test_grr, 16)
    custom_cache.put_pipeline("dummy", "")

    quota_mock = MagicMock()
    allowed_mock = MagicMock(return_value=False)
    quota_mock.single_allele_allowed = allowed_mock

    request_data = MagicMock()
    request_data.data = {
        "pipeline_id": "dummy",
        "annotatable": {"chrom": "1", "pos": 12345, "ref": "A", "alt": "T"},
    }
    request_data.user.is_unlimited = False
    request_data.user.get_quota.return_value = quota_mock

    mocker.patch(
        "web_annotation.single_allele_annotation"
        ".views.SingleAnnotation.lru_cache",
        new=custom_cache,
    )

    response = await view.post(request_data)

    assert response.status_code == 429
    request_data.user.quota_single_allele_complete.assert_not_called()


@pytest.mark.asyncio
async def test_single_annotation_records_quota_usage_on_success(
    mocker: MockerFixture,
    test_grr: GenomicResourceRepo,
) -> None:
    view = SingleAnnotation()
    custom_cache = LRUPipelineCache(test_grr, 16)
    custom_cache.put_pipeline("dummy", "")

    quota_mock = MagicMock()
    allowed_mock = MagicMock(return_value=True)
    quota_mock.single_allele_allowed = allowed_mock

    request_data = MagicMock()
    request_data.data = {
        "pipeline_id": "dummy",
        "annotatable": {"chrom": "1", "pos": 12345, "ref": "A", "alt": "T"},
    }
    request_data.user.is_unlimited = False
    request_data.user.is_authenticated = False
    request_data.user.get_quota.return_value = quota_mock

    mocker.patch(
        "web_annotation.single_allele_annotation"
        ".views.SingleAnnotation.lru_cache",
        new=custom_cache,
    )

    response = await view.post(request_data)

    assert response.status_code == 200
    # Empty pipeline has no annotators, so attributes_count == 0
    quota_mock.single_allele_allowed.assert_called_once_with(0)
    request_data.user.quota_single_allele_complete.assert_called_once_with(0)


@pytest.mark.asyncio
async def test_single_annotation_quota_counts_only_non_internal_attributes(
    mocker: MockerFixture,
    test_grr: GenomicResourceRepo,
) -> None:
    view = SingleAnnotation()

    non_internal = SimpleNamespace(
        internal=False, name="attr", source="s",
        description="d", value_type="str",
    )
    internal = SimpleNamespace(internal=True)
    annotator = SimpleNamespace(
        attributes=[non_internal, non_internal, internal],
        resource_ids={"test"},
        get_info=lambda: SimpleNamespace(
            type="t", documentation="d", resources=[],
        ),
    )

    dummy_pipeline = DummyPipeline()
    dummy_pipeline.annotators = [annotator]

    mocker.patch.object(
        view, "_build_attribute_description", return_value={},
    )
    mocker.patch.object(
        view, "aget_pipeline",
        new=AsyncMock(return_value=dummy_pipeline),
    )

    quota_mock = MagicMock()
    quota_mock.single_allele_allowed.return_value = True

    request_data = MagicMock()
    request_data.data = {
        "pipeline_id": "some_pipeline",
        "annotatable": {"chrom": "1", "pos": 12345, "ref": "A", "alt": "T"},
    }
    request_data.user.is_unlimited = False
    request_data.user.is_authenticated = False
    request_data.user.get_quota.return_value = quota_mock

    await view.post(request_data)

    # 2 non-internal attributes, 1 internal — only non-internal counted
    quota_mock.single_allele_allowed.assert_called_once_with(2)
    request_data.user.quota_single_allele_complete.assert_called_once_with(2)


@pytest.mark.parametrize(
    "annotatable,expected", [
        (
            {"chrom": "chr1", "pos": "3"},
            {
                 "chrom": "chr1", "pos": 3,
                 "type": "POSITION",
            },
        ),
        (
            {"chrom": "chr1", "pos": "4", "ref": "C", "alt": "CT"},
            {
                 "chrom": "chr1", "pos": 4,
                 "ref": "C", "alt": "CT",
                 "type": "SMALL_INSERTION",
            },
        ),
        (
            {"vcf_like": "chr1:4:C:CT"},
            {
                 "chrom": "chr1", "pos": 4,
                 "ref": "C", "alt": "CT",
                 "type": "SMALL_INSERTION",
            },
        ),
        (
            {"chrom": "chr1", "pos_beg": "4", "pos_end": "30"},
            {
                 "chrom": "chr1", "pos_begin": 4,
                 "pos_end": 30,
                 "type": "REGION",
            },
        ),
        (
            {"location": "chr1:13", "variant": "sub(A->T)"},
            {
                 "chrom": "chr1", "pos": 13,
                 "ref": "A", "alt": "T",
                 "type": "SUBSTITUTION",
            },
        ),
        (
            {"location": "chr1:3-13", "variant": "duplication"},
            {
                 "chrom": "chr1", "pos_begin": 3, "pos_end": 13,
                 "type": "LARGE_DUPLICATION",
            },
        ),
        (
            {"location": "chr1:3-13", "variant": "CNV+"},
            {
                 "chrom": "chr1", "pos_begin": 3, "pos_end": 13,
                 "type": "LARGE_DUPLICATION",
            },
        ),
        (
            {"location": "chr1:3-13", "variant": "deletion"},
            {
                 "chrom": "chr1", "pos_begin": 3, "pos_end": 13,
                 "type": "LARGE_DELETION",
            },
        ),
        (
            {"location": "chr1:3-13", "variant": "CNV-"},
            {
                 "chrom": "chr1", "pos_begin": 3, "pos_end": 13,
                 "type": "LARGE_DELETION",
            },
        ),
    ],
)
def test_different_annotatables(
    annotatable: dict[str, Any],
    expected: dict[str, Any],
    user_client: Client,
) -> None:
    response = user_client.post(
        "/api/single_allele/annotate",
        {
            "annotatable": annotatable,
            "pipeline_id": "t4c8/t4c8_pipeline",
        },
        content_type="application/json",
    )

    assert response.status_code == 200

    assert response.json()["annotatable"] == expected


@pytest.fixture
def allele_query() -> AlleleQuery:
    user = User.objects.get(email="user@example.com")
    return AlleleQuery.objects.create(allele="chr1:100:A:T", owner=user)


def test_update_note_sets_note_on_allele(
    user_client: Client,
    allele_query: AlleleQuery,
) -> None:
    response = user_client.post(
        "/api/single_allele/note",
        {"allele": "chr1:100:A:T", "note": "interesting variant"},
        content_type="application/json",
    )

    assert response.status_code == 200
    allele_query.refresh_from_db()
    assert allele_query.note == "interesting variant"


def test_update_note_overwrites_existing_note(
    user_client: Client,
    allele_query: AlleleQuery,
) -> None:
    allele_query.note = "old note"
    allele_query.save()

    user_client.post(
        "/api/single_allele/note",
        {"allele": "chr1:100:A:T", "note": "new note"},
        content_type="application/json",
    )

    allele_query.refresh_from_db()
    assert allele_query.note == "new note"


def test_update_note_returns_400_when_allele_missing(
    user_client: Client,
) -> None:
    response = user_client.post(
        "/api/single_allele/note",
        {"note": "some note"},
        content_type="application/json",
    )

    assert response.status_code == 400


def test_update_note_returns_404_for_unknown_allele(
    user_client: Client,
) -> None:
    response = user_client.post(
        "/api/single_allele/note",
        {"allele": "chr99:1 X:Y", "note": "whatever"},
        content_type="application/json",
    )

    assert response.status_code == 404


def test_update_note_requires_authentication(
    anonymous_client: Client,
    allele_query: AlleleQuery,
) -> None:
    response = anonymous_client.post(
        "/api/single_allele/note",
        {"allele": "chr1:100 A:T", "note": "note"},
        content_type="application/json",
    )

    assert response.status_code == 403


def test_history_ordered_by_last_used_descending(user_client: Client) -> None:
    user = User.objects.get(email="user@example.com")
    now = timezone.now()
    AlleleQuery.objects.create(
        allele="chr1:1 A>T", owner=user, last_used=now - timedelta(minutes=10))
    AlleleQuery.objects.create(
        allele="chr2:2 C>G", owner=user, last_used=now - timedelta(minutes=5))
    AlleleQuery.objects.create(
        allele="chr3:3 T>A", owner=user, last_used=now)

    response = user_client.get("/api/single_allele/history")

    assert response.status_code == 200
    alleles = [r["allele"] for r in response.json()]
    assert alleles == ["chr3:3 T>A", "chr2:2 C>G", "chr1:1 A>T"]


def test_annotation_updates_last_used_on_existing_allele(
    user_client: Client,
) -> None:
    user = User.objects.get(email="user@example.com")
    old_time = timezone.now() - timedelta(hours=1)
    allele_query = AlleleQuery.objects.create(
        allele="1:3 A>T",
        owner=user,
        last_used=old_time,
    )

    user_client.post(
        "/api/single_allele/annotate",
        {
            "annotatable": {"chrom": "1", "pos": "3", "ref": "A", "alt": "T"},
            "pipeline_id": "t4c8/t4c8_pipeline",
        },
        content_type="application/json",
    )

    allele_query.refresh_from_db()
    assert allele_query.last_used > old_time


def test_annotation_does_not_create_duplicate_allele_query(
    user_client: Client,
) -> None:
    user = User.objects.get(email="user@example.com")
    AlleleQuery.objects.create(allele="1:3 A>T", owner=user)

    user_client.post(
        "/api/single_allele/annotate",
        {
            "annotatable": {"chrom": "1", "pos": "3", "ref": "A", "alt": "T"},
            "pipeline_id": "t4c8/t4c8_pipeline",
        },
        content_type="application/json",
    )

    assert AlleleQuery.objects.filter(
        allele="1:3 A>T", owner=user).count() == 1


def test_single_annotation_allele_attribute(admin_client: Client) -> None:
    response = admin_client.post(
        "/api/single_allele/annotate",
        {
            "pipeline_id": "pipeline/allele_pipeline",
            "annotatable": {
                "chrom": "chr1", "pos": 1, "ref": "C", "alt": "A",
            },
        },
        content_type="application/json",
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["annotators"]) == 1
    attributes = data["annotators"][0]["attributes"]

    freq_attr = next(a for a in attributes if a["name"] == "freq")
    assert freq_attr["result"]["value"] == pytest.approx(0.05)
    assert freq_attr["result"]["histogram"] is None

    allele_attr = next(a for a in attributes if a["name"] == "allele")
    assert allele_attr["result"]["value"] == ["chr1:1:C:A"]
    assert allele_attr["result"]["histogram"] is None


def test_unlimited_user_bypasses_single_allele_quota(
    user_client: Client,
) -> None:
    user = User.objects.get(email="user@example.com")
    user.is_unlimited = True
    user.save()
    quota = UserQuota(
        user=user,
        daily_variants=0,
        monthly_variants=0,
        daily_attributes=0,
        monthly_attributes=0,
    )
    quota.save()

    response = user_client.post(
        "/api/single_allele/annotate",
        {
            "pipeline_id": "pipeline/allele_pipeline",
            "annotatable": {
                "chrom": "chr1", "pos": 1, "ref": "C", "alt": "A",
            },
        },
        content_type="application/json",
    )

    assert response.status_code == 200


def test_unlimited_user_quota_not_deducted_after_single_allele_query(
    user_client: Client,
) -> None:
    user = User.objects.get(email="user@example.com")
    user.is_unlimited = True
    user.save()
    quota = UserQuota(user=user)
    quota.reset_daily()
    quota.reset_monthly()

    before_daily = quota.daily_variants
    before_monthly = quota.monthly_variants

    user_client.post(
        "/api/single_allele/annotate",
        {
            "pipeline_id": "pipeline/allele_pipeline",
            "annotatable": {
                "chrom": "chr1", "pos": 1, "ref": "C", "alt": "A",
            },
        },
        content_type="application/json",
    )

    quota.refresh_from_db()
    assert quota.daily_variants == before_daily
    assert quota.monthly_variants == before_monthly


def test_non_unlimited_user_is_blocked_by_quota(
    user_client: Client,
) -> None:
    user = User.objects.get(email="user@example.com")
    user.is_unlimited = False
    user.save()
    quota = UserQuota(
        user=user,
        daily_variants=0,
        monthly_variants=0,
        daily_attributes=0,
        monthly_attributes=0,
    )
    quota.save()

    response = user_client.post(
        "/api/single_allele/annotate",
        {
            "pipeline_id": "pipeline/allele_pipeline",
            "annotatable": {
                "chrom": "chr1", "pos": 1, "ref": "C", "alt": "A",
            },
        },
        content_type="application/json",
    )

    assert response.status_code == 429
