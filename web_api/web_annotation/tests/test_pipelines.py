# pylint: disable=W0621,C0114,C0116,W0212,W0613
import contextlib
import json
import textwrap
from unittest.mock import MagicMock

import pytest
import pytest_mock
from django.conf import settings
from django.core.files.base import ContentFile
from django.test import Client
from gain.genomic_resources.repository import GenomicResourceRepo
from rest_framework.exceptions import NotFound, ValidationError

from web_annotation.annotation_base_view import AnnotationBaseView
from web_annotation.consumers import AnnotationStateConsumer
from web_annotation.executor import SequentialTaskExecutor
from web_annotation.models import Pipeline, User
from web_annotation.pipeline_cache import LRUPipelineCache, PipelineNotCached


@pytest.mark.django_db
def test_pipeline_doc_returns_html_download(
    user_client: Client,
) -> None:
    response = user_client.get(
        "/api/pipelines/doc?pipeline_id=pipeline/test_pipeline")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/html")
    assert "attachment" in response["Content-Disposition"]
    assert "pipeline/test_pipeline.html" in response["Content-Disposition"]
    assert len(response.content) > 0


@pytest.mark.django_db
def test_pipeline_doc_does_not_expose_pipeline_path(
    user_client: Client,
) -> None:
    response = user_client.get(
        "/api/pipelines/doc?pipeline_id=pipeline/test_pipeline")
    assert response.status_code == 200
    content = response.content.decode()
    assert "Pipeline path:" not in content


@pytest.mark.django_db
def test_pipeline_doc_missing_pipeline_id(
    user_client: Client,
) -> None:
    response = user_client.get("/api/pipelines/doc")
    assert response.status_code == 400


@pytest.mark.django_db
def test_list_pipelines_default_pipeline_first(
    user_client: Client,
    mocker: pytest_mock.MockerFixture,
) -> None:
    mocker.patch.object(settings, "DEFAULT_PIPELINE", "pipeline/test_pipeline")
    response = user_client.get("/api/pipelines")
    assert response.status_code == 200
    pipelines = response.json()
    assert len(pipelines) > 0
    assert pipelines[0]["name"] == "pipeline/test_pipeline"


@pytest.mark.django_db
def test_list_pipelines_default_pipeline_none_preserves_order(
    user_client: Client,
    mocker: pytest_mock.MockerFixture,
) -> None:
    mocker.patch.object(settings, "DEFAULT_PIPELINE", None)
    response = user_client.get("/api/pipelines")
    assert response.status_code == 200
    pipelines = response.json()
    names = [p["name"] for p in pipelines]
    assert names == sorted(names)


@pytest.mark.django_db
def test_list_pipelines_default_pipeline_not_found_errors(
    user_client: Client,
    mocker: pytest_mock.MockerFixture,
) -> None:
    mocker.patch.object(settings, "DEFAULT_PIPELINE", "pipeline/nonexistent")
    response = user_client.get("/api/pipelines")
    assert response.status_code == 500


@pytest.mark.django_db
def test_create_pipeline_stores_in_cache(
    test_grr: GenomicResourceRepo,
    user_client: Client,
    mocker: pytest_mock.MockerFixture,
) -> None:
    pipeline_config = "- position_score: scores/pos1"

    params = {
        "config": ContentFile(pipeline_config),
        "name": "cache_test_pipeline",
    }

    user = User.objects.get(email="user@example.com")
    assert Pipeline.objects.filter(owner=user).count() == 0

    cache = LRUPipelineCache(test_grr, 16)
    mocker.patch(
        "web_annotation.pipelines"
        ".views.UserPipeline.lru_cache",
        new=cache,
    )
    assert "1" not in cache._cache

    response = user_client.post("/api/pipelines/user", params)

    assert response.status_code == 200
    assert "1" in cache._cache
    pipeline = cache._cache["1"]
    assert pipeline.future.result().raw == [{"position_score": "scores/pos1"}]

    pipeline_config = textwrap.dedent("""
        - position_score:
            attributes:
              - name: position_1
                source: pos1
            resource_id: scores/pos1
    """)

    params = {
        "id": "1",
        "config": ContentFile(pipeline_config),
        "name": "cache_test_pipeline",
    }
    response = user_client.post("/api/pipelines/user", params)

    assert response.status_code == 200
    assert "1" in cache._cache
    pipeline = cache._cache["1"]
    assert pipeline.future.result().raw == [{"position_score": {
        "attributes": [{"name": "position_1", "source": "pos1"}],
        "resource_id": "scores/pos1",
    }}]


