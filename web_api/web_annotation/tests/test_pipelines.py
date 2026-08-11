# pylint: disable=W0621,C0114,C0116,W0212,W0613
import contextlib
import json
import logging
import textwrap
from unittest.mock import MagicMock

import pytest
import pytest_mock
from django.conf import settings
from django.core.files.base import ContentFile
from django.test import Client
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.resource_query import MAX_RESOURCE_QUERY_LENGTH
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.request import Request

from web_annotation.annotation_base_view import (
    AnnotationBaseView,
    AnnotationMixin,
)
from web_annotation.consumers import AnnotationStateConsumer
from web_annotation.executor import SequentialTaskExecutor
from web_annotation.models import Pipeline, User
from web_annotation.pipeline_cache import LRUPipelineCache, PipelineNotCached
from web_annotation.pipelines.throttling import PipelineValidationRateThrottle
from web_annotation.pipelines.views import PipelineValidation
from web_annotation.single_allele_annotation.throttling import (
    AnnotateUserRateThrottle,
)


# ``PipelineDoc.get`` is async as of #167; the async-native round-trip is
# covered in ``test_pipelines_doc_async.py``. These sync-Client tests are
# retained on purpose: they exercise the converted async view through Django's
# *synchronous* test Client (a real production path -- a sync caller hitting an
# async view), and assert the HttpResponse/Response paths are unchanged.
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
@pytest.mark.parametrize("forged", [
    # A wildcard payload -- the failure is raised by the config parser.
    '- position_score: "*\\nERROR 2026-08-04 forged.module: forged record"',
    # A minimal-form payload whose annotator type does not exist -- the
    # failure is raised deep in the pipeline *build*, not the parser, and
    # reaches the log sinks by a different route (the tail this issue's
    # acceptance names).
    '- "no_such_annotator\\nERROR 2026-08-04 forged.module: forged record"',
])
def test_anonymous_pipeline_save_cannot_forge_a_log_record(
    anonymous_client: Client,
    test_grr: GenomicResourceRepo,
    mocker: pytest_mock.MockerFixture,
    caplog: pytest.LogCaptureFixture,
    forged: str,
) -> None:
    """A config body's newlines must not survive into any log record.

    An anonymous caller may save a temporary pipeline, and a structurally
    valid config still fails deep validation on the background loader.
    That failure is logged twice -- at ERROR by the executor and at
    WARNING by the loader's fail callback -- so the assertion spans both
    levels; a ``\\n`` in the config would otherwise emit a second,
    fully-formed-looking record of the caller's choosing
    (iossifovlab/gain#655).
    """
    cache = LRUPipelineCache(test_grr, 16)
    mocker.patch(
        "web_annotation.pipelines.views.UserPipeline.lru_cache", new=cache)

    params = {"config": ContentFile(forged)}

    with caplog.at_level(logging.WARNING):
        response = anonymous_client.post("/api/pipelines/user", params)
        assert response.status_code == 200

        # Both records are emitted by the loader's done-callback, which
        # runs on the worker thread *after* the future resolves. wait_all
        # blocks until the executor's pending set is empty, and the
        # callback empties it last of all -- after logging -- so this is
        # the sync point, not Future.result().
        cache._load_executor.wait_all(10.0)

    forged_messages = [
        record.getMessage()
        for record in caplog.records
        if "forged record" in record.getMessage()
    ]
    # The payload does reach the log -- escaped onto one line. An empty
    # list here would mean the test lost the path to the sink, not that
    # the sink is safe.
    assert forged_messages
    assert all("\n" not in message for message in forged_messages)


