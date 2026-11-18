"""The CI images name their Python interpreter in one place (gain#2387).

The ``environment`` block of the root ``Jenkinsfile`` names the CI
interpreter as ``PYTHON_IMAGE``, and each CI ``docker build`` passes it
with ``--build-arg``. The guard below reads the source tree and fails
when a Dockerfile names a literal ``python:`` image in a ``FROM`` line,
or when the default of its ``ARG PYTHON_IMAGE`` differs from the
``Jenkinsfile`` value. The guard also fails when the ``PYTHON_IMAGE`` of
``web_e2e/Jenkinsfile.e2e`` differs from the ``Jenkinsfile`` value.

The CI images hold no ``Jenkinsfile``: only a checkout runs the guard
on the tree.
"""
import os
import pathlib
import re

import pytest

CORE = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = CORE.parent
JENKINSFILE = REPO_ROOT / "Jenkinsfile"
E2E_JENKINSFILE = REPO_ROOT / "web_e2e" / "Jenkinsfile.e2e"

# The Dockerfiles that the guard does not check, relative to the repo
# root. ``Dockerfile.freethreaded`` builds a free-threaded interpreter of
# its own. ``web_api/Dockerfile.production`` builds the production
# image, which gain#2389 moves to the CI interpreter.
EXEMPT_DOCKERFILES = [
    "Dockerfile.freethreaded",
    "web_api/Dockerfile.production",
]

# The directories that hold no Dockerfile of the repo.
SKIPPED_DIRS = {"node_modules", "__pycache__", "target", "dist"}


def is_literal_python_image(image: str) -> bool:
    """Tell if ``image`` names the ``python`` image of a registry.

    The image can carry a registry prefix, for example
    ``docker.io/library/python:3.12-slim``.
    """
    name = image.split("@", 1)[0].rsplit("/", 1)[-1]
    return name.split(":", 1)[0] == "python"


def literal_python_from_lines(dockerfile: str) -> list[str]:
    """Return the ``FROM`` lines that name a literal ``python`` image.

    The guard skips the ``--platform`` flag and the other flags of a
    ``FROM`` line before it reads the image.
    """
    found: list[str] = []
    for line in dockerfile.splitlines():
        words = line.split()
        if not words or words[0].upper() != "FROM":
            continue
        images = [word for word in words[1:] if not word.startswith("--")]
        if images and is_literal_python_image(images[0]):
            found.append(line.strip())
    return found


ARG_PYTHON_IMAGE = re.compile(r"^\s*ARG\s+PYTHON_IMAGE=(\S+)", re.MULTILINE)


def arg_default_mismatches(dockerfile: str, expected: str) -> list[str]:
    """Return the ``ARG PYTHON_IMAGE`` lines whose default is not ``expected``.
    """
    return [
        match.group(0).strip()
        for match in ARG_PYTHON_IMAGE.finditer(dockerfile)
        if match.group(1) != expected]


def test_literal_python_from_line_is_reported() -> None:
    """A FROM line with a literal python image is reported."""
    dockerfile = "ARG X=1\nFROM python:3.12-slim\nRUN true\n"

    assert literal_python_from_lines(dockerfile) == [
        "FROM python:3.12-slim"]


@pytest.mark.parametrize("line", [
    "FROM --platform=linux/amd64 python:3.12-slim",
    "FROM docker.io/library/python:3.12-slim AS base",
    "FROM python@sha256:0123",
])
def test_literal_python_from_line_with_a_flag_or_a_registry_is_reported(
    line: str,
) -> None:
    """A flag or a registry prefix does not hide a literal python image."""
    dockerfile = f"ARG X=1\n{line}\nRUN true\n"

    assert literal_python_from_lines(dockerfile) == [line]


@pytest.mark.parametrize("line", [
    "FROM ${PYTHON_IMAGE}",
    "FROM --platform=linux/amd64 ${PYTHON_IMAGE} AS pages",
    "FROM mambaorg/micromamba:2.3.2",
    "FROM mcr.microsoft.com/playwright:v1.62.1-noble",
])
def test_from_line_without_a_literal_python_image_is_not_reported(
    line: str,
) -> None:
    """A FROM line with an ARG or another image is not reported."""
    assert not literal_python_from_lines(f"{line}\n")


