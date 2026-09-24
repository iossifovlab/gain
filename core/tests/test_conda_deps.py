# pylint: disable=W0621,C0116
"""The committed conda files match the workspace pyprojects.

The environment files are rendered by ``scripts/conda_env.py``; the guard
below fails whenever a committed file is stale. The rattler-build recipes
stay hand-written, and a second guard fails whenever a recipe's run list
differs from its pyproject's dependencies under the same name mapping.
The remaining tests pin both sets of rules on a throwaway workspace.
"""
import importlib.util
import pathlib
import re
import subprocess
import sys
import textwrap
from collections.abc import Iterable
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
    assert set(rendered) == {"environment.yml", "dev-environment.yml"}

    for filename, text in rendered.items():
        committed = REPO_ROOT / filename
        if not committed.exists():
            pytest.fail(f"{committed} is missing; {REGENERATE}")
        assert committed.read_text() == text, (
            f"{committed} is stale; {REGENERATE}")


def test_pip_only_entries_carry_a_reason(conda_env: ModuleType) -> None:
    assert conda_env.PIP_ONLY
    assert [
        name for name, reason in conda_env.PIP_ONLY.items()
        if not reason.strip()
    ] == []


def _workspace(
    root: pathlib.Path,
    core: list[str],
    web_api: list[str],
    requires_python: str = ">=3.12",
    *,
    core_dev: Iterable[str] = (),
    web_api_dev: Iterable[str] = (),
    docs: Iterable[str] = (),
) -> pathlib.Path:
    def listed(deps: Iterable[str]) -> str:
        return "[\n" + "".join(f"    {dep!r},\n" for dep in deps) + "]\n"

    def pyproject(
        name: str, deps: list[str], group: str, group_deps: Iterable[str],
    ) -> str:
        return (
            f'[project]\nname = "{name}"\n'
            f'requires-python = "{requires_python}"\n'
            f"dependencies = {listed(deps)}"
            f"[dependency-groups]\n{group} = {listed(group_deps)}")

    (root / "core").mkdir(parents=True, exist_ok=True)
    (root / "web_api").mkdir(exist_ok=True)
    (root / "pyproject.toml").write_text(
        pyproject("monorepo", [], "docs", docs) + textwrap.dedent("""\
            [tool.uv.sources]
            gain-core = { workspace = true }
            gain-web-api = { workspace = true }
            """))
    (root / "core/pyproject.toml").write_text(
        pyproject("gain-core", core, "dev", core_dev))
    (root / "web_api/pyproject.toml").write_text(
        pyproject("gain-web-api", web_api, "dev", web_api_dev))
    return root


def _render(
    conda_env: ModuleType,
    root: pathlib.Path,
    pip_only: dict[str, str] | None = None,
) -> str:
    rendered = conda_env.render_all(root, pip_only=pip_only or {})
    return str(rendered["environment.yml"])


def _render_dev(
    conda_env: ModuleType,
    root: pathlib.Path,
    pip_only: dict[str, str] | None = None,
) -> str:
    rendered = conda_env.render_all(root, pip_only=pip_only or {})
    return str(rendered["dev-environment.yml"])


def _dependencies(rendered: str) -> list[str]:
    return rendered.split("dependencies:\n", 1)[1].splitlines()


CORE = "  # gain-core (core/pyproject.toml [project.dependencies])"
WEB_API = "  # gain-web-api (web_api/pyproject.toml [project.dependencies])"


