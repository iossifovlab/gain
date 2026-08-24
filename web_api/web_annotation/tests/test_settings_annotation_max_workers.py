# pylint: disable=C0114,C0116,W0621
import importlib
from collections.abc import Generator
from types import ModuleType

import pytest
from django.core.exceptions import ImproperlyConfigured

from web_annotation import (
    settings_daphne,
    settings_default,
    settings_e2e,
    settings_gunicorn,
)

ENV_VAR = "GPFWA_ANNOTATION_MAX_WORKERS"

#: Every settings module that carries a worker count, with the value it must
#: keep when the environment says nothing. The deployment modules are NOT
#: interchangeable with the base one: each re-assigns the setting *after*
#: ``from .settings_default import *``, so a fix applied only to the base
#: module would be silently clobbered by all three.
MODULE_DEFAULTS = [
    (settings_default, 4),
    (settings_gunicorn, 16),
    (settings_daphne, 16),
    (settings_e2e, 16),
]
MODULE_IDS = [
    module.__name__.rsplit(".", 1)[-1] for module, _ in MODULE_DEFAULTS
]


@pytest.fixture
def restore_settings_modules(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[None, None, None]:
    """Reload every settings module so per-test env tweaks don't leak out."""
    yield
    # Undo the env tweaks BEFORE reloading: a test that pins a deliberately
    # invalid value would otherwise re-raise from this teardown, since the
    # modules refuse to import with one.
    monkeypatch.undo()
    # settings_default LAST. The deployment modules alias its INSTALLED_APPS
    # list, and settings_e2e appends to that list IN PLACE, so reloading a
    # deployment module last would leave the base module's app list mutated
    # for whatever runs next.
    for module, _ in reversed(MODULE_DEFAULTS):
        importlib.reload(module)


@pytest.mark.parametrize(("module", "default"), MODULE_DEFAULTS, ids=MODULE_IDS)
def test_worker_count_is_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
    restore_settings_modules: None,
    module: ModuleType,
    default: int,
) -> None:
    monkeypatch.setenv(ENV_VAR, "6")

    importlib.reload(module)

    workers = module.ANNOTATION_MAX_WORKERS
    assert workers == 6


@pytest.mark.parametrize(("module", "default"), MODULE_DEFAULTS, ids=MODULE_IDS)
def test_unset_leaves_each_module_at_its_own_default(
    monkeypatch: pytest.MonkeyPatch,
    restore_settings_modules: None,
    module: ModuleType,
    default: int,
) -> None:
    # The point of the whole change: merging it must move no deployment. The
    # base module keeps 4 and the three deployment modules keep 16.
    monkeypatch.delenv(ENV_VAR, raising=False)

    importlib.reload(module)

    workers = module.ANNOTATION_MAX_WORKERS
    assert workers == default


@pytest.mark.parametrize(
    "raw", ["", "   ", "\t"], ids=["empty", "spaces", "tab"],
)
@pytest.mark.parametrize(("module", "default"), MODULE_DEFAULTS, ids=MODULE_IDS)
def test_blank_is_treated_as_unset(
    monkeypatch: pytest.MonkeyPatch,
    restore_settings_modules: None,
    module: ModuleType,
    default: int,
    raw: str,
) -> None:
    # `-e GPFWA_ANNOTATION_MAX_WORKERS` with no value, or an empty line in an
    # env file, means "not configured" -- not "misconfigured".
    monkeypatch.setenv(ENV_VAR, raw)

    importlib.reload(module)

    workers = module.ANNOTATION_MAX_WORKERS
    assert workers == default


@pytest.mark.parametrize(
    "raw",
    [
        "two", "1.5", "1,2", "-1", "+2", "0x10", "16 workers", "1_0",
        "\uff11\uff10",  # fullwidth "10": int() accepts it, we must not
    ],
    ids=[
        "word", "float", "comma", "negative", "signed", "hex", "trailing-word",
        "pep515-underscore", "fullwidth-digits",
    ],
)
@pytest.mark.parametrize(("module", "default"), MODULE_DEFAULTS, ids=MODULE_IDS)
def test_malformed_worker_count_refuses_to_boot(
    monkeypatch: pytest.MonkeyPatch,
    restore_settings_modules: None,
    module: ModuleType,
    default: int,
    raw: str,
) -> None:
    # Strict like GPFWA_NUM_PROXIES, not lenient like
    # GPFWA_ANONYMOUS_JOB_TTL_HOURS: a silently-defaulted worker count would
    # mis-size the pool for the whole life of the process, and the pool is
    # built once at import, so there is no later opportunity to notice.
    #
    # The last two cases are why the shape check cannot be delegated to
    # int(): `int("1_0")` is 10 (PEP 515 separators) and int() also accepts
    # every Unicode digit spelling, so a typo would boot with ten workers
    # instead of failing. isdigit() alone is not enough either -- it is true
    # for those same fullwidth digits.
    monkeypatch.setenv(ENV_VAR, raw)

    with pytest.raises(ImproperlyConfigured, match=ENV_VAR):
        importlib.reload(module)


@pytest.mark.parametrize(("module", "default"), MODULE_DEFAULTS, ids=MODULE_IDS)
def test_zero_workers_refuses_to_boot(
    monkeypatch: pytest.MonkeyPatch,
    restore_settings_modules: None,
    module: ModuleType,
    default: int,
) -> None:
    # Zero is well-formed as digits but meaningless as a pool width, and it is
    # NOT clamped to one: a deployment that asks for zero workers has said
    # something it did not mean, and quietly running with one would hide that.
    # Rejecting here also keeps the failure next to its cause -- an unchecked
    # zero reaches ThreadPoolExecutor(max_workers=0), which raises ValueError
    # from the annotation view's class body at import, a long way from the
    # environment variable that actually caused it.
    monkeypatch.setenv(ENV_VAR, "0")

    with pytest.raises(ImproperlyConfigured, match=ENV_VAR):
        importlib.reload(module)


def test_refusal_echoes_the_offending_value(
    monkeypatch: pytest.MonkeyPatch,
    restore_settings_modules: None,
) -> None:
    # The operator sees this in a crash-looping container's logs and needs to
    # recognise their own typo in it -- naming the variable is not enough when
    # the value came from a templated env file they cannot see from here.
    monkeypatch.setenv(ENV_VAR, "sixteen")

    with pytest.raises(ImproperlyConfigured) as excinfo:
        importlib.reload(settings_gunicorn)

    assert "'sixteen'" in str(excinfo.value)
