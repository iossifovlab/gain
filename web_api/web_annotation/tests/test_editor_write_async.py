# pylint: disable=W0621,C0114,C0116,W0212,W0613
import asyncio
import json
import textwrap
import time
from typing import TYPE_CHECKING

import pytest
import yaml
from django.test import AsyncClient
from pytest_mock import MockerFixture

from web_annotation import annotation_base_view
from web_annotation.editor.views import (
    AnnotatorAggregators,
    AnnotatorAttributes,
    AnnotatorYAML,
    AsyncEditorView,
)
from web_annotation.pipeline_cache import LRUPipelineCache

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedASGIResponse

ATTRIBUTES_POST_URL = "/api/editor/annotator_attributes"
YAML_POST_URL = "/api/editor/annotator_yaml"
AGGREGATORS_POST_URL = "/api/editor/annotator_aggregators"


async def _post_json(
    client: AsyncClient, url: str, payload: dict,
) -> "_MonkeyPatchedASGIResponse":
    return await client.post(
        url, data=json.dumps(payload), content_type="application/json",
    )


# ---------------------------------------------------------------------------
# Async-view structure: each converted POST must expose ONLY async handlers
# ---------------------------------------------------------------------------

def test_converted_editor_posts_are_async_views() -> None:
    """The three converted POSTs must be async ``AsyncEditorView`` subclasses.

    adrf dispatches a view async iff *all* its handlers are coroutines, so each
    converted class must expose only an async ``post``.
    """
    for view in (AnnotatorAttributes, AnnotatorYAML, AnnotatorAggregators):
        assert issubclass(view, AsyncEditorView), view
        assert asyncio.iscoroutinefunction(view.post), view
        assert view.view_is_async, view


def test_converted_editor_posts_share_one_cache() -> None:
    """Single-shared-cache invariant holds for the converted POST views."""
    for view in (AnnotatorAttributes, AnnotatorYAML, AnnotatorAggregators):
        assert view.lru_cache is AsyncEditorView.lru_cache, view


