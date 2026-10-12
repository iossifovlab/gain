"""Web annotation django models."""
# pylint: disable=unused-import

from __future__ import annotations

import os
import pathlib
import time
import uuid
from abc import abstractmethod
from datetime import timedelta
from typing import Any, ClassVar, cast

from django.conf import settings
from django.contrib.auth.models import AbstractUser, AnonymousUser
from django.db import connection, models
from django.utils import timezone
from gain import logging

from web_annotation.mail import send_email
from web_annotation.quota_models import (  # ruff: ignore[unused-import]
    AnonymousUserQuota,
    DailyQuotaRefreshLog,
    MonthlyQuotaRefreshLog,
    Quota,
    QuotaSnapshot,
    SessionQuota,
    UserQuota,
)

logger = logging.getLogger(__name__)


class BaseUser:
    """Base user class for helper functions."""

    @property
    def job_class(self) -> type[BaseJob]:
        """Get job class used."""
        raise NotImplementedError

    @property
    def identifier(self) -> str:
        """Get identifier for user."""
        raise NotImplementedError

    @property
    def session_id(self) -> str:
        """Get session ID for user."""
        raise NotImplementedError

    def create_job(self, **kwargs: Any) -> BaseJob:
        """Create a new job for the user."""
        raise NotImplementedError

    def check_pipeline_owner(self, pipeline: BasePipeline) -> bool:
        """Check if user is owner of the pipeline."""
        raise NotImplementedError

    def get_socket_group(self) -> str:
        """Get socket group for user."""
        raise NotImplementedError

    def get_pipeline(self, pipeline_id: str) -> BasePipeline:
        """Get pipeline from respective table, checking ownership."""
        raise NotImplementedError

    def get_quota(self) -> QuotaSnapshot:
        """Get a snapshot of the current quota state for this user."""
        raise NotImplementedError

    def quota_job_complete(
        self, variants_count: int, attributes_count: int,
    ) -> None:
        """Deduct quota after a job completes."""
        raise NotImplementedError

    def quota_single_allele_complete(self, attributes_count: int) -> None:
        """Deduct quota after a single-allele query completes."""
        raise NotImplementedError

    def delete_pipelines(self) -> None:
        """Delete user pipelines."""
        raise NotImplementedError

    @property
    def as_owner(self) -> User | str:
        raise NotImplementedError

    def get_pipelines(self) -> list[BasePipeline]:
        """Get pipelines for user."""
        raise NotImplementedError

    @property
    def is_unlimited(self) -> bool:
        """Return whether this user bypasses quota limits."""
        return False

    def get_temporary_pipeline(
        self, session_id: str | None = None,
    ) -> BasePipeline | None:
        """Get temporary pipeline for user."""
        if session_id is None:
            session_id = self.session_id
        pipeline = TemporaryPipeline.objects.filter(
            session_id=session_id).first()
        if pipeline is None:
            return None
        if session_id != self.session_id:
            raise ValueError("Session ID does not match user session!")
        return cast(BasePipeline, pipeline)

    @abstractmethod
    def can_create(self) -> bool:
        """Check if a user is not limited by the daily quota."""


