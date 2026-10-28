# pylint: disable=W0621,C0116
"""``scripts/crap_report_html.py`` renders the CRAP table of crapper."""
import importlib.util
import pathlib
import sys
import textwrap
from types import ModuleType

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "crap_report_html.py"

TABLE = textwrap.dedent("""\
    CRAP Report
    ===========
    Languages: python

    Function      Namespace                  CC    Cov%     CRAP
    ------------------------------------------------------------
    cli_vcf       gain.effect_annotation.cli 10   0.0%    110.0
    parse_gtf     gain.gene_models.parsers   24  97.4%     24.0
    __lt__        gain.utils.Region<T>        3  50.0%      4.1
    """)


@pytest.fixture(scope="module")
def crap_report_html() -> ModuleType:
    if not SCRIPT.exists():
        pytest.fail(f"{SCRIPT} is missing; the CI image must copy scripts/")
    spec = importlib.util.spec_from_file_location(
        "crap_report_html", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_parse_reads_every_row_after_the_rule(
    crap_report_html: ModuleType,
) -> None:
    rows = crap_report_html.parse_crap_table(TABLE)

    assert [
        (row.function, row.complexity, row.coverage, row.crap)
        for row in rows
    ] == [
        ("cli_vcf", 10, 0.0, 110.0),
        ("parse_gtf", 24, 97.4, 24.0),
        ("__lt__", 3, 50.0, 4.1),
    ]


def test_parse_of_text_without_the_rule_is_empty(
    crap_report_html: ModuleType,
) -> None:
    assert crap_report_html.parse_crap_table("crapper: no files\n") == []


def test_page_has_the_summary_counts_and_one_row_per_function(
    crap_report_html: ModuleType,
) -> None:
    rows = crap_report_html.parse_crap_table(TABLE)

    page = crap_report_html.render_html(rows)

    assert "<li>Functions: 3</li>" in page
    assert "<li>CRAP &gt;= 30: 1</li>" in page
    assert "<li>CRAP &gt;= 10: 2</li>" in page
    assert "<li>Coverage 0%: 1</li>" in page
    assert page.count("<tr><td>") == 3
    assert (
        "<tr><td>cli_vcf</td><td>gain.effect_annotation.cli</td>"
        '<td align="right">10</td><td align="right">0.0%</td>'
        '<td align="right">110.0</td></tr>') in page


def test_page_escapes_the_namespace(
    crap_report_html: ModuleType,
) -> None:
    page = crap_report_html.render_html(
        crap_report_html.parse_crap_table(TABLE))

    assert "gain.utils.Region&lt;T&gt;" in page
    assert "Region<T>" not in page


def test_main_writes_a_no_rows_page_for_a_missing_table(
    crap_report_html: ModuleType, tmp_path: pathlib.Path,
) -> None:
    target = tmp_path / "crap.html"

    exit_code = crap_report_html.main(
        ["crap_report_html.py", str(tmp_path / "absent.txt"), str(target)])

    assert exit_code == 0
    assert "crapper wrote no rows." in target.read_text()
    assert "<table" not in target.read_text()
