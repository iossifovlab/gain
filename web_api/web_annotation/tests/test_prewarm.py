# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Startup prewarm of the GRR default pipelines (iossifovlab/gain#657).

Without a prewarm, the first request that names a cold GRR pipeline pays
its whole build on the request path -- ~3.5 min for
``pipeline/hs1_clinical_annotation`` on the deployed gainweb, far past the
frontend's 60 s proxy timeout.
"""
import asyncio
import json
import logging
import os
import subprocess
import sys
import threading
from collections import Counter
from collections.abc import Generator
from concurrent.futures import wait
from dataclasses import dataclass, field

import pytest
from asgiref.sync import sync_to_async
from django.core.exceptions import ImproperlyConfigured
from django.test import AsyncClient
from pytest_mock import MockerFixture

from web_annotation.annotation_base_view import (
    GRR_PIPELINES,
    AnnotationMixin,
    prewarm_grr_pipelines,
)
from web_annotation.pipeline_cache import LRUPipelineCache
from web_annotation.settings_default import resolve_prewarm_grr_pipelines

#: The fixture GRR's ``annotation_pipeline`` resources. Pinned so that an
#: empty ``GRR_PIPELINES`` cannot make the "every pipeline" assertions pass
#: vacuously.
FIXTURE_GRR_PIPELINES = {
    "pipeline/test_pipeline",
    "pipeline/allele_pipeline",
    "t4c8/t4c8_pipeline",
}

#: Upper bound on how long teardown waits for a fixture-GRR build.
BUILD_SETTLE_SECONDS = 30

#: How long a gated build stays held once the request under test is sent.
GATE_OPEN_DELAY_SECONDS = 0.3

STATUS_URL = "/api/editor/pipeline_status"

PREWARM_ENV_VAR = "GPFWA_PREWARM_GRR_PIPELINES"

#: Upper bound on a fresh-interpreter probe (Django + GRR start-up).
PROBE_TIMEOUT_SECONDS = 120


def _cached(pipeline_ids: list[str]) -> list[str]:
    return [
        pid for pid in pipeline_ids
        if AnnotationMixin.lru_cache.has_pipeline(pid)]


def _logged(
    caplog: pytest.LogCaptureFixture, level: str, text: str,
) -> list[logging.LogRecord]:
    return [
        record for record in caplog.records
        if record.levelname == level and text in record.getMessage()]


@pytest.fixture
def cold_grr_pipelines() -> Generator[list[str], None, None]:
    """Start and end with no GRR pipeline in the shared cache.

    The cache lives on the mixin's class body and is shared by every test,
    so a pipeline an earlier test built would make "is cached" vacuous.
    Teardown lets every build finish first: a build still running after
    the session ends logs into closed capture streams.
    """
    ids = sorted(GRR_PIPELINES)
    _settle_and_unload(ids)
    yield ids
    _settle_and_unload(ids)


def _settle_and_unload(pipeline_ids: list[str]) -> None:
    cache = AnnotationMixin.lru_cache
    for pipeline_id in pipeline_ids:
        if cache.has_pipeline(pipeline_id):
            wait(
                [cache.get_pipeline_future(pipeline_id)],
                timeout=BUILD_SETTLE_SECONDS)
        cache.unload_pipeline(pipeline_id)


@dataclass
class GatedBuilds:
    """Builds that block on ``gate`` and count themselves per pipeline id."""

    gate: threading.Event
    counts: Counter[str] = field(default_factory=Counter)


@pytest.fixture
def gated_builds(
    cold_grr_pipelines: list[str],
    mocker: MockerFixture,
) -> Generator[GatedBuilds, None, None]:
    """Hold every build on the loader thread until the test opens the gate.

    Depends on ``cold_grr_pipelines`` so the gate opens before that
    fixture's teardown waits for the builds to settle.
    """
    builds = GatedBuilds(gate=threading.Event())
    real_load = LRUPipelineCache._load_pipeline_raw

    def gated_load(raw, grr, pipeline_id="unknown"):  # type: ignore
        builds.counts[pipeline_id] += 1
        builds.gate.wait(timeout=BUILD_SETTLE_SECONDS)
        return real_load(raw, grr, pipeline_id)

    mocker.patch.object(
        LRUPipelineCache, "_load_pipeline_raw", staticmethod(gated_load))
    yield builds
    builds.gate.set()


def test_fixture_grr_carries_the_expected_pipelines() -> None:
    assert set(GRR_PIPELINES) == FIXTURE_GRR_PIPELINES


def test_prewarm_returns_while_the_builds_are_still_running(
    cold_grr_pipelines: list[str],
    gated_builds: GatedBuilds,
) -> None:
    prewarm_grr_pipelines()

    cache = AnnotationMixin.lru_cache
    in_flight = [
        pid for pid in _cached(cold_grr_pipelines)
        if not cache.get_pipeline_future(pid).done()]
    assert in_flight == cold_grr_pipelines


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_request_during_prewarm_joins_the_in_flight_build(
    gated_builds: GatedBuilds,
) -> None:
    # Off the loop, as at server start: daphne imports the ASGI application
    # before its event loop runs.
    await sync_to_async(prewarm_grr_pipelines)()
    prewarm_build = AnnotationMixin.lru_cache.get_pipeline_future(
        "t4c8/t4c8_pipeline")
    assert not prewarm_build.done()
    asyncio.get_running_loop().call_later(
        GATE_OPEN_DELAY_SECONDS, gated_builds.gate.set)

    response = await AsyncClient().get(
        f"{STATUS_URL}?pipeline_id=t4c8/t4c8_pipeline")

    assert response.status_code == 200, response.content
    assert gated_builds.counts["t4c8/t4c8_pipeline"] == 1


@pytest.fixture
def broken_grr_pipeline(monkeypatch: pytest.MonkeyPatch) -> str:
    """Add a GRR pipeline whose build fails: its score does not exist."""
    pipeline_id = "broken/prewarm_bad_pipeline"
    monkeypatch.setitem(GRR_PIPELINES, pipeline_id, {
        "id": pipeline_id,
        "content": "- position_score: scores/does_not_exist",
    })
    return pipeline_id


def test_a_failed_build_is_logged_and_the_others_still_load(
    broken_grr_pipeline: str,
    cold_grr_pipelines: list[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    prewarm_grr_pipelines()
    cache = AnnotationMixin.lru_cache
    wait(
        [cache.get_pipeline_future(pid) for pid in cold_grr_pipelines],
        timeout=BUILD_SETTLE_SECONDS)

    loaded = [
        pid for pid in cold_grr_pipelines if cache.is_pipeline_loaded(pid)]
    assert loaded == sorted(FIXTURE_GRR_PIPELINES)
    assert cache.get_pipeline_error(broken_grr_pipeline) is not None
    assert _logged(caplog, "WARNING", broken_grr_pipeline)


def test_a_pipeline_that_fails_to_submit_does_not_stop_the_others(
    cold_grr_pipelines: list[str],
    mocker: MockerFixture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Submitting can raise synchronously, e.g. the "loading" notification
    # on an unreachable channel layer.
    failing_id = "pipeline/allele_pipeline"
    real_put = AnnotationMixin.put_grr_pipeline

    def put_or_fail(self: AnnotationMixin, pipeline_id: str) -> None:
        if pipeline_id == failing_id:
            raise ConnectionError("channel layer unreachable")
        real_put(self, pipeline_id)

    mocker.patch.object(AnnotationMixin, "put_grr_pipeline", put_or_fail)

    prewarm_grr_pipelines()

    assert _cached(cold_grr_pipelines) == sorted(
        FIXTURE_GRR_PIPELINES - {failing_id})
    errors = _logged(caplog, "ERROR", failing_id)
    assert errors
    assert errors[0].exc_info is not None


#: Runs in a fresh interpreter under ``web_annotation.test_settings``:
#: ``serve`` imports the ASGI application, as daphne does; ``manage`` runs a
#: management command. Prints which GRR pipelines are in the shared cache,
#: then exits without waiting for the builds.
_PROBE = """
import json, os, sys
os.environ["DJANGO_SETTINGS_MODULE"] = "web_annotation.test_settings"
if sys.argv[1] == "serve":
    import web_annotation.asgi
