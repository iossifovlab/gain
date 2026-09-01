"""
ASGI config for gpf_web_annotation project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os
from typing import Any, cast

from channels.auth import (
    AuthMiddlewareStack,
    UserLazyObject,
    get_user,
)
from channels.middleware import BaseMiddleware
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE", "web_annotation.settings")

django_asgi_app = get_asgi_application()

# pylint: disable=wrong-import-position
# These two imports must stay below `get_asgi_application()`: importing the
# models (and the URL conf that pulls them in) before the app registry is
# populated raises AppRegistryNotReady.  Ruff 0.16's suppression migration
# reformatted these to multi-line and dropped the trailing E402 directive
# along the way, so the suppression is spelled out on the statement here.
from web_annotation.models import (  # ruff: ignore[module-import-not-at-top-of-file]
    WebAnnotationAnonymousUser,
)
from web_annotation.urls import (  # ruff: ignore[module-import-not-at-top-of-file]
    websocket_urlpatterns,
)


class AnonymousAuthMiddleware(BaseMiddleware):
    """
    Middleware which populates scope["user"] from a Django session.
    Requires SessionMiddleware to function.
    """

    def populate_scope(self, scope: Any) -> None:
        """Populate the scope with a user lazy object if not initiated."""
        # Make sure we have a session
        if "session" not in scope:
            raise ValueError(
                "AuthMiddleware cannot find session in scope. "
                "SessionMiddleware must be above it.",
            )
        # Add it to the scope if it's not there already
        if "user" not in scope:
            scope["user"] = UserLazyObject()

    async def resolve_scope(self, scope: Any) -> None:
        """Turn anonymous users into the custom anonymous user."""
        user = await get_user(scope)
        if user.is_anonymous:
            user = WebAnnotationAnonymousUser(scope["session"].session_key)
        scope["user"]._wrapped = user  # ruff: ignore[private-member-access]

    async def __call__(self, scope: Any, receive: Any, send: Any) -> Any:
        scope = dict(scope)
        # Scope injection/mutation per this middleware's needs.
        self.populate_scope(scope)
        # Grab the finalized/resolved scope
        await self.resolve_scope(scope)

        return await super().__call__(scope, receive, send)


def custom_middleware_stack(inner: Any) -> Any:
    """Custom middleware stack that uses AnonymousAuthMiddleware."""
    return AuthMiddlewareStack(AnonymousAuthMiddleware(inner))


application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AllowedHostsOriginValidator(
        custom_middleware_stack(
            URLRouter(cast(Any, websocket_urlpatterns))),
    ),
})
