# pylint: disable=C0116
"""``Jenkinsfile.release`` publishes to the wheel index last.

The ``gain_core`` wheel on the wheel index is the only sign that a
release finished. A failure in an earlier step of ``Publish`` then
leaves the tag off the wheel index, and a rerun of ``gain-release``
with the same tag resumes the release (#2534).

The guards read the text of the Jenkinsfile. The CI image has no
``Jenkinsfile.release``, so only a checkout runs them.
"""
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
JENKINSFILE = REPO_ROOT / "Jenkinsfile.release"

WHEELS_RSYNC_TARGET = '"${WHEELS_USER}@${WHEELS_HOST}:${WHEELS_PATH}/"'
# The dir2pi path inside the container on the wheels host, not a temp file.
DIR2PI = "/tmp/.local/bin/dir2pi"  # ruff: ignore[hardcoded-temp-file]


def stage_body(stage: str) -> str:
    """Return the text of ``stage`` up to the next stage, less comments.

    A comment line is a line whose first non-blank characters are
    ``//`` or ``#``.
    """
    # The CI image has no Jenkinsfile.release: only a checkout runs this.
    if not JENKINSFILE.exists():
        pytest.skip(f"{JENKINSFILE} is not in this checkout")
    text = JENKINSFILE.read_text()
    start = text.index(f"stage('{stage}')")
    end = text.find("stage('", start + 1)
    if end == -1:
        end = len(text)
    return "\n".join(
        line for line in text[start:end].splitlines()
        if not line.lstrip().startswith(("//", "#")))


def test_publish_runs_pypi_conda_docker_then_wheel_index() -> None:
    body = stage_body("Publish")

    positions = [
        body.index("uv publish"),
        body.index("rattler-build upload anaconda"),
        body.index("docker push"),
        body.rindex("docker push"),
        body.index(WHEELS_RSYNC_TARGET),
        body.index(DIR2PI),
    ]

    assert positions == sorted(positions)


def test_tag_freshness_does_not_probe_anaconda() -> None:
    body = stage_body("Pre-flight: tag freshness")

    assert "anaconda show" not in body


def test_tag_freshness_probes_the_gain_core_wheel() -> None:
    body = stage_body("Pre-flight: tag freshness")

    assert "gain_core-${TAG_NAME}-py3-none-any.whl" in body