def test_view_get_pipeline_reloads_on_cache_miss(
    mocker: pytest_mock.MockerFixture,
) -> None:
    """View ``get_pipeline`` recovers from a cache-miss by reloading (#140).

    Reproduces Findings 1/2: even with pinning, an entry can vanish between
    the view's has/put check-then-act and the pin taken inside the cache's
    ``get_pipeline`` (capacity eviction in the residual window, or the timeout
    reaper / a force reload), surfacing a ``ValueError`` cache-miss. The view
    must re-``put_pipeline`` (reload from the same source) and retry, not
    propagate a spurious 4xx for a pipeline that is genuinely available.
    """
    view = AnnotationBaseView()
    user = MagicMock()
    sentinel = object()

    fake_cache = MagicMock()
    fake_cache.has_pipeline.return_value = True
    # First resolution misses (evicted/reaped in the residual window);
    # after a reload it resolves.
    fake_cache.get_pipeline.side_effect = [
        PipelineNotCached("Pipeline p not found"),
        sentinel,
    ]
    mocker.patch.object(view, "lru_cache", fake_cache)
    put_spy = mocker.patch.object(view, "put_pipeline")

    result = view.get_pipeline("p", user)

    assert result is sentinel
    assert fake_cache.get_pipeline.call_count == 2
    # Reload happened via the existing locked put_pipeline path.
    assert put_spy.called


def test_view_get_pipeline_reraises_after_exhausting_retries(
    mocker: pytest_mock.MockerFixture,
) -> None:
    """A genuinely-missing pipeline still raises after bounded retries (#140).

    The reload-on-miss retry must be bounded so a pipeline that cannot be
    loaded does not loop forever; after the bound it raises NotFound so the
    view layer still returns a 4xx.
    """
    view = AnnotationBaseView()
    user = MagicMock()

    fake_cache = MagicMock()
    fake_cache.has_pipeline.return_value = False
    fake_cache.get_pipeline.side_effect = PipelineNotCached(
        "Pipeline p not found")
    mocker.patch.object(view, "lru_cache", fake_cache)
    put_spy = mocker.patch.object(view, "put_pipeline")

    with pytest.raises(NotFound):
        view.get_pipeline("p", user)

    # Bounded: a small finite number of attempts, not an infinite loop.
    assert 1 < fake_cache.get_pipeline.call_count <= 5
    assert put_spy.call_count >= 1