def test_render_maps_sorts_and_sections(
    conda_env: ModuleType, tmp_path: pathlib.Path,
) -> None:
    root = _workspace(
        tmp_path,
        core=[
            "pyBigWig>=0.3", "dask>=2026.1", "anndata", "matplotlib",
            "Brotli",
        ],
        web_api=["gain-core", "django>=5.2,<5.3", "docker>=7.1"],
        requires_python=">=3.13",
    )

    assert _dependencies(_render(conda_env, root)) == [
        "  - python>=3.13",
        CORE,
        "  - anndata",
        "  - brotli-python",
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


def test_clauses_keep_their_written_order(
    conda_env: ModuleType, tmp_path: pathlib.Path,
) -> None:
    root = _workspace(
        tmp_path,
        core=["numpy<3,>=2,!=2.5", "scipy (!=1.5, <2, >=1)"],
        web_api=[],
        requires_python="<3.15,>=3.12",
    )

    deps = _dependencies(_render(conda_env, root))

    assert deps[0] == "  - python<3.15,>=3.12"
    assert "  - numpy<3,>=2,!=2.5" in deps
    assert "  - scipy!=1.5,<2,>=1" in deps


@pytest.mark.parametrize("requirement", [
    'foo>=1; sys_platform == "win32"',
    "foo[bar]>=1",
    "foo @ https://example.com/foo-1.0.tar.gz",
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


def test_dev_environment_renders_the_dev_and_docs_groups(
    conda_env: ModuleType, tmp_path: pathlib.Path,
) -> None:
    root = _workspace(
        tmp_path,
        core=["numpy>=2"],
        web_api=["django>=5.2"],
        core_dev=["pytest>=9", "ruff==0.16.5", "brotli"],
        web_api_dev=["pytest", "gain-core", "pylint-django"],
        docs=["sphinx", "myst-parser"],
    )

    assert _dependencies(_render_dev(conda_env, root)) == [
        "  - python>=3.12",
        "  # gain-core (core/pyproject.toml [dependency-groups.dev])",
        "  - brotli-python",
        "  - pytest>=9",
        "  - ruff==0.16.5",
        "  # gain-web-api (web_api/pyproject.toml [dependency-groups.dev])",
        "  - pylint-django",
        "  # monorepo (pyproject.toml [dependency-groups.docs])",
        "  - myst-parser",
        "  - sphinx",
    ]


def test_dev_pip_only_entry_stays_out_of_the_runtime_file(
    conda_env: ModuleType, tmp_path: pathlib.Path,
) -> None:
    root = _workspace(
        tmp_path, core=["numpy"], web_api=[], core_dev=["pytestarch"])

    rendered = conda_env.render_all(
        root, pip_only={"pytestarch": "not on conda"})

    assert "pip" not in rendered["environment.yml"]
    assert rendered["dev-environment.yml"].endswith(
        "  - pip:\n"
        "    # pytestarch: not on conda\n"
        "    - pytestarch\n")


@pytest.mark.parametrize(("core", "docs", "stale"), [
    (["numpy>=2"], [], "environment.yml"),
    (["numpy"], ["sphinx"], "dev-environment.yml"),
])
def test_check_reports_drift_without_rewriting(
    conda_env: ModuleType, tmp_path: pathlib.Path,
    core: list[str], docs: list[str], stale: str,
) -> None:
    # Every real PIP_ONLY entry must be declared somewhere, or render_all
    # refuses the workspace.
    pip_only = list(conda_env.PIP_ONLY)
    root = _workspace(
        tmp_path, core=["numpy"], web_api=[], core_dev=pip_only)
    assert conda_env.main([], root=root) == 0
    assert conda_env.main(["--check"], root=root) == 0

    _workspace(tmp_path, core=core, web_api=[], core_dev=pip_only, docs=docs)
    before = (root / stale).read_text()

    assert conda_env.main(["--check"], root=root) == 1
    assert (root / stale).read_text() == before


RECIPE_PACKAGES = (
    "core", "demo_annotator", "vep_annotator", "spliceai_annotator")


@pytest.mark.parametrize("package", RECIPE_PACKAGES)
def test_recipe_run_list_matches_the_pyproject(
    conda_env: ModuleType, package: str,
) -> None:
    for path in (
        REPO_ROOT / package / "conda-recipe" / "recipe.yaml",
        REPO_ROOT / package / "pyproject.toml",
    ):
        if not path.exists():
            pytest.fail(f"{path} is missing; the CI image must copy it")

    conda_env.check_recipe_run(REPO_ROOT, package)


def _package(
    root: pathlib.Path,
    package: str,
    deps: Iterable[str],
    run: Iterable[str],
    requires_python: str = ">=3.12",
) -> pathlib.Path:
    """Write ``<package>/pyproject.toml`` and its rattler-build recipe."""
    listed = "".join(f"    {dep!r},\n" for dep in deps)
    (root / package / "conda-recipe").mkdir(parents=True, exist_ok=True)
    (root / package / "pyproject.toml").write_text(
        f'[project]\nname = "gain-{package}"\n'
        f'requires-python = "{requires_python}"\n'
        f"dependencies = [\n{listed}]\n")
    # The Jinja expressions keep the reader honest about real recipes,
    # which are YAML only until rattler-build renders them.
    (root / package / "conda-recipe" / "recipe.yaml").write_text(
        textwrap.dedent("""\
            context:
              version: ${{ env.get("VCS_VERSION") | default("0.0.0") }}
            package:
              name: ${{ name }}
            requirements:
              host:
                - python >=3.12
                - pip
              run:
            """)
        + "".join(f"    - {entry}\n" for entry in run))
    return root


def test_recipe_matching_the_mapped_pyproject_passes(
    conda_env: ModuleType, tmp_path: pathlib.Path,
) -> None:
    root = _package(
        tmp_path, "core",
        deps=["pyBigWig>=0.3", "dask>=2026.1", "scipy>=1.14,<2", "psutil"],
        run=[
            "python >=3.12", "dask-core >=2026.1", "psutil",
            "pybigwig >=0.3", "scipy >=1.14, <2",
        ],
    )

    conda_env.check_recipe_run(root, "core")


def test_dependency_missing_from_the_recipe_is_named(
    conda_env: ModuleType, tmp_path: pathlib.Path,
) -> None:
    root = _package(
        tmp_path, "core",
        deps=["numpy>=2.2", "tqdm>=4.66"],
        run=["python >=3.12", "numpy >=2.2"],
    )

    with pytest.raises(ValueError, match="only in the recipe: none") as info:
        conda_env.check_recipe_run(root, "core")

    assert "core/conda-recipe/recipe.yaml" in str(info.value)
    assert "only in the pyproject: tqdm>=4.66" in str(info.value)


def test_recipe_bound_tighter_than_the_pyproject_fails(
    conda_env: ModuleType, tmp_path: pathlib.Path,
) -> None:
    root = _package(
        tmp_path, "core",
        deps=["numpy>=2.2"],
        run=["python >=3.12", "numpy >=2.3"],
    )

    with pytest.raises(
            ValueError, match=re.escape("only in the recipe: numpy>=2.3")):
        conda_env.check_recipe_run(root, "core")


def test_recipe_python_follows_requires_python(
    conda_env: ModuleType, tmp_path: pathlib.Path,
) -> None:
    root = _package(
        tmp_path, "core",
        deps=["numpy"], run=["python >=3.12", "numpy"],
        requires_python=">=3.13",
    )

    with pytest.raises(
            ValueError,
            match=re.escape("only in the pyproject: python>=3.13")):
        conda_env.check_recipe_run(root, "core")


def test_recipe_under_the_pypi_name_fails(
    conda_env: ModuleType, tmp_path: pathlib.Path,
) -> None:
    root = _package(
        tmp_path, "core",
        deps=["dask>=2026.1"], run=["python >=3.12", "dask >=2026.1"],
    )

    with pytest.raises(
            ValueError, match=re.escape("only in the recipe: dask>=2026.1")):
        conda_env.check_recipe_run(root, "core")


def test_plugin_recipe_keeps_the_workspace_member(
    conda_env: ModuleType, tmp_path: pathlib.Path,
) -> None:
    root = _package(
        tmp_path, "vep_annotator",
        deps=["gain-core"], run=["python >=3.12", "gain-core"],
    )

    conda_env.check_recipe_run(root, "vep_annotator")


def test_plugin_recipe_without_the_workspace_member_fails(
    conda_env: ModuleType, tmp_path: pathlib.Path,
) -> None:
    root = _package(
        tmp_path, "vep_annotator",
        deps=["gain-core"], run=["python >=3.12"],
    )

    with pytest.raises(
            ValueError, match=re.escape("only in the pyproject: gain-core")):
        conda_env.check_recipe_run(root, "vep_annotator")


def test_recipe_row_order_is_not_compared(
    conda_env: ModuleType, tmp_path: pathlib.Path,
) -> None:
    root = _package(
        tmp_path, "core",
        deps=["pathspec>=1.0", "numpy>=2.2"],
        run=["numpy >=2.2", "python >=3.12", "pathspec >=1.0"],
    )

    conda_env.check_recipe_run(root, "core")


def test_recipe_row_listed_twice_fails(
    conda_env: ModuleType, tmp_path: pathlib.Path,
) -> None:
    root = _package(
        tmp_path, "core",
        deps=["numpy>=2.2"],
        run=["python >=3.12", "numpy >=2.2", "numpy >=2.2"],
    )

    with pytest.raises(
            ValueError, match=re.escape("only in the recipe: numpy>=2.2")):
        conda_env.check_recipe_run(root, "core")


@pytest.mark.parametrize("entry", [
    "{if: unix, then: numpy}",
    "3.12",
])
def test_recipe_run_entry_that_is_not_a_string_raises(
    conda_env: ModuleType, tmp_path: pathlib.Path, entry: str,
) -> None:
    root = _package(
        tmp_path, "core", deps=["numpy"],
        run=["python >=3.12", "numpy", entry],
    )

    with pytest.raises(
            TypeError, match=re.escape("core/conda-recipe/recipe.yaml")):
        conda_env.check_recipe_run(root, "core")


def test_recipe_without_a_run_list_raises(
    conda_env: ModuleType, tmp_path: pathlib.Path,
) -> None:
    root = _package(tmp_path, "core", deps=["numpy"], run=[])

    with pytest.raises(
            ValueError, match=re.escape("no requirements.run list")):
        conda_env.check_recipe_run(root, "core")


def test_script_does_not_import_yaml_until_a_recipe_is_read() -> None:
    # #1638 keeps the generator on the stdlib and packaging, so
    # `python scripts/conda_env.py` runs without PyYAML installed; only
    # the recipe reader needs it.
    probe = textwrap.dedent(f"""\
        import importlib.util, sys
        spec = importlib.util.spec_from_file_location(
            "conda_env", {str(SCRIPT)!r})
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        module.render_all()
        print("yaml" in sys.modules)
        """)

    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True, text=True, check=True)

    assert result.stdout == "False\n"