@pytest.mark.django_db
def test_anonymous_pipeline_save_rejects_a_value_transform_rce(
    anonymous_client: Client,
    test_grr: GenomicResourceRepo,
    mocker: pytest_mock.MockerFixture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An anonymous temporary save whose value_transform calls arbitrary
    Python fails the background build instead of executing it (gain#764).

    The temporary-save path builds the pipeline on the background loader, so
    the gate's rejection surfaces as a logged build failure rather than a
    non-200 response.
    """
    cache = LRUPipelineCache(test_grr, 16)
    mocker.patch(
        "web_annotation.pipelines.views.UserPipeline.lru_cache", new=cache)

    config = (
        "- position_score:\n"
        "    attributes:\n"
        "      - source: pos1\n"
        "        value_transform: \"__import__('os').system('id') or value\"\n"
        "    resource_id: scores/pos1\n"
    )
    params = {"config": ContentFile(config)}

    with caplog.at_level(logging.WARNING):
        response = anonymous_client.post("/api/pipelines/user", params)
        assert response.status_code == 200
        cache._load_executor.wait_all(10.0)

    messages = [record.getMessage() for record in caplog.records]
    assert any("disallowed" in message for message in messages), messages


@pytest.mark.django_db
def test_deferred_build_error_is_escaped_before_it_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The re-logged build error must not carry caller newlines either.

    ``_build_error_to_drf`` runs on every later fetch of an unbuildable
    saved pipeline, so an un-escaped forged config would forge a record
    per fetch, not once (iossifovlab/gain#655).
    """
    forged = ValueError(
        "unsupported annotator type: no_such\n"
        "ERROR 2026-08-04 forged.module: forged record")

    with caplog.at_level(logging.WARNING):
        AnnotationMixin._build_error_to_drf(forged)

    forged_messages = [
        record.getMessage()
        for record in caplog.records
        if "forged record" in record.getMessage()
    ]
    assert forged_messages
    assert all("\n" not in message for message in forged_messages)


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
    # PipelineDoc is async (#167) and extends AsyncAnnotationBaseView, while the
    # save view (UserPipeline) is sync. Patch the shared owner AnnotationMixin
    # so BOTH the sync save-path and the async doc-path resolve to this one
    # fixture cache (the single-shared-cache invariant); patching only
    # AnnotationBaseView would leave the async doc view reading the real cache.
    mocker.patch.object(AnnotationMixin, "lru_cache", new=cache)

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
    # PipelineDoc is async (#167); patch the shared AnnotationMixin so both the
    # sync save-path and the async doc-path use this fixture cache (see the
    # companion test above for the rationale).
    mocker.patch.object(AnnotationMixin, "lru_cache", new=cache)

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


# The validation endpoint is anonymous and takes a free-form config body, so
# it is the widest untrusted surface in the API (iossifovlab/gain#635). The
# tests below pin the bounds that keep one request cheap. They deliberately
# assert on status codes and messages rather than on wall-clock time: a
# timing assertion would be flaky in CI, and the bound is observable without
# one.
@pytest.mark.django_db
def test_validate_refuses_an_overlong_resource_query(
    anonymous_client: Client,
) -> None:
    """An unbounded wildcard here was ~900s of CPU for a single POST.

    A too-long query is an invalid *config*, not a malformed *request*, so
    it keeps the endpoint's 200 + ``errors`` contract -- the same shape the
    deferred-load failure path reports.

    One character over the bound rather than the kilobytes that made the
    original report: without the bound, those would hang this test for
    minutes rather than fail it.
    """
    wildcard = "*" + "a" * MAX_RESOURCE_QUERY_LENGTH

    response = anonymous_client.post(
        "/api/pipelines/validate",
        {"config": f"- position_score: '{wildcard}'"},
    )

    assert response.status_code == 200
    assert "too long" in response.json()["errors"]


@pytest.mark.django_db
def test_validate_refuses_an_oversized_config_body(
    anonymous_client: Client,
) -> None:
    """Django's default body limit is 2.5 MB -- far too much to hand a parser.

    Unlike a bad query, an oversized body is a malformed *request*: it is
    refused with 400 before the config is parsed at all.
    """
    oversized = "#" * (PipelineValidation.MAX_CONFIG_LENGTH + 1)

    response = anonymous_client.post(
        "/api/pipelines/validate",
        {"config": oversized},
    )

    assert response.status_code == 400
    assert str(PipelineValidation.MAX_CONFIG_LENGTH) in response.json()["error"]


@pytest.mark.django_db
def test_validate_accepts_a_config_exactly_at_the_size_limit(
    anonymous_client: Client,
) -> None:
    """The refusal is for bodies *over* the limit, so one at it still runs."""
    at_limit = "#" * PipelineValidation.MAX_CONFIG_LENGTH

    response = anonymous_client.post(
        "/api/pipelines/validate",
        {"config": at_limit},
    )

    assert response.status_code == 200


@pytest.mark.django_db
def test_validate_refuses_an_oversized_body_without_parsing_it(
    anonymous_client: Client,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """The parse is the work being bounded, so it must not run (#676).

    ``MAX_CONFIG_LENGTH`` bounds the config *string*, which does not exist
    until the body has been parsed -- so it cannot be what refuses an
    oversized body. Asserting the status alone would not show the
    difference, because a 400 comes back either way; what is pinned here is
    that DRF never parsed the body at all. ``Request.data`` is the only
    route to ``_load_data_and_files``, so a call to it means the parse ran.
    """
    parse = mocker.patch.object(
        Request, "_load_data_and_files",
        side_effect=AssertionError("the body was parsed"),
    )
    oversized = "#" * (PipelineValidation.MAX_BODY_LENGTH + 1)

    response = anonymous_client.post(
        "/api/pipelines/validate",
        {"config": oversized},
    )

    assert response.status_code == 400
    assert str(PipelineValidation.MAX_BODY_LENGTH) in response.json()["error"]
    parse.assert_not_called()


@pytest.mark.django_db
def test_validate_accepts_a_config_at_the_config_limit_through_the_body_bound(
    anonymous_client: Client,
) -> None:
    """The body bound must not clip a config the config bound still allows.

    The two limits are in different units -- one counts characters of
    ``config``, the other bytes of the encoded request -- so the body bound
    has to leave room for whatever the encoding adds around a config sitting
    exactly at ``MAX_CONFIG_LENGTH``. If it does not, the config bound
    becomes unreachable and its at-limit case starts answering the wrong
    refusal.
    """
    at_limit = "#" * PipelineValidation.MAX_CONFIG_LENGTH

    response = anonymous_client.post(
        "/api/pipelines/validate",
        {"config": at_limit},
    )

    assert response.status_code == 200
    assert "too large" not in response.json().get("error", "")


@pytest.mark.django_db
def test_validate_refuses_a_body_that_declares_no_length(
    anonymous_client: Client,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """An undeclared size is refused rather than waved through (#676).

    A bound read off ``CONTENT_LENGTH`` is only a bound if a request cannot
    opt out of it by omitting the header -- chunked transfer encoding sends
    no ``Content-Length``, and this route is anonymous, so waving those
    through would leave the parse reachable unbounded by exactly the client
    that meant to. No caller of this endpoint streams: the editor posts a
    small JSON body with a length. Refusing is therefore the cheap side of
    the trade, and it keeps the refusal ahead of the parse.
    """
    parse = mocker.patch.object(
        Request, "_load_data_and_files",
        side_effect=AssertionError("the body was parsed"),
    )

    response = anonymous_client.post(
        "/api/pipelines/validate",
        {"config": "- position_score: scores/pos1"},
        CONTENT_LENGTH=None,
    )

    assert response.status_code == 400
    assert "length" in response.json()["error"].lower()
    parse.assert_not_called()


@pytest.mark.django_db
def test_validate_refuses_a_config_declaring_too_many_annotators(
    anonymous_client: Client,
) -> None:
    """Many short wildcards cost the same as one long query.

    Each wildcard annotator scans every resource in the GRR, so bounding
    the query length alone leaves the amplification open: a body full of
    individually-legal wildcards buys the same worker time. The count is
    refused before any resource is resolved.
    """
    config = "- position_score: 'score_*'\n" * (
        PipelineValidation.MAX_ANNOTATORS + 1)

    response = anonymous_client.post(
        "/api/pipelines/validate",
        {"config": config},
    )

    assert response.status_code == 400
    assert str(PipelineValidation.MAX_ANNOTATORS) in response.json()["error"]


@pytest.mark.django_db
def test_validate_accepts_a_config_at_the_annotator_limit(
    anonymous_client: Client,
) -> None:
    """The cap must not clip a config anyone would really write.

    The largest config in this repo declares 32 annotators; the limit sits
    well above that, and a config sitting exactly on it is still validated
    rather than refused.

    A bare ``200`` would not say much -- this endpoint answers 200 for
    invalid configs too, and this one is invalid (repeated attributes). What
    is asserted is that whatever it objects to, it is not the cap.
    """
    config = "- position_score: 'score_one'\n" * (
        PipelineValidation.MAX_ANNOTATORS)

    response = anonymous_client.post(
        "/api/pipelines/validate",
        {"config": config},
    )

    assert response.status_code == 200
    assert "too many annotators" not in response.json().get("errors", "")


@pytest.mark.django_db
def test_validate_is_rate_limited(
    anonymous_client: Client,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """A backstop under the per-request bounds, on its own generous scope.

    The rate is lowered here rather than exercised at its configured value:
    the configured rate is deliberately beyond what a human at a keyboard
    can reach, which is exactly what makes it impractical to reach from a
    test.

    Patching ``THROTTLE_RATES`` rather than ``settings.REST_FRAMEWORK`` is
    not incidental. DRF binds ``SimpleRateThrottle.THROTTLE_RATES`` to the
    settings dict once, at import; reloading the settings rebinds
    ``api_settings`` but leaves the class attribute pointing at the original
    dict, so an override through the settings fixture is read back correctly
    and still never reaches the throttle instance.
    """
    mocker.patch.dict(
        PipelineValidationRateThrottle.THROTTLE_RATES,
        {"pipeline_validate": "2/minute"},
    )
    config = {"config": "- position_score: scores/pos1"}

    first = anonymous_client.post("/api/pipelines/validate", config)
    second = anonymous_client.post("/api/pipelines/validate", config)
    third = anonymous_client.post("/api/pipelines/validate", config)

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429


@pytest.mark.django_db
def test_validate_does_not_share_the_annotate_budget(
    anonymous_client: Client,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """The two endpoints must not draw on one bucket.

    Annotate is budgeted for expensive work; the editor validates at a
    keystroke cadence. Sharing a bucket means whichever runs first starves
    the other -- a user editing a pipeline would exhaust the annotate quota
    in seconds.

    Both rates are driven to their floor and the annotate bucket is then
    emptied, so that a shared bucket would be visibly empty by the time
    validate is called. Asserting that validate merely answers 200 some
    number of times would not discriminate: it does that with no throttle
    at all.
    """
    mocker.patch.dict(
        AnnotateUserRateThrottle.THROTTLE_RATES,
        {"annotate": "1/minute", "pipeline_validate": "1/minute"},
    )

    # Exhaust the annotate bucket.
    annotate = {
        "pipeline_id": "pipeline/test_pipeline",
        "annotatable": {"chrom": "chr1", "pos": 1, "ref": "C", "alt": "A"},
    }
    first_annotate = anonymous_client.post(
        "/api/single_allele/annotate", annotate,
        content_type="application/json")
    second_annotate = anonymous_client.post(
        "/api/single_allele/annotate", annotate,
        content_type="application/json")

    validate = anonymous_client.post(
        "/api/pipelines/validate",
        {"config": "- position_score: scores/pos1"},
    )

    assert first_annotate.status_code != 429
    assert second_annotate.status_code == 429
    assert validate.status_code != 429


@pytest.mark.django_db
def test_validate_refuses_a_request_with_no_config(
    anonymous_client: Client,
) -> None:
    """A missing ``config`` is a bad request, not a server fault.

    It used to reach an ``assert isinstance(content, str)``, i.e. an
    AssertionError and a 500 on an anonymous endpoint. Bounding the body
    means reading it before the assert did, so the assert had to become a
    real response.
    """
    response = anonymous_client.post("/api/pipelines/validate", {})

    assert response.status_code == 400


@pytest.mark.django_db
def test_validate_survives_deeply_nested_yaml(
    anonymous_client: Client,
) -> None:
    """Nesting deep enough to exhaust the stack is an invalid config, not a
    server fault. ``yaml.safe_load`` signals it with ``RecursionError``,
    which is not a ``YAMLError``, so counting annotators before the view's
    own error handling has to catch it too.
    """
    response = anonymous_client.post(
        "/api/pipelines/validate",
        {"config": "[" * 2000 + "]" * 2000},
    )

    assert response.status_code == 200
    assert response.json()["errors"]


@pytest.mark.django_db
@pytest.mark.parametrize("body", ["[]", '"x"', "3"])
def test_validate_refuses_a_non_object_json_body(
    anonymous_client: Client,
    body: str,
) -> None:
    """A JSON body that is not an object is a bad request, not a 500.

    The handler asserted its way through ``request.data`` being a dict;
    a three-byte body was enough to raise AssertionError on an anonymous
    endpoint.
    """
    response = anonymous_client.post(
        "/api/pipelines/validate",
        body,
        content_type="application/json",
    )

    assert response.status_code == 400


@pytest.mark.django_db
def test_validate_refuses_a_config_that_expands_past_the_build_limit(
    anonymous_client: Client,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """Counting *declared* annotators does not bound the work.

    Every declared wildcard expands to as many annotators as it matches --
    up to WILDCARD_LIMIT (500) each -- and each expanded annotator is then
    built against the GRR. So a config well inside the declared cap can
    still ask for orders of magnitude more work than it looks like. The
    expansion is what has to be bounded, and it has to be bounded before
    the build.

    The limit is lowered here because the test GRR is too small to reach
    the configured one; what matters is that expansion, not declaration,
    is what the refusal counts.
    """
    mocker.patch.object(PipelineValidation, "MAX_EXPANDED_ANNOTATORS", 4)
    build = mocker.patch(
        "web_annotation.pipelines.views.load_pipeline_from_yaml")

    # Three declared annotators, each a wildcard matching every score in
    # the fixture GRR -- well inside MAX_ANNOTATORS, far past the expansion
    # limit set above.
    response = anonymous_client.post(
        "/api/pipelines/validate",
        {"config": "- position_score: '*'\n" * 3},
    )

    assert response.status_code == 400
    assert "4" in response.json()["error"]
    # Refused before the expensive half: nothing was built.
    build.assert_not_called()
