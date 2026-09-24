# pylint: disable=C0114,C0116
import re

import pytest
from django.template.loader import render_to_string

# The three pages the web API renders itself, by the template name the views
# and the auth throttle ask for, with the title each one must carry.
PAGES = [
    ("forgotten-password.html", "GAIn Reset password"),
    ("reset-password.html", "GAIn New password"),
    ("throttled.html", "GAIn Too many requests"),
]


def _normalized(html: str) -> str:
    """Collapse whitespace, and drop it entirely between tags and text."""
    html = re.sub(r"\s+", " ", html).strip()
    html = re.sub(r">\s+", ">", html)
    return re.sub(r"\s+<", "<", html)


def _scaffold_opening(title: str) -> str:
    return (
        '<!DOCTYPE html><html lang="en">'
        "<head>"
        '<meta charset="utf-8" />'
        f"<title>{title}</title>"
        '<meta content="width=device-width, initial-scale=1, '
        'shrink-to-fit=no" name="viewport" />'
        '<link href="https://fonts.googleapis.com/css?'
        'family=Roboto:400,500,700&amp;subset=latin-ext" rel="stylesheet">'
        '<link href="/static/gain-logo.png" rel="icon" type="image/x-icon">'
        '<link href="/static/styles.css" rel="stylesheet">'
        '<link href="https://maxcdn.bootstrapcdn.com/font-awesome/4.1.0/'
        'css/font-awesome.min.css" rel="stylesheet">'
        "</head>"
        "<body>"
        '<div class="login-container">'
        '<div class="logo-header">'
        '<img class="logo" src="/static/gain-logo.png">'
        '<label class="header-text">'
        "GAIn: Genomic Annotation Infrastructure</label>"
        "</div>"
    )


@pytest.mark.parametrize(("template", "title"), PAGES)
def test_standalone_page_opens_with_the_shared_head_and_branding(
    template: str, title: str,
) -> None:
    page = _normalized(render_to_string(template, {}))

    assert page.startswith(_scaffold_opening(title)), page


@pytest.mark.parametrize("template", [template for template, _ in PAGES])
def test_standalone_page_renders_its_message_inside_the_branded_container(
    template: str,
) -> None:
    # Every page renders the message last; it must be the container's last
    # child, not a sibling after the container closes.
    page = _normalized(render_to_string(
        template, {"message": "Try again later", "message_type": "warn"}))

    assert page.endswith(
        '<div class="message warn">Try again later</div></div></body></html>',
    ), page