class UserWrapper(BaseUser):
    """Wrapper for user objects to implement BaseUser functions."""

    def __init__(self, user: User, session_id: str) -> None:
        self.user = user
        self._session_id = session_id

    @property
    def job_class(self) -> type[Job]:
        """Get job class used."""
        return self.user.job_class

    @property
    def identifier(self) -> str:
        """Get identifier for user."""
        return self.user.identifier

    @property
    def session_id(self) -> str:
        """Get session ID for user."""
        return self._session_id

    def create_job(self, **kwargs: Any) -> Job:
        """Create a new job for the user."""
        return self.user.create_job(**kwargs)

    def check_pipeline_owner(self, pipeline: BasePipeline) -> bool:
        """Check if user is owner of the pipeline."""
        return self.user.check_pipeline_owner(pipeline)

    def can_create(self) -> bool:
        """Check if a user is not limited by the daily quota."""
        return self.user.can_create()

    def generate_job_name(self) -> int:
        """Generate a new job name for the user."""
        return self.user.generate_job_name()

    def get_socket_group(self) -> str:
        """Get socket group for user."""
        return self.user.get_socket_group()

    def get_pipeline(self, pipeline_id: str) -> BasePipeline:
        """Get pipeline from respective table, checking ownership."""
        pipeline = Pipeline.objects.filter(
            pk=int(pipeline_id),
        ).first()
        if pipeline is None:
            raise ValueError(f"Pipeline {pipeline_id} not found!")
        if not self.check_pipeline_owner(cast(BasePipeline, pipeline)):
            raise ValueError("User not authorized to access pipeline!")

        return cast(BasePipeline, pipeline)

    def get_pipelines(self) -> list[BasePipeline]:
        """Get pipelines for user."""
        pipelines = Pipeline.objects.filter(
            owner=cast(User, self.user.as_owner),
        )
        return list(pipelines)

    def delete_pipelines(self) -> None:
        """Delete user pipelines."""
        self.user.delete_pipelines()

    def delete_pipeline(self, pipeline_id: str) -> None:
        """Delete user pipelines."""
        self.user.delete_pipeline(pipeline_id)

    @property
    def is_superuser(self) -> bool:
        """Check if user is superuser."""
        return self.user.is_superuser

    @property
    def is_authenticated(self) -> bool:
        """Check if user is authenticated."""
        return self.user.is_authenticated

    def is_owner(self, job: Job) -> bool:
        return job.owner == self.user

    @property
    def is_staff(self) -> bool:
        return self.user.is_staff

    @property
    def pk(self) -> int:
        return self.user.pk

    @property
    def email(self) -> str:
        return self.user.email

    def get_jobs(self) -> list[Job]:
        """Get user's jobs."""
        return self.user.get_jobs()

    def get_quota(self) -> QuotaSnapshot:
        """Get a snapshot of the current quota state for this user."""
        return self.user.get_quota()

    def quota_job_complete(
        self, variants_count: int, attributes_count: int,
    ) -> None:
        """Deduct quota after a job completes."""
        self.user.quota_job_complete(variants_count, attributes_count)

    def quota_single_allele_complete(self, attributes_count: int) -> None:
        """Deduct quota after a single-allele query completes."""
        self.user.quota_single_allele_complete(attributes_count)

    @property
    def is_unlimited(self) -> bool:
        return self.user.is_unlimited

    @property
    def as_owner(self) -> User | str:
        return self.user.as_owner


