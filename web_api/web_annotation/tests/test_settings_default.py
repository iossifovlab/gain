# pylint: disable=C0114,C0116,W0621
import importlib
from collections.abc import Generator
from types import ModuleType

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from web_annotation import settings_default, test_settings
from web_annotation.models import Quota


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


def test_num_proxies_is_not_also_a_bare_django_setting() -> None:
    # The hop count must be readable exactly one way. A bare
    # ``settings.NUM_PROXIES`` next to ``REST_FRAMEWORK["NUM_PROXIES"]`` would
    # be a second source of truth that ALSO bypasses DRF's setting_changed
    # reload -- overriding one would move one reader and not the other, which
    # is the drift #667 exists to remove. Every settings module pulls this one
    # in by wildcard import, which skips underscore-prefixed names.
    assert not hasattr(settings, "NUM_PROXIES")
    assert "NUM_PROXIES" not in [
        name for name in dir(settings_default) if not name.startswith("_")
    ]


@pytest.mark.parametrize("raw", ["two", "-1", "1.5", "1,2"])
def test_num_proxies_invalid_value_fails_at_startup(
    restore_settings_default: None,
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
) -> None:
    monkeypatch.setenv("GPFWA_NUM_PROXIES", raw)

    with pytest.raises(ImproperlyConfigured, match="GPFWA_NUM_PROXIES"):
        importlib.reload(settings_default)


def test_num_proxies_rejects_pep515_underscore_separator(
    restore_settings_default: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `int("1_0")` is 10, so delegating validation to int() lets a typo boot
    # with TEN trusted hops. DRF selects with
    # `addrs[-min(num_proxies, len(addrs))]` -- the min() clamps instead of
    # erroring, so a count larger than the entries present walks all the way
    # to addrs[0], the leftmost and entirely client-supplied entry. A
    # mistyped hop count would therefore silently reinstate #660 rather than
    # fail loudly.
    monkeypatch.setenv("GPFWA_NUM_PROXIES", "1_0")

    with pytest.raises(ImproperlyConfigured, match="GPFWA_NUM_PROXIES"):
        importlib.reload(settings_default)


@pytest.mark.parametrize(
    "raw",
    # Escaped rather than written literally: these glyphs are visually
    # confusable with ASCII, which is the whole reason they are a hazard.
    ["\u0661\u0660", "\uff11\uff10", "\u0e51\u0e50"],
    ids=["arabic-indic", "fullwidth", "thai"],
)
def test_num_proxies_rejects_non_ascii_digits(
    restore_settings_default: None,
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
) -> None:
    # str.isdigit() is NOT sufficient on its own: it is true for every Unicode
    # digit, and int() converts them all -- each of these parses as 10.
    # Guarding with isdigit() alone would close the underscore case above and
    # leave this one wide open, with the same consequence (a hop count far
    # larger than the header, so DRF selects the client-supplied entry).
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


@pytest.mark.parametrize(
    "module",
    [settings_default, test_settings],
    ids=["settings_default", "test_settings"],
)
def test_query_quotas_name_exactly_the_counter_fields(
    module: ModuleType,
) -> None:
    # settings_default is read as a MODULE, deliberately, rather than through
    # django.conf.settings. Tests run under DJANGO_SETTINGS_MODULE=
    # web_annotation.test_settings, whose QUERY_QUOTAS is a separate dict --
    # so asserting through the configured settings pins the test fixture and
    # says nothing about what production ships. That is how
    # daily_allele_queries / monthly_allele_queries survived for two
    # migrations after the columns were dropped (gain#749): nothing reads a
    # surplus key, so it costs nothing until someone believes it. The test
    # fixture is pinned too -- if it disagreed with the model, every other
    # quota test would be asserting against a shape production does not have.
    assert set(module.QUERY_QUOTAS) == {"anonymous", "user"}
    for quota_type, configured in module.QUERY_QUOTAS.items():
        assert set(configured) == set(Quota.COUNTER_FIELDS), quota_type
