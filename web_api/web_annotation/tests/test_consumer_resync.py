# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pathlib

import pytest
import pytest_mock
from asgiref.sync import sync_to_async
from channels.auth import UserLazyObject
from django.conf import settings
from django.test import Client
from gain.annotation.annotation_config import AnnotationConfigurationError
from gain.genomic_resources.repository import GenomicResourceRepo

from web_annotation.consumers import AnnotationStateConsumer
from web_annotation.models import (
    Pipeline,
    TemporaryPipeline,
    User,
    WebAnnotationAnonymousUser,
)
from web_annotation.pipeline_cache import LRUPipelineCache
from web_annotation.testing import CustomWebsocketCommunicator


@pytest.fixture
def patched_lru_cache(
    mocker: pytest_mock.MockerFixture,
    test_grr: GenomicResourceRepo,
) -> LRUPipelineCache:
    """Swap the shared view cache for an isolated one over the test GRR."""
    cache = LRUPipelineCache(test_grr, 16)
    mocker.patch(
        "web_annotation.annotation_base_view.AnnotationBaseView.lru_cache",
        new=cache,
    )
    return cache


def _write_saved_pipeline(
    user: User, name: str, config: str,
) -> Pipeline:
    config_dir = pathlib.Path(
        settings.ANNOTATION_CONFIG_STORAGE_DIR, user.identifier)
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / f"{name}.yaml"
    config_path.write_text(config, encoding="utf-8")
    return Pipeline.objects.create(
        name=name, config_path=str(config_path), owner=user)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_connect_resyncs_loaded_saved_pipeline(
    patched_lru_cache: LRUPipelineCache,
) -> None:
    """A client connecting after the one-shot 'loaded' fired still re-syncs.

    The consumer must re-send the current load status of the user's pipelines
    on connect, so a socket that was not connected at the instant the pipeline
    transitioned to 'loaded' receives the status immediately on (re)connect.
    """
    user = await sync_to_async(User.objects.get)(email="user@example.com")
    pipeline = await sync_to_async(_write_saved_pipeline)(
        user, "resync-pipe", "- position_score: scores/pos1")

    # Drive the pipeline to a finished, loaded state in the cache *before*
    # the websocket connects (the missed-transition scenario).
    patched_lru_cache.put_pipeline(
        str(pipeline.pk), "- position_score: scores/pos1")
    await sync_to_async(patched_lru_cache.get_pipeline)(str(pipeline.pk))

    communicator = CustomWebsocketCommunicator(
        AnnotationStateConsumer.as_asgi(), "/ws/test/", user=user)
    connected, _ = await communicator.connect(timeout=1000)
    assert connected

    output = await communicator.receive_json_from(timeout=5)
    assert output == {
        "type": "pipeline_status",
        "pipeline_id": str(pipeline.pk),
        "status": "loaded",
    }

    await communicator.disconnect(timeout=1000)


