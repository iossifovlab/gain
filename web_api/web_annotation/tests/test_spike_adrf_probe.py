# pylint: disable=C0114,C0116,W0621
#
# SPIKE ARTIFACT for iossifovlab/gain#162 -- throwaway async-vehicle probe.
# Proves that `adrf` can host an `async def` DRF handler against the real
# `WebAnnotationAuthentication` + `UserRateThrottle` stack, that the blocking
# session I/O runs OFF the event loop, and that DRF exception mapping
# (ValidationError -> 400, NotFound -> 404) survives an async handler.
# Delete together with `spike_adrf_probe.py` once #163 lands.
import threading

import pytest
from django.test import AsyncClient

from web_annotation import spike_adrf_probe
from web_annotation.models import User

PROBE_URL = "/api/_spike/adrf-probe"


@pytest.fixture(autouse=True)
def reset_probe_record() -> None:
    spike_adrf_probe.PROBE_RECORD.clear()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_probe_anonymous_returns_200() -> None:
    """Real WebAnnotationAuthentication works through adrf for anon users."""
    client = AsyncClient()
    response = await client.get(PROBE_URL)
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["authenticated"] is False


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_probe_authenticated_returns_200() -> None:
    """A session-authenticated request authenticates through adrf."""
    user = await User.objects.acreate_user(
        "spike-user", "spike@example.com", "secret",
    )
    client = AsyncClient()
    await client.aforce_login(user)
    response = await client.get(PROBE_URL)
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["authenticated"] is True


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_probe_validation_error_maps_to_400() -> None:
    """A DRF ValidationError raised in the async handler -> 400 (not 500)."""
    client = AsyncClient()
    response = await client.get(f"{PROBE_URL}?raise=validation")
    assert response.status_code == 400, response.content
    assert "spike validation boom" in response.content.decode()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_probe_not_found_maps_to_404() -> None:
    """A DRF NotFound raised in the async handler -> 404 (not 500)."""
    client = AsyncClient()
    response = await client.get(f"{PROBE_URL}?raise=notfound")
    assert response.status_code == 404, response.content


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_probe_auth_and_throttle_run_off_event_loop() -> None:
    """The blocking session I/O + throttle check must not run on the loop.

    adrf's ``async_dispatch`` wraps DRF's ``initial()`` (which runs
    ``perform_authentication`` -> our session read + ``session.save()`` and
    ``check_throttles``) in ``await sync_to_async(self.initial)(...)``, so it
    executes in a thread-pool worker, not on the event-loop thread. The probe
    records the thread idents; this test encodes that finding as an assertion.
    """
    user = await User.objects.acreate_user(
        "spike-user", "spike@example.com", "secret",
    )
    client = AsyncClient()
    await client.aforce_login(user)
    response = await client.get(PROBE_URL)
    assert response.status_code == 200, response.content

    record = spike_adrf_probe.PROBE_RECORD
    loop_thread = record["handler_thread"]
    auth_thread = record["auth_thread"]
    throttle_thread = record["throttle_thread"]
    session_saved = record["session_save_thread"]

    # The async handler body runs ON the event-loop thread.
    assert loop_thread == threading.get_ident()
    # Authentication (incl. the session read) ran OFF the loop thread.
    assert auth_thread is not None
    assert auth_thread != loop_thread
    # The blocking session.save() ran OFF the loop thread too.
    assert session_saved is not None
    assert session_saved != loop_thread
    # Throttling ran OFF the loop thread.
    assert throttle_thread is not None
    assert throttle_thread != loop_thread