else:
    import django
    from django.core.management import call_command
    django.setup()
    call_command("check")
from web_annotation.annotation_base_view import GRR_PIPELINES, AnnotationMixin
cached = sorted(
    pid for pid in GRR_PIPELINES if AnnotationMixin.lru_cache.has_pipeline(pid))
print("CACHED=" + json.dumps(cached), flush=True)
os._exit(0)
"""


def _cached_after(mode: str, *, prewarm: str) -> list[str]:
    result = subprocess.run(
        [sys.executable, "-c", _PROBE, mode],
        capture_output=True, text=True, check=True,
        timeout=PROBE_TIMEOUT_SECONDS,
        env={**os.environ, PREWARM_ENV_VAR: prewarm},
    )
    # Logging may share stdout; the marker line is the probe's answer.
    marker = [
        line for line in result.stdout.splitlines()
        if line.startswith("CACHED=")]
    assert marker, result.stdout + result.stderr
    cached: list[str] = json.loads(marker[-1].removeprefix("CACHED="))
    return cached


def test_serving_process_prewarms_when_enabled() -> None:
    assert _cached_after("serve", prewarm="1") == sorted(
        FIXTURE_GRR_PIPELINES)


def test_serving_process_does_not_prewarm_when_disabled() -> None:
    assert _cached_after("serve", prewarm="0") == []


def test_management_command_does_not_prewarm() -> None:
    assert _cached_after("manage", prewarm="1") == []


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", " FALSE "])
def test_environment_switches_the_prewarm_off(
    monkeypatch: pytest.MonkeyPatch, raw: str,
) -> None:
    monkeypatch.setenv(PREWARM_ENV_VAR, raw)

    assert resolve_prewarm_grr_pipelines(default=True) is False


@pytest.mark.parametrize("raw", ["1", "true", "yes", "on", " TRUE "])
def test_environment_switches_the_prewarm_on(
    monkeypatch: pytest.MonkeyPatch, raw: str,
) -> None:
    monkeypatch.setenv(PREWARM_ENV_VAR, raw)

    assert resolve_prewarm_grr_pipelines(default=False) is True


@pytest.mark.parametrize("raw", [None, "", "  "])
@pytest.mark.parametrize("default", [True, False])
def test_blank_or_unset_environment_keeps_the_default(
    monkeypatch: pytest.MonkeyPatch, raw: str | None, default: bool,
) -> None:
    if raw is None:
        monkeypatch.delenv(PREWARM_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(PREWARM_ENV_VAR, raw)

    assert resolve_prewarm_grr_pipelines(default=default) is default


def test_unparseable_environment_value_fails_at_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(PREWARM_ENV_VAR, "sometimes")

    with pytest.raises(ImproperlyConfigured, match=PREWARM_ENV_VAR):
        resolve_prewarm_grr_pipelines(default=True)
