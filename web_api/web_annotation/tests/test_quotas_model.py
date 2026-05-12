# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pytest

from web_annotation.models import (
    AnonymousUserQuota,
    QuotaSnapshot,
    SessionQuota,
    User,
    UserQuota,
    WebAnnotationAnonymousUser,
)


@pytest.fixture
def anonymous_quota() -> AnonymousUserQuota:
    quota = AnonymousUserQuota(ip="127.0.0.1")
    quota.reset_daily()
    quota.reset_monthly()
    return quota


@pytest.fixture
def user_quota() -> UserQuota:
    user = User.objects.get(email="user@example.com")
    quota = UserQuota(user=user)
    quota.reset_daily()
    quota.reset_monthly()
    return quota


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


def test_reset_daily_sets_all_fields(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    anonymous_quota.daily_jobs = 0
    anonymous_quota.daily_variants = 0
    anonymous_quota.daily_attributes = 0
    anonymous_quota.save()

    anonymous_quota.reset_daily()

    assert anonymous_quota.daily_jobs == \
        anonymous_quota.get_daily_job_max()
    assert anonymous_quota.daily_variants == \
        anonymous_quota.get_daily_variant_max()
    assert anonymous_quota.daily_attributes == \
        anonymous_quota.get_daily_attribute_max()


def test_reset_monthly_sets_all_fields(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    anonymous_quota.monthly_jobs = 0
    anonymous_quota.monthly_variants = 0
    anonymous_quota.monthly_attributes = 0
    anonymous_quota.save()

    anonymous_quota.reset_monthly()

    assert anonymous_quota.monthly_jobs == \
        anonymous_quota.get_monthly_job_max()
    assert anonymous_quota.monthly_variants == \
        anonymous_quota.get_monthly_variant_max()
    assert anonymous_quota.monthly_attributes == \
        anonymous_quota.get_monthly_attribute_max()


def test_reset_daily_updates_timestamp(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    before = anonymous_quota.last_daily_reset
    anonymous_quota.reset_daily()
    assert anonymous_quota.last_daily_reset >= before


def test_reset_monthly_updates_timestamp(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    before = anonymous_quota.last_monthly_reset
    anonymous_quota.reset_monthly()
    assert anonymous_quota.last_monthly_reset >= before


def test_reset_daily_persisted(anonymous_quota: AnonymousUserQuota) -> None:
    anonymous_quota.daily_jobs = 0
    anonymous_quota.save()
    anonymous_quota.reset_daily()

    refreshed = AnonymousUserQuota.objects.get(pk=anonymous_quota.pk)
    assert refreshed.daily_jobs == anonymous_quota.get_daily_job_max()


def test_reset_monthly_persisted(anonymous_quota: AnonymousUserQuota) -> None:
    anonymous_quota.monthly_jobs = 0
    anonymous_quota.save()
    anonymous_quota.reset_monthly()

    refreshed = AnonymousUserQuota.objects.get(pk=anonymous_quota.pk)
    assert refreshed.monthly_jobs == anonymous_quota.get_monthly_job_max()


def test_check_job_quota_true_when_quota_available(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    assert anonymous_quota.check_job_quota() is True


def test_check_job_quota_false_when_daily_exhausted(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    anonymous_quota.daily_jobs = 0
    assert anonymous_quota.check_job_quota() is False


def test_check_job_quota_false_when_monthly_exhausted(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    anonymous_quota.monthly_jobs = 0
    assert anonymous_quota.check_job_quota() is False


def test_check_job_quota_true_with_extra_even_when_daily_exhausted(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    anonymous_quota.daily_jobs = 0
    anonymous_quota.extra_jobs = 5
    assert anonymous_quota.check_job_quota() is True


def test_single_allele_allowed_true_when_quota_available(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    assert anonymous_quota.single_allele_allowed(attributes_count=10) is True


def test_single_allele_allowed_false_when_variant_quota_exhausted(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    anonymous_quota.daily_variants = 0
    assert anonymous_quota.single_allele_allowed(attributes_count=10) is False


def test_single_allele_allowed_true_with_extra_variant_quota(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    anonymous_quota.daily_variants = 0
    anonymous_quota.extra_variants = 1
    assert anonymous_quota.single_allele_allowed(attributes_count=10) is True


def test_single_allele_allowed_false_when_attribute_quota_exhausted(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    anonymous_quota.daily_attributes = 0
    anonymous_quota.monthly_attributes = 0
    assert anonymous_quota.single_allele_allowed(attributes_count=10) is False


def test_job_complete_decrements_job_counts(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    before_daily = anonymous_quota.daily_jobs
    before_monthly = anonymous_quota.monthly_jobs

    anonymous_quota.job_complete(variants_count=1_000, attributes_count=5_000)

    assert anonymous_quota.daily_jobs == before_daily - 1
    assert anonymous_quota.monthly_jobs == before_monthly - 1


def test_job_complete_decrements_variant_counts(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    before_daily = anonymous_quota.daily_variants
    before_monthly = anonymous_quota.monthly_variants
    variants = 1_000

    anonymous_quota.job_complete(variants_count=variants, attributes_count=0)

    assert anonymous_quota.daily_variants == before_daily - variants
    assert anonymous_quota.monthly_variants == before_monthly - variants


def test_job_complete_decrements_attribute_counts(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    before_daily = anonymous_quota.daily_attributes
    before_monthly = anonymous_quota.monthly_attributes
    attributes = 5_000

    anonymous_quota.job_complete(variants_count=0, attributes_count=attributes)

    assert anonymous_quota.daily_attributes == before_daily - attributes
    assert anonymous_quota.monthly_attributes == before_monthly - attributes


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
    anonymous_quota.daily_attributes = 0
    before_extra = anonymous_quota.extra_attributes

    anonymous_quota.job_complete(variants_count=0, attributes_count=5_000)

    assert anonymous_quota.extra_attributes == before_extra


def test_job_complete_consumes_extras_when_both_periods_exhausted(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    anonymous_quota.daily_attributes = 0
    anonymous_quota.monthly_attributes = 0
    anonymous_quota.extra_attributes = 20_000

    anonymous_quota.job_complete(variants_count=0, attributes_count=5_000)

    assert anonymous_quota.extra_attributes == 20_000 - 5_000


def test_job_complete_consumes_extras_for_remainder_beyond_max_period(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    # daily=3, monthly=3 → max=3, amount=10 → extras cover 7
    anonymous_quota.daily_attributes = 3
    anonymous_quota.monthly_attributes = 3
    anonymous_quota.extra_attributes = 20

    anonymous_quota.job_complete(variants_count=0, attributes_count=10)

    assert anonymous_quota.daily_attributes == 0
    assert anonymous_quota.monthly_attributes == 0
    assert anonymous_quota.extra_attributes == 20 - 7


def test_job_complete_zeros_all_extras_when_extra_exhausted(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    anonymous_quota.daily_attributes = 0
    anonymous_quota.monthly_attributes = 0
    anonymous_quota.extra_attributes = 5_000
    anonymous_quota.extra_jobs = 50
    anonymous_quota.extra_variants = 500_000

    anonymous_quota.job_complete(variants_count=0, attributes_count=5_000)

    assert anonymous_quota.extra_attributes == 0
    assert anonymous_quota.extra_jobs == 0
    assert anonymous_quota.extra_variants == 0


def test_job_complete_zeros_all_extras_when_extra_overdrawn(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    anonymous_quota.daily_attributes = 0
    anonymous_quota.monthly_attributes = 0
    anonymous_quota.extra_attributes = 3_000
    anonymous_quota.extra_jobs = 50

    anonymous_quota.job_complete(variants_count=0, attributes_count=5_000)

    assert anonymous_quota.extra_attributes == 0
    assert anonymous_quota.extra_jobs == 0


def test_job_complete_does_not_zero_extras_when_partial_consumption(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    anonymous_quota.daily_attributes = 0
    anonymous_quota.monthly_attributes = 0
    anonymous_quota.extra_attributes = 10_000
    anonymous_quota.extra_jobs = 50

    anonymous_quota.job_complete(variants_count=0, attributes_count=5_000)

    assert anonymous_quota.extra_attributes == 5_000
    assert anonymous_quota.extra_jobs == 50


def test_job_complete_persisted(anonymous_quota: AnonymousUserQuota) -> None:
    anonymous_quota.job_complete(variants_count=500, attributes_count=2_000)

    refreshed = AnonymousUserQuota.objects.get(pk=anonymous_quota.pk)
    assert refreshed.daily_jobs == anonymous_quota.get_daily_job_max() - 1
    assert refreshed.daily_variants \
        == anonymous_quota.get_daily_variant_max() - 500
    assert refreshed.daily_attributes == \
        anonymous_quota.get_daily_attribute_max() - 2_000


def test_single_allele_query_complete_decrements_variant_counts(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    before_daily = anonymous_quota.daily_variants
    before_monthly = anonymous_quota.monthly_variants

    anonymous_quota.single_allele_query_complete(attributes_count=10)

    assert anonymous_quota.daily_variants == before_daily - 1
    assert anonymous_quota.monthly_variants == before_monthly - 1


def test_single_allele_query_complete_decrements_attribute_counts(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    before_daily = anonymous_quota.daily_attributes
    before_monthly = anonymous_quota.monthly_attributes
    attributes = 10

    anonymous_quota.single_allele_query_complete(attributes_count=attributes)

    assert anonymous_quota.daily_attributes == before_daily - attributes
    assert anonymous_quota.monthly_attributes == before_monthly - attributes


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
    anonymous_quota.daily_attributes = 0
    before_extra = anonymous_quota.extra_attributes

    anonymous_quota.single_allele_query_complete(attributes_count=10)

    assert anonymous_quota.extra_attributes == before_extra


def test_single_allele_query_complete_consumes_extras_when_both_periods_exhausted(  # noqa: E501
    anonymous_quota: AnonymousUserQuota,
) -> None:
    anonymous_quota.daily_attributes = 0
    anonymous_quota.monthly_attributes = 0
    anonymous_quota.extra_attributes = 50

    anonymous_quota.single_allele_query_complete(attributes_count=10)

    assert anonymous_quota.extra_attributes == 40


def test_single_allele_query_complete_consumes_extras_for_remainder_beyond_max_period(  # noqa: E501
    anonymous_quota: AnonymousUserQuota,
) -> None:
    # daily=4, monthly=4 → max=4, amount=10 → extras cover 6
    anonymous_quota.daily_attributes = 4
    anonymous_quota.monthly_attributes = 4
    anonymous_quota.extra_attributes = 20

    anonymous_quota.single_allele_query_complete(attributes_count=10)

    assert anonymous_quota.daily_attributes == 0
    assert anonymous_quota.monthly_attributes == 0
    assert anonymous_quota.extra_attributes == 14


def test_single_allele_query_complete_zeros_all_extras_when_extra_exhausted(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    anonymous_quota.daily_attributes = 0
    anonymous_quota.monthly_attributes = 0
    anonymous_quota.extra_attributes = 10
    anonymous_quota.extra_variants = 5

    anonymous_quota.single_allele_query_complete(attributes_count=10)

    assert anonymous_quota.extra_attributes == 0
    assert anonymous_quota.extra_variants == 0


def test_single_allele_query_complete_does_not_zero_extras_when_partial_consumption(  # noqa: E501
    anonymous_quota: AnonymousUserQuota,
) -> None:
    anonymous_quota.daily_attributes = 0
    anonymous_quota.monthly_attributes = 0
    anonymous_quota.extra_attributes = 50
    anonymous_quota.extra_variants = 5

    anonymous_quota.single_allele_query_complete(attributes_count=10)

    assert anonymous_quota.extra_attributes == 40
    assert anonymous_quota.extra_variants == 5


def test_single_allele_query_complete_persisted(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    anonymous_quota.single_allele_query_complete(attributes_count=10)

    refreshed = AnonymousUserQuota.objects.get(pk=anonymous_quota.pk)
    assert refreshed.daily_variants == \
        anonymous_quota.get_daily_variant_max() - 1
    assert refreshed.daily_attributes == \
        anonymous_quota.get_daily_attribute_max() - 10


def test_user_quota_linked_to_user(user_quota: UserQuota) -> None:
    user = User.objects.get(email="user@example.com")
    assert user_quota.user == user


def test_user_quota_reset_daily(user_quota: UserQuota) -> None:
    user_quota.daily_jobs = 0
    user_quota.save()
    user_quota.reset_daily()
    assert user_quota.daily_jobs == user_quota.get_daily_job_max()


def test_user_quota_job_complete(user_quota: UserQuota) -> None:
    before = user_quota.daily_jobs
    user_quota.job_complete(variants_count=100, attributes_count=500)
    assert user_quota.daily_jobs == before - 1


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


def test_add_units_clamps_negative_extras_before_adding(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    anonymous_quota.extra_jobs = -5
    anonymous_quota.extra_variants = -100
    anonymous_quota.extra_attributes = -1_000
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
    user_quota.daily_jobs = 5
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
    anonymous_quota.daily_jobs = 3
    anonymous_quota.save()
    anon = WebAnnotationAnonymousUser(session_id="test-session", ip="127.0.0.1")

    quota = anon.get_quota()

    assert isinstance(quota, QuotaSnapshot)
    assert quota.daily_jobs == 3


def test_anonymous_user_get_quota_minimum_of_session_and_ip() -> None:
    ip_quota = AnonymousUserQuota(ip="10.0.0.3")
    ip_quota.reset_daily()
    ip_quota.reset_monthly()
    ip_quota.daily_jobs = 7
    ip_quota.save()

    session_quota = SessionQuota(session_id="low-session")
    session_quota.reset_daily()
    session_quota.reset_monthly()
    session_quota.daily_jobs = 2
    session_quota.save()

    anon = WebAnnotationAnonymousUser(session_id="low-session", ip="10.0.0.3")
    quota = anon.get_quota()

    assert quota.daily_jobs == 2


def test_anonymous_user_get_quota_does_not_duplicate(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    anon = WebAnnotationAnonymousUser(session_id="test-session", ip="127.0.0.1")

    anon.get_quota()
    anon.get_quota()

    assert AnonymousUserQuota.objects.filter(ip="127.0.0.1").count() == 1
    assert SessionQuota.objects.filter(session_id="test-session").count() == 1
