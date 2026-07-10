"""Report which C/Rust extensions force CPython's GIL back on.

Under a free-threaded build (``Py_GIL_DISABLED=1``) an extension
module that has not declared ``Py_mod_gil = Py_MOD_GIL_NOT_USED``
makes CPython re-enable the GIL at import time. Once that happens
the interpreter stays GIL-ed for the rest of the process, so a
single import of the whole annotation stack can tell us *that*
something forced it on, but never *which* module did.

This probe therefore imports every candidate module in its own
subprocess and records ``sys._is_gil_enabled()`` afterwards. A
module whose child process comes back with the GIL enabled is an
offender.

Usage::

    python scripts/ft_gil_probe.py --junit-xml /reports/ft-gil.xml

Exit status:
    0 — free-threading held; no offenders
    1 — at least one offender (or the stack import re-enabled it)
    2 — the running interpreter is not free-threaded and
        ``--require-freethreaded`` was passed
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import sysconfig
from dataclasses import dataclass
from xml.sax.saxutils import escape, quoteattr

# CPython names the culprit in its warning, and it is often a
# submodule -- "pysam.libchtslib", not "pysam".
_OFFENDER_RE = re.compile(r"module '([^']+)'")

# Native extensions in the `gain-core` + `gain-web-api` closure.
# Pure-Python packages are pointless to probe: they cannot hold the
# `Py_mod_gil` slot and can never force the GIL on.
DEFAULT_MODULES = (
    "numpy",
    "pandas",
    "pyarrow",
    "pysam",
    "pyBigWig",
    "apsw",
    "pydantic_core",
    "psutil",
    "yaml",
    "aiohttp",
    "matplotlib",
)

# Importing this pulls the annotation pipeline and, transitively,
# `gain.genomic_resources.repository` -> pysam.
DEFAULT_STACK_MODULE = "gain.annotation.annotate_tabular"

# Run in a fresh interpreter, one module per process, so that the
# first offender does not poison the verdict for every module
# probed after it.
_CHILD_SRC = """
import importlib
import json
import sys
import warnings

module = sys.argv[1]
error = None
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    try:
        importlib.import_module(module)
    except BaseException as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
    messages = [str(entry.message) for entry in caught]

