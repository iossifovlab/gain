# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pytest
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext

from web_annotation.models import (
    AnonymousUserQuota,
    Quota,
    SessionQuota,
    User,
    UserQuota,
    WebAnnotationAnonymousUser,
)


def _row_writes(captured: CaptureQueriesContext) -> list[str]:
    """Return the INSERT/UPDATE statements among the captured queries."""
    return [
        query["sql"] for query in captured.captured_queries
        if query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE"))
    ]


def _assert_at_limits(quota: Quota) -> None:
    """Assert every counter of ``quota`` sits at its configured maximum."""
    assert quota.daily_jobs == quota.get_daily_job_max()
    assert quota.monthly_jobs == quota.get_monthly_job_max()
    assert quota.daily_variants == quota.get_daily_variant_max()
    assert quota.monthly_variants == quota.get_monthly_variant_max()
    assert quota.daily_attributes == quota.get_daily_attribute_max()
    assert quota.monthly_attributes == quota.get_monthly_attribute_max()


def test_user_quota_user_is_unique() -> None:
    """A second UserQuota for the same user must be rejected by the DB.

    Without a uniqueness guarantee on ``UserQuota.user`` the
    ``get_or_create`` in ``User._get_or_create_user_quota`` races and
    inserts duplicate rows, which later makes ``get_quota`` raise
    ``MultipleObjectsReturned``.
    """
    user = User.objects.get(email="user@example.com")
    UserQuota.objects.create(user=user)
    with pytest.raises(IntegrityError), transaction.atomic():
        UserQuota.objects.create(user=user)


def test_get_quota_resilient_to_existing_quota_rows() -> None:
    """``get_quota`` must not raise even if a quota row already exists.

    Simulates the outcome of a race that already happened: a quota row is
    present, and a subsequent request must reuse it rather than blow up.
    """
    user = User.objects.get(email="user@example.com")
    UserQuota.objects.create(user=user)

    # Must not raise MultipleObjectsReturned (or anything else).
    snapshot = user.get_quota()
    assert snapshot is not None
    assert UserQuota.objects.filter(user=user).count() == 1


def test_a_created_user_quota_allows_a_single_allele_query() -> None:
    """A quota row must be usable the moment it exists.

    The row used to be inserted carrying the model's zero field defaults and
    only afterwards raised to its configured limits by two further saves. A
    reader arriving in between found the row present, skipped the reset that
    only the creating caller performs, and read an all-zero quota -- refusing
    a user who had consumed nothing.
    """
    user = User.objects.get(email="user@example.com")
    UserQuota.objects.create(user=user)

    assert user.get_quota().single_allele_allowed(1)


def test_lazily_creating_a_user_quota_writes_the_row_once() -> None:
    """Lazy creation must leave no window for a concurrent reader.

    Every write after the insert publishes a second committed state that
    another request can read; the quota has to arrive complete instead.
    """
    user = User.objects.get(email="user@example.com")

    with CaptureQueriesContext(connection) as captured:
        user.get_quota()

    assert len(_row_writes(captured)) == 1


def test_an_exhausted_user_quota_still_refuses_a_single_allele_query() -> None:
    """All-zero has to keep meaning exhausted.

    Starting a new quota at its limits is what makes an all-zero row
    unambiguous; a quota that reached zero by being spent must not be
    mistaken for one that was never initialised and quietly healed.

    This is also the only test that loads an all-zero quota back out of the
    database, so it is what pins the guard in ``Quota.__init__`` that starts
    counters at their limits on construction but leaves a load alone. Keep a
    case that reads spent counters from the database: without one, a quota
    healing itself on load would go unnoticed, and it fails open -- every
    exhausted quota silently becomes full again.
    """
    user = User.objects.get(email="user@example.com")
    UserQuota.objects.create(user=user)
    UserQuota.objects.filter(user=user).update(
        **dict.fromkeys(UserQuota.COUNTER_FIELDS, 0))

    assert not user.get_quota().single_allele_allowed(1)


def test_lazily_creating_anonymous_quotas_writes_each_row_once() -> None:
    """The session and the IP quota are each written once, like the user's."""
    anonymous = WebAnnotationAnonymousUser(session_id="sess-4", ip="10.0.0.5")

    with CaptureQueriesContext(connection) as captured:
        anonymous.get_quota()

    assert len(_row_writes(captured)) == 2


def test_created_anonymous_quotas_allow_a_single_allele_query() -> None:
    """Both quotas an anonymous user is measured against must be usable.

    The snapshot is the field-wise minimum of the session and the IP quota,
    so either one arriving empty is enough to refuse the request.
    """
    anonymous = WebAnnotationAnonymousUser(session_id="sess-2", ip="10.0.0.3")
    SessionQuota.objects.create(session_id="sess-2")
    AnonymousUserQuota.objects.create(ip="10.0.0.3")

    assert anonymous.get_quota().single_allele_allowed(1)


def test_a_created_user_quota_is_stored_at_its_limits() -> None:
    """What reaches the database -- not just the instance -- is usable."""
    user = User.objects.get(email="user@example.com")

    UserQuota.objects.create(user=user)

    _assert_at_limits(UserQuota.objects.get(user=user))


def test_a_created_ip_quota_is_stored_at_its_limits() -> None:
    AnonymousUserQuota.objects.create(ip="10.0.0.4")

    _assert_at_limits(AnonymousUserQuota.objects.get(ip="10.0.0.4"))


def test_a_created_session_quota_is_stored_at_its_limits() -> None:
    SessionQuota.objects.create(session_id="sess-3")

    _assert_at_limits(SessionQuota.objects.get(session_id="sess-3"))


def test_anonymous_ip_quota_is_unique() -> None:
    """A second AnonymousUserQuota for the same IP must be DB-rejected."""
    AnonymousUserQuota.objects.create(ip="10.0.0.1")
    with pytest.raises(IntegrityError), transaction.atomic():
        AnonymousUserQuota.objects.create(ip="10.0.0.1")


def test_anonymous_get_quota_resilient_to_existing_rows() -> None:
    """Anonymous ``get_quota`` must not raise when quota rows exist."""
    anon = WebAnnotationAnonymousUser(session_id="sess-1", ip="10.0.0.2")
    AnonymousUserQuota.objects.create(ip="10.0.0.2")

    snapshot = anon.get_quota()
    assert snapshot is not None
    assert AnonymousUserQuota.objects.filter(ip="10.0.0.2").count() == 1