class FakeCache:
    """Minimal cache stand-in exposing only the status query surface.

    Lets the consumer's status-mapping behaviour be exercised deterministically
    for the loading/failed states, which are awkward to pin down with the real
    threaded loader's timing.
    """

    def __init__(
        self,
        *,
        present: bool = True,
        loaded: bool = False,
        error: BaseException | None = None,
    ) -> None:
        self._present = present
        self._loaded = loaded
        self._error = error

    def has_pipeline(self, _pipeline_id: str) -> bool:
        return self._present

    def is_pipeline_loaded(self, _pipeline_id: str) -> bool:
        return self._loaded

    def get_pipeline_error(self, _pipeline_id: str) -> BaseException | None:
        return self._error


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_connect_resyncs_loading_saved_pipeline(
    mocker: pytest_mock.MockerFixture,
) -> None:
    """A still-building pipeline re-syncs as 'loading', not blocking connect."""
    mocker.patch(
        "web_annotation.annotation_base_view.AnnotationBaseView.lru_cache",
        new=FakeCache(present=True, loaded=False, error=None),
    )
    user = await sync_to_async(User.objects.get)(email="user@example.com")
    pipeline = await sync_to_async(_write_saved_pipeline)(
        user, "loading-pipe", "- position_score: scores/pos1")

    communicator = CustomWebsocketCommunicator(
        AnnotationStateConsumer.as_asgi(), "/ws/test/", user=user)
    connected, _ = await communicator.connect(timeout=1000)
    assert connected

    output = await communicator.receive_json_from(timeout=5)
    assert output == {
        "type": "pipeline_status",
        "pipeline_id": str(pipeline.pk),
        "status": "loading",
    }

    await communicator.disconnect(timeout=1000)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_connect_resyncs_failed_saved_pipeline(
    mocker: pytest_mock.MockerFixture,
) -> None:
    """A finished-but-failed deferred build re-syncs as 'failed' with reason.

    Parity with the live fail path (#155) and the HTTP listing: the resync
    frame must carry the actionable failure reason, not just the bare 'failed'
    status (which would leave the editor showing an empty error box).
    """
    mocker.patch(
        "web_annotation.annotation_base_view.AnnotationBaseView.lru_cache",
        new=FakeCache(
            present=True, loaded=False,
            error=AnnotationConfigurationError("bad config")),
    )
    user = await sync_to_async(User.objects.get)(email="user@example.com")
    pipeline = await sync_to_async(_write_saved_pipeline)(
        user, "failed-pipe", "- position_score: scores/pos1")

    communicator = CustomWebsocketCommunicator(
        AnnotationStateConsumer.as_asgi(), "/ws/test/", user=user)
    connected, _ = await communicator.connect(timeout=1000)
    assert connected

    output = await communicator.receive_json_from(timeout=5)
    assert output == {
        "type": "pipeline_status",
        "pipeline_id": str(pipeline.pk),
        "status": "failed",
        "error": "Invalid configuration, reason: bad config",
    }

    await communicator.disconnect(timeout=1000)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_connect_sends_nothing_for_uncached_pipeline(
    mocker: pytest_mock.MockerFixture,
) -> None:
    """A never-loaded pipeline emits no frame (no spurious 'unloaded')."""
    mocker.patch(
        "web_annotation.annotation_base_view.AnnotationBaseView.lru_cache",
        new=FakeCache(present=False),
    )
    user = await sync_to_async(User.objects.get)(email="user@example.com")
    await sync_to_async(_write_saved_pipeline)(
        user, "uncached-pipe", "- position_score: scores/pos1")

    communicator = CustomWebsocketCommunicator(
        AnnotationStateConsumer.as_asgi(), "/ws/test/", user=user)
    connected, _ = await communicator.connect(timeout=1000)
    assert connected

    assert await communicator.receive_nothing(timeout=1) is True

    await communicator.disconnect(timeout=1000)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_connect_sends_nothing_for_user_without_pipelines(
    patched_lru_cache: LRUPipelineCache,
) -> None:
    """A user with no pipelines connects cleanly and emits no frame."""
    user = await sync_to_async(User.objects.get)(email="user@example.com")

    communicator = CustomWebsocketCommunicator(
        AnnotationStateConsumer.as_asgi(), "/ws/test/", user=user)
    connected, _ = await communicator.connect(timeout=1000)
    assert connected

    assert await communicator.receive_nothing(timeout=1) is True

    await communicator.disconnect(timeout=1000)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_connect_resyncs_anonymous_temporary_pipeline(
    anonymous_client: Client,
    patched_lru_cache: LRUPipelineCache,
) -> None:
    """The editor-critical path: an anonymous user's temporary pipeline.

    The temporary pipeline is keyed by the session, and the connecting scope
    user carries no session id, so it is resolved from the WS session. A loaded
    temporary pipeline must re-sync on (re)connect -- this is the exact state
    the editor's ``loaded-editor`` depends on.
    """
    session = await anonymous_client.asession()
    assert session.session_key is not None
    user = WebAnnotationAnonymousUser(session.session_key, ip="test")

    config_dir = pathlib.Path(
        settings.ANNOTATION_CONFIG_STORAGE_DIR, "temporary")
    await sync_to_async(config_dir.mkdir)(parents=True, exist_ok=True)
    config_path = config_dir / "pipeline-temp.yaml"
    await sync_to_async(config_path.write_text)(
        "- position_score: scores/pos1", encoding="utf-8")
    temporary = await sync_to_async(TemporaryPipeline.objects.create)(
        session_id=session.session_key,
        name="pipeline-temp.yaml",
        config_path=str(config_path),
    )

    patched_lru_cache.put_pipeline(
        str(temporary.id), "- position_score: scores/pos1")
    await sync_to_async(patched_lru_cache.get_pipeline)(str(temporary.id))

    communicator = CustomWebsocketCommunicator(
        AnnotationStateConsumer.as_asgi(),
        "/ws/test/", user=user, session=session,
    )
    connected, _ = await communicator.connect(timeout=1000)
    assert connected

    output = await communicator.receive_json_from(timeout=5)
    assert output == {
        "type": "pipeline_status",
        "pipeline_id": str(temporary.id),
        "status": "loaded",
    }

    await communicator.disconnect(timeout=1000)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_connect_resyncs_with_lazy_wrapped_scope_user(
    patched_lru_cache: LRUPipelineCache,
) -> None:
    """Resync still works when the scope user is a UserLazyObject.

    Production wraps ``scope["user"]`` in ``channels.auth.UserLazyObject``
    (see asgi.py's AnonymousAuthMiddleware) -- the unit tests otherwise pass a
    bare ``User``. This guards the ``isinstance(user, User)`` branch (which
    gates saved-pipeline resync) against a future regression: Django's
    LazyObject proxies ``__class__`` so the isinstance check must still hold
    through the wrapper.
    """
    user = await sync_to_async(User.objects.get)(email="user@example.com")
    pipeline = await sync_to_async(_write_saved_pipeline)(
        user, "lazy-pipe", "- position_score: scores/pos1")

    patched_lru_cache.put_pipeline(
        str(pipeline.pk), "- position_score: scores/pos1")
    await sync_to_async(patched_lru_cache.get_pipeline)(str(pipeline.pk))

    lazy_user = UserLazyObject()
    lazy_user._wrapped = user

    communicator = CustomWebsocketCommunicator(
        AnnotationStateConsumer.as_asgi(), "/ws/test/", user=lazy_user)
    connected, _ = await communicator.connect(timeout=1000)
    assert connected

    output = await communicator.receive_json_from(timeout=5)
    assert output == {
        "type": "pipeline_status",
        "pipeline_id": str(pipeline.pk),
        "status": "loaded",
    }

    await communicator.disconnect(timeout=1000)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_connect_resyncs_multiple_saved_pipelines_mixed_cache(
    patched_lru_cache: LRUPipelineCache,
) -> None:
    """Multiple saved pipelines: a frame per cached one, none for uncached.

    Pins both the per-connect fan-out (every owned pipeline is considered) and
    the per-id filtering (only cached pipelines emit, with the correct id and
    status), so neither can silently regress.
    """
    user = await sync_to_async(User.objects.get)(email="user@example.com")
    cached_a = await sync_to_async(_write_saved_pipeline)(
        user, "cached-a", "- position_score: scores/pos1")
    cached_b = await sync_to_async(_write_saved_pipeline)(
        user, "cached-b", "- position_score: scores/pos2")
    uncached = await sync_to_async(_write_saved_pipeline)(
        user, "uncached", "- position_score: scores/pos1")

    for pipeline, config in (
        (cached_a, "- position_score: scores/pos1"),
        (cached_b, "- position_score: scores/pos2"),
    ):
        patched_lru_cache.put_pipeline(str(pipeline.pk), config)
        await sync_to_async(patched_lru_cache.get_pipeline)(str(pipeline.pk))

    communicator = CustomWebsocketCommunicator(
        AnnotationStateConsumer.as_asgi(), "/ws/test/", user=user)
    connected, _ = await communicator.connect(timeout=1000)
    assert connected

    received = [
        await communicator.receive_json_from(timeout=5) for _ in range(2)
    ]

    assert await communicator.receive_nothing(timeout=1) is True

    by_id = {frame["pipeline_id"]: frame for frame in received}
    assert by_id == {
        str(cached_a.pk): {
            "type": "pipeline_status",
            "pipeline_id": str(cached_a.pk),
            "status": "loaded",
        },
        str(cached_b.pk): {
            "type": "pipeline_status",
            "pipeline_id": str(cached_b.pk),
            "status": "loaded",
        },
    }
    assert str(uncached.pk) not in by_id

    await communicator.disconnect(timeout=1000)
