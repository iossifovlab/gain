# pylint: disable=W0621,C0114,C0116,W0212,W0613
import copy
import dataclasses
from typing import cast

import pytest
from django.conf import LazySettings, settings

from web_annotation.models import (
    AnonymousUserQuota,
    Quota,
    QuotaSnapshot,
    SessionQuota,
    User,
    UserQuota,
    WebAnnotationAnonymousUser,
)


def _leave(quota: Quota, **headroom: int) -> None:
    """Consume each named period counter down to the units given.

    The counters store consumption, but every test here is written about how
    much a quota has *left*, which is also what the endpoint and the admin
    panel speak in. Setting the headroom rather than the column keeps those
    tests saying what they mean, and keeps them from re-encoding the
    conversion they exist to check (gain#750).
    """
    for field, remaining in headroom.items():
        quota.set_remaining(field, remaining)


def _seed_sentinels(
    quota: Quota, fields: tuple[str, ...],
) -> dict[str, int]:
    """Set each field to a distinct non-zero value.

    Distinct so a reset touching the wrong field is visible, and non-zero
    because zero is what a reset writes -- a sentinel of zero would survive
    a reset it was meant to detect.
    """
    sentinels = {field: index for index, field in enumerate(fields, start=1)}
    for field, sentinel in sentinels.items():
        setattr(quota, field, sentinel)
    return sentinels


@pytest.fixture
def anonymous_quota() -> AnonymousUserQuota:
    return AnonymousUserQuota.objects.create(ip="127.0.0.1")


@pytest.fixture
def user_quota() -> UserQuota:
    user = User.objects.get(email="user@example.com")
    return UserQuota.objects.create(user=user)


@pytest.fixture
def session_quota() -> SessionQuota:
    return SessionQuota.objects.create(session_id="test-session")


