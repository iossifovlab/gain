# pylint: disable=C0114,C0116,W0621
import importlib
from collections.abc import Generator

import pytest
from django.core.exceptions import ImproperlyConfigured

from web_annotation import settings_default


@pytest.fixture
def restore_settings_default(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[None, None, None]:
    """Reload settings_default so per-test env tweaks don't leak out."""
    yield
    # Undo the test's env tweaks BEFORE reloading: a test that pins a
    # deliberately-invalid value would otherwise re-raise from this teardown,
    # since the module now refuses to import with one (GPFWA_NUM_PROXIES).
    monkeypatch.undo()
    importlib.reload(settings_default)


def test_num_proxies_reads_environment(
    monkeypatch: pytest.MonkeyPatch,
    restore_settings_default: None,
) -> None:
    monkeypatch.setenv("GPFWA_NUM_PROXIES", "2")

    importlib.reload(settings_default)

    assert settings_default.REST_FRAMEWORK["NUM_PROXIES"] == 2


def test_num_proxies_unset_leaves_drf_default(
    monkeypatch: pytest.MonkeyPatch,
    restore_settings_default: None,
) -> None:
    # Unset must mean "no hop count configured" -- DRF's own default -- so
    # that shipping #667 alone changes no bucket key and no quota key.
    monkeypatch.delenv("GPFWA_NUM_PROXIES", raising=False)

    importlib.reload(settings_default)

    assert settings_default.REST_FRAMEWORK["NUM_PROXIES"] is None


def test_num_proxies_blank_is_treated_as_unset(
    monkeypatch: pytest.MonkeyPatch,
    restore_settings_default: None,
) -> None:
    # `-e GPFWA_NUM_PROXIES` with no value (or an empty line in an env file)
    # means "not configured", not "misconfigured".
    monkeypatch.setenv("GPFWA_NUM_PROXIES", "")

    importlib.reload(settings_default)

    assert settings_default.REST_FRAMEWORK["NUM_PROXIES"] is None


@pytest.mark.parametrize("raw", ["two", "-1", "1.5", "1,2"])
def test_num_proxies_invalid_value_fails_at_startup(
    restore_settings_default: None,
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
) -> None:
    monkeypatch.setenv("GPFWA_NUM_PROXIES", raw)

    with pytest.raises(ImproperlyConfigured, match="GPFWA_NUM_PROXIES"):
        importlib.reload(settings_default)


def test_quota_reset_timezone_defaults_to_utc(
    monkeypatch: pytest.MonkeyPatch,
    restore_settings_default: None,
) -> None:
    monkeypatch.delenv("GPFWA_QUOTA_RESET_TIMEZONE", raising=False)

    importlib.reload(settings_default)

    assert settings_default.QUOTA_RESET_TIMEZONE == "UTC"


def test_quota_reset_timezone_reads_environment(
    monkeypatch: pytest.MonkeyPatch,
    restore_settings_default: None,
) -> None:
    monkeypatch.setenv("GPFWA_QUOTA_RESET_TIMEZONE", "America/New_York")

    importlib.reload(settings_default)

    assert settings_default.QUOTA_RESET_TIMEZONE == "America/New_York"
