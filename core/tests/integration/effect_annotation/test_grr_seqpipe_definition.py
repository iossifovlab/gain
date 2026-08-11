# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pytest

from .conftest import (
    GRR_INTEGRATION_CACHE_DIR_ENV,
    GRR_INTEGRATION_DIR_ENV,
    grr_seqpipe_definition,
)


@pytest.fixture(autouse=True)
def clean_grr_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(GRR_INTEGRATION_CACHE_DIR_ENV, raising=False)
    monkeypatch.delenv(GRR_INTEGRATION_DIR_ENV, raising=False)


def test_default_is_the_checked_in_http_definition() -> None:
    definition = grr_seqpipe_definition()

    assert definition["id"] == "grr-seqpipe"
    assert definition["type"] == "http"
    assert definition["url"] == "https://grr-seqpipe.seqpipe.org"
    assert "cache_dir" not in definition


def test_cache_dir_env_wraps_the_http_definition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(GRR_INTEGRATION_CACHE_DIR_ENV, "/somewhere/cache")

    definition = grr_seqpipe_definition()

    assert definition["type"] == "http"
    assert definition["cache_dir"] == "/somewhere/cache"


def test_dir_env_selects_a_directory_definition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(GRR_INTEGRATION_DIR_ENV, "/grr/grr_seqpipe")

    definition = grr_seqpipe_definition()

    assert definition == {
        "id": "grr-seqpipe",
        "type": "directory",
        "directory": "/grr/grr_seqpipe",
    }


def test_dir_env_wins_over_cache_dir_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(GRR_INTEGRATION_DIR_ENV, "/grr/grr_seqpipe")
    monkeypatch.setenv(GRR_INTEGRATION_CACHE_DIR_ENV, "/somewhere/cache")

    definition = grr_seqpipe_definition()

    assert definition["type"] == "directory"
    assert "cache_dir" not in definition