# ---------------------------------------------------------------------------
# Async functional tests (full HTTP round-trip through adrf), anonymous client
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_annotator_attributes_anonymous_returns_200() -> None:
    client = AsyncClient()
    response = await _post_json(client, ATTRIBUTES_POST_URL, {
        "annotator_type": "position_score",
        "resource_id": "scores/pos1",
        "pipeline_id": "pipeline/test_pipeline",
    })
    assert response.status_code == 200, response.content
    assert response.json() == {
        "page": 0,
        "total_pages": 1,
        "total_attributes": 2,
        "attributes": [{
            "name": "pos1",
            "source": "pos1",
            "type": "float",
            "description": "test position score",
            "default": True,
            "internal": False,
            "attribute_type": "attribute",
            "supports_aggregation": True,
        }, {
            # Opt-in: offered by the annotator, never a default.
            "name": "pos1_coverage",
            "source": "pos1_coverage",
            "type": "int",
            "description": (
                "The number of base pairs of the annotated region that"
                " carried a value for 'pos1'. Divide by the length of the"
                " region for the covered fraction."
            ),
            "default": False,
            "internal": False,
            "attribute_type": "attribute",
            "supports_aggregation": False,
        }],
    }


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_annotator_attributes_cnv_collection() -> None:
    client = AsyncClient()
    response = await _post_json(client, ATTRIBUTES_POST_URL, {
        "annotator_type": "cnv_collection",
        "resource_id": "cnv_collections/test_collection",
        "pipeline_id": "pipeline/test_pipeline",
    })
    assert response.status_code == 200, response.content
    data = response.json()
    assert data["page"] == 0
    assert data["total_pages"] == 1
    assert data["total_attributes"] == 4
    assert data["attributes"][0] == {
        "name": "count",
        "source": "count",
        "type": "int",
        "description": (
            "The number of CNVs overlapping with the annotatable."
        ),
        "default": True,
        "internal": False,
        "attribute_type": "attribute",
        "supports_aggregation": True,
    }


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_effect_annotator_attributes_paginates() -> None:
    client = AsyncClient()
    response = await _post_json(client, ATTRIBUTES_POST_URL, {
        "annotator_type": "effect_annotator",
        "genome": "t4c8/t4c8_genome",
        "gene_models": "t4c8/t4c8_genes",
        "pipeline_id": "pipeline/test_pipeline",
    })
    assert response.status_code == 200, response.content
    data = response.json()
    assert data["page"] == 0
    assert data["total_pages"] == 2
    assert data["total_attributes"] == 61
    assert len(data["attributes"]) == 50
    attr_names = [attr["name"] for attr in data["attributes"]]
    assert "worst_effect" in attr_names
    assert "gene_list" in attr_names


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_annotator_attributes_search_paginates() -> None:
    client = AsyncClient()
    response = await _post_json(client, ATTRIBUTES_POST_URL, {
        "annotator_type": "effect_annotator",
        "genome": "t4c8/t4c8_genome",
        "gene_models": "t4c8/t4c8_genes",
        "pipeline_id": "pipeline/test_pipeline",
        "search": "worst_effect",
    })
    assert response.status_code == 200, response.content
    data = response.json()
    assert data["page"] == 0
    assert data["total_pages"] == 1
    assert data["total_attributes"] == 3
    assert [a["name"] for a in data["attributes"]] == [
        "worst_effect", "worst_effect_genes", "worst_effect_gene_list",
    ]


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_annotator_attributes_search_matches_description() -> None:
    """A search term matching ``spec.description`` (not the source) hits.

    Mirrors the second case of the deleted sync ``test_attributes_search``:
    "all transcripts" matches only ``worst_effect`` via its description.
    """
    client = AsyncClient()
    response = await _post_json(client, ATTRIBUTES_POST_URL, {
        "annotator_type": "effect_annotator",
        "genome": "t4c8/t4c8_genome",
        "gene_models": "t4c8/t4c8_genes",
        "pipeline_id": "pipeline/test_pipeline",
        "search": "all transcripts",
    })
    assert response.status_code == 200, response.content
    data = response.json()
    assert data["page"] == 0
    assert data["total_pages"] == 1
    assert data["total_attributes"] == 1
    assert len(data["attributes"]) == 1
    assert data["attributes"][0]["name"] == "worst_effect"


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_annotator_attributes_missing_type_400() -> None:
    client = AsyncClient()
    response = await _post_json(client, ATTRIBUTES_POST_URL, {
        "pipeline_id": "pipeline/test_pipeline",
    })
    assert response.status_code == 400, response.content
    assert "annotator_type is required" in response.content.decode()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_annotator_attributes_missing_pipeline_id_400() -> None:
    client = AsyncClient()
    response = await _post_json(client, ATTRIBUTES_POST_URL, {
        "annotator_type": "position_score",
        "resource_id": "scores/pos1",
    })
    assert response.status_code == 400, response.content
    assert "pipeline_id" in response.content.decode()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_annotator_yaml_anonymous_returns_200() -> None:
    client = AsyncClient()
    response = await _post_json(client, YAML_POST_URL, {
        "pipeline_id": "pipeline/test_pipeline",
        "annotator_type": "position_score",
        "resource_id": "scores/pos1",
        "attributes": [
            {"name": "pos1", "source": "pos1", "internal": False},
        ],
    })
    assert response.status_code == 200, response.content
    assert yaml.safe_load(response.json()) == [{
        "position_score": {
            "resource_id": "scores/pos1",
            "attributes": [
                {"name": "pos1", "source": "pos1", "internal": False},
            ],
        },
    }]


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_annotator_yaml_exact_format() -> None:
    client = AsyncClient()
    response = await _post_json(client, YAML_POST_URL, {
        "pipeline_id": "pipeline/test_pipeline",
        "annotator_type": "position_score",
        "resource_id": "scores/pos1",
        "attributes": [
            {"name": "pos1", "source": "pos1", "internal": False},
        ],
    })
    assert response.status_code == 200, response.content
    assert response.json().strip() == textwrap.dedent("""
    - position_score:
        resource_id: scores/pos1
        attributes:
        - name: pos1
          source: pos1
          internal: false
    """).strip()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_annotator_yaml_name_clash_400() -> None:
    client = AsyncClient()
    response = await _post_json(client, YAML_POST_URL, {
        "pipeline_id": "pipeline/test_pipeline",
        "annotator_type": "position_score",
        "resource_id": "scores/pos1",
        "attributes": [
            {"name": "position_1", "source": "pos1", "internal": False},
        ],
    })
    assert response.status_code == 400, response.content
    assert response.json()["error"] == (
        "Invalid annotator configuration: "
        "Repeated attributes in pipeline were found - {'position_1': ['A0']}"
    )


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_annotator_yaml_missing_pipeline_id_400() -> None:
    client = AsyncClient()
    response = await _post_json(client, YAML_POST_URL, {
        "annotator_type": "position_score",
        "resource_id": "scores/pos1",
    })
    assert response.status_code == 400, response.content
    assert "pipeline_id is required" in response.content.decode()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_annotator_aggregators_numeric_source() -> None:
    client = AsyncClient()
    response = await _post_json(client, AGGREGATORS_POST_URL, {
        "annotator_type": "position_score_annotator",
        "resource_id": "scores/pos1",
        "pipeline_id": "pipeline/test_pipeline",
        "attribute_sources": ["pos1"],
    })
    assert response.status_code == 200, response.content
    result = response.json()
    assert "pos1" in result
    assert set(result["pos1"]["aggregators"]) == {
        "max", "min", "mean", "median", "count", "concatenate", "mode",
        "join", "list", "bool", "value_count",
    }
    assert result["pos1"]["default_aggregator"] == "mean"


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_annotator_aggregators_non_aggregatable_source() -> None:
    client = AsyncClient()
    response = await _post_json(client, AGGREGATORS_POST_URL, {
        "annotator_type": "effect_annotator",
        "genome": "t4c8/t4c8_genome",
        "gene_models": "t4c8/t4c8_genes",
        "pipeline_id": "pipeline/test_pipeline",
        "attribute_sources": ["gene_effects"],
    })
    assert response.status_code == 200, response.content
    result = response.json()
    assert result["gene_effects"]["aggregators"] is None
    assert result["gene_effects"]["default_aggregator"] is None


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_annotator_aggregators_missing_type_400() -> None:
    client = AsyncClient()
    response = await _post_json(client, AGGREGATORS_POST_URL, {
        "pipeline_id": "pipeline/test_pipeline",
    })
    assert response.status_code == 400, response.content
    assert "annotator_type is required" in response.content.decode()