def test_arg_default_that_differs_from_the_jenkinsfile_is_reported() -> None:
    """An ARG PYTHON_IMAGE default that differs is reported."""
    dockerfile = "ARG PYTHON_IMAGE=python:3.12-slim\nFROM ${PYTHON_IMAGE}\n"

    assert arg_default_mismatches(dockerfile, "python:3.13-slim") == [
        "ARG PYTHON_IMAGE=python:3.12-slim"]


JENKINS_PYTHON_IMAGE = re.compile(
    r"""^\s*PYTHON_IMAGE\s*=\s*['"]([^'"]+)['"]""", re.MULTILINE)


def jenkins_python_image(jenkinsfile: str) -> str:
    """Return the ``PYTHON_IMAGE`` value of a Jenkinsfile.

    The value must stand on exactly one line.
    """
    values = JENKINS_PYTHON_IMAGE.findall(jenkinsfile)
    assert len(values) == 1, (
        f"expected one PYTHON_IMAGE line, found {values}")
    return str(values[0])


def read_or_skip(path: pathlib.Path) -> str:
    # The CI images have no Jenkinsfile: only a checkout runs the guard.
    if not path.exists():
        pytest.skip(f"{path} is not in this checkout")
    return path.read_text()


def ci_dockerfiles() -> list[pathlib.Path]:
    """Return each Dockerfile of the repo that the guard checks."""
    found: list[pathlib.Path] = []
    for top, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [
            name for name in dirs
            if not name.startswith(".") and name not in SKIPPED_DIRS]
        for name in files:
            path = pathlib.Path(top) / name
            if name.startswith("Dockerfile") and \
                    path.relative_to(REPO_ROOT).as_posix() \
                    not in EXEMPT_DOCKERFILES:
                found.append(path)
    return sorted(found)


@pytest.fixture(name="ci_python_image")
def fixture_ci_python_image() -> str:
    return jenkins_python_image(read_or_skip(JENKINSFILE))


def test_jenkins_python_image_reads_the_environment_line() -> None:
    """The value comes from the PYTHON_IMAGE line."""
    jenkinsfile = (
        "environment {\n"
        '    CI_TAG = "x"\n'
        "    PYTHON_IMAGE = 'python:3.13-slim'\n"
        "}\n")

    assert jenkins_python_image(jenkinsfile) == "python:3.13-slim"


def test_exempt_dockerfiles_exist() -> None:
    """Each exempt Dockerfile is in the tree."""
    read_or_skip(JENKINSFILE)

    missing = [
        name for name in EXEMPT_DOCKERFILES
        if not (REPO_ROOT / name).exists()]

    assert missing == []


# The CI Dockerfiles that gain#2387 names. The walk must find each one.
NAMED_CI_DOCKERFILES = [
    "core/Dockerfile",
    "demo_annotator/Dockerfile",
    "info_pages_e2e/Dockerfile",
    "spliceai_annotator/Dockerfile",
    "vep_annotator/Dockerfile",
    "web_api/Dockerfile",
]


def test_ci_dockerfiles_include_the_named_ci_dockerfiles() -> None:
    """The walk finds each CI Dockerfile that gain#2387 names."""
    read_or_skip(JENKINSFILE)

    found = {
        path.relative_to(REPO_ROOT).as_posix() for path in ci_dockerfiles()}

    assert sorted(set(NAMED_CI_DOCKERFILES) - found) == []


def test_no_ci_dockerfile_names_a_literal_python_image() -> None:
    """No CI Dockerfile has a FROM line with a literal python image."""
    read_or_skip(JENKINSFILE)

    found = {
        path.relative_to(REPO_ROOT).as_posix(): lines
        for path in ci_dockerfiles()
        if (lines := literal_python_from_lines(path.read_text()))}

    assert found == {}


def test_ci_dockerfile_defaults_match_the_jenkinsfile(
    ci_python_image: str,
) -> None:
    """Each ARG PYTHON_IMAGE default equals the Jenkinsfile value."""
    found = {
        path.relative_to(REPO_ROOT).as_posix(): lines
        for path in ci_dockerfiles()
        if (lines := arg_default_mismatches(
            path.read_text(), ci_python_image))}

    assert found == {}


def test_e2e_jenkinsfile_names_the_same_python_image(
    ci_python_image: str,
) -> None:
    """The e2e Jenkinsfile names the interpreter of the Jenkinsfile."""
    e2e = jenkins_python_image(read_or_skip(E2E_JENKINSFILE))

    assert e2e == ci_python_image
