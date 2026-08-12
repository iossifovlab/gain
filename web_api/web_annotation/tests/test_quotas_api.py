# pylint: disable=W0621,C0114,C0116,W0212,W0613
import copy
from typing import Any

import pytest
from django.conf import LazySettings
from django.test import Client

from web_annotation.models import User, UserQuota


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
        },
    ),
])
def test_limits_api(
    clients: dict[str, Client], client: str, expected_limits: dict[str, Any],
) -> None:
    response = clients[client].get("/api/quotas")
    assert response.status_code == 200
    assert response.json() == expected_limits


def test_raising_a_limit_moves_the_reported_figure_with_no_reset(
    clients: dict[str, Client],
    settings: LazySettings,
) -> None:
    """A raised limit reaches an existing, partly-consumed row immediately.

    The wart this endpoint exhibited: it pairs a stored counter with a limit
    read at call time, so raising a limit moved the denominator and left the
    numerator behind until the next period refresh -- an untouched user
    reported at half of a limit that had just been doubled. Both figures now
    derive from the live limit, so both move together (gain#750).

    Asserted on a *consumed* row rather than a fresh one, because a fresh row
    reports ``current == max`` under either representation and so cannot tell
    them apart.
    """
    user = User.objects.get(email="user@example.com")
    quota = UserQuota.objects.create(user=user)
    quota.job_complete(variants_count=400_000, attributes_count=0)
    before = clients["user"].get("/api/quotas").json()["variants"]["daily"]
    assert before == {"current": 600_000, "max": 1_000_000}

    raised = copy.deepcopy(settings.QUERY_QUOTAS)
    raised["user"]["daily_variants"] = 2_000_000
    settings.QUERY_QUOTAS = raised

    after = clients["user"].get("/api/quotas").json()["variants"]["daily"]
    # The 400_000 already spent is all that is missing from the new limit;
    # nothing was reset, and no stored value moved.
    assert after == {"current": 1_600_000, "max": 2_000_000}