json.dump(
    {
        "gil_enabled": sys._is_gil_enabled(),
        "error": error,
        "warnings": [
            message for message in messages
            if "GIL" in message or "global interpreter lock" in message
        ],
    },
    sys.stdout,
)
"""


@dataclass
class ProbeResult:
    """Outcome of importing one module in a free-threaded child."""

    module: str
    gil_enabled: bool
    error: str | None
    warnings: list[str]

    @property
    def is_offender(self) -> bool:
        """Whether importing this module re-enabled the GIL."""
        return self.error is None and self.gil_enabled

    @property
    def culprit(self) -> str:
        """The extension CPython blamed, e.g. `pysam.libchtslib`."""
        for message in self.warnings:
            match = _OFFENDER_RE.search(message)
            if match is not None:
                return match.group(1)
        return self.module


def is_freethreaded() -> bool:
    """Whether the running interpreter is a free-threaded build."""
    return bool(sysconfig.get_config_var("Py_GIL_DISABLED"))


def _child_env() -> dict[str, str]:
    """Environment for a probe child.

    ``PYTHON_GIL=0`` forces the GIL to stay disabled even when a
    module asks for it, which would make every offender look clean.
    Drop it so the child observes CPython's default behaviour.
    """
    env = dict(os.environ)
    env.pop("PYTHON_GIL", None)
    return env


def probe_module(module: str, executable: str = sys.executable) -> ProbeResult:
    """Import `module` in a fresh child and report the GIL state."""
    completed = subprocess.run(
        [executable, "-c", _CHILD_SRC, module],
        capture_output=True,
        text=True,
        env=_child_env(),
        check=False,
    )
    if completed.returncode != 0:
        return ProbeResult(
            module=module,
            gil_enabled=False,
            error=(
                f"probe child exited {completed.returncode}: "
                f"{completed.stderr.strip()[:200]}"
            ),
            warnings=[],
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return ProbeResult(
            module=module,
            gil_enabled=False,
            error=f"unparsable probe output: {completed.stdout[:200]!r}",
            warnings=[],
        )
    return ProbeResult(
        module=module,
        gil_enabled=bool(payload["gil_enabled"]),
        error=payload["error"],
        warnings=list(payload["warnings"]),
    )


def render_table(results: list[ProbeResult]) -> str:
    """Human-readable summary, widest-module-name aligned."""
    if not results:
        return "no modules probed"
    width = max(len(result.module) for result in results)
    lines = [f"{'module'.ljust(width)}  verdict"]
    lines.append("-" * (width + 40))
    for result in results:
        if result.error is not None:
            verdict = f"SKIP  ({result.error})"
        elif result.gil_enabled:
            verdict = f"GIL RE-ENABLED  <- {result.culprit}"
        else:
            verdict = "ok    (free-threading held)"
        lines.append(f"{result.module.ljust(width)}  {verdict}")
    return "\n".join(lines)


def render_junit(results: list[ProbeResult]) -> str:
    """JUnit XML so Jenkins can trend offenders build over build."""
    offenders = sum(1 for result in results if result.is_offender)
    skipped = sum(1 for result in results if result.error is not None)
    cases = []
    for result in results:
        name = quoteattr(result.module)
        if result.error is not None:
            body = f"<skipped message={quoteattr(result.error)}/>"
        elif result.gil_enabled:
            detail = "\n".join(result.warnings) or (
                "sys._is_gil_enabled() was True after import"
            )
            body = (
                f"<failure message={quoteattr('re-enabled the GIL')}>"
                f"{escape(detail)}</failure>"
            )
        else:
            body = ""
        cases.append(
            f'    <testcase classname="ft_gil_probe" name={name}>'
            f"{body}</testcase>",
        )
    joined = "\n".join(cases)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<testsuite name="ft_gil_probe" tests="{len(results)}" '
        f'failures="{offenders}" skipped="{skipped}">\n'
        f"{joined}\n"
        "</testsuite>\n"
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--module", action="append", dest="modules", default=None,
        help="module to probe (repeatable); defaults to the gain closure",
    )
    parser.add_argument(
        "--stack-module", default=DEFAULT_STACK_MODULE,
        help="module whose import must leave the GIL disabled",
    )
    parser.add_argument(
        "--junit-xml", default=None,
        help="write a JUnit report to this path",
    )
    parser.add_argument(
        "--require-freethreaded", action="store_true",
        help="exit 2 if the interpreter is not free-threaded",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Probe every module, report offenders, return an exit status."""
    args = _parse_args(argv)

    if not is_freethreaded():
        message = (
            f"{sys.executable} is not a free-threaded build "
            "(Py_GIL_DISABLED is unset) — every module would look "
            "like an offender."
        )
        if args.require_freethreaded:
            print(f"error: {message}", file=sys.stderr)
            return 2
        print(f"skipping: {message}")
        return 0

    modules = args.modules or list(DEFAULT_MODULES)
    results = [probe_module(module) for module in modules]
    stack = probe_module(args.stack_module)
    results.append(stack)

    print(render_table(results))

    if args.junit_xml:
        pathlib.Path(args.junit_xml).write_text(
            render_junit(results), encoding="utf-8",
        )

    offenders = [result.module for result in results if result.is_offender]
    if offenders:
        print(
            f"\n{len(offenders)} module(s) force the GIL back on: "
            f"{', '.join(offenders)}",
            file=sys.stderr,
        )
        return 1

    # A module that failed to import is reported as a skip, which is
    # right for an optional dependency but not for the stack itself:
    # a typo'd or Django-dependent `--stack-module` would otherwise
    # skip the one assertion this probe exists to make, and the job
    # would go green having asserted nothing.
    if stack.error is not None:
        print(
            f"\nerror: stack module {stack.module!r} never imported, so "
            f"free-threading was never asserted: {stack.error}",
            file=sys.stderr,
        )
        return 1

    print("\nfree-threading held for the whole annotation stack")
    return 0


if __name__ == "__main__":
    sys.exit(main())
