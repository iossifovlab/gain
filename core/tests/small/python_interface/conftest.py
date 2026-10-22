"""Fixtures for the tests that pin the GAIn Python interface pages.

The pages run their examples on resource ids of the public GRR. A test
cannot fetch them, so the tests run the same calls on the mini-GRR test
fixture, ``test_fixtures/mini-GRR``, a resource of the same kind for each
page.
"""
import pathlib
import shutil

import pytest
from gain import grr
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GenomicResourceRepo,
)

#: ``parents[4]`` is the repository root: this file is at
#: ``core/tests/small/python_interface/conftest.py``.
MINI_GRR_SOURCE = (
    pathlib.Path(__file__).parents[4] / "test_fixtures" / "mini-GRR"
)

#: Config keys that GAIn has retired but mini-GRR still sets. A retired key
#: logs a deprecation notice that fails a test which does not own it, so
#: the copy drops them.
_RETIRED_CONFIG_KEYS = ("chrom_prefix",)


def _drop_retired_config_keys(repo_dir: pathlib.Path) -> None:
    prefixes = tuple(f"{key}:" for key in _RETIRED_CONFIG_KEYS)
    for config in repo_dir.rglob(GR_CONF_FILE_NAME):
        lines = config.read_text().splitlines(keepends=True)
        kept = [line for line in lines if not line.startswith(prefixes)]
        if kept != lines:
            config.write_text("".join(kept))


@pytest.fixture(scope="session", name="mini_grr_dir")
def fixture_mini_grr_dir(
    tmp_path_factory: pytest.TempPathFactory,
) -> pathlib.Path:
    """A copy of mini-GRR, so that no test writes into the submodule."""
    if next(MINI_GRR_SOURCE.rglob(GR_CONF_FILE_NAME), None) is None:
        raise AssertionError(
            "the mini-GRR fixture submodule is not checked out; run: "
            "git submodule update --init test_fixtures/mini-GRR")
    repo_dir = tmp_path_factory.mktemp("python_interface") / "mini-GRR"
    shutil.copytree(
        MINI_GRR_SOURCE, repo_dir, ignore=shutil.ignore_patterns(".git"))
    _drop_retired_config_keys(repo_dir)
    return repo_dir


@pytest.fixture(name="repo")
def fixture_repo(mini_grr_dir: pathlib.Path) -> GenomicResourceRepo:
    """The mini-GRR copy as a directory repository."""
    return grr.build_genomic_resource_repository({
        "id": "mini",
        "type": "directory",
        "directory": str(mini_grr_dir),
    })
