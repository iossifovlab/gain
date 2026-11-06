# pylint: disable=C0114,C0116
"""How the score info page renders the Default annotation column (gain#310).

The cell of a score lists the names of its ``default_annotation``
entries, comma-joined.  An entry without a ``name`` shows the score id,
and a score that no entry names shows ``-``.
"""
import pathlib
import re

from gain.genomic_resources.repository_factory import (
    build_resource_implementation,
)
from gain.genomic_resources.testing.builders import a_position_score


def _default_annotation_cells(tmp_path: pathlib.Path) -> dict[str, str]:
    resource = (
        a_position_score()
        .with_score("score1", "float")
        .with_score("score2", "float")
        .with_score("score3", "float")
        .with_data("""
            chrom pos_begin pos_end score1 score2 score3
            1     10        10      0.1    0.2    0.3
        """)
        .with_default_annotation([
            {"source": "score1", "name": "primary"},
            {"source": "score1", "name": "secondary"},
            {"source": "score2"},
        ])
        .build_resource(tmp_path)
    )
    page = build_resource_implementation(resource).get_info()
    rows = re.findall(
        r'<tr class="score-definition">\s*<td>(\w+)</td>\s*<td>\w+</td>'
        r"\s*<td>\s*<p>([^<]*)</p>",
        page)
    return dict(rows)


def test_the_default_annotation_column_shows_the_entry_names(
    tmp_path: pathlib.Path,
) -> None:
    cells = _default_annotation_cells(tmp_path)

    assert cells == {
        "score1": "primary,secondary",
        "score2": "score2",
        "score3": "-",
    }
