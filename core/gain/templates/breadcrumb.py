"""The trail a resource's generated pages show back to the repository.

A resource page and its statistics page carry the same breadcrumb the
index page's hierarchical view draws (gain#1477): the root, one crumb
per id segment, and the page itself last.  Each crumb's address is
decided here rather than in the templates, from the same two facts the
root climb is computed from -- the resource id and where the page sits
inside the resource directory -- so the trail and the climb cannot
drift.  The templates only loop over what this returns.

A crumb into the tree addresses a folder the way the index page
addresses one itself, ``index.html#/<segments>`` (gain#579), so it is a
plain link and the index page does the rest on load.  The root crumb
goes to the tree's root, not to a bare ``index.html``: an empty fragment
means the table view, and the trail is one kind of navigation.
"""
from __future__ import annotations

from dataclasses import dataclass

from gain.templates.static_assets import climb_to_root

#: The root crumb's name, the same words the index page's breadcrumb
#: gives its own root.
ROOT_CRUMB_NAME = "All resources"

#: What follows ``index.html`` to address the tree's root; a folder is
#: this plus its segments, joined by ``/``.
_TREE_HASH = "#/"


@dataclass(frozen=True)
class Crumb:
    """One step of the trail: its name, and where it leads.

    ``href`` is ``None`` for the page itself, which is never a link.
    """

    name: str
    href: str | None


def page_breadcrumb(resource_id: str, page: str) -> list[Crumb]:
    """The trail to one of a resource's pages, outermost crumb first.

    ``page`` is the page's path inside the resource directory, as the
    publisher names it -- ``index.html`` for the info page,
    ``statistics/index.html`` for the statistics page -- the same
    argument the root climb takes.  Every directory in it after the id's
    own is a crumb of its own, and the last crumb is the current page.
    """
    root = climb_to_root(f"{resource_id}/{page}")
    index = f"{root}index.html"
    segments = resource_id.split("/")
    # The directories between the resource and the page, if any: for
    # the info page there are none and the resource is the current
    # crumb; for the statistics page there is one, and the resource's
    # crumb links to its info page.
    page_dirs = page.split("/")[:-1]

    trail = [Crumb(ROOT_CRUMB_NAME, f"{index}{_TREE_HASH}")]
    for depth, name in enumerate(segments, start=1):
        trail.append(Crumb(
            name,
            f"{index}{_TREE_HASH}{'/'.join(segments[:depth])}",
        ))
    if page_dirs:
        trail[-1] = Crumb(segments[-1], f"{root}{resource_id}/index.html")
        trail.extend(Crumb(name, None) for name in page_dirs)
    else:
        trail[-1] = Crumb(segments[-1], None)
    return trail
