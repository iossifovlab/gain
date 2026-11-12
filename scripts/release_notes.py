"""Check the release notes of a gain release.

Usage: ``python3 release_notes.py check <tag> [--changes PATH]``

The ``Pre-flight: release notes`` stage of ``Jenkinsfile.release`` runs
the script on a ``builder`` agent, outside of a CI image, so the script
uses the Python standard library only.

``check`` reads the first column-0 ``* `` heading of ``--changes``
(default ``docs/source/changes.rst``). The check exits 0 when that
heading is the tag. Otherwise the check prints one line that names the
tag and the first section, and exits 1.
"""
import argparse
import pathlib
import re
import sys
from collections.abc import Sequence

DEFAULT_CHANGES = "docs/source/changes.rst"

# A section heading starts at column 0. The items under a section are
# indented, so a nested ``* `` bullet never matches. The first heading
# is the section, also when its text is not a bare version: a heading
# such as ``* 2026.12.0 (draft)`` then fails the check.
_SECTION = re.compile(r"^\* (.*\S)\s*$")


def first_section(text: str) -> str | None:
    """Return the text of the first section heading, or None."""
    for line in text.splitlines():
        match = _SECTION.match(line)
        if match:
            return match.group(1)
    return None


def check(tag: str, changes: pathlib.Path) -> int:
    """Exit 0 when the first section of ``changes`` is ``tag``."""
    first = first_section(changes.read_text(encoding="utf-8"))
    if first == tag:
        return 0
    if first is None:
        print(f"release notes: {changes} has no section, "
              f"so tag {tag} has no notes")
    else:
        print(f"release notes: the first section of {changes} is "
              f"{first}, not the tag {tag}")
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    """Run the subcommand that ``argv`` names."""
    parser = argparse.ArgumentParser(prog="release_notes.py")
    subcommands = parser.add_subparsers(dest="command", required=True)
    check_parser = subcommands.add_parser(
        "check", help="the tag is the first section of the release notes")
    check_parser.add_argument("tag")
    check_parser.add_argument(
        "--changes", type=pathlib.Path, default=pathlib.Path(DEFAULT_CHANGES))
    args = parser.parse_args(argv)
    return check(args.tag, args.changes)


if __name__ == "__main__":
    sys.exit(main())
