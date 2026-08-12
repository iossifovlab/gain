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


def _assert_at_full_headroom(quota: Quota) -> None:
    """Assert ``quota`` has consumed nothing, and so has its whole limit.

    The stored zero is the load-bearing assertion; the headroom follows from
    it arithmetically and cannot fail on its own. It is asserted anyway
    because it is the property callers actually depend on, and because it
    names the limit the row is measured against, which the zero does not.
    """
    for field in Quota.COUNTER_FIELDS:
        assert getattr(quota, field) == 0, field
        assert quota.remaining(field) == quota._max_for(field), field


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
    a user who had consumed nothing. Zero now *means* "consumed nothing", so
    there is nothing to raise and no window to arrive in; this keeps checking
    the outcome the window used to break.
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
    """A fully consumed row must refuse, and stay refused across a load.

    The counterpart of the fresh-row tests above: those assert that an
    all-zero row is usable, so this one has to assert that a row which has
    spent everything is not. Together they are what makes the two states
    distinguishable -- the ambiguity gain#670 reported, now resolved by the
    counter meaning consumption rather than by a construction-time override
    (gain#750).

    Written through a queryset ``update`` so the exhausted state is read back
    from the database rather than staged on an instance: a quota that healed
    itself on load would otherwise go unnoticed, and it fails open.
    """
    user = User.objects.get(email="user@example.com")
    quota = UserQuota.objects.create(user=user)
    UserQuota.objects.filter(user=user).update(**{
        field: quota._max_for(field) for field in UserQuota.COUNTER_FIELDS
    })

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


def test_a_created_user_quota_is_stored_having_consumed_nothing() -> None:
    """What reaches the database -- not just the instance -- is usable."""
    user = User.objects.get(email="user@example.com")

    UserQuota.objects.create(user=user)

    _assert_at_full_headroom(UserQuota.objects.get(user=user))


def test_a_created_ip_quota_is_stored_having_consumed_nothing() -> None:
    AnonymousUserQuota.objects.create(ip="10.0.0.4")

    _assert_at_full_headroom(AnonymousUserQuota.objects.get(ip="10.0.0.4"))


def test_a_created_session_quota_is_stored_having_consumed_nothing() -> None:
    SessionQuota.objects.create(session_id="sess-3")

    _assert_at_full_headroom(SessionQuota.objects.get(session_id="sess-3"))


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
