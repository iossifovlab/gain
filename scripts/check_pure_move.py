#!/usr/bin/env python
"""Check that module-level definitions moved between modules unchanged.

A slice of a module split moves functions, classes and constants from one
module to another. The move is pure when each moved definition has the
same ``ast.dump``, docstring included, in its new module as it had in the
old module at the base commit.

Usage, from inside the git checkout::

    python scripts/check_pure_move.py --base <commit> \\
        --old core/gain/genomic_resources/repository.py \\
        --new core/gain/genomic_resources/repository_ids.py \\
        validate_resource_id GR_CONF_FILE_NAME ...

The script prints one line for each name: ``same <name>``,
``DIFFERENT <name>``, or ``MISSING <name>`` when the base version of the
old module or the new module does not define the name. It exits with 0
when every name is ``same``, and with 1 otherwise.

A name is a function, an async function, a class, or a module-level
assignment (``X = ...`` or ``X: T = ...``). Decorators are part of the
dump, so a class that lost its ``@dataclass`` is ``DIFFERENT``.
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import shutil
import subprocess
import sys
from collections.abc import Sequence


def _defined_names(node: ast.stmt) -> list[str]:
    """Return the names that the module-level statement ``node`` defines."""
    if isinstance(
            node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return [node.name]
    if isinstance(node, ast.Assign):
        return [
            target.id for target in node.targets
            if isinstance(target, ast.Name)]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    return []


def definitions(source: str) -> dict[str, str]:
    """Map each module-level name of ``source`` to its ``ast.dump``."""
    result: dict[str, str] = {}
    for node in ast.parse(source).body:
        for name in _defined_names(node):
            result[name] = ast.dump(node)
    return result


def base_source(base: str, path: str) -> str:
    """Return the text of ``path`` at the commit ``base``."""
    git = shutil.which("git")
    if git is None:
        raise SystemExit("git is not on PATH")
    return subprocess.run(
        [git, "show", f"{base}:./{path}"],
        check=True, capture_output=True, text=True).stdout


def compare(
    old: dict[str, str], new: dict[str, str], names: Sequence[str],
) -> list[tuple[str, str]]:
    """Return the verdict for each name, in the order of ``names``."""
    verdicts = []
    for name in names:
        if name not in old or name not in new:
            verdicts.append(("MISSING", name))
        elif old[name] == new[name]:
            verdicts.append(("same", name))
        else:
            verdicts.append(("DIFFERENT", name))
    return verdicts


def main(argv: Sequence[str] | None = None) -> int:
    """Run the check and return the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--base", required=True, help="the commit before the move")
    parser.add_argument(
        "--old", required=True,
        help="the module the names moved out of, as a path in the repo")
    parser.add_argument(
        "--new", required=True,
        help="the module the names moved into, as a path in the work tree")
    parser.add_argument("names", nargs="+", help="the moved names")
    args = parser.parse_args(argv)

    old = definitions(base_source(args.base, args.old))
    new = definitions(pathlib.Path(args.new).read_text(encoding="utf-8"))
    verdicts = compare(old, new, args.names)
    for verdict, name in verdicts:
        print(f"{verdict} {name}")
    return 0 if all(verdict == "same" for verdict, _ in verdicts) else 1


if __name__ == "__main__":
    sys.exit(main())