# ---------------------------------------------------------------------------
# Authenticated round-trip (these three carry WebAnnotationAuthentication)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_annotator_attributes_authenticated_returns_200() -> None:
    client = AsyncClient()
    await client.alogin(email="user@example.com", password="secret")
    response = await _post_json(client, ATTRIBUTES_POST_URL, {
        "annotator_type": "position_score",
        "resource_id": "scores/pos1",
        "pipeline_id": "pipeline/test_pipeline",
    })
    assert response.status_code == 200, response.content
    assert response.json()["attributes"][0]["name"] == "pos1"


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_annotator_yaml_authenticated_returns_200() -> None:
    client = AsyncClient()
    await client.alogin(email="user@example.com", password="secret")
    response = await _post_json(client, YAML_POST_URL, {
        "pipeline_id": "pipeline/test_pipeline",
        "annotator_type": "position_score",
        "resource_id": "scores/pos1",
        "attributes": [
            {"name": "pos1", "source": "pos1", "internal": False},
        ],
    })
    assert response.status_code == 200, response.content
    assert yaml.safe_load(response.json()) == [{
        "position_score": {
            "resource_id": "scores/pos1",
            "attributes": [
                {"name": "pos1", "source": "pos1", "internal": False},
            ],
        },
    }]


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_annotator_aggregators_authenticated_returns_200() -> None:
    client = AsyncClient()
    await client.alogin(email="user@example.com", password="secret")
    response = await _post_json(client, AGGREGATORS_POST_URL, {
        "annotator_type": "position_score_annotator",
        "resource_id": "scores/pos1",
        "pipeline_id": "pipeline/test_pipeline",
        "attribute_sources": ["pos1"],
    })
    assert response.status_code == 200, response.content
    result = response.json()
    assert "pos1" in result
    assert result["pos1"]["default_aggregator"] == "mean"


