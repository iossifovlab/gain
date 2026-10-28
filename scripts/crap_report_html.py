"""Render the text CRAP table of crapper as an HTML page.

Usage: ``python crap_report_html.py <crap.txt> <crap.html>``

The core stage of the root ``Jenkinsfile`` writes ``crap.txt`` with
``crapper`` and publishes the page that this script writes with
``publishHTML``. Jenkins serves that page under a Content-Security-Policy
that blocks inline styles and scripts, so the page is plain HTML: a
summary list and one table, worst score first, as crapper sorts it.

A row of the table has five fields: function, namespace, cyclomatic
complexity, coverage (``97.4%``) and CRAP. Lines before the dashed rule
under the column header are the report header. An input without that
rule, or with no rows, gives a page that says so.
"""
import html
import pathlib
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class CrapRow:
    """One function of the CRAP table."""
    function: str
    namespace: str
    complexity: int
    coverage: float
    crap: float


def parse_crap_table(text: str) -> list[CrapRow]:
    """Return the rows of a crapper text table, in table order."""
    lines = text.splitlines()
    start = next(
        (i + 1 for i, line in enumerate(lines)
         if line.startswith("---")),
        None)
    if start is None:
        return []
    rows = []
    for line in lines[start:]:
        fields = line.split()
        if len(fields) != 5:
            continue
        function, namespace, complexity, coverage, crap = fields
        rows.append(CrapRow(
            function, namespace, int(complexity),
            float(coverage.rstrip("%")), float(crap)))
    return rows


def render_html(rows: list[CrapRow]) -> str:
    """Return the HTML page for the given rows."""
    title = "CRAP report of gain"
    parts = [
        "<!DOCTYPE html>",
        (f'<html><head><meta charset="utf-8"><title>{title}</title>'
         "</head><body>"),
        f"<h1>{title}</h1>",
    ]
    if not rows:
        parts.append("<p>crapper wrote no rows.</p></body></html>")
        return "\n".join(parts)
    parts.append("<ul>")
    parts.extend(
        f"<li>{label}: {count}</li>" for label, count in (
            ("Functions", len(rows)),
            ("CRAP &gt;= 30", sum(row.crap >= 30 for row in rows)),
            ("CRAP &gt;= 10", sum(row.crap >= 10 for row in rows)),
            ("Coverage 0%", sum(row.coverage == 0 for row in rows)),
        ))
    parts.extend((
        "</ul>",
        ('<table border="1" cellpadding="3">'
         "<thead><tr><th>Function</th><th>Namespace</th><th>CC</th>"
         "<th>Cov%</th><th>CRAP</th></tr></thead><tbody>"),
    ))
    parts.extend(
        f"<tr><td>{html.escape(row.function)}</td>"
        f"<td>{html.escape(row.namespace)}</td>"
        f'<td align="right">{row.complexity}</td>'
        f'<td align="right">{row.coverage:.1f}%</td>'
        f'<td align="right">{row.crap:.1f}</td></tr>'
        for row in rows)
    parts.append("</tbody></table></body></html>")
    return "\n".join(parts)


def main(argv: list[str]) -> int:
    """Read the text table at argv[1] and write the page to argv[2]."""
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    source, target = pathlib.Path(argv[1]), pathlib.Path(argv[2])
    text = source.read_text() if source.exists() else ""
    target.write_text(render_html(parse_crap_table(text)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
