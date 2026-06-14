# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pytest

from web_annotation.models import User


@pytest.fixture
def user() -> User:
    return User.objects.get(email="user@example.com")


def test_generate_job_name_increments(user: User) -> None:
    """Single-threaded behavior: successive names are distinct and rising."""
    first = user.generate_job_name()
    second = user.generate_job_name()
    third = user.generate_job_name()

    assert first < second < third
    assert len({first, second, third}) == 3


def test_generate_job_name_concurrent_instances_are_unique() -> None:
    """Two request handlers each load their own ``User`` for the same row.

    This is the real concurrency scenario from
    ``annotation_base_view._create_job``: every request resolves
    ``request.user`` to a freshly-loaded ``User`` instance. With a
    non-atomic read-modify-write of ``job_counter`` both instances read
    the same value, both increment to the same number and both return it,
    producing duplicate job names (and therefore colliding result paths).

    The allocation must be atomic at the database level so that two
    separate in-memory ``User`` instances of the same row never hand out
    the same name.
    """
    # Two independent in-memory copies of the same database row, mirroring
    # two concurrent request handlers that each loaded request.user.
    handler_a = User.objects.get(email="user@example.com")
    handler_b = User.objects.get(email="user@example.com")

    name_a = handler_a.generate_job_name()
    name_b = handler_b.generate_job_name()

    assert name_a != name_b, (
        f"duplicate job name allocated: {name_a} == {name_b}"
    )


def test_generate_job_name_many_concurrent_instances_are_unique() -> None:
    """Generalize: N stale-but-equal copies must produce N distinct names."""
    handlers = [
        User.objects.get(email="user@example.com") for _ in range(10)
    ]
    names = [h.generate_job_name() for h in handlers]

    assert len(set(names)) == len(names), (
        f"duplicate job names allocated: {names}"
    )