class User(AbstractUser):
    """Model for user accounts."""
    email = models.EmailField(("email address"), unique=True)
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: ClassVar = []
    job_counter = models.IntegerField(default=0)
    is_unlimited = models.BooleanField(default=False)

    @property
    def job_class(self) -> type[Job]:
        """Get job class used."""
        return Job

    @property
    def identifier(self) -> str:
        """Get identifier for user."""
        return self.email

    @property
    def as_owner(self) -> User | str:
        return self

    def is_owner(self, job: Job) -> bool:
        return job.owner == self

    def check_pipeline_owner(self, pipeline: BasePipeline) -> bool:
        """Check if user is owner of the pipeline."""
        assert isinstance(pipeline, Pipeline)
        return pipeline.owner == self.as_owner

    def change_password(self, new_password: str) -> None:
        """Update user with new password."""
        self.set_password(new_password)
        self.save()

    def get_socket_group(self) -> str:
        """Get socket group for user."""
        return str(self.pk)

    def activate(self) -> None:
        """Enable a user's account."""
        self.is_active = True
        self.save()

    def generate_job_name(self) -> int:
        """Atomically allocate the next job name for this user.

        Concurrent callers (each request resolves ``request.user`` to its
        own freshly-loaded ``User`` instance) must never receive the same
        name, since the name derives the job's file paths. The increment is
        therefore performed as a single ``UPDATE`` against the row, with the
        counter as the sole source of truth, and the fresh value read back.

        The counter is also floored at the number of existing jobs so that a
        lagging counter (e.g. for users that predate the counter) cannot hand
        out a name that collides with an existing job's file paths. That floor
        is computed inside the same statement (a correlated subquery) so it,
        too, is evaluated atomically rather than read separately.

        ``UPDATE ... RETURNING`` is used so the allocate and the read-back
        happen in a single statement: each caller reads back exactly the value
        it wrote. A separate ``SELECT`` (e.g. ``refresh_from_db``) would
        reopen a TOCTOU window under READ COMMITTED, letting two concurrent
        callers read the same post-increment value and hand out duplicates.
        Both Postgres (prod) and SQLite >= 3.35 (tests) support RETURNING;
        the per-row "max" scalar is ``GREATEST`` on Postgres and ``MAX`` on
        SQLite.
        """
        # Table/column names are the verified (and migration-stable) schema
        # names for User.job_counter and Job.owner. SQLite's per-row "max"
        # scalar is MAX(...); Postgres uses GREATEST(...).
        max_fn = "MAX" if connection.vendor == "sqlite" else "GREATEST"
        # max_fn is a hardcoded keyword (MAX/GREATEST), not user input.
        sql = (
            "UPDATE web_annotation_user "  # ruff: ignore[hardcoded-sql-expression]
            f"SET job_counter = {max_fn}(job_counter, "
            "(SELECT COUNT(*) FROM web_annotation_job "
            "WHERE owner_id = %s)) + 1 "
            "WHERE id = %s "
            "RETURNING job_counter"
        )
        with connection.cursor() as cursor:
            cursor.execute(sql, [self.pk, self.pk])
            row = cursor.fetchone()

        new_counter = int(row[0])
        self.job_counter = new_counter
        return new_counter

    def create_job(self, **kwargs: Any) -> Job:
        """Create a new job for the user."""
        return self.job_class(
            owner=self,
            **kwargs,
        )

    def get_jobs(self) -> list[Job]:
        """Get user's jobs."""
        jobs = self.job_class.objects.filter(
            owner=cast(User, self.as_owner),
        )
        return list(jobs)

    def delete_jobs(self) -> None:
        """Deactivate the user's jobs, sparing any that are still running.

        A job that is still ``WAITING`` or ``IN_PROGRESS`` is left untouched so
        its result file is not unlinked out from under an in-flight annotation
        task, which would make the job's ``on_success`` ``stat`` fail with
        ``[Errno 2]``.
        """
        jobs = self.job_class.objects.filter(
            owner=cast(User, self.as_owner),
        ).exclude(
            status__in=self.job_class.ACTIVE_STATUSES,
        )
        for job in jobs:
            job.deactivate()

    def delete_pipelines(self) -> None:
        """Delete user pipelines."""
        pipelines = Pipeline.objects.filter(
            owner=cast(User, self.as_owner),
        )
        for pipeline in pipelines:
            pipeline.remove()

    def delete_pipeline(self, pipeline_id: str) -> None:
        """Delete user pipelines."""
        pipelines = Pipeline.objects.filter(
            owner=cast(User, self.as_owner),
            pk=int(pipeline_id),
        )
        for pipeline in pipelines:
            pipeline.remove()

    def can_create(self) -> bool:
        """Check if a user is not limited by the daily quota."""
        if self.is_superuser:
            return True
        today = timezone.now().replace(
            hour=0, minute=0, second=0, microsecond=0)
        jobs_made = self.job_class.objects.filter(
            created__gte=today, owner__exact=self.pk)
        return not len(jobs_made) >= cast(int, settings.QUOTAS["daily_jobs"])

    def _get_or_create_user_quota(self) -> UserQuota:
        return UserQuota.get_or_create_for(user=self)

    def get_quota(self) -> QuotaSnapshot:
        """Get a snapshot of the current quota state for this user."""
        return QuotaSnapshot.from_quota(self._get_or_create_user_quota())

    def quota_job_complete(
        self, variants_count: int, attributes_count: int,
    ) -> None:
        """Deduct quota after a job completes."""
        self._get_or_create_user_quota().job_complete(
            variants_count, attributes_count)

    def quota_single_allele_complete(self, attributes_count: int) -> None:
        """Deduct quota after a single-allele query completes."""
        self._get_or_create_user_quota().single_allele_query_complete(
            attributes_count)

    def quota_add_units(self) -> None:
        """Add extra quota units to this user."""
        self._get_or_create_user_quota().add_units()


