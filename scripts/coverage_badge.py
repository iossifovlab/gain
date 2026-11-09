"""Write a shields.io endpoint file with the line coverage of a build.

Usage: ``python coverage_badge.py <coverage.xml> <badge.json>``

The input is a Cobertura XML file, as ``pytest --cov`` writes it. The
message of the badge is the ``line-rate`` attribute of the root
``coverage`` element as a whole percentage. The branch rate is not used.
The colour is ``green`` from 80%, ``yellow`` from 60% and ``red`` below.
A missing input writes no file: the script prints one line and exits 0,
because a docs-only build has no coverage report.

``docs/build_docs.sh`` writes the file into the rendered docs site, so
the ``Deploy docs`` stage publishes it with the site. The input is the
report that the ``core`` stage of CI writes, so ``xml.etree`` parses it.
"""
import json
import pathlib
import sys
import xml.etree.ElementTree as ET  # ruff: ignore[suspicious-xml-etree-import]


def badge(line_rate: float) -> dict[str, int | str]:
    """Return the shields.io endpoint document for a line rate."""
    percent = round(line_rate * 100)
    return {
        "schemaVersion": 1,
        "label": "coverage",
        "message": f"{percent}%",
        "color": color(percent),
    }


def color(percent: int) -> str:
    """Return the badge colour for a whole coverage percentage."""
    if percent >= 80:
        return "green"
    if percent >= 60:
        return "yellow"
    return "red"


def main(argv: list[str]) -> int:
    """Read the Cobertura file at argv[1] and write the badge to argv[2]."""
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    source, target = pathlib.Path(argv[1]), pathlib.Path(argv[2])
    if not source.is_file():
        print(f"coverage_badge.py: no {source}, no badge written")
        return 0
    tree = ET.parse(source)  # ruff: ignore[suspicious-xml-element-tree-usage]
    root = tree.getroot()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(badge(float(root.attrib["line-rate"]))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
