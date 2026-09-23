# pylint: disable=W0621,C0116
"""The committed conda environment files match the workspace pyprojects.

The files are rendered by ``scripts/conda_env.py``; the guard below fails
whenever a committed file is stale, and the remaining tests pin the
rendering rules on a throwaway workspace.
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
    "the result")


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


def test_committed_environment_files_are_current(
    conda_env: ModuleType,
) -> None:
    rendered = conda_env.render_all()
    assert set(rendered) == {"environment.yml"}

    for filename, text in rendered.items():
        committed = REPO_ROOT / filename
        if not committed.exists():
            pytest.fail(f"{committed} is missing; {REGENERATE}")
        assert committed.read_text() == text, (
            f"{committed} is stale; {REGENERATE}")


def _workspace(
    root: pathlib.Path,
    core: list[str],
    web_api: list[str],
    requires_python: str = ">=3.12",
) -> pathlib.Path:
    def pyproject(name: str, deps: list[str]) -> str:
        listed = "".join(f"    {dep!r},\n" for dep in deps)
        return (
            f'[project]\nname = "{name}"\n'
            f'requires-python = "{requires_python}"\n'
            f"dependencies = [\n{listed}]\n")

    (root / "core").mkdir(parents=True, exist_ok=True)
    (root / "web_api").mkdir(exist_ok=True)
    (root / "pyproject.toml").write_text(textwrap.dedent("""\
        [project]
        name = "monorepo"
        [tool.uv.sources]
        gain-core = { workspace = true }
        gain-web-api = { workspace = true }
        """))
    (root / "core/pyproject.toml").write_text(pyproject("gain-core", core))
    (root / "web_api/pyproject.toml").write_text(
        pyproject("gain-web-api", web_api))
    return root


def _render(
    conda_env: ModuleType,
    root: pathlib.Path,
    pip_only: dict[str, str] | None = None,
) -> str:
    rendered = conda_env.render_all(root, pip_only=pip_only or {})
    return str(rendered["environment.yml"])


def _dependencies(rendered: str) -> list[str]:
    return rendered.split("dependencies:\n", 1)[1].splitlines()


CORE = "  # gain-core (core/pyproject.toml [project.dependencies])"
WEB_API = "  # gain-web-api (web_api/pyproject.toml [project.dependencies])"


def test_render_maps_sorts_and_sections(
    conda_env: ModuleType, tmp_path: pathlib.Path,
) -> None:
    root = _workspace(
        tmp_path,
        core=["pyBigWig>=0.3", "dask>=2026.1", "anndata", "matplotlib"],
        web_api=["gain-core", "django>=5.2,<5.3", "docker>=7.1"],
        requires_python=">=3.13",
    )

    assert _dependencies(_render(conda_env, root)) == [
        "  - python>=3.13",
        CORE,
        "  - anndata",
        "  - dask-core>=2026.1",
        "  - matplotlib-base",
        "  - pybigwig>=0.3",
        WEB_API,
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

    deps = _dependencies(_render(conda_env, root))

    assert [d for d in deps if "pyyaml" in d] == ["  - pyyaml>=6"]
    assert [d for d in deps if "numpy" in d] == ["  - numpy>=2,<3"]
    assert [d for d in deps if "scipy" in d] == ["  - scipy>=1,<2"]


@pytest.mark.parametrize("requirement", [
    'foo>=1; sys_platform == "win32"',
    "foo[bar]>=1",
    "foo===1.0",
])
def test_unmodelled_requirement_raises(
    conda_env: ModuleType, tmp_path: pathlib.Path, requirement: str,
) -> None:
    root = _workspace(tmp_path, core=[requirement], web_api=[])

    with pytest.raises(ValueError, match="does not model"):
        _render(conda_env, root)


def test_pip_only_dependency_renders_under_pip(
    conda_env: ModuleType, tmp_path: pathlib.Path,
) -> None:
    root = _workspace(tmp_path, core=["numpy"], web_api=["adrf>=0.1.13"])

    rendered = _render(conda_env, root, {"adrf": "not on conda"})

    assert rendered.endswith(
        f"{WEB_API}\n"
        "  # pip-only: PIP_ONLY in scripts/conda_env.py\n"
        "  - pip\n"
        "  - pip:\n"
        "    # adrf: not on conda\n"
        "    - adrf>=0.1.13\n")


def test_no_pip_block_without_pip_only_dependencies(
    conda_env: ModuleType, tmp_path: pathlib.Path,
) -> None:
    root = _workspace(tmp_path, core=["numpy"], web_api=["adrf>=0.1.13"])

    deps = _dependencies(_render(conda_env, root))

    assert "  - pip" not in deps
    assert "  - pip:" not in deps
    assert "  - adrf>=0.1.13" in deps


def test_undeclared_pip_only_entry_raises(
    conda_env: ModuleType, tmp_path: pathlib.Path,
) -> None:
    root = _workspace(tmp_path, core=["numpy"], web_api=[])

    with pytest.raises(ValueError, match="adrf"):
        _render(conda_env, root, {"adrf": "not on conda"})


def test_pip_only_entry_declared_by_another_output_is_accepted(
    conda_env: ModuleType, tmp_path: pathlib.Path,
) -> None:
    root = _workspace(tmp_path, core=["numpy"], web_api=["adrf"])
    runtime = conda_env.Output(
        "runtime.yml", "rt", (conda_env.Feed("core/pyproject.toml"),))
    web = conda_env.Output(
        "web.yml", "web", (conda_env.Feed("web_api/pyproject.toml"),))

    rendered = conda_env.render_all(
        root, outputs=(runtime, web), pip_only={"adrf": "not on conda"})

    assert "pip" not in rendered["runtime.yml"]
    assert "    - adrf\n" in rendered["web.yml"]


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
