# pylint: disable=C0114,C0116
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

import pytest_mock

from web_annotation import settings_e2e
from web_annotation.loadtest import ping_view


def test_settings_e2e_enables_loadtest_ping() -> None:
    # The flag gates the route into the URLconf only under settings_e2e (the
    # module run_daphne_server.sh uses). pytest runs under test_settings, which
    # does NOT set it, so assert on the settings_e2e module object directly.
    assert settings_e2e.LOADTEST_PING_ENABLED is True


def test_ping_view_group_sends_sentinel(
    mocker: pytest_mock.MockerFixture,
) -> None:
    # The route is import-time gated and absent under test_settings, so call the
    # view callable directly rather than going through a URL/Client.
    captured: dict[str, Any] = {}

    def fake_async_to_sync(
        func: Callable[..., Any],
    ) -> Callable[..., Any]:  # mimic async_to_sync(group_send)
        def _call(group: str, message: dict[str, Any]) -> None:
            captured["group"] = group
            captured["message"] = message
        return _call

    mocker.patch.object(
        ping_view, "get_channel_layer", return_value=MagicMock(),
    )
    mocker.patch.object(ping_view, "async_to_sync", fake_async_to_sync)

    request = MagicMock()
    request.GET = {"seq": "7"}
    response = ping_view.loadtest_ping(request)

    assert response.status_code == 200
    assert captured["group"] == "global"
    assert captured["message"] == {
        "type": "annotation_notify",
        "message": "loadtest_ping:7",
    }
