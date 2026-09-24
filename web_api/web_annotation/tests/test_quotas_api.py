# pylint: disable=W0621,C0114,C0116,W0212,W0613
import copy
from typing import Any

import pytest
from django.conf import LazySettings
from django.test import Client

from web_annotation.models import Quota, User, UserQuota


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


def test_every_declared_resource_is_reported(
    clients: dict[str, Client],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A fourth resource, declared over the jobs columns, must be reported
    # exactly like jobs. The shipped three alone would read the same from a
    # hand-written body, so they cannot show the body follows the table.
    monkeypatch.setattr(Quota, "RESOURCE_FIELDS", {
        **Quota.RESOURCE_FIELDS,
        "jobs_again": Quota.RESOURCE_FIELDS["jobs"],
    })

    body = clients["user"].get("/api/quotas").json()

    assert set(body) == set(Quota.RESOURCE_FIELDS)
    assert body["jobs_again"] == body["jobs"]


@pytest.mark.parametrize("columns,missing", [
    (("daily_gpu_hours", "monthly_jobs", "extra_jobs"), "daily_gpu_hours"),
    (("daily_jobs", "monthly_gpu_hours", "extra_jobs"), "monthly_gpu_hours"),
    (("daily_jobs", "monthly_jobs", "extra_gpu_hours"), "extra_gpu_hours"),
], ids=["daily", "monthly", "extra"])
def test_a_declared_resource_the_snapshot_lacks_fails_the_request(
    clients: dict[str, Client],
    monkeypatch: pytest.MonkeyPatch,
    columns: tuple[str, str, str],
    missing: str,
) -> None:
    # Reporting it as zero, or leaving it out, would show the user a quota
    # page that silently disagrees with what is deducted from them. One
    # column missing at a time, the others real, so each lookup is reached.
    monkeypatch.setattr(Quota, "RESOURCE_FIELDS", {
        **Quota.RESOURCE_FIELDS,
        "gpu_hours": columns,
    })

    with pytest.raises(AttributeError, match=f"'{missing}'"):
        clients["user"].get("/api/quotas")


def test_extra_units_are_reported_on_the_resource_they_were_granted_to(
    clients: dict[str, Client],
) -> None:
    user = User.objects.get(email="user@example.com")
    UserQuota.objects.create(user=user, extra_variants=5)

    body = clients["user"].get("/api/quotas").json()

    assert {resource: body[resource]["extra"] for resource in body} == {
        "jobs": 0, "variants": 5, "attributes": 0,
    }