@pytest.mark.django_db
def test_save_user_pipeline_defers_resource_validation(
    user_client: Client,
    test_grr: GenomicResourceRepo,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """Saving a pipeline must not build it against the GRR on the request
    thread (#150 H1).

    A structurally-valid config that references a resource which does not
    exist in the GRR is accepted (200): deep, resource-resolving validation
    is deferred to the background loader, not performed inline. Previously
    this returned 400 because the view built the pipeline synchronously.
    """
    cache = LRUPipelineCache(test_grr, 16)
    mocker.patch(
        "web_annotation.pipelines.views.UserPipeline.lru_cache", new=cache)

    params = {
        "config": ContentFile("- position_score: scores/NONEXISTENT"),
        "name": "deferred_validation_pipeline",
    }
    response = user_client.post("/api/pipelines/user", params)

    assert response.status_code == 200

    # The deep build is deferred to the background loader and fails there
    # (the resource is missing); drain that future so the worker thread does
    # not outlive the test.
    pipeline_id = response.json()["id"]
    with contextlib.suppress(Exception):
        cache._cache[pipeline_id].future.result(timeout=10)


@pytest.mark.django_db
def test_save_user_pipeline_rejects_malformed_yaml(
    user_client: Client,
    test_grr: GenomicResourceRepo,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """Structurally-broken config is still rejected synchronously (400).

    Deferring *resource* validation to the loader must not drop the cheap
    structural check: a config that is not even valid YAML never reaches the
    background loader and is rejected up front.
    """
    cache = LRUPipelineCache(test_grr, 16)
    mocker.patch(
        "web_annotation.pipelines.views.UserPipeline.lru_cache", new=cache)

    params = {
        "config": ContentFile("annotators: [unbalanced"),
        "name": "malformed_pipeline",
    }
    response = user_client.post("/api/pipelines/user", params)

    assert response.status_code == 400
    assert Pipeline.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize("config", ["   ", "# only a comment\n", ""])
def test_save_user_pipeline_accepts_empty_config(
    user_client: Client,
    test_grr: GenomicResourceRepo,
    mocker: pytest_mock.MockerFixture,
    config: str,
) -> None:
    """An empty / whitespace / comment-only pipeline saves (200).

    The app treats an empty config as a valid (empty) pipeline -- the
    /validate endpoint returns no error for "   " -- and the web_ui saves an
    empty temp pipeline whenever the editor is cleared (e.g. 'New pipeline').
    The cheap structural check must not reject these (#152 e2e regression).
    """
    cache = LRUPipelineCache(test_grr, 16)
    mocker.patch(
        "web_annotation.pipelines.views.UserPipeline.lru_cache", new=cache)

    response = user_client.post(
        "/api/pipelines/user", {"config": ContentFile(config, name="c.yaml")})

    assert response.status_code == 200


@pytest.mark.django_db
def test_background_load_failure_notifies_user(
    user_client: Client,
    test_grr: GenomicResourceRepo,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """A deferred load failure is surfaced to the user via the load path.

    Because resource validation is deferred (#150 H1), a config that cannot
    be built must not leave the client waiting on a 'loading' status forever:
    the background loader must notify the user when the load fails.
    """
    cache = LRUPipelineCache(test_grr, 16)
    # Run the deferred load inline + synchronously so the failure callback
    # fires deterministically within the request.
    cache._load_executor = SequentialTaskExecutor()
    mocker.patch(
        "web_annotation.pipelines.views.UserPipeline.lru_cache", new=cache)
    notify = mocker.patch.object(
        AnnotationBaseView, "_notify_user_pipeline")

    params = {
        "config": ContentFile("- position_score: scores/NONEXISTENT"),
        "name": "bad_resource_pipeline",
    }
    response = user_client.post("/api/pipelines/user", params)

    assert response.status_code == 200
    notified_statuses = [call.args[2] for call in notify.call_args_list]
    assert "failed" in notified_statuses


@pytest.mark.django_db
def test_background_load_failure_notifies_reason(
    user_client: Client,
    test_grr: GenomicResourceRepo,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """The 'failed' notification carries an actionable reason (#155).

    A bare 'unloaded'/'failed' status is indistinguishable from a delete and
    gives the user nothing to act on. The deferred-build failure must thread
    the formatted configuration error through the pipeline_status channel so
    the editor can show why the load failed.
    """
    cache = LRUPipelineCache(test_grr, 16)
    cache._load_executor = SequentialTaskExecutor()
    mocker.patch(
        "web_annotation.pipelines.views.UserPipeline.lru_cache", new=cache)
    notify = mocker.patch.object(
        AnnotationBaseView, "_notify_user_pipeline")

    response = user_client.post("/api/pipelines/user", {
        "config": ContentFile("- position_score: scores/NONEXISTENT"),
        "name": "bad_resource_pipeline",
    })

    assert response.status_code == 200
    failed_calls = [c for c in notify.call_args_list if c.args[2] == "failed"]
    assert failed_calls, "expected a 'failed' status notification"
    error = failed_calls[-1].kwargs.get("error")
    assert error
    assert "Invalid configuration" in error


@pytest.mark.django_db
def test_use_unbuildable_saved_pipeline_returns_4xx_not_500(
    user_client: Client,
    test_grr: GenomicResourceRepo,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """Consuming a saved-but-unbuildable pipeline yields a 4xx, not a 500.

    Resource validation is deferred (#150 H1), so an unresolvable config is
    saved (200). Using it later (here via the doc endpoint, which resolves the
    pipeline through get_pipeline) must surface a clean client error, not a
    500 from the deferred build exception leaking out.
    """
    cache = LRUPipelineCache(test_grr, 16)
    mocker.patch(
        "web_annotation.annotation_base_view.AnnotationBaseView.lru_cache",
        new=cache)

    save = user_client.post("/api/pipelines/user", {
        "config": ContentFile("- position_score: scores/NONEXISTENT"),
        "name": "broken_pipeline",
    })
    assert save.status_code == 200
    pipeline_id = save.json()["id"]

    # Wait for the deferred build to fail.
    with contextlib.suppress(Exception):
        cache._cache[pipeline_id].future.result(timeout=10)

    response = user_client.get(
        f"/api/pipelines/doc?pipeline_id={pipeline_id}")
    assert 400 <= response.status_code < 500


@pytest.mark.django_db
def test_use_pipeline_with_unsupported_annotator_returns_4xx(
    user_client: Client,
    test_grr: GenomicResourceRepo,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """A deferred build that fails on a bad config yields 4xx, not 500.

    Real-stack regression for an unsupported-annotator config (which the
    factory raises as AnnotationConfigurationError).
    """
    cache = LRUPipelineCache(test_grr, 16)
    mocker.patch(
        "web_annotation.annotation_base_view.AnnotationBaseView.lru_cache",
        new=cache)

    save = user_client.post("/api/pipelines/user", {
        "config": ContentFile("- not_a_real_annotator: scores/pos1"),
        "name": "bad_annotator_pipeline",
    })
    assert save.status_code == 200
    pipeline_id = save.json()["id"]
    with contextlib.suppress(Exception):
        cache._cache[pipeline_id].future.result(timeout=10)

    response = user_client.get(
        f"/api/pipelines/doc?pipeline_id={pipeline_id}")
    assert 400 <= response.status_code < 500


def test_get_pipeline_build_error_is_4xx_not_cache_miss_retry(
    mocker: pytest_mock.MockerFixture,
) -> None:
    """A build error (even a plain ValueError) is surfaced as a 4xx, not
    misclassified as a cache-miss and retried (#150 review).

    The cache-miss signal must be a distinct type so that a build ValueError
    is not conflated with it (which would re-run the expensive build up to the
    retry bound and then escape as a 500).
    """
    view = AnnotationBaseView()
    user = MagicMock()

    fake_cache = MagicMock()
    fake_cache.has_pipeline.return_value = True
    fake_cache.get_pipeline.side_effect = ValueError(
        "unsupported annotator type")
    mocker.patch.object(view, "lru_cache", fake_cache)
    put_spy = mocker.patch.object(view, "put_pipeline")

    with pytest.raises(ValidationError):
        view.get_pipeline("p", user)

    # Not retried as a cache-miss: built once, no reload.
    assert fake_cache.get_pipeline.call_count == 1
    assert not put_spy.called


@pytest.mark.django_db
def test_list_pipelines_reports_failed_load_with_reason(
    user_client: Client,
    test_grr: GenomicResourceRepo,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """A pipeline whose deferred build failed lists as 'failed' + reason (#155).

    The listing is the durable signal after a page refresh: a build that failed
    must read as a distinct 'failed' status carrying an actionable error, not a
    bare 'unloaded' indistinguishable from a never-loaded or deleted pipeline.
    """
    cache = LRUPipelineCache(test_grr, 16)
    mocker.patch(
        "web_annotation.annotation_base_view.AnnotationBaseView.lru_cache",
        new=cache)

    save = user_client.post("/api/pipelines/user", {
        "config": ContentFile("- position_score: scores/NONEXISTENT"),
        "name": "broken_pipeline",
    })
    assert save.status_code == 200
    pipeline_id = save.json()["id"]
    with contextlib.suppress(Exception):
        cache._cache[pipeline_id].future.result(timeout=10)

    pipelines = user_client.get("/api/pipelines").json()
    broken = next(p for p in pipelines if p["id"] == pipeline_id)
    assert broken["status"] == "failed"
    assert "Invalid configuration" in broken["error"]
    assert "NONEXISTENT" in broken["error"]


@pytest.mark.django_db
def test_list_pipeline_recovers_from_failed_to_loaded(
    user_client: Client,
    test_grr: GenomicResourceRepo,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """Re-saving a good config flips the listing 'failed' -> 'loaded' (#155).

    The durable failed status must not be sticky: once the config is fixed and
    the deferred build succeeds, the listing reports 'loaded' with no error.
    """
    cache = LRUPipelineCache(test_grr, 16)
    mocker.patch(
        "web_annotation.annotation_base_view.AnnotationBaseView.lru_cache",
        new=cache)

    save = user_client.post("/api/pipelines/user", {
        "config": ContentFile("- position_score: scores/NONEXISTENT"),
        "name": "recovering_pipeline",
    })
    pipeline_id = save.json()["id"]
    with contextlib.suppress(Exception):
        cache._cache[pipeline_id].future.result(timeout=10)
    broken = next(p for p in user_client.get("/api/pipelines").json()
                  if p["id"] == pipeline_id)
    assert broken["status"] == "failed"

    resave = user_client.post("/api/pipelines/user", {
        "id": pipeline_id,
        "config": ContentFile("- position_score: scores/pos1"),
        "name": "recovering_pipeline",
    })
    assert resave.status_code == 200
    cache._cache[pipeline_id].future.result(timeout=10)

    fixed = next(p for p in user_client.get("/api/pipelines").json()
                 if p["id"] == pipeline_id)
    assert fixed["status"] == "loaded"
    assert fixed["error"] is None


@pytest.mark.django_db
def test_resaving_identical_broken_config_does_not_notify_loaded(
    user_client: Client,
    test_grr: GenomicResourceRepo,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """Re-saving an identical broken config must not report 'loaded'.

    The same-config cache short-circuit in put_pipeline must not fire the
    success notification for a cached *failed* load (#150 H1 follow-up).
    """
    cache = LRUPipelineCache(test_grr, 16)
    mocker.patch(
        "web_annotation.annotation_base_view.AnnotationBaseView.lru_cache",
        new=cache)
    config = "- position_score: scores/NONEXISTENT"

    save = user_client.post("/api/pipelines/user", {
        "config": ContentFile(config), "name": "broken_pipeline",
    })
    assert save.status_code == 200
    pipeline_id = save.json()["id"]
    with contextlib.suppress(Exception):
        cache._cache[pipeline_id].future.result(timeout=10)

    # Re-save the identical config -> same-config short-circuit path.
    notify = mocker.patch.object(AnnotationBaseView, "_notify_user_pipeline")
    resave = user_client.post("/api/pipelines/user", {
        "id": pipeline_id,
        "config": ContentFile(config),
        "name": "broken_pipeline",
    })
    assert resave.status_code == 200
    statuses = [call.args[-1] for call in notify.call_args_list]
    assert "loaded" not in statuses


def test_pipeline_status_consumer_relays_error(
    mocker: pytest_mock.MockerFixture,
) -> None:
    """The websocket consumer forwards the failure reason to the client (#155).

    fail_load_callback puts an 'error' on the channel event; the consumer must
    relay it to the browser, otherwise the live failure reason is dropped at
    the socket boundary.
    """
    consumer = AnnotationStateConsumer()
    send = mocker.patch.object(consumer, "send")

    consumer.pipeline_status({
        "pipeline_id": "7",
        "status": "failed",
        "error": "Invalid configuration, reason: boom",
    })

    payload = json.loads(send.call_args.kwargs["text_data"])
    assert payload["status"] == "failed"
    assert payload["error"] == "Invalid configuration, reason: boom"