# ---------------------------------------------------------------------------
# Pinned exception-mapping regression (unbuildable -> 400, missing -> 404)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_aggregators_unbuildable_pipeline_maps_to_400() -> None:
    """A genuinely unbuildable (in-GRR) pipeline -> 400 (not 500).

    Validation is deferred to the background build (#150 H1), so an unbuildable
    saved pipeline first fails inside ``aget_pipeline``; that build failure must
    map to a 400, not escape as a 500.
    """
    bad_id = "broken/editor_post_bad_pipeline"
    bad_config = "- position_score: scores/does_not_exist"
    original = dict(annotation_base_view.GRR_PIPELINES)
    annotation_base_view.GRR_PIPELINES[bad_id] = {
        "id": bad_id, "content": bad_config,
    }
    try:
        client = AsyncClient()
        response = await _post_json(client, AGGREGATORS_POST_URL, {
            "annotator_type": "position_score_annotator",
            "resource_id": "scores/pos1",
            "pipeline_id": bad_id,
            "attribute_sources": ["pos1"],
        })
        assert response.status_code == 400, response.content
    finally:
        annotation_base_view.GRR_PIPELINES.clear()
        annotation_base_view.GRR_PIPELINES.update(original)
        AnnotatorAggregators.lru_cache.unload_pipeline(bad_id)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_attributes_missing_pipeline_maps_to_404() -> None:
    """A pipeline id that resolves to nothing -> 404 (not 500)."""
    client = AsyncClient()
    response = await _post_json(client, ATTRIBUTES_POST_URL, {
        "annotator_type": "position_score",
        "resource_id": "scores/pos1",
        "pipeline_id": "no/such/pipeline/exists",
    })
    assert response.status_code == 404, response.content


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_yaml_missing_pipeline_maps_to_404() -> None:
    """A pipeline id that resolves to nothing -> 404 (not 500)."""
    client = AsyncClient()
    response = await _post_json(client, YAML_POST_URL, {
        "pipeline_id": "no/such/pipeline/exists",
        "annotator_type": "position_score",
        "resource_id": "scores/pos1",
        "attributes": [
            {"name": "pos1", "source": "pos1", "internal": False},
        ],
    })
    assert response.status_code == 404, response.content


# ---------------------------------------------------------------------------
# Threaded non-blocking proof: a slow build must not park the event loop
# ---------------------------------------------------------------------------

@pytest.fixture
def slow_build(mocker: MockerFixture) -> float:
    """Make pipeline builds take ~SLOW seconds on the loader thread.

    The injected delay sits at ``_load_pipeline_raw`` -- the same point a slow
    real GRR build would block, on the loader thread -- so the async path's
    off-loop await of that build is exercised faithfully. (#164 added the
    ``GPFWA_BUILD_DELAY_SECONDS`` env hook there too; monkeypatching here keeps
    the test self-contained and independent of process env.)
    """
    slow_seconds = 0.4
    real_load = LRUPipelineCache._load_pipeline_raw

    def slow_load(raw, grr, pipeline_id="unknown"):  # type: ignore
        time.sleep(slow_seconds)
        return real_load(raw, grr, pipeline_id)

    mocker.patch.object(
        LRUPipelineCache, "_load_pipeline_raw", staticmethod(slow_load),
    )
    return slow_seconds


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_concurrent_slow_aggregator_posts_do_not_park_event_loop(
    slow_build: float,
) -> None:
    """Concurrent slow GRR builds behind POSTs leave the loop responsive.

    Uses the REAL ThreadedTaskExecutor (production path). With the build wait
    awaited off the loop via ``aget_pipeline``, a cheap heartbeat coroutine
    keeps ticking while N annotator_aggregators POSTs block on slow builds. If
    the build were resolved ON the loop thread (the bug this issue fixes), the
    heartbeat would stall for the whole slow-build window.

    Discriminating: the assertion ``max_gap < slow_build`` only holds if the
    loop kept turning *during* the build. With four concurrent cold builds each
    sleeping 0.4s, a loop parked on ``future.result()`` would show a single gap
    >= 0.4s; the off-loop await keeps every inter-tick gap well under that.
    """
    # Force a cold cache so each request actually waits on a build.
    AnnotatorAggregators.lru_cache.unload_pipeline("pipeline/test_pipeline")

    heartbeats: list[float] = []
    stop = asyncio.Event()

    async def heartbeat() -> None:
        while not stop.is_set():
            heartbeats.append(time.monotonic())
            await asyncio.sleep(0.02)

    async def fire_request() -> int:
        client = AsyncClient()
        response = await _post_json(client, AGGREGATORS_POST_URL, {
            "annotator_type": "position_score_annotator",
            "resource_id": "scores/pos1",
            "pipeline_id": "pipeline/test_pipeline",
            "attribute_sources": ["pos1"],
        })
        return response.status_code

    hb_task = asyncio.ensure_future(heartbeat())
    requests = [asyncio.ensure_future(fire_request()) for _ in range(4)]
    statuses = await asyncio.gather(*requests)
    stop.set()
    await hb_task

    assert all(s == 200 for s in statuses), statuses

    assert len(heartbeats) >= 5
    gaps = [
        heartbeats[i + 1] - heartbeats[i]
        for i in range(len(heartbeats) - 1)
    ]
    max_gap = max(gaps)
    assert max_gap < slow_build, (
        f"event loop stalled for {max_gap:.3f}s "
        f"(>= slow build {slow_build:.3f}s) -- build ran ON the loop"
    )
