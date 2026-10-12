"""Quota models: per-user, per-session and per-IP usage counters."""
from __future__ import annotations

import dataclasses
from abc import abstractmethod
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from types import MappingProxyType
from typing import Any, ClassVar, Self, cast

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone


class Quota(models.Model):
    """Model for tracking user quotas.

    A period counter stores the units **consumed** since that period was last
    refreshed, so zero is a fresh quota and no limit is stored anywhere. Every
    reader derives what is left as ``limit - consumed`` against configuration
    read at that moment, which is what makes a limit change reach an existing
    row immediately rather than at the next refresh. It may also go negative,
    for a row granted more than its limit (gain#750; ADR 0019).

    The three ``extra_*`` fields do **not** follow that convention: an
    extra-unit grant is a balance with no limit to measure against, and a
    refresh must not clear it, so those store units *remaining*. A row
    therefore holds both conventions, deliberately.

    One rule governs every write to a quota row: **write a column only if you
    re-read it under a lock or computed it yourself.** An instance's other
    columns are as stale as the read that produced it, and saving them back
    reverts whatever committed since.

    The two write paths satisfy it differently. A method that mutates a
    counter by reading it first runs inside ``_mutating_stored_row``, which
    re-reads the whole row under a lock -- so the instance is authoritative
    for every column and a full-row save is right there. Spending quota goes
    through ``_consume``, which owns that lock already. ``refresh_all_daily``
    and ``refresh_all_monthly`` take the other route: they zero their
    counters in one UPDATE per table rather than deriving values from
    anything read, so they need no lock, and in exchange write only the
    columns they themselves set -- see ``_refresh_all``.
    """
    daily_jobs = models.IntegerField(default=0)
    monthly_jobs = models.IntegerField(default=0)

    daily_variants = models.IntegerField(default=0)
    monthly_variants = models.IntegerField(default=0)

    daily_attributes = models.IntegerField(default=0)
    monthly_attributes = models.IntegerField(default=0)

    last_daily_reset = models.DateTimeField(default=timezone.now)
    last_monthly_reset = models.DateTimeField(default=timezone.now)

    extra_jobs = models.IntegerField(default=0)
    extra_variants = models.IntegerField(default=0)
    extra_attributes = models.IntegerField(default=0)

    #: The columns metering each quota resource -- its daily counter, its
    #: monthly counter, and the extra-unit field an admin grant tops up --
    #: declared once, because granting, deducting and the extras exhaustion
    #: rule all read them from here (gain#788). The names are spelled out
    #: rather than built from the resource key so that grepping a column
    #: finds its declaration; the daily and monthly ones double as the
    #: settings keys their limits are configured under.
    RESOURCE_FIELDS: ClassVar[Mapping[str, tuple[str, str, str]]] = \
        MappingProxyType({
            "jobs": (
                "daily_jobs", "monthly_jobs", "extra_jobs"),
            "variants": (
                "daily_variants", "monthly_variants", "extra_variants"),
            "attributes": (
                "daily_attributes", "monthly_attributes", "extra_attributes"),
        })

    #: The counters ``refresh_all_daily`` refreshes.
    DAILY_COUNTER_FIELDS: ClassVar[tuple[str, ...]] = tuple(
        daily for daily, _, _ in RESOURCE_FIELDS.values())

    #: The counters ``refresh_all_monthly`` refreshes.
    MONTHLY_COUNTER_FIELDS: ClassVar[tuple[str, ...]] = tuple(
        monthly for _, monthly, _ in RESOURCE_FIELDS.values())

    #: The extra-unit fields, which belong to neither period and so are
    #: refreshed by neither refresh.
    EXTRA_UNIT_FIELDS: ClassVar[tuple[str, ...]] = tuple(
        extra for _, _, extra in RESOURCE_FIELDS.values())

    #: Every period counter, i.e. every field storing units consumed, and
    #: exactly the keys each type's limits are configured under -- the
    #: identity ``_max_for`` relies on. No timestamp or extra field meters a
    #: period, so none is among them.
    COUNTER_FIELDS: ClassVar[tuple[str, ...]] = (
        *DAILY_COUNTER_FIELDS, *MONTHLY_COUNTER_FIELDS,
    )

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta class for quota model."""
        abstract = True

    @classmethod
    @abstractmethod
    def _quota_config(cls) -> dict:
        """Return the settings QUOTAS sub-dict for this quota type."""
        raise NotImplementedError

    @classmethod
    def get_or_create_for(cls, **lookup: Any) -> Self:
        """Return the quota identified by ``lookup``, creating it if absent."""
        quota, _ = cls._default_manager.get_or_create(**lookup)
        return quota

    @classmethod
    def _max_for(cls, field: str) -> int:
        """Return the configured limit the named counter is measured against.

        Indexed by column name, so a period counter's column name doubles as
        the settings key its limit is configured under. That identity is what
        makes the settings-correspondence test fire when a counter is added;
        renaming a column means giving ``RESOURCE_FIELDS`` a settings key of
        its own (gain#788).
        """
        return cast(int, cls._quota_config()[field])

    def remaining(self, field: str) -> int:
        """Return the units still available on any of the nine counters.

        A period counter stores consumption, so what is left is derived
        against the limit as configured *now* -- which is what makes a raised
        limit reach an existing row (gain#750, ADR 0019). Floored at zero, so
        a limit lowered below what a row consumed reads as exhausted rather
        than as negative headroom.

        An extra-unit field already stores what is left, and is returned as
        stored. Answering for both lets a caller walking the whole row -- the
        admin panel, the export -- ask one question per column rather than
        branch on which convention each uses.
        """
        if field in self.EXTRA_UNIT_FIELDS:
            return cast(int, getattr(self, field))
        consumed: int = getattr(self, field)
        return max(0, self._max_for(field) - consumed)

    def set_remaining(self, field: str, remaining: int) -> None:
        """Stage whatever leaves ``remaining`` units on any of the nine.

        The inverse of ``remaining``, for the admin panel: its setters name
        the units the operator wants the user to have *left*, which is the
        figure the panel displays. Storing that on a period counter directly
        would invert it -- "set daily_variants to 0" would grant a full day
        rather than exhaust it; an extra-unit field stores it as given.

        Deliberately *not* floored at zero, unlike ``remaining``: an operator
        may grant more than the configured limit, which the panel could
        always do and which the e2e suite relies on. That is stored as a
        negative consumption -- the row has used less than nothing against
        its limit -- and is spent before the limit begins to apply.

        Staged on the instance only; the caller decides how to persist it.
        """
        if field in self.EXTRA_UNIT_FIELDS:
            setattr(self, field, remaining)
            return
        setattr(self, field, self._max_for(field) - remaining)

    def get_daily_job_max(self) -> int:
        """Get the maximum number of daily jobs allowed."""
        return self._max_for("daily_jobs")

    def get_monthly_job_max(self) -> int:
        """Get the maximum number of monthly jobs allowed."""
        return self._max_for("monthly_jobs")

    def get_daily_variant_max(self) -> int:
        """Get the maximum number of daily variants allowed."""
        return self._max_for("daily_variants")

    def get_monthly_variant_max(self) -> int:
        """Get the maximum number of monthly variants allowed."""
        return self._max_for("monthly_variants")

    def get_daily_attribute_max(self) -> int:
        """Get the maximum number of daily attributes allowed."""
        return self._max_for("daily_attributes")

    def get_monthly_attribute_max(self) -> int:
        """Get the maximum number of monthly attributes allowed."""
        return self._max_for("monthly_attributes")

    @classmethod
    def _refresh_all(cls, fields: tuple[str, ...], stamp_field: str) -> None:
        """Refresh one period on every row of this table, in one UPDATE.

        Every counter named in ``fields`` is zeroed -- a refreshed period has
        consumed nothing -- so a counter added to the period's tuple
        refreshes without a second edit here. Counters of the *other* period,
        and the extra-unit fields, are left out of the UPDATE entirely; the
        extras store units remaining and a refresh must not clear a granted
        balance.

        Zeroing rather than writing a configured limit is what makes the
        refresh independent of configuration: it does not bake the limit of
        the moment into the row, so a limit changed after a refresh still
        reaches the row (gain#750). It is also what lets one statement serve
        every row, since nothing written depends on the row.

        The statement loads no rows, so the table is never materialised and
        a caller's transaction holds the row locks only for this UPDATE and
        whatever follows it. Writing only the period's own columns keeps
        the rule in the class docstring (gain#768): nothing is read,
        so nothing stale can be written back over a consumption's deduction
        or an admin's extra-unit grant committed meanwhile.
        """
        cls._default_manager.update(
            **dict.fromkeys(fields, 0), **{stamp_field: timezone.now()})

    @classmethod
    def refresh_all_daily(cls) -> None:
        """Zero every row's daily counters and stamp ``last_daily_reset``."""
        cls._refresh_all(cls.DAILY_COUNTER_FIELDS, "last_daily_reset")

    @classmethod
    def refresh_all_monthly(cls) -> None:
        """Zero every row's monthly counters, stamp ``last_monthly_reset``."""
        cls._refresh_all(cls.MONTHLY_COUNTER_FIELDS, "last_monthly_reset")

    def add_units(self) -> None:
        """Grant every resource a further month's worth of extra units.

        Driven off ``RESOURCE_FIELDS`` so a resource cannot be declared and
        then silently left out of the grant, which would have the admin panel
        report a success the user never receives.
        """
        with self._mutating_stored_row():
            for _, monthly, extra in self.RESOURCE_FIELDS.values():
                current = max(getattr(self, extra), 0)
                setattr(self, extra, current + self._max_for(monthly))

    # The gating predicates below answer from a snapshot of this row rather
    # than reimplementing the same policy. Production reaches only the
    # snapshot's copies -- every gate goes through ``get_quota()`` -- so a
    # parallel implementation here could drift, in a different unit
    # convention, with the suite green either way (gain#750).

    def check_job_quota(self) -> bool:
        """Check if the user has quota for a job."""
        return QuotaSnapshot.from_quota(self).check_job_quota()

    def check_variant_quota(self, variants_count: int) -> bool:
        """Check if the user has the necessary variant quota."""
        return QuotaSnapshot.from_quota(self).check_variant_quota(
            variants_count)

    def check_attribute_quota(self, attributes_count: int) -> bool:
        """Check if the user has the necessary attribute quota."""
        return QuotaSnapshot.from_quota(self).check_attribute_quota(
            attributes_count)

    def single_allele_allowed(self, attributes_count: int) -> bool:
        """Check if a single query is allowed."""
        return QuotaSnapshot.from_quota(self).single_allele_allowed(
            attributes_count)

    def job_allowed(self, variants_count: int, attributes_count: int) -> bool:
        """Check if a job is allowed based on the current quotas."""
        return QuotaSnapshot.from_quota(self).job_allowed(
            variants_count, attributes_count)

    @contextmanager
    def _mutating_stored_row(self) -> Iterator[None]:
        """Apply the enclosed mutation to the stored row, under a row lock.

        Consuming quota is a read-modify-write, so two of them overlapping
        would otherwise lose a deduction: each reads the same counters and the
        second write erases the first. The row is therefore re-read under
        ``select_for_update`` and written back within one transaction, which
        serialises the whole read-modify-write against any other consumer of
        the same row.

        The re-read lands in ``self``, so the enclosed mutation and the caller
        that inspects the instance afterwards both see the stored values. Any
        unsaved change staged on the instance beforehand is discarded -- the
        stored row, not the instance, is what a consumption operates on.

        ``select_for_update`` is a silent no-op on SQLite, so the guarantee
        this provides is real only on the PostgreSQL backend that production
        and the e2e stack run; nothing here needs a backend guard, but a test
        of the locking itself cannot be written against the unit suite.
        """
        using = self._state.db
        with transaction.atomic(using=using):
            self.refresh_from_db(
                from_queryset=self._meta.base_manager
                .using(using).select_for_update())
            yield  # ruff: ignore[fallible-context-manager] -- atomic() rolls the row back on error; saving on the way out would persist what the rollback discards
            self.save(using=using)

    def _consume(self, *deductions: tuple[str, int]) -> None:
        """Charge each ``(resource, amount)`` to the stored row, as one write.

        The single way to spend quota: it owns the row lock, so there is no
        path that deducts without one. Order is significant -- an earlier
        deduction can zero the extras a later one reads.

        Callers name the resource and the amount, which is all they know; the
        columns metering it come from ``RESOURCE_FIELDS``. Amounts cannot live
        in that declaration because they belong to the call, not the resource.
        """
        with self._mutating_stored_row():
            for resource, amount in deductions:
                self._deduct(*self.RESOURCE_FIELDS[resource], amount)

    def _charge(self, field: str, amount: int) -> None:
        """Add ``amount`` to a period counter, stopping at its limit.

        Consumption is not recorded past the limit -- the same forgiveness
        the old floor-at-zero gave, and deliberate rather than inherited:
        when extras cover an overshoot the excess is already charged there,
        so recording it here too would charge the same units twice and
        surface as missing headroom after a limit raise (gain#750).

        A row already *above* its limit, which lowering a limit leaves
        behind, is never charged back down to it -- hence the stop being the
        larger of the limit and what is already consumed.
        """
        consumed: int = getattr(self, field)
        ceiling = max(self._max_for(field), consumed)
        setattr(self, field, min(consumed + amount, ceiling))

    def _deduct(
        self,
        daily_field: str,
        monthly_field: str,
        extra_field: str,
        amount: int,
    ) -> None:
        """Charge `amount` to daily and monthly (each stopping at its limit).
        Only consume from extras if the more-limiting period could not fully
        cover the amount, and only once. If extras are fully consumed,
        zero out all extra quotas.

        The extras arithmetic reads each period's headroom rather than its
        stored column, so storing consumption does not change it:
        ``remaining`` returns what the column used to hold, except that it
        floors at zero where the old column could hold a value a lowered
        limit had stranded above it."""
        daily_before = self.remaining(daily_field)
        monthly_before = self.remaining(monthly_field)
        self._charge(daily_field, amount)
        self._charge(monthly_field, amount)
        extra_deduction = max(0, amount - max(daily_before, monthly_before))
        new_extra = getattr(self, extra_field) - extra_deduction
        setattr(self, extra_field, new_extra)
        if new_extra <= 0 < extra_deduction:
            for field in self.EXTRA_UNIT_FIELDS:
                setattr(self, field, 0)

    def job_complete(self, variants_count: int, attributes_count: int) -> None:
        """Update quotas after a job is completed."""
        self._consume(
            ("jobs", 1),
            ("variants", variants_count),
            ("attributes", attributes_count),
        )

    def single_allele_query_complete(self, attributes_count: int) -> None:
        """Update quotas after a single allele query is completed."""
        self._consume(
            ("variants", 1),
            ("attributes", attributes_count),
        )


@dataclasses.dataclass(frozen=True)
class QuotaSnapshot:
    """Immutable quota state snapshot for read-only checks and views.

    The nine counter fields hold units **remaining**, unlike the model's six
    period columns, which store units consumed. The conversion happens once,
    in ``from_quota``, against the limit configured at that moment.

    Keeping the snapshot in remaining terms is what makes a limit live
    without touching anything downstream: the predicates below, ``minimum``,
    the quota endpoint and the admin panel's response all read the same
    figures they always did, but recomputed per read instead of per period
    reset (gain#750; ADR 0019). It also keeps ``minimum`` a minimum -- under
    consumed counters "more restrictive" would invert to a maximum for six of
    the nine fields and not the other three, which is easy to port wrongly
    and impossible for the suite to catch, since both anonymous quota classes
    resolve to the same configuration.
    """
    daily_jobs: int
    monthly_jobs: int
    daily_variants: int
    monthly_variants: int
    daily_attributes: int
    monthly_attributes: int
    extra_jobs: int
    extra_variants: int
    extra_attributes: int
    max_daily_jobs: int
    max_monthly_jobs: int
    max_daily_variants: int
    max_monthly_variants: int
    max_daily_attributes: int
    max_monthly_attributes: int

    @classmethod
    def from_quota(cls, quota: Quota) -> QuotaSnapshot:
        """Read a row's headroom against the limits configured right now."""
        return cls(
            daily_jobs=quota.remaining("daily_jobs"),
            monthly_jobs=quota.remaining("monthly_jobs"),
            daily_variants=quota.remaining("daily_variants"),
            monthly_variants=quota.remaining("monthly_variants"),
            daily_attributes=quota.remaining("daily_attributes"),
            monthly_attributes=quota.remaining("monthly_attributes"),
            extra_jobs=quota.extra_jobs,
            extra_variants=quota.extra_variants,
            extra_attributes=quota.extra_attributes,
            max_daily_jobs=quota.get_daily_job_max(),
            max_monthly_jobs=quota.get_monthly_job_max(),
            max_daily_variants=quota.get_daily_variant_max(),
            max_monthly_variants=quota.get_monthly_variant_max(),
            max_daily_attributes=quota.get_daily_attribute_max(),
            max_monthly_attributes=quota.get_monthly_attribute_max(),
        )

    @staticmethod
    def _require_identical_limits(a: QuotaSnapshot, b: QuotaSnapshot) -> None:
        """Refuse to merge two snapshots configured with different limits.

        A merge mixes two rows' counters *and* their limits, which describes
        a real quota only while both are configured identically -- true today
        because the session and IP classes resolve to the same block, and
        silent until now because the predicates ignored limits. Measuring
        headroom against a limit makes it load-bearing, so it is asserted
        rather than assumed (gain#750).
        """
        mismatched = {
            name: (getattr(a, name), getattr(b, name))
            for name in _SNAPSHOT_LIMIT_FIELDS
            if getattr(a, name) != getattr(b, name)
        }
        if mismatched:
            raise ValueError(
                "Refusing to merge quota snapshots configured with different "
                f"limits: {mismatched}",
            )

    @classmethod
    def minimum(cls, a: QuotaSnapshot, b: QuotaSnapshot) -> QuotaSnapshot:
        """Return the more restrictive snapshot (field-wise minimum).

        A minimum throughout, the six period counters included: every field
        holds units remaining, so fewer is stricter for all of them.
        """
        cls._require_identical_limits(a, b)
        return cls(
            daily_jobs=min(a.daily_jobs, b.daily_jobs),
            monthly_jobs=min(a.monthly_jobs, b.monthly_jobs),
            daily_variants=min(a.daily_variants, b.daily_variants),
            monthly_variants=min(a.monthly_variants, b.monthly_variants),
            daily_attributes=min(a.daily_attributes, b.daily_attributes),
            monthly_attributes=min(a.monthly_attributes, b.monthly_attributes),
            extra_jobs=min(a.extra_jobs, b.extra_jobs),
            extra_variants=min(a.extra_variants, b.extra_variants),
            extra_attributes=min(a.extra_attributes, b.extra_attributes),
            # From ``a`` alone: the guard proved both carry the same limits,
            # and a minimum here would imply they may differ.
            max_daily_jobs=a.max_daily_jobs,
            max_monthly_jobs=a.max_monthly_jobs,
            max_daily_variants=a.max_daily_variants,
            max_monthly_variants=a.max_monthly_variants,
            max_daily_attributes=a.max_daily_attributes,
            max_monthly_attributes=a.max_monthly_attributes,
        )

    def limit_for(self, counter: str) -> int:
        """Return the limit the named period counter is measured against.

        Each period counter's limit is carried as ``max_<counter>``.
        """
        return cast(int, getattr(self, f"max_{counter}"))

    def check_job_quota(self) -> bool:
        """Check if there is quota available for a job."""
        if self.extra_jobs > 0:
            return True
        return not (self.daily_jobs <= 0 or self.monthly_jobs <= 0)

    def check_variant_quota(self, variants_count: int) -> bool:
        """Check if there are units available for a number of variants."""
        if (
            self.extra_variants > 0
            and self.monthly_variants + self.extra_variants >= variants_count
        ):
            return True
        if self.daily_variants <= 0 or self.monthly_variants <= 0:
            return False
        return not (
            self.daily_variants < variants_count
            or self.monthly_variants < variants_count
        )

    def check_attribute_quota(self, attributes_count: int) -> bool:
        """Check if there is quota for the given number of attributes."""
        if self.extra_attributes > 0:
            total = self.monthly_attributes + self.extra_attributes
            if total >= attributes_count:
                return True
        if self.daily_attributes <= 0 or self.monthly_attributes <= 0:
            return False
        return not (
            self.daily_attributes < attributes_count
            or self.monthly_attributes < attributes_count
        )

    def single_allele_allowed(self, attributes_count: int) -> bool:
        """Check if a single-allele query is within quota."""
        return (
            self.check_variant_quota(1)
            and self.check_attribute_quota(attributes_count)
        )

    def job_allowed(self, variants_count: int, attributes_count: int) -> bool:
        """Check if a full job is within quota."""
        return (
            self.check_job_quota()
            and self.check_attribute_quota(attributes_count)
            and self.check_variant_quota(variants_count)
        )


#: The snapshot fields carrying a configured limit rather than a count.
#: Derived once at import, not per merge: ``minimum`` runs on every anonymous
#: quota read, where reflecting over the dataclass was its largest cost.
_SNAPSHOT_LIMIT_FIELDS: tuple[str, ...] = tuple(
    field.name for field in dataclasses.fields(QuotaSnapshot)
    if field.name.startswith("max_")
)


class AnonymousUserQuota(Quota):
    """Quota limits for anonymous (unauthenticated) users."""

    ip = models.CharField(max_length=256, default="", unique=True)

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta class for anonymous user quotas."""
        db_table = "anonymous_user_quotas"

    @classmethod
    def _quota_config(cls) -> dict:
        return cast(dict, settings.QUERY_QUOTAS["anonymous"])


class SessionQuota(Quota):
    """Quota limits keyed by session ID."""

    session_id = models.CharField(max_length=1024, unique=True)

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta class for session quotas."""
        db_table = "session_quotas"

    @classmethod
    def _quota_config(cls) -> dict:
        return cast(dict, settings.QUERY_QUOTAS["anonymous"])


class UserQuota(Quota):
    """Quota limits for authenticated users."""

    user = models.OneToOneField(
        "web_annotation.User",
        related_name="quota",
        on_delete=models.CASCADE,
    )

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta class for user quotas."""
        db_table = "user_quotas"

    @classmethod
    def _quota_config(cls) -> dict:
        return cast(dict, settings.QUERY_QUOTAS["user"])


class DailyQuotaRefreshLog(models.Model):
    """Tracks each execution of the daily quota refresh command."""

    executed_at = models.DateTimeField(default=timezone.now)

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta class for daily quota refresh log."""
        db_table = "daily_quota_refresh_log"


class MonthlyQuotaRefreshLog(models.Model):
    """Tracks each execution of the monthly quota refresh command."""

    executed_at = models.DateTimeField(default=timezone.now)

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta class for monthly quota refresh log."""
        db_table = "monthly_quota_refresh_log"
