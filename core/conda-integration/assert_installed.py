"""Refuse to test anything but the gain-core from the archived .conda.

Run by run.sh with the solved env's interpreter, before pytest:

    python assert_installed.py <env prefix> <version off the .conda name> \
        <test bed>

Three checks, all printed so the console log carries the evidence
(#1429 acceptance criteria):

- ``gain.__file__`` resolves under the env prefix. A stray ``core/`` on
  sys.path (the shadowing the test-bed copy exists to prevent) or a
  second gain-core would put it somewhere else.
- ``gain.__version__`` equals the artefact's version. Both come from
  the same hatch-vcs string -- the wheel's ``_version.py`` and the
  ``VCS_VERSION`` the recipe stamps on the package -- so a mismatch
  means the solve picked up a different gain-core than the one copied
  from the upstream build.
- The early pytest plugins ``pytest.ini`` names (``-p tests.dask_guard``,
  ``-p tests.memory_guard``) resolve under the test bed, with the test
  bed at ``sys.path[0]`` the way ``python -m pytest`` puts the cwd
  there. They are module plugins, not distributions, so pytest's own
  ``plugins:`` header never lists them; this is the only place the log
  says where they came from.
"""

import importlib
import pathlib
import sys

EARLY_PLUGINS = ("tests.dask_guard", "tests.memory_guard")


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(f"usage: {argv[0]} <env prefix> <expected version> <test bed>",
              file=sys.stderr)
        return 2
    prefix = pathlib.Path(argv[1]).resolve()
    expected_version = argv[2]
    testbed = pathlib.Path(argv[3]).resolve()
    sys.path.insert(0, str(testbed))

    import gain  # deliberately after the args check and the sys.path insert

    location = pathlib.Path(gain.__file__).resolve()
    version = gain.__version__
    print(f"gain.__file__    = {location}")
    print(f"gain.__version__ = {version}")
    print(f"env prefix       = {prefix}")
    print(f"artefact version = {expected_version}")

    failures = []
    if not location.is_relative_to(prefix):
        failures.append(
            f"gain is imported from {location}, not from under {prefix}")
    if version != expected_version:
        failures.append(
            f"gain.__version__ is {version!r}, "
            f"the .conda under test is {expected_version!r}")

    for name in EARLY_PLUGINS:
        try:
            plugin = importlib.import_module(name)
        except ImportError as exc:
            print(f"{name} = <not importable: {exc}>")
            failures.append(f"{name} does not import from {testbed}")
            continue
        plugin_file = pathlib.Path(plugin.__file__ or "").resolve()
        print(f"{name} = {plugin_file}")
        if not plugin_file.is_relative_to(testbed):
            failures.append(
                f"{name} is imported from {plugin_file}, "
                f"not from under {testbed}")

    if failures:
        print("ERROR: the test bed is not what #1429 requires:",
              file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print("OK: testing the gain-core from the archived .conda")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
