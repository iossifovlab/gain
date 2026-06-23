# pylint: disable=W0201
import json
from typing import Any, cast

from asgiref.sync import async_to_sync
from channels.generic.websocket import WebsocketConsumer
from channels.layers import InMemoryChannelLayer

from web_annotation.models import (
    BaseUser,
    Pipeline,
    TemporaryPipeline,
    User,
)


class AnnotationStateConsumer(WebsocketConsumer):
    """Web socket consumer made for notifying users of job progress."""

    def get_user(self) -> User:
        assert "user" in self.scope, "User not found in scope"
        user = cast(User, self.scope["user"])
        assert user is not None, "User is None in scope"
        return user

    def connect(self) -> None:
        user = self.get_user()
        self.user_id = user.get_socket_group()
        async_to_sync(self.channel_layer.group_add)(
            self.user_id, self.channel_name)
        async_to_sync(self.channel_layer.group_add)(
            "global", self.channel_name)
        self.accept()
        self._resync_pipeline_status(user)

    def _resync_pipeline_status(self, user: User | BaseUser) -> None:
        """Re-send the current load status of the user's pipelines on connect.

        The ``loaded`` group_send fired by the deferred loader is a one-shot
        transition over an in-memory (no-replay) channel layer: a socket that
        is between reconnects at that instant misses it permanently and the
        editor stays stuck on ``loading`` (iossifovlab/gain#160). Replaying the
        current status when the socket (re)connects makes the editor converge
        regardless of WS churn, instead of relying on catching the transition.

        Only pipelines present in the cache emit a frame -- a never-loaded
        pipeline (and a user with no pipelines) sends nothing, so the editor is
        not driven by a spurious status. The frame shape matches
        ``pipeline_status`` exactly so the existing client handler parses it.
        """
        for pipeline_id in self._owned_pipeline_ids(user):
            status = self._pipeline_status_for(pipeline_id)
            if status is None:
                continue
            self.pipeline_status({
                "pipeline_id": pipeline_id,
                "status": status,
            })

    def _owned_pipeline_ids(self, user: User | BaseUser) -> list[str]:
        """Enumerate the cache ids of the connecting user's pipelines.

        Covers the temporary editor pipeline (keyed by the session, the case
        the editor's ``loaded-editor`` state depends on) and, for an
        authenticated user, their saved pipelines. The connecting scope user is
        a bare ``User`` or ``WebAnnotationAnonymousUser`` (not a UserWrapper),
        so saved pipelines are read straight from the table by owner; the
        temporary pipeline is resolved from the WS session because the scope
        ``User`` carries no session id.
        """
        pipeline_ids: list[str] = []

        session = self.scope.get("session")
        session_key = getattr(session, "session_key", None)
        if session_key is not None:
            temporary = TemporaryPipeline.objects.filter(
                session_id=session_key).first()
            if temporary is not None:
                pipeline_ids.append(str(temporary.id))

        if isinstance(user, User) and user.is_authenticated:
            pipeline_ids.extend(
                str(pipeline.pk)
                for pipeline in Pipeline.objects.filter(owner=user)
            )

        return pipeline_ids

    def _pipeline_status_for(self, pipeline_id: str) -> str | None:
        """Map a pipeline's cache state to a status frame value, or None.

        Reuses the cache's own loaded/failed/loading notions (the same source
        the listing and notifications use) rather than inventing a parallel
        one. Returns None when the pipeline is not cached at all, so no
        ``unloaded`` frame is emitted on connect.
        """
        # Deferred import: annotation_base_view builds the GRR at module load
        # (a module-level build_genomic_resource_repository scan). Importing it
        # at the top of this module -- which urls/asgi import eagerly -- would
        # force that expensive scan on every consumer import. Importing it
        # lazily here reuses the single shared LRUPipelineCache the views
        # already built.
        # pylint: disable=import-outside-toplevel
        from web_annotation.annotation_base_view import AnnotationBaseView
        cache = AnnotationBaseView.lru_cache
        if not cache.has_pipeline(pipeline_id):
            return None
        if cache.is_pipeline_loaded(pipeline_id):
            return "loaded"
        if cache.get_pipeline_error(pipeline_id) is not None:
            return "failed"
        return "loading"

    def disconnect(self, _code: Any) -> None:
        async_to_sync(self.channel_layer.group_discard)(
            self.user_id, self.channel_name)

        user = self.get_user()
        channel_count = len(
            cast(
                InMemoryChannelLayer,
                self.channel_layer,
            ).groups.get(self.user_id, {}),
        )
        if not user.is_authenticated and channel_count == 0:
            user.delete_jobs()
            user.delete_pipelines()

    def annotation_notify(self, event: Any) -> None:
        self.send(
            text_data=json.dumps({"message": event["message"]}),
        )

    def pipeline_status(self, event: Any) -> None:
        """Relay a pipeline load status to the client, with any error reason."""
        payload = {
            "type": "pipeline_status",
            "pipeline_id": event["pipeline_id"],
            "status": event["status"],
        }
        error = event.get("error")
        if error is not None:
            payload["error"] = error
        self.send(text_data=json.dumps(payload))

    def job_status(self, event: Any) -> None:
        self.send(
            text_data=json.dumps({
                "type": "job_status",
                "job_id": event["job_id"],
                "status": event["status"],
            }),
        )
