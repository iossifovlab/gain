"""Tell whether bioconda lags PyPI for ``gain-core``.

Usage: ``python3 bioconda_lag_check.py``

The ``Check bioconda lag`` stage of ``Jenkinsfile.nightly`` runs the
script on a ``builder`` agent, outside of a CI image, so the script uses
the Python standard library only. The bioconda recipe builds from the
PyPI sdist, and BiocondaBot reacts to PyPI, so the script compares PyPI
with bioconda and not the git tag with bioconda.

- The newest PyPI version is ``info.version`` of the PyPI JSON. Its age
  counts from the earliest ``upload_time_iso_8601`` of that release.
- The newest bioconda version is ``latest_version`` of the anaconda.org
  JSON.

Exit codes:

- 0: the two versions are equal, or the PyPI release is 7 days old or
  younger.
- 1: the versions differ and the PyPI release is older than 7 days. The
  script prints both versions and the age of the PyPI release.
- 2: the script cannot decide, for example on a network failure, on an
  HTTP error, or on a body without the expected keys and types.

Exit 1 means a real lag and nothing else: ``main`` turns every exception
into exit 2.
"""
import datetime
import json
import sys
import urllib.request

PYPI_URL = "https://pypi.org/pypi/gain-core/json"
BIOCONDA_URL = "https://api.anaconda.org/package/bioconda/gain-core"
MAX_AGE = datetime.timedelta(days=7)


def fetch(url: str) -> bytes:
    """Return the body of a GET of ``url``.

    An HTTP error status raises ``urllib.error.HTTPError``.
    """
    # Both URLs are constants of this module, with the https scheme.
    with urllib.request.urlopen(  # ruff: ignore[suspicious-url-open-usage]
            url, timeout=30) as response:
        body: bytes = response.read()
    return body


def now() -> datetime.datetime:
    """Return the current time in UTC."""
    return datetime.datetime.now(tz=datetime.UTC)


def value_at(body: object, *path: str) -> object:
    """Return the value at ``path`` in nested JSON objects."""
    value = body
    for key in path:
        if not isinstance(value, dict) or key not in value:
            raise ValueError(f"no key {'.'.join(path)}")
        value = value[key]
    return value


def string_at(body: object, *path: str) -> str:
    """Return the string at ``path`` in nested JSON objects."""
    value = value_at(body, *path)
    if not isinstance(value, str):
        raise TypeError(f"{'.'.join(path)} is not a string: {value!r}")
    return value


def first_upload(pypi: object, version: str) -> datetime.datetime:
    """Return the earliest upload time of ``version`` on PyPI."""
    files = value_at(pypi, "releases", version)
    if not isinstance(files, list) or not files:
        raise ValueError(f"release {version} has no files: {files!r}")
    uploaded = min(
        datetime.datetime.fromisoformat(
            string_at(item, "upload_time_iso_8601"))
        for item in files)
    if uploaded.tzinfo is None:
        raise ValueError(f"upload time without a zone: {uploaded}")
    return uploaded


def check() -> int:
    """Compare the two versions and return 0 or 1."""
    pypi = json.loads(fetch(PYPI_URL))
    bioconda = json.loads(fetch(BIOCONDA_URL))
    version = string_at(pypi, "info", "version")
    bioconda_version = string_at(bioconda, "latest_version")
    if version == bioconda_version:
        return 0
    age = now() - first_upload(pypi, version)
    if age <= MAX_AGE:
        return 0
    print(
        f"bioconda lags PyPI for gain-core: PyPI has {version}, "
        f"bioconda has {bioconda_version}, and the PyPI release is "
        f"{age.days} days old (limit {MAX_AGE.days} days)")
    return 1


def main() -> int:
    """Return the exit code of the check: 0, 1 or 2."""
    try:
        return check()
    except Exception as error:  # ruff: ignore[blind-except]
        print(f"cannot decide the bioconda lag of gain-core: {error!r}",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