class WebAnnotationAnonymousUser(BaseUser, AnonymousUser):
    """Model for anonymous user accounts."""

    def __init__(self, session_id: str, ip: str = "") -> None:
        super().__init__()
        self.ip = ip
        self._session_id = session_id

    @property
    def job_class(self) -> type[AnonymousJob]:
        """Get job class used."""
        return AnonymousJob

    @property
    def session_id(self) -> str:
        """Get session ID for user."""
        return self._session_id

    @property
    def identifier(self) -> str:
        """Get identifier for anonymous user."""
        return f"anon_{self.session_id}"

    @property
    def as_owner(self) -> User | str:
        return self.identifier

    def get_socket_group(self) -> str:
        """Get socket group for user."""
        return self.identifier

    def is_owner(self, job: AnonymousJob) -> bool:
        return job.owner == self.identifier

    def generate_job_name(self) -> int:
        return time.time_ns()

    def create_job(self, **kwargs: Any) -> AnonymousJob:
        """Create a new job for the anonymous user."""
        return self.job_class(
            owner=self.identifier,
            ip=self.ip,
            **kwargs,
        )

    def get_jobs(self) -> list[AnonymousJob]:
        """Get user's jobs."""
        jobs = self.job_class.objects.filter(
            owner=self.as_owner,
        )
        return list(jobs)

    def delete_jobs(self) -> None:
        """Anonymous jobs are never deleted on WebSocket lifecycle (#216).

        Called when an anonymous user's last WebSocket disconnects
        (see ``AnnotationStateConsumer.disconnect``). gain#147 already spared
        active (``WAITING``/``IN_PROGRESS``) jobs so their result files were
        not unlinked out from under an in-flight annotation task (its
        ``on_success`` ``stat`` would fail with ``[Errno 2]``). #216 spares
        terminal (``SUCCESS``/``FAILED``) jobs too: destroying a just-completed
        job and its result file on any last-subscriber socket drop (daphne
        restart, network blip, proxy/idle timeout, reconnect-backoff window)
        404s a captured download link.

        Result-file lifetime is therefore decoupled from the socket lifecycle;
        stale anonymous jobs are reaped by age instead, via the
        ``cleanup_anonymous_jobs`` management command. Nothing is deleted here.
        """

    def can_create(self) -> bool:
        """Check if a anonymous user is not limited by the daily quota."""
        today = timezone.now().replace(
            hour=0, minute=0, second=0, microsecond=0)
        jobs_made = self.job_class.objects.filter(
            created__gte=today, ip=self.ip)
        return not len(jobs_made) >= cast(int, settings.QUOTAS["daily_jobs"])

    def _get_or_create_session_quota(self) -> SessionQuota:
        return SessionQuota.get_or_create_for(session_id=self.session_id)

    def _get_or_create_ip_quota(self) -> AnonymousUserQuota:
        return AnonymousUserQuota.get_or_create_for(ip=self.ip)

    def get_quota(self) -> QuotaSnapshot:
        """Get the more restrictive of the session and IP quota snapshots."""
        return QuotaSnapshot.minimum(
            QuotaSnapshot.from_quota(self._get_or_create_session_quota()),
            QuotaSnapshot.from_quota(self._get_or_create_ip_quota()),
        )

    def quota_job_complete(
        self, variants_count: int, attributes_count: int,
    ) -> None:
        """Deduct units after a job completes from both quotas."""
        self._get_or_create_session_quota().job_complete(
            variants_count, attributes_count)
        self._get_or_create_ip_quota().job_complete(
            variants_count, attributes_count)

    def quota_single_allele_complete(self, attributes_count: int) -> None:
        """Deduct units from both quotas after a single query."""
        self._get_or_create_session_quota().single_allele_query_complete(
            attributes_count)
        self._get_or_create_ip_quota().single_allele_query_complete(
            attributes_count)

    def save(self) -> None:
        """Anonymous user cannot be saved."""
        raise NotImplementedError

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta class for verification model."""
        abstract = True

    def get_pipeline(self, pipeline_id: str) -> BasePipeline:
        raise NotImplementedError(
            "Anonymous users cannot have named pipelines!")

    def get_pipelines(self) -> list[BasePipeline]:
        """Get pipelines for user."""
        return []

    def delete_pipelines(self) -> None:
        """Delete user pipelines."""
        return

    def delete_pipeline(self, pipeline_id: str) -> BasePipeline:
        raise NotImplementedError(
            "Anonymous users cannot have named pipelines!")


class BasePipeline(models.Model):
    """Base Model for saving pipeline configs"""
    name = models.CharField(max_length=1024, default="")
    config_path = models.FilePathField()

    def remove(self) -> None:
        """Clean a user pipeline's resources."""
        os.remove(self.config_path)
        self.delete()

    @property
    def identifier(self) -> str:
        """Get a unique identifier for the pipeline."""
        raise NotImplementedError

    def table_id(self) -> tuple[str, str]:
        """Get a unique table ID for the pipeline."""
        return ("unspecified", str(self.pk))

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta class for verification model."""
        abstract = True


class Pipeline(BasePipeline):
    """Model for saving user created pipeline configs"""
    owner = models.ForeignKey(
        "web_annotation.User",
        related_name="pipelines",
        on_delete=models.CASCADE,

    )

    @property
    def identifier(self) -> str:
        """Get a unique identifier for the pipeline."""
        return str(self.pk)

    def table_id(self) -> tuple[str, str]:
        """Get a unique table ID for the pipeline."""
        return ("user", str(self.pk))


class TemporaryPipeline(BasePipeline):
    """Model for saving anonymous created pipeline configs"""
    session_id = models.CharField(max_length=1024, primary_key=True)

    @property
    def identifier(self) -> str:
        """Get a unique identifier for the pipeline."""
        return str(self.session_id)

    @property
    def id(self) -> str:
        return self.session_id

    def table_id(self) -> tuple[str, str]:
        """Get a unique table ID for the pipeline."""
        return ("anonymous", str(self.pk))


class AlleleQuery(models.Model):
    """Model for saving user created pipeline configs"""
    allele = models.CharField(max_length=1024)
    owner = models.ForeignKey(
        "web_annotation.User",
        related_name="allele_query",
        on_delete=models.CASCADE,
    )
    note = models.CharField(max_length=1024, default="")
    last_used = models.DateTimeField(default=timezone.now)

    def remove(self) -> None:
        """Deactivate a job and clean its resources."""
        self.delete()


class BaseJob(models.Model):
    """Base model for storing job data."""
    class Status(models.IntegerChoices):  # pylint: disable=too-many-ancestors
        """Class for job status."""
        WAITING = 1
        IN_PROGRESS = 2
        SUCCESS = 3
        FAILED = 4

    class AnnotationType(models.TextChoices):  # pylint: disable=too-many-ancestors
        """Kind of input file a job annotates."""
        VCF = "vcf"
        TABULAR = "tabular"

    #: Statuses of a job that is queued or executing in ``JOB_EXECUTOR``.
    #: Cleanup paths must never remove such a job's files: doing so unlinks
    #: ``result-<name>.vcf`` out from under the running annotation task and
    #: makes its ``on_success`` ``stat`` fail with ``[Errno 2]`` (gain#147).
    ACTIVE_STATUSES: ClassVar = (Status.WAITING, Status.IN_PROGRESS)

    input_path = models.FilePathField()
    config_path = models.FilePathField()
    result_path = models.FilePathField()
    name = models.BigIntegerField(default=0)
    reference_genome = models.CharField(max_length=1024, default="")
    created = models.DateTimeField(default=timezone.now)
    status = models.IntegerField(choices=Status, default=Status.WAITING)
    duration = models.FloatField(null=True, default=None)
    command_line = models.TextField(default="")
    error = models.TextField(default="")
    annotation_type = models.CharField(max_length=1024, default="")
    disk_size = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    def _cleanup_files(self) -> None:
        """Clean up job files, tolerating already-missing ones.

        ``unlink(missing_ok=True)`` makes ``deactivate()``/``delete()``
        idempotent w.r.t. disk state: a job whose input/config/result file is
        already gone (a prior cleanup interrupted by SIGTERM/OOM between the
        unlink and the DB row delete, or manual cleanup) does not raise
        ``FileNotFoundError``. Otherwise one such row would abort the
        ``cleanup_anonymous_jobs`` janitor and wedge it forever (#216).
        """
        pathlib.Path(self.input_path).unlink(missing_ok=True)
        pathlib.Path(self.config_path).unlink(missing_ok=True)
        pathlib.Path(self.result_path).unlink(missing_ok=True)

    def deactivate(self) -> None:
        """Diactivate a job and clean its resources."""
        self.is_active = False
        self._cleanup_files()
        self.disk_size = 0
        self.save()

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Delete a job and its resources."""
        self._cleanup_files()
        return super().delete(*args, **kwargs)

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta class for verification model."""
        abstract = True

    def update_job_in_progress(self) -> None:
        """Update a job's state to in progress."""
        if self.status != Job.Status.WAITING:
            raise ValueError(
                f"Attempted to start job {self.pk}, which is not in waiting! "
                f"({self.status})",
            )
        self.status = Job.Status.IN_PROGRESS
        self.save()

    def update_job_failed(self, args: str, exc: str) -> None:
        """Update a job's state to failed."""
        if self.status != Job.Status.IN_PROGRESS:
            raise ValueError(
                f"Attempted to mark job {self.pk} as failed, "
                f"which is not in in progress! ({self.status})",
            )
        self.status = Job.Status.FAILED
        self.command_line = args
        self.error = exc
        self.save()

    def update_job_success(self, args: str) -> None:
        """Update a job's state to success."""
        if self.status != Job.Status.IN_PROGRESS:
            raise ValueError(
                f"Attempted to start job {self.pk}, which is not in waiting! "
                f"({self.status})",
            )
        self.status = Job.Status.SUCCESS
        self.command_line = args
        self.save()


class Job(BaseJob):
    """Model for storing job data."""

    owner = models.ForeignKey(
        "web_annotation.User",
        related_name="jobs",
        on_delete=models.CASCADE,
    )

    def update_job_failed(self, args: str, exc: str) -> None:
        """Update a job's state to failed."""
        super().update_job_failed(args, exc)

        send_email(
            "GPFWA: Annotation job failed",
            (
                "Your job has failed. "
                "Visit the web site to try running it again: "
                f"{settings.EMAIL_REDIRECT_ENDPOINT}/jobs"
            ),
            [self.owner.identifier],
        )

    def update_job_success(self, args: str) -> None:
        """Update a job's state to success."""
        super().update_job_success(args)
        send_email(
            "GPFWA: Annotation job finished successfully",
            (
                "Your job has finished successfully. "
                "Visit the web site to download the results: "
                f"{settings.EMAIL_REDIRECT_ENDPOINT}/jobs"
            ),
            [self.owner.identifier],
        )

    def get_job_details(self) -> JobDetails:
        """Get or initiate job details."""
        try:
            return JobDetails.objects.get(job__pk=self.pk)
        except models.ObjectDoesNotExist:
            details = JobDetails(job=self)
            details.save()
            return details


class AnonymousJob(BaseJob):
    """Model for storing job data."""

    owner = models.CharField(max_length=1024)
    ip = models.CharField(max_length=256, default="")

    def get_job_details(self) -> AnonymousJobDetails:
        """Get or initiate job details."""
        try:
            return AnonymousJobDetails.objects.get(job__pk=self.pk)
        except models.ObjectDoesNotExist:
            details = AnonymousJobDetails(job=self)
            details.save()
            return details


class BaseJobDetails(models.Model):
    """Model for storing job details for tsv files."""
    class Meta:  # pylint: disable=too-few-public-methods
        """Meta class for verification model."""
        abstract = True
    col_chr = models.CharField(max_length=1024, default="")
    col_pos = models.CharField(max_length=1024, default="")
    col_ref = models.CharField(max_length=1024, default="")
    col_alt = models.CharField(max_length=1024, default="")
    col_pos_beg = models.CharField(max_length=1024, default="")
    col_pos_end = models.CharField(max_length=1024, default="")
    col_cnv_type = models.CharField(max_length=1024, default="")
    col_vcf_like = models.CharField(max_length=1024, default="")
    col_variant = models.CharField(max_length=1024, default="")
    col_location = models.CharField(max_length=1024, default="")
    separator = models.CharField(max_length=1, null=True)
    columns = models.TextField()


class JobDetails(BaseJobDetails):
    """Model for storing job details for tsv files."""
    class Meta(BaseJobDetails.Meta):  # pylint: disable=too-few-public-methods
        """Meta class for details model."""
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["job"], name="unique_job_details"),
        ]
    job = models.ForeignKey(
        "web_annotation.Job", related_name="details", on_delete=models.CASCADE)


