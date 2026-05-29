# pylint: disable=C0114,C0116,W0621
import importlib
from collections.abc import Generator

import pytest

from web_annotation import settings_default


@pytest.fixture
def restore_settings_default() -> Generator[None, None, None]:
    """Reload settings_default so per-test env tweaks don't leak out."""
    yield
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