def test_anonymous_quota_max_values(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    assert anonymous_quota.get_daily_job_max() == 10
    assert anonymous_quota.get_monthly_job_max() == 100
    assert anonymous_quota.get_daily_variant_max() == 100_000
    assert anonymous_quota.get_monthly_variant_max() == 1_000_000
    assert anonymous_quota.get_daily_attribute_max() == 1_000_000
    assert anonymous_quota.get_monthly_attribute_max() == 10_000_000


def test_user_quota_max_values(user_quota: UserQuota) -> None:
    assert user_quota.get_daily_job_max() == 100
    assert user_quota.get_monthly_job_max() == 1_000
    assert user_quota.get_daily_variant_max() == 1_000_000
    assert user_quota.get_monthly_variant_max() == 10_000_000
    assert user_quota.get_daily_attribute_max() == 10_000_000
    assert user_quota.get_monthly_attribute_max() == 100_000_000


def test_a_fresh_quota_has_consumed_nothing_and_is_usable(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    # Zero is the honest starting value once a counter means units consumed,
    # so a new row needs no construction-time override to be usable. The
    # override existed only because zero used to mean "exhausted" (gain#750).
    for field in Quota.COUNTER_FIELDS:
        assert getattr(anonymous_quota, field) == 0, field

    assert anonymous_quota.job_allowed(
        variants_count=1, attributes_count=1) is True


@pytest.mark.parametrize(
    "quota_fixture", ["anonymous_quota", "user_quota", "session_quota"])
def test_raising_a_limit_widens_an_existing_rows_headroom_with_no_reset(
    request: pytest.FixtureRequest,
    quota_fixture: str,
    settings: LazySettings,
) -> None:
    # The defect this issue is named for. Under stored remaining, a raised
    # limit reached a row only at its next period refresh -- and the
    # predicates never consulted a limit at all, so it granted no capacity
    # even then until the reset rewrote the row. Run for all three quota
    # types, since each resolves its own configuration block.
    quota = cast(Quota, request.getfixturevalue(quota_fixture))
    limit_before = quota.get_daily_variant_max()
    _leave(quota, daily_variants=0)
    quota.save()
    assert quota.daily_variants == limit_before  # spent the whole limit
    assert quota.check_variant_quota(1) is False

    raised = copy.deepcopy(settings.QUERY_QUOTAS)
    for block in raised.values():
        block["daily_variants"] *= 2
    settings.QUERY_QUOTAS = raised

    # No reset ran and no stored value moved -- the row still records the
    # same consumption -- but the headroom is measured against the new
    # limit, so the row gains exactly what the limit gained.
    assert quota.daily_variants == limit_before
    assert quota.remaining("daily_variants") == 2 * limit_before - limit_before
    assert quota.check_variant_quota(1) is True


def test_lowering_a_limit_below_what_a_row_consumed_refuses_it_at_once(
    anonymous_quota: AnonymousUserQuota,
    settings: LazySettings,
) -> None:
    # The accepted other half of live limits: a lowered limit bites
    # immediately, where previously a user kept their allowance until the
    # next reset. Deliberate, and recorded in ADR 0019.
    limit = anonymous_quota.get_daily_variant_max()
    anonymous_quota.job_complete(
        variants_count=limit // 2, attributes_count=0)
    assert anonymous_quota.check_variant_quota(1) is True

    lowered = copy.deepcopy(settings.QUERY_QUOTAS)
    lowered["anonymous"]["daily_variants"] = limit // 4
    settings.QUERY_QUOTAS = lowered

    assert anonymous_quota.remaining("daily_variants") == 0
    assert anonymous_quota.check_variant_quota(1) is False


def test_consuming_past_a_limit_stops_the_counter_and_charges_extras_once(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    # Consumption is not recorded past the limit, and the overshoot is
    # charged to extras exactly once. Recording it in both places would
    # charge the same units twice, and would surface only much later, as
    # missing headroom after a limit raise (gain#750).
    daily_limit = anonymous_quota.get_daily_attribute_max()
    monthly_limit = anonymous_quota.get_monthly_attribute_max()
    _leave(anonymous_quota, daily_attributes=100, monthly_attributes=100)
    anonymous_quota.extra_attributes = 10_000
    anonymous_quota.save()

    anonymous_quota.job_complete(variants_count=0, attributes_count=5_000)

    assert anonymous_quota.daily_attributes == daily_limit
    assert anonymous_quota.monthly_attributes == monthly_limit
    # 100 units of headroom covered part of the charge; the other 4_900 came
    # out of extras, and only out of extras.
    assert anonymous_quota.extra_attributes == 10_000 - 4_900


def test_granting_more_units_than_the_limit_survives_and_is_spent_first(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    # An operator may put a single row above its type's configured limit --
    # the admin panel could always do it while the column held units
    # outright, and the e2e suite depends on it to keep the shared IP quota
    # from binding. Stored as a negative consumption, so the row has used
    # less than nothing against its limit (gain#750).
    limit = anonymous_quota.get_daily_job_max()
    granted = limit * 10

    anonymous_quota.set_remaining("daily_jobs", granted)

    assert anonymous_quota.daily_jobs == limit - granted < 0
    assert anonymous_quota.remaining("daily_jobs") == granted

    # Saved before charging: consumption re-reads the stored row under its
    # lock and discards whatever was staged on the instance, so an unsaved
    # grant would be silently dropped here and the assertion below would
    # measure a fresh quota instead.
    anonymous_quota.save()

    # The grant is spent before the limit begins to apply: charging counts
    # up from where the row is, rather than from the limit. (This exercises
    # the ceiling's *limit* arm, since a granted row sits below zero; the
    # `consumed` arm is reached by the lowered-limit test below.)
    anonymous_quota.job_complete(variants_count=0, attributes_count=0)

    assert anonymous_quota.remaining("daily_jobs") == granted - 1


def test_charging_a_row_a_lowered_limit_left_above_it_never_credits_it(
    anonymous_quota: AnonymousUserQuota,
    settings: LazySettings,
) -> None:
    # Lowering a limit strands a row above it. A charge stops at the limit,
    # so a stop computed from the limit alone would *reduce* the recorded
    # consumption -- crediting the user the difference for the privilege of
    # spending more. The stop is therefore the larger of the limit and what
    # the row already holds (gain#750).
    limit = anonymous_quota.get_daily_variant_max()
    anonymous_quota.job_complete(variants_count=limit, attributes_count=0)
    assert anonymous_quota.daily_variants == limit

    lowered = copy.deepcopy(settings.QUERY_QUOTAS)
    lowered["anonymous"]["daily_variants"] = limit // 10
    settings.QUERY_QUOTAS = lowered

    anonymous_quota.job_complete(variants_count=1, attributes_count=0)

    assert anonymous_quota.daily_variants >= limit
    assert anonymous_quota.remaining("daily_variants") == 0


def test_no_counter_is_declared_in_both_periods() -> None:
    # A field in both tuples would be refreshed by both resets -- daily one
    # night and monthly the same night -- and would appear twice in the
    # concatenated COUNTER_FIELDS, where every consumer of that tuple would
    # then convert or report it twice.
    repeated = [
        field for field in Quota.COUNTER_FIELDS
        if Quota.COUNTER_FIELDS.count(field) > 1
    ]
    assert not repeated


def test_every_declared_counter_is_a_real_model_field() -> None:
    # Names which counter is wrong. A counter in this tuple with no column
    # behind it is reached through getattr/setattr on every conversion and
    # every reset, which invents an instance attribute rather than raising --
    # so the row would silently never be metered.
    model_fields = {
        field.name for field in AnonymousUserQuota._meta.get_fields()
    }
    assert set(Quota.COUNTER_FIELDS) <= model_fields


def test_every_declared_extra_unit_field_is_a_real_model_field() -> None:
    # The extras get no such loud failure: a name no column backs is only
    # ever reached through setattr, which would happily create an instance
    # attribute and drop the grant on save (gain#788).
    model_fields = {
        field.name for field in AnonymousUserQuota._meta.get_fields()
    }
    assert set(Quota.EXTRA_UNIT_FIELDS) <= model_fields


def test_the_period_tuples_are_still_derived_from_the_resource_table() -> None:
    # A tripwire, not a property: while the tuples are projections of
    # RESOURCE_FIELDS this cannot fail, which is the point. It fires when a
    # period tuple is written by hand again and *diverges* from the table --
    # a tuple that quietly loses a counter, which no other test catches: the
    # model-field test still passes because the remaining names are real, and
    # the reset tests loop the shortened tuple and so pass vacuously. A
    # hand-written tuple that matches the table is a harmless no-op and stays
    # green, correctly.
    rows = Quota.RESOURCE_FIELDS.values()
    derived_daily = tuple(daily for daily, _, _ in rows)
    derived_monthly = tuple(monthly for _, monthly, _ in rows)
    derived_extra = tuple(extra for _, _, extra in rows)

    assert derived_daily == Quota.DAILY_COUNTER_FIELDS
    assert derived_monthly == Quota.MONTHLY_COUNTER_FIELDS
    assert derived_extra == Quota.EXTRA_UNIT_FIELDS


def test_reset_daily_refreshes_every_declared_daily_counter(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    # Driven off the declared tuple rather than a hand-written list, so that
    # adding a counter to DAILY_COUNTER_FIELDS and forgetting to refresh it
    # fails here instead of silently never refreshing (gain#749).
    configured = settings.QUERY_QUOTAS["anonymous"]
    for field in Quota.DAILY_COUNTER_FIELDS:
        anonymous_quota.set_remaining(field, 0)
    anonymous_quota.save()

    anonymous_quota.reset_daily()

    for field in Quota.DAILY_COUNTER_FIELDS:
        assert getattr(anonymous_quota, field) == 0, field
        assert anonymous_quota.remaining(field) == configured[field], field


def test_reset_monthly_refreshes_every_declared_monthly_counter(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    configured = settings.QUERY_QUOTAS["anonymous"]
    for field in Quota.MONTHLY_COUNTER_FIELDS:
        anonymous_quota.set_remaining(field, 0)
    anonymous_quota.save()

    anonymous_quota.reset_monthly()

    for field in Quota.MONTHLY_COUNTER_FIELDS:
        assert getattr(anonymous_quota, field) == 0, field
        assert anonymous_quota.remaining(field) == configured[field], field


def test_reset_daily_leaves_the_monthly_counters_and_extras_untouched(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    # A shared reset handed the wrong tuple would refresh the other period
    # too, silently handing back monthly quota every night. Seeded off the
    # tuple so a counter added to a period stays covered here.
    untouched = _seed_sentinels(
        anonymous_quota, Quota.MONTHLY_COUNTER_FIELDS + Quota.EXTRA_UNIT_FIELDS)
    anonymous_quota.save()
    monthly_stamp_before = anonymous_quota.last_monthly_reset

    anonymous_quota.reset_daily()

    refreshed = AnonymousUserQuota.objects.get(pk=anonymous_quota.pk)
    for field, sentinel in untouched.items():
        assert getattr(refreshed, field) == sentinel, field
    assert refreshed.last_monthly_reset == monthly_stamp_before


def test_reset_monthly_leaves_the_daily_counters_and_extras_untouched(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    untouched = _seed_sentinels(
        anonymous_quota, Quota.DAILY_COUNTER_FIELDS + Quota.EXTRA_UNIT_FIELDS)
    anonymous_quota.save()
    daily_stamp_before = anonymous_quota.last_daily_reset

    anonymous_quota.reset_monthly()

    refreshed = AnonymousUserQuota.objects.get(pk=anonymous_quota.pk)
    for field, sentinel in untouched.items():
        assert getattr(refreshed, field) == sentinel, field
    assert refreshed.last_daily_reset == daily_stamp_before


def test_reset_daily_updates_timestamp(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    before = anonymous_quota.last_daily_reset

    anonymous_quota.reset_daily()

    # Read back: the reset names the columns it writes, so the stamp reaches
    # the row only by being one of them. Asserting on the instance alone
    # passes on the setattr whatever the write leaves behind.
    refreshed = AnonymousUserQuota.objects.get(pk=anonymous_quota.pk)
    assert refreshed.last_daily_reset > before


def test_reset_monthly_updates_timestamp(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    before = anonymous_quota.last_monthly_reset

    anonymous_quota.reset_monthly()

    refreshed = AnonymousUserQuota.objects.get(pk=anonymous_quota.pk)
    assert refreshed.last_monthly_reset > before


def test_reset_daily_persisted(anonymous_quota: AnonymousUserQuota) -> None:
    _leave(anonymous_quota, daily_jobs=0)
    anonymous_quota.save()
    anonymous_quota.reset_daily()

    refreshed = AnonymousUserQuota.objects.get(pk=anonymous_quota.pk)
    assert refreshed.daily_jobs == 0
    assert refreshed.remaining("daily_jobs") \
        == anonymous_quota.get_daily_job_max()


def test_reset_monthly_persisted(anonymous_quota: AnonymousUserQuota) -> None:
    _leave(anonymous_quota, monthly_jobs=0)
    anonymous_quota.save()
    anonymous_quota.reset_monthly()

    refreshed = AnonymousUserQuota.objects.get(pk=anonymous_quota.pk)
    assert refreshed.monthly_jobs == 0
    assert refreshed.remaining("monthly_jobs") \
        == anonymous_quota.get_monthly_job_max()


def test_check_job_quota_true_when_quota_available(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    assert anonymous_quota.check_job_quota() is True


def test_check_job_quota_false_when_daily_exhausted(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    _leave(anonymous_quota, daily_jobs=0)
    assert anonymous_quota.check_job_quota() is False


def test_check_job_quota_false_when_monthly_exhausted(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    _leave(anonymous_quota, monthly_jobs=0)
    assert anonymous_quota.check_job_quota() is False


def test_check_job_quota_true_with_extra_even_when_daily_exhausted(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    _leave(anonymous_quota, daily_jobs=0)
    anonymous_quota.extra_jobs = 5
    assert anonymous_quota.check_job_quota() is True


def test_single_allele_allowed_true_when_quota_available(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    assert anonymous_quota.single_allele_allowed(attributes_count=10) is True


def test_single_allele_allowed_false_when_variant_quota_exhausted(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    _leave(anonymous_quota, daily_variants=0)
    assert anonymous_quota.single_allele_allowed(attributes_count=10) is False


def test_single_allele_allowed_true_with_extra_variant_quota(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    _leave(anonymous_quota, daily_variants=0)
    anonymous_quota.extra_variants = 1
    assert anonymous_quota.single_allele_allowed(attributes_count=10) is True


def test_single_allele_allowed_false_when_attribute_quota_exhausted(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    _leave(anonymous_quota, daily_attributes=0, monthly_attributes=0)
    assert anonymous_quota.single_allele_allowed(attributes_count=10) is False


def test_job_complete_decrements_job_counts(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    before_daily = anonymous_quota.remaining("daily_jobs")
    before_monthly = anonymous_quota.remaining("monthly_jobs")

    anonymous_quota.job_complete(variants_count=1_000, attributes_count=5_000)

    assert anonymous_quota.remaining("daily_jobs") == before_daily - 1
    assert anonymous_quota.remaining("monthly_jobs") == before_monthly - 1


def test_job_complete_decrements_variant_counts(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    before_daily = anonymous_quota.remaining("daily_variants")
    before_monthly = anonymous_quota.remaining("monthly_variants")
    variants = 1_000

    anonymous_quota.job_complete(variants_count=variants, attributes_count=0)

    assert anonymous_quota.remaining("daily_variants") \
        == before_daily - variants
    assert anonymous_quota.remaining("monthly_variants") \
        == before_monthly - variants


def test_job_complete_decrements_attribute_counts(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    before_daily = anonymous_quota.remaining("daily_attributes")
    before_monthly = anonymous_quota.remaining("monthly_attributes")
    attributes = 5_000

    anonymous_quota.job_complete(variants_count=0, attributes_count=attributes)

    assert anonymous_quota.remaining("daily_attributes") \
        == before_daily - attributes
    assert anonymous_quota.remaining("monthly_attributes") \
        == before_monthly - attributes


def test_job_complete_does_not_consume_extras_when_sufficient(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    before_extra_jobs = anonymous_quota.extra_jobs
    before_extra_variants = anonymous_quota.extra_variants
    before_extra_attributes = anonymous_quota.extra_attributes

    anonymous_quota.job_complete(variants_count=1_000, attributes_count=5_000)

    assert anonymous_quota.extra_jobs == before_extra_jobs
    assert anonymous_quota.extra_variants == before_extra_variants
    assert anonymous_quota.extra_attributes == before_extra_attributes


def test_job_complete_does_not_consume_extras_when_monthly_covers(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    # daily exhausted, but monthly alone covers the amount — no extras needed
    _leave(anonymous_quota, daily_attributes=0)
    anonymous_quota.save()
    before_extra = anonymous_quota.extra_attributes

    anonymous_quota.job_complete(variants_count=0, attributes_count=5_000)

    assert anonymous_quota.extra_attributes == before_extra


def test_job_complete_consumes_extras_when_both_periods_exhausted(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    _leave(anonymous_quota, daily_attributes=0, monthly_attributes=0)
    anonymous_quota.extra_attributes = 20_000
    anonymous_quota.save()

    anonymous_quota.job_complete(variants_count=0, attributes_count=5_000)

    assert anonymous_quota.extra_attributes == 20_000 - 5_000


def test_job_complete_consumes_extras_for_remainder_beyond_max_period(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    # daily=3, monthly=3 → max=3, amount=10 → extras cover 7
    _leave(anonymous_quota, daily_attributes=3, monthly_attributes=3)
    anonymous_quota.extra_attributes = 20
    anonymous_quota.save()

    anonymous_quota.job_complete(variants_count=0, attributes_count=10)

    assert anonymous_quota.remaining("daily_attributes") == 0
    assert anonymous_quota.remaining("monthly_attributes") == 0
    assert anonymous_quota.extra_attributes == 20 - 7


def test_job_complete_zeros_all_extras_when_extra_exhausted(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    _leave(anonymous_quota, daily_attributes=0, monthly_attributes=0)
    anonymous_quota.extra_attributes = 5_000
    anonymous_quota.extra_jobs = 50
    anonymous_quota.extra_variants = 500_000
    anonymous_quota.save()

    anonymous_quota.job_complete(variants_count=0, attributes_count=5_000)

    assert anonymous_quota.extra_attributes == 0
    assert anonymous_quota.extra_jobs == 0
    assert anonymous_quota.extra_variants == 0


def test_job_complete_zeros_all_extras_when_extra_overdrawn(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    _leave(anonymous_quota, daily_attributes=0, monthly_attributes=0)
    anonymous_quota.extra_attributes = 3_000
    anonymous_quota.extra_jobs = 50
    anonymous_quota.save()

    anonymous_quota.job_complete(variants_count=0, attributes_count=5_000)

    assert anonymous_quota.extra_attributes == 0
    assert anonymous_quota.extra_jobs == 0


def test_job_complete_charges_each_resource_through_its_declared_columns(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    # A distinct amount per resource, so that charging a resource through
    # another resource's columns -- the mismatch that spelling the triples out
    # by hand invites -- shows up as the wrong amount rather than cancelling
    # out. Equal amounts would make any permutation of the resources
    # invisible here (gain#788). Indexed, not `.get`, so that declaring a
    # resource forces a decision about what a job completion charges it.
    charged = {"jobs": 1, "variants": 2, "attributes": 3}
    before = {
        field: anonymous_quota.remaining(field)
        for daily, monthly, _ in Quota.RESOURCE_FIELDS.values()
        for field in (daily, monthly)
    }

    anonymous_quota.job_complete(
        variants_count=charged["variants"],
        attributes_count=charged["attributes"],
    )

    for resource, (daily, monthly, _) in Quota.RESOURCE_FIELDS.items():
        amount = charged[resource]
        for field in (daily, monthly):
            actual = anonymous_quota.remaining(field)
            assert actual == before[field] - amount, field


def test_extras_exhaustion_zeroes_every_declared_extra_field(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    # Drawing any one extras pool down to zero zeroes them all. Driven off the
    # declaration rather than naming the three, so a resource added to
    # RESOURCE_FIELDS and left out of the zeroing keeps a live balance after
    # extras are supposed to be exhausted -- free quota, with no exception and
    # no failing test (gain#788).
    for _, _, extra in Quota.RESOURCE_FIELDS.values():
        setattr(anonymous_quota, extra, 5_000)
    _leave(anonymous_quota, daily_attributes=0, monthly_attributes=0)
    anonymous_quota.save()

    anonymous_quota.job_complete(variants_count=0, attributes_count=5_000)

    for _, _, extra in Quota.RESOURCE_FIELDS.values():
        assert getattr(anonymous_quota, extra) == 0, extra


def test_job_complete_does_not_zero_extras_when_partial_consumption(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    _leave(anonymous_quota, daily_attributes=0, monthly_attributes=0)
    anonymous_quota.extra_attributes = 10_000
    anonymous_quota.extra_jobs = 50
    anonymous_quota.save()

    anonymous_quota.job_complete(variants_count=0, attributes_count=5_000)

    assert anonymous_quota.extra_attributes == 5_000
    assert anonymous_quota.extra_jobs == 50


def test_job_complete_persisted(anonymous_quota: AnonymousUserQuota) -> None:
    anonymous_quota.job_complete(variants_count=500, attributes_count=2_000)

    refreshed = AnonymousUserQuota.objects.get(pk=anonymous_quota.pk)
    assert refreshed.remaining("daily_jobs") \
        == anonymous_quota.get_daily_job_max() - 1
    assert refreshed.remaining("daily_variants") \
        == anonymous_quota.get_daily_variant_max() - 500
    assert refreshed.remaining("daily_attributes") == \
        anonymous_quota.get_daily_attribute_max() - 2_000


@pytest.mark.parametrize(
    "quota_fixture", ["anonymous_quota", "user_quota", "session_quota"])
def test_two_overlapping_job_completions_are_both_recorded(
    request: pytest.FixtureRequest,
    quota_fixture: str,
) -> None:
    """Two instances loaded from the same state each deduct, and both land.

    This models the overlap deterministically rather than with threads: what
    makes a concurrent deduction disappear is the second writer working from
    state it read before the first committed, which two independently loaded
    instances reproduce exactly. The row lock itself is a no-op on SQLite, so
    it is the re-read this pins, not the locking.

    Run for each concrete quota, since the guarantee lives on the abstract
    Quota and has to reach all three.
    """
    quota = cast(Quota, request.getfixturevalue(quota_fixture))
    first = copy.copy(quota)
    second = copy.copy(quota)

    first.job_complete(variants_count=1_000, attributes_count=0)
    second.job_complete(variants_count=1_000, attributes_count=0)

    quota.refresh_from_db()
    assert quota.remaining("daily_variants") \
        == quota.get_daily_variant_max() - 2_000
    assert quota.remaining("daily_jobs") == quota.get_daily_job_max() - 2


def test_single_allele_query_complete_decrements_variant_counts(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    before_daily = anonymous_quota.remaining("daily_variants")
    before_monthly = anonymous_quota.remaining("monthly_variants")

    anonymous_quota.single_allele_query_complete(attributes_count=10)

    assert anonymous_quota.remaining("daily_variants") == before_daily - 1
    assert anonymous_quota.remaining("monthly_variants") == before_monthly - 1


def test_single_allele_query_complete_decrements_attribute_counts(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    before_daily = anonymous_quota.remaining("daily_attributes")
    before_monthly = anonymous_quota.remaining("monthly_attributes")
    attributes = 10

    anonymous_quota.single_allele_query_complete(attributes_count=attributes)

    assert anonymous_quota.remaining("daily_attributes") \
        == before_daily - attributes
    assert anonymous_quota.remaining("monthly_attributes") \
        == before_monthly - attributes


def test_single_allele_query_complete_does_not_consume_extras_when_sufficient(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    before_extra_variants = anonymous_quota.extra_variants
    before_extra_attributes = anonymous_quota.extra_attributes

    anonymous_quota.single_allele_query_complete(attributes_count=10)

    assert anonymous_quota.extra_variants == before_extra_variants
    assert anonymous_quota.extra_attributes == before_extra_attributes


def test_single_allele_query_complete_does_not_consume_extras_when_monthly_covers(  # noqa: E501
    anonymous_quota: AnonymousUserQuota,
) -> None:
    _leave(anonymous_quota, daily_attributes=0)
    anonymous_quota.save()
    before_extra = anonymous_quota.extra_attributes

    anonymous_quota.single_allele_query_complete(attributes_count=10)

    assert anonymous_quota.extra_attributes == before_extra


def test_single_allele_query_complete_consumes_extras_when_both_periods_exhausted(  # noqa: E501
    anonymous_quota: AnonymousUserQuota,
) -> None:
    _leave(anonymous_quota, daily_attributes=0, monthly_attributes=0)
    anonymous_quota.extra_attributes = 50
    anonymous_quota.save()

    anonymous_quota.single_allele_query_complete(attributes_count=10)

    assert anonymous_quota.extra_attributes == 40


def test_single_allele_query_complete_consumes_extras_for_remainder_beyond_max_period(  # noqa: E501
    anonymous_quota: AnonymousUserQuota,
) -> None:
    # daily=4, monthly=4 → max=4, amount=10 → extras cover 6
    _leave(anonymous_quota, daily_attributes=4, monthly_attributes=4)
    anonymous_quota.extra_attributes = 20
    anonymous_quota.save()

    anonymous_quota.single_allele_query_complete(attributes_count=10)

    assert anonymous_quota.remaining("daily_attributes") == 0
    assert anonymous_quota.remaining("monthly_attributes") == 0
    assert anonymous_quota.extra_attributes == 14


def test_single_allele_query_complete_zeros_all_extras_when_extra_exhausted(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    _leave(anonymous_quota, daily_attributes=0, monthly_attributes=0)
    anonymous_quota.extra_attributes = 10
    anonymous_quota.extra_variants = 5
    anonymous_quota.save()

    anonymous_quota.single_allele_query_complete(attributes_count=10)

    assert anonymous_quota.extra_attributes == 0
    assert anonymous_quota.extra_variants == 0


def test_single_allele_query_complete_does_not_zero_extras_when_partial_consumption(  # noqa: E501
    anonymous_quota: AnonymousUserQuota,
) -> None:
    _leave(anonymous_quota, daily_attributes=0, monthly_attributes=0)
    anonymous_quota.extra_attributes = 50
    anonymous_quota.extra_variants = 5
    anonymous_quota.save()

    anonymous_quota.single_allele_query_complete(attributes_count=10)

    assert anonymous_quota.extra_attributes == 40
    assert anonymous_quota.extra_variants == 5


def test_single_allele_query_complete_persisted(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    anonymous_quota.single_allele_query_complete(attributes_count=10)

    refreshed = AnonymousUserQuota.objects.get(pk=anonymous_quota.pk)
    assert refreshed.remaining("daily_variants") == \
        anonymous_quota.get_daily_variant_max() - 1
    assert refreshed.remaining("daily_attributes") == \
        anonymous_quota.get_daily_attribute_max() - 10


def test_user_quota_linked_to_user(user_quota: UserQuota) -> None:
    user = User.objects.get(email="user@example.com")
    assert user_quota.user == user


def test_user_quota_reset_daily(user_quota: UserQuota) -> None:
    _leave(user_quota, daily_jobs=0)
    user_quota.save()
    user_quota.reset_daily()
    assert user_quota.remaining("daily_jobs") == user_quota.get_daily_job_max()


def test_user_quota_job_complete(user_quota: UserQuota) -> None:
    before = user_quota.remaining("daily_jobs")
    user_quota.job_complete(variants_count=100, attributes_count=500)
    assert user_quota.remaining("daily_jobs") == before - 1


# --- Quota.add_units ---

def test_add_units_increments_extra_jobs(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    before = anonymous_quota.extra_jobs
    anonymous_quota.add_units()
    assert anonymous_quota.extra_jobs == (
        before + anonymous_quota.get_monthly_job_max()
    )


def test_add_units_increments_extra_variants(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    before = anonymous_quota.extra_variants
    anonymous_quota.add_units()
    assert anonymous_quota.extra_variants == \
        before + anonymous_quota.get_monthly_variant_max()


def test_add_units_increments_extra_attributes(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    before = anonymous_quota.extra_attributes
    anonymous_quota.add_units()
    assert anonymous_quota.extra_attributes == \
        before + anonymous_quota.get_monthly_attribute_max()


def test_add_units_grants_every_declared_extra_field(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    # Driven off the declaration rather than naming the three extras, so a
    # resource added to RESOURCE_FIELDS and forgotten in add_units fails here
    # instead of silently never receiving a grant -- the admin panel would
    # report success and the user would get nothing (gain#788).
    configured = settings.QUERY_QUOTAS["anonymous"]

    anonymous_quota.add_units()

    for _, monthly, extra in Quota.RESOURCE_FIELDS.values():
        assert getattr(anonymous_quota, extra) == configured[monthly], extra


def test_add_units_clamps_negative_extras_before_adding(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    anonymous_quota.extra_jobs = -5
    anonymous_quota.extra_variants = -100
    anonymous_quota.extra_attributes = -1_000
    anonymous_quota.save()
    anonymous_quota.add_units()
    assert anonymous_quota.extra_jobs == anonymous_quota.get_monthly_job_max()
    assert anonymous_quota.extra_variants == (
        anonymous_quota.get_monthly_variant_max()
    )
    assert anonymous_quota.extra_attributes == \
        anonymous_quota.get_monthly_attribute_max()


def test_add_units_accumulates_on_repeated_calls(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    anonymous_quota.add_units()
    anonymous_quota.add_units()
    assert anonymous_quota.extra_jobs == (
        2 * anonymous_quota.get_monthly_job_max()
    )
    assert anonymous_quota.extra_variants == \
        2 * anonymous_quota.get_monthly_variant_max()


def test_add_units_persisted(anonymous_quota: AnonymousUserQuota) -> None:
    anonymous_quota.add_units()
    refreshed = AnonymousUserQuota.objects.get(pk=anonymous_quota.pk)
    assert refreshed.extra_jobs == anonymous_quota.get_monthly_job_max()
    assert refreshed.extra_variants == anonymous_quota.get_monthly_variant_max()
    assert refreshed.extra_attributes == (
        anonymous_quota.get_monthly_attribute_max()
    )


# --- User.get_quota ---

def test_user_get_quota_creates_when_missing() -> None:
    user = User.objects.get(email="user@example.com")
    assert not UserQuota.objects.filter(user=user).exists()

    quota = user.get_quota()

    assert isinstance(quota, QuotaSnapshot)
    assert UserQuota.objects.filter(user=user).exists()


def test_user_get_quota_initializes_values() -> None:
    user = User.objects.get(email="user@example.com")

    quota = user.get_quota()

    assert quota.daily_jobs == quota.get_daily_job_max()
    assert quota.monthly_jobs == quota.get_monthly_job_max()
    assert quota.daily_variants == quota.get_daily_variant_max()
    assert quota.monthly_variants == quota.get_monthly_variant_max()
    assert quota.daily_attributes == quota.get_daily_attribute_max()
    assert quota.monthly_attributes == quota.get_monthly_attribute_max()


def test_user_get_quota_returns_existing(user_quota: UserQuota) -> None:
    _leave(user_quota, daily_jobs=5)
    user_quota.save()
    user = User.objects.get(email="user@example.com")

    quota = user.get_quota()

    assert isinstance(quota, QuotaSnapshot)
    assert quota.daily_jobs == 5


def test_user_get_quota_does_not_duplicate(user_quota: UserQuota) -> None:
    user = User.objects.get(email="user@example.com")

    user.get_quota()
    user.get_quota()

    assert UserQuota.objects.filter(user=user).count() == 1


# --- WebAnnotationAnonymousUser.get_quota ---

def test_anonymous_user_get_quota_creates_when_missing() -> None:
    anon = WebAnnotationAnonymousUser(session_id="test-session", ip="10.0.0.1")
    assert not AnonymousUserQuota.objects.filter(ip="10.0.0.1").exists()
    assert not SessionQuota.objects.filter(session_id="test-session").exists()

    quota = anon.get_quota()

    assert isinstance(quota, QuotaSnapshot)
    assert AnonymousUserQuota.objects.filter(ip="10.0.0.1").exists()
    assert SessionQuota.objects.filter(session_id="test-session").exists()


def test_anonymous_user_get_quota_initializes_values() -> None:
    anon = WebAnnotationAnonymousUser(session_id="test-session", ip="10.0.0.2")

    quota = anon.get_quota()

    assert quota.daily_jobs == quota.get_daily_job_max()
    assert quota.monthly_jobs == quota.get_monthly_job_max()
    assert quota.daily_variants == quota.get_daily_variant_max()
    assert quota.monthly_variants == quota.get_monthly_variant_max()
    assert quota.daily_attributes == quota.get_daily_attribute_max()
    assert quota.monthly_attributes == quota.get_monthly_attribute_max()


def test_anonymous_user_get_quota_returns_existing(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    _leave(anonymous_quota, daily_jobs=3)
    anonymous_quota.save()
    anon = WebAnnotationAnonymousUser(session_id="test-session", ip="127.0.0.1")

    quota = anon.get_quota()

    assert isinstance(quota, QuotaSnapshot)
    assert quota.daily_jobs == 3


def test_anonymous_user_get_quota_minimum_of_session_and_ip() -> None:
    ip_quota = AnonymousUserQuota.objects.create(ip="10.0.0.3")
    _leave(ip_quota, daily_jobs=7)
    ip_quota.save()

    session_quota = SessionQuota.objects.create(session_id="low-session")
    _leave(session_quota, daily_jobs=2)
    session_quota.save()

    anon = WebAnnotationAnonymousUser(session_id="low-session", ip="10.0.0.3")
    quota = anon.get_quota()

    assert quota.daily_jobs == 2


@pytest.mark.parametrize("exhausted", ["session", "ip"])
def test_the_merged_anonymous_quota_refuses_when_either_quota_would(
    exhausted: str,
) -> None:
    # The merged snapshot carries the predicates that gate access, so it has
    # to inherit a refusal from *either* row. Exercised from both sides, and
    # with the two rows holding different consumption, because a merge that
    # picked the more permissive side is invisible when they agree -- and the
    # suite cannot fall back on their limits differing, since both anonymous
    # quota classes resolve to the same configuration block (gain#750).
    spent = settings.QUERY_QUOTAS["anonymous"]["daily_variants"]
    session_quota = SessionQuota.objects.create(session_id="merge-session")
    ip_quota = AnonymousUserQuota.objects.create(ip="10.0.0.9")
    refusing = session_quota if exhausted == "session" else ip_quota
    permitting = ip_quota if exhausted == "session" else session_quota
    refusing.daily_variants = spent
    refusing.save()

    merged = WebAnnotationAnonymousUser(
        session_id="merge-session", ip="10.0.0.9").get_quota()

    assert refusing.check_variant_quota(1) is False
    assert permitting.check_variant_quota(1) is True
    assert merged.check_variant_quota(1) is False


def test_merging_snapshots_configured_differently_fails_loudly(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    # The merge takes both rows' limits, which is coherent only because the
    # session and IP quota classes resolve to the same configuration block.
    # That assumption was harmless while the predicates ignored limits
    # entirely; now that they measure against them, merging rows configured
    # differently yields an object describing no real row. Refuse to answer
    # rather than answer wrongly (gain#750).
    snapshot = QuotaSnapshot.from_quota(anonymous_quota)
    diverging = dataclasses.replace(
        snapshot, max_daily_variants=snapshot.max_daily_variants + 1)

    with pytest.raises(ValueError, match="max_daily_variants"):
        QuotaSnapshot.minimum(snapshot, diverging)


def test_the_session_and_ip_quota_types_are_configured_identically() -> None:
    # The premise the merge above depends on. Stated here so that pointing
    # one of the two classes at its own configuration block fails on this
    # single assertion rather than on whichever merged request happens to
    # notice first.
    assert SessionQuota._quota_config() == AnonymousUserQuota._quota_config()


def test_anonymous_user_get_quota_does_not_duplicate(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    anon = WebAnnotationAnonymousUser(session_id="test-session", ip="127.0.0.1")

    anon.get_quota()
    anon.get_quota()

    assert AnonymousUserQuota.objects.filter(ip="127.0.0.1").count() == 1
    assert SessionQuota.objects.filter(session_id="test-session").count() == 1


def test_job_complete_does_not_overwrite_a_concurrent_write(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    stale = AnonymousUserQuota.objects.get(pk=anonymous_quota.pk)
    AnonymousUserQuota.objects.filter(pk=anonymous_quota.pk).update(
        daily_variants=4_000, extra_jobs=7)

    stale.job_complete(variants_count=1_000, attributes_count=0)

    stored = AnonymousUserQuota.objects.get(pk=anonymous_quota.pk)
    # The concurrent write left 4_000 consumed, so the charge has to land on
    # top of that. A stale instance would charge the zero it read and store
    # 1_000, losing the 4_000 -- which is the whole point of the re-read.
    assert stored.daily_variants == 5_000
    assert stored.extra_jobs == 7


def test_single_allele_query_complete_does_not_overwrite_a_concurrent_write(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    stale = AnonymousUserQuota.objects.get(pk=anonymous_quota.pk)
    AnonymousUserQuota.objects.filter(pk=anonymous_quota.pk).update(
        daily_attributes=800, extra_jobs=7)

    stale.single_allele_query_complete(attributes_count=300)

    stored = AnonymousUserQuota.objects.get(pk=anonymous_quota.pk)
    assert stored.daily_attributes == 1_100
    assert stored.extra_jobs == 7


def test_add_units_does_not_overwrite_a_concurrent_write(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    stale = AnonymousUserQuota.objects.get(pk=anonymous_quota.pk)
    AnonymousUserQuota.objects.filter(pk=anonymous_quota.pk).update(
        daily_jobs=4, extra_jobs=6)

    stale.add_units()

    stored = AnonymousUserQuota.objects.get(pk=anonymous_quota.pk)
    assert stored.extra_jobs == 6 + anonymous_quota.get_monthly_job_max()
    assert stored.daily_jobs == 4


def test_reset_daily_does_not_overwrite_a_concurrent_write(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    # The refresh commands read every row and write each one back, so a write
    # committing in between is carried off again unless the reset touches only
    # its own period's columns (gain#768). The sentinels stand in for the two
    # losses the issue names: the other period's counters are where a
    # consumption's deduction lands, the extras are where an admin's grant
    # does. Seeded off the declared tuples, as the sibling reset tests are, so
    # a counter added to a period stays covered here.
    stale = AnonymousUserQuota.objects.get(pk=anonymous_quota.pk)
    concurrent = AnonymousUserQuota.objects.get(pk=anonymous_quota.pk)
    untouched = _seed_sentinels(
        concurrent, Quota.MONTHLY_COUNTER_FIELDS + Quota.EXTRA_UNIT_FIELDS)
    concurrent.save()

    stale.reset_daily()

    stored = AnonymousUserQuota.objects.get(pk=anonymous_quota.pk)
    for field, sentinel in untouched.items():
        assert getattr(stored, field) == sentinel, field


def test_reset_monthly_does_not_overwrite_a_concurrent_write(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    stale = AnonymousUserQuota.objects.get(pk=anonymous_quota.pk)
    concurrent = AnonymousUserQuota.objects.get(pk=anonymous_quota.pk)
    untouched = _seed_sentinels(
        concurrent, Quota.DAILY_COUNTER_FIELDS + Quota.EXTRA_UNIT_FIELDS)
    concurrent.save()

    stale.reset_monthly()

    stored = AnonymousUserQuota.objects.get(pk=anonymous_quota.pk)
    for field, sentinel in untouched.items():
        assert getattr(stored, field) == sentinel, field
