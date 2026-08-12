"""Web annotation django models."""

from __future__ import annotations

import dataclasses
import os
import pathlib
import time
import uuid
from abc import abstractmethod
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import timedelta
from types import MappingProxyType
from typing import Any, ClassVar, Self, cast

from django.conf import settings
from django.contrib.auth.models import AbstractUser, AnonymousUser
from django.db import connection, models, transaction
from django.utils import timezone
from gain import logging

from web_annotation.mail import send_email

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
            "UPDATE web_annotation_user "  # noqa: S608
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
        """Create an email verification code."""
        try:
            # pylint: disable=no-member
            verif_code = cls.objects.get(user=user)  # type: ignore
        except models.ObjectDoesNotExist:
            # pylint: disable=no-member
            verif_code = cls.objects.create(  # type: ignore
                user=user, path=uuid.uuid4())
            return cast(BaseVerificationCode, verif_code)

        if verif_code.validate is not True:
            verif_code.delete()
            return cls.create(user)

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
    through ``_consume``, which owns that lock already. ``reset_daily`` and
    ``reset_monthly`` take the other route: they zero their counters rather
    than deriving values from what they read, so they need no lock, and in
    exchange may write only the columns they themselves set -- see
    ``_reset``.
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

    #: The counters ``reset_daily`` refreshes.
    DAILY_COUNTER_FIELDS: ClassVar[tuple[str, ...]] = tuple(
        daily for daily, _, _ in RESOURCE_FIELDS.values())

    #: The counters ``reset_monthly`` refreshes.
    MONTHLY_COUNTER_FIELDS: ClassVar[tuple[str, ...]] = tuple(
        monthly for _, monthly, _ in RESOURCE_FIELDS.values())

    #: The extra-unit fields, which belong to neither period and so are
    #: refreshed by neither reset.
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

    def _reset(self, fields: tuple[str, ...], stamp_field: str) -> None:
        """Refresh one period's counters and stamp when it was refreshed.

        Every counter named in ``fields`` is zeroed -- a refreshed period has
        consumed nothing -- so a counter added to the period's tuple
        refreshes without a second edit here. Counters of the *other* period,
        and the extra-unit fields, are left untouched; the extras store units
        remaining and a refresh must not clear a granted balance.

        Zeroing rather than writing a configured limit is what makes the
        refresh independent of configuration: it no longer bakes the limit
        of the moment into the row, so a limit changed after a refresh still
        reaches the row (gain#750).

        Only those columns are written, because only those were derived here.
        Nothing re-read this instance under a lock, so every other column
        still holds whatever the read that produced it saw; a full-row save
        would put all of that back and revert whatever committed in the
        meantime -- a consumption's deduction, or an admin's extra-unit grant
        (gain#768). The class docstring states the rule this follows.

        ``update_fields`` requires a row that already exists; every caller
        operates on a persisted one, which is what #765 made true. It also
        leaves the instance stale in the columns it declined to write -- no
        caller re-reads it today, and one that needed to would pair this with
        ``refresh_from_db`` -- and it raises on an UPDATE that matches no
        row, so a quota deleted mid-refresh aborts the refresh instead of
        being silently re-inserted.
        """
        for field in fields:
            setattr(self, field, 0)
        setattr(self, stamp_field, timezone.now())
        self.save(update_fields=(*fields, stamp_field))

    def reset_daily(self) -> None:
        """Reset all daily quota counts."""
        self._reset(self.DAILY_COUNTER_FIELDS, "last_daily_reset")

    def reset_monthly(self) -> None:
        """Reset all monthly quota counts."""
        self._reset(self.MONTHLY_COUNTER_FIELDS, "last_monthly_reset")

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
            yield
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

    def get_daily_job_max(self) -> int:
        return self.max_daily_jobs

    def get_monthly_job_max(self) -> int:
        return self.max_monthly_jobs

    def get_daily_variant_max(self) -> int:
        return self.max_daily_variants

    def get_monthly_variant_max(self) -> int:
        return self.max_monthly_variants

    def get_daily_attribute_max(self) -> int:
        return self.max_daily_attributes

    def get_monthly_attribute_max(self) -> int:
        return self.max_monthly_attributes

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
