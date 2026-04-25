# pylint: disable=W0201
import json
from typing import Any, cast
from asgiref.sync import async_to_sync
from channels.generic.websocket import WebsocketConsumer
from channels.layers import InMemoryChannelLayer

from web_annotation.models import User


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

    def disconnect(self, code: Any) -> None:
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
            text_data=json.dumps({"message": event["message"]})
        )

    def pipeline_status(self, event: Any) -> None:
        self.send(
            text_data=json.dumps({
                "type": "pipeline_status",
                "pipeline_id": event["pipeline_id"],
                "status": event["status"],
            })
        )

    def job_status(self, event: Any) -> None:
        self.send(
            text_data=json.dumps({
                "type": "job_status",
                "job_id": event["job_id"],
                "status": event["status"],
            })
        )