class AnonymousJobDetails(BaseJobDetails):
    """Model for storing job details for tsv files."""
    class Meta(BaseJobDetails.Meta):  # pylint: disable=too-few-public-methods
        """Meta class for details model."""
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["job"],
                name="unique_anonymous_job_details",
            ),
        ]
    job = models.ForeignKey(
        "web_annotation.AnonymousJob",
        related_name="details",
        on_delete=models.CASCADE)


class BaseVerificationCode(models.Model):
    """Base class for temporary codes for verifying the user without login."""

    path: models.Field = models.CharField(max_length=255, unique=True)
    user: models.Field = models.OneToOneField(
        User, on_delete=models.CASCADE)
    created_at: models.Field = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return str(self.path)

    def validate(self) -> bool:
        """Check whether the code is valid."""
        raise NotImplementedError

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta class for verification model."""
        abstract = True

    @classmethod
    def get_code(
        cls, user: User,
    ) -> BaseVerificationCode | None:
        """Get a verification code for a user."""
        try:
            # pylint: disable=no-member
            return cast(
                BaseVerificationCode,
                cls.objects.get(user=user))  # type: ignore
        except models.ObjectDoesNotExist:
            return None

    @classmethod
    def create(cls, user: User) -> BaseVerificationCode:
        """Issue a fresh code for a user, deleting any code they had.

        The new code has its own path and creation time, so a previously
        mailed code stops working and the validity window starts over.
        """
        # pylint: disable=no-member
        cls.objects.filter(user=user).delete()  # type: ignore
        verif_code = cls.objects.create(  # type: ignore
            user=user, path=uuid.uuid4())
        return cast(BaseVerificationCode, verif_code)


class ResetPasswordCode(BaseVerificationCode):
    """Class used for verification of password resets."""

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta class for reset password codes."""
        db_table = "reset_password_verification_codes"

    def validate(self) -> bool:
        # pylint: disable=import-outside-toplevel
        max_delta = timedelta(
            hours=getattr(settings, "RESET_PASSWORD_TIMEOUT_HOURS", 24))
        return not timezone.now() - self.created_at > max_delta


class AccountConfirmationCode(BaseVerificationCode):
    """Class used for verification of password resets."""

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta class for account confirmation codes."""
        db_table = "account_confirmation_codes"

    def validate(self) -> bool:
        # pylint: disable=import-outside-toplevel
        return True
