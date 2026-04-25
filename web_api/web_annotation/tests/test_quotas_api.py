# pylint: disable=W0621,C0114,C0116,W0212,W0613
from typing import Any

import pytest
from django.test import Client


@pytest.mark.parametrize("client,expected_limits", [
    (
        "anonymous", {
            "variants": {
                "daily": {
                    "current": 100_000,
                    "max": 100_000,
                },
                "monthly": {
                    "current": 1_000_000,
                    "max": 1_000_000,
                },
                "extra": 0,
            },
            "attributes": {
                "daily": {
                    "current": 1_000_000,
                    "max": 1_000_000,
                },
                "monthly": {
                    "current": 10_000_000,
                    "max": 10_000_000,
                },
                "extra": 0,
            },
            "jobs": {
                "daily": {
                    "current": 10,
                    "max": 10,
                },
                "monthly": {
                    "current": 100,
                    "max": 100,
                },
                "extra": 0,
            },
            "single_variant_queries": {
                "daily": {
                    "current": 100,
                    "max": 100,
                },
                "monthly": {
                    "current": 1000,
                    "max": 1000,
                },
                "extra": 0,
            },
        },
    ),
    (
        "user", {
            "variants": {
                "daily": {
                    "current": 1_000_000,
                    "max": 1_000_000,
                },
                "monthly": {
                    "current": 10_000_000,
                    "max": 10_000_000,
                },
                "extra": 0,
            },
            "attributes": {
                "daily": {
                    "current": 10_000_000,
                    "max": 10_000_000,
                },
                "monthly": {
                    "current": 100_000_000,
                    "max": 100_000_000,
                },
                "extra": 0,
            },
            "jobs": {
                "daily": {
                    "current": 100,
                    "max": 100,
                },
                "monthly": {
                    "current": 1000,
                    "max": 1000,
                },
                "extra": 0,
            },
            "single_variant_queries": {
                "daily": {
                    "current": 1000,
                    "max": 1000,
                },
                "monthly": {
                    "current": 10000,
                    "max": 10000,
                },
                "extra": 0,
            },
        },
    ),
])
def test_limits_api(
    clients: dict[str, Client], client: str, expected_limits: dict[str, Any],
) -> None:
    response = clients[client].get("/api/quotas")
    assert response.status_code == 200
    assert response.json() == expected_limits
