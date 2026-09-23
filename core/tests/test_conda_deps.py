# pylint: disable=W0621,C0116
"""The committed conda environment file matches the workspace pyprojects.

``environment.yml`` is rendered by ``scripts/conda_env.py``; the guard
below fails whenever the committed file is stale, and the remaining tests
pin the rendering rules on a throwaway workspace.
"""
import importlib.util
import pathlib
import sys
import textwrap
from types import ModuleType

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "conda_env.py"

REGENERATE = (
    "run `python scripts/conda_env.py` from the repo root and commit "
    "environment.yml")


@pytest.fixture(scope="module")
def conda_env() -> ModuleType:
    if not SCRIPT.exists():
        pytest.fail(f"{SCRIPT} is missing; the CI image must copy scripts/")
    spec = importlib.util.spec_from_file_location("conda_env", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_committed_environment_file_is_current(
    conda_env: ModuleType,
) -> None:
    committed = REPO_ROOT / conda_env.ENVIRONMENT_FILE
    if not committed.exists():
        pytest.fail(f"{committed} is missing; {REGENERATE}")

    assert committed.read_text() == conda_env.render_environment(), (
        f"{committed} is stale; {REGENERATE}")


def _workspace(
    root: pathlib.Path,
    core: list[str],
    web_api: list[str],
    requires_python: str = ">=3.12",
) -> pathlib.Path:
    def write(path: str, text: str) -> None:
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(textwrap.dedent(text))

    def pyproject(name: str, deps: list[str]) -> str:
        listed = "".join(f"    {dep!r},\n" for dep in deps)
        return (
            f'[project]\nname = "{name}"\n'
            f'requires-python = "{requires_python}"\n'
            f"dependencies = [\n{listed}]\n")

    write("pyproject.toml", """\
        [project]
        name = "monorepo"
        [tool.uv.sources]
        gain-core = { workspace = true }
        gain-web-api = { workspace = true }
        """)
    write("core/pyproject.toml", pyproject("gain-core", core))
    write("web_api/pyproject.toml", pyproject("gain-web-api", web_api))
    return root


def _dependencies(rendered: str) -> list[str]:
    return rendered.split("dependencies:\n", 1)[1].splitlines()


def test_render_maps_sorts_and_sections(
    conda_env: ModuleType, tmp_path: pathlib.Path,
) -> None:
    root = _workspace(
        tmp_path,
        core=["pyBigWig>=0.3", "dask>=2026.1", "anndata", "matplotlib"],
        web_api=["gain-core", "django>=5.2,<5.3", "docker>=7.1"],
        requires_python=">=3.13",
    )

    rendered = conda_env.render_environment(root, pip_only={})

    assert _dependencies(rendered) == [
        "  - python>=3.13",
        "  # gain-core (core/pyproject.toml)",
        "  - anndata",
        "  - dask-core>=2026.1",
        "  - matplotlib-base",
        "  - pybigwig>=0.3",
        "  # gain-web-api (web_api/pyproject.toml)",
        "  - django>=5.2,<5.3",
        "  - docker-py>=7.1",
    ]


def test_two_sources_merge_into_one_line(
    conda_env: ModuleType, tmp_path: pathlib.Path,
) -> None:
    root = _workspace(
        tmp_path,
        core=["pyyaml>=6", "numpy>=2", "scipy>=1,<2"],
        web_api=["PyYAML", "numpy<3", "scipy>=1"],
    )

    deps = _dependencies(conda_env.render_environment(root, pip_only={}))

    assert [d for d in deps if "pyyaml" in d] == ["  - pyyaml>=6"]
    assert [d for d in deps if "numpy" in d] == ["  - numpy>=2,<3"]
    assert [d for d in deps if "scipy" in d] == ["  - scipy>=1,<2"]


@pytest.mark.parametrize("requirement", [
    'foo>=1; sys_platform == "win32"',
    "foo[bar]>=1",
    "foo (>=1,<2)",
    "foo===1.0",
])
def test_unmodelled_requirement_raises(
    conda_env: ModuleType, tmp_path: pathlib.Path, requirement: str,
) -> None:
    root = _workspace(tmp_path, core=[requirement], web_api=[])

    with pytest.raises(ValueError, match="does not model"):
        conda_env.render_environment(root, pip_only={})


def test_pip_only_dependency_renders_under_pip(
    conda_env: ModuleType, tmp_path: pathlib.Path,
) -> None:
    root = _workspace(tmp_path, core=["numpy"], web_api=["adrf>=0.1.13"])

    rendered = conda_env.render_environment(
        root, pip_only={"adrf": "not on conda"})

    assert rendered.endswith(
        "  # pip-only: PIP_ONLY in scripts/conda_env.py\n"
        "  - pip\n"
        "  - pip:\n"
        "    # adrf: not on conda\n"
        "    - adrf>=0.1.13\n")


def test_no_pip_block_without_pip_only_dependencies(
    conda_env: ModuleType, tmp_path: pathlib.Path,
) -> None:
    root = _workspace(tmp_path, core=["numpy"], web_api=["adrf>=0.1.13"])

    deps = _dependencies(conda_env.render_environment(root, pip_only={}))

    assert "  - pip" not in deps
    assert "  - pip:" not in deps
    assert "  - adrf>=0.1.13" in deps


def test_undeclared_pip_only_entry_raises(
    conda_env: ModuleType, tmp_path: pathlib.Path,
) -> None:
    root = _workspace(tmp_path, core=["numpy"], web_api=[])

    with pytest.raises(ValueError, match="adrf"):
        conda_env.render_environment(root, pip_only={"adrf": "not on conda"})


def test_check_reports_drift_without_rewriting(
    conda_env: ModuleType, tmp_path: pathlib.Path,
) -> None:
    root = _workspace(tmp_path, core=["numpy", "adrf"], web_api=[])
    assert conda_env.main([], root=root) == 0
    assert conda_env.main(["--check"], root=root) == 0

    _workspace(tmp_path, core=["numpy>=2", "adrf"], web_api=[])
    before = (root / "environment.yml").read_text()

    assert conda_env.main(["--check"], root=root) == 1
    assert (root / "environment.yml").read_text() == before
