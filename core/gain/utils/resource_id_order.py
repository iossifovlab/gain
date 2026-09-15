"""The order the repository index page lists resource ids in.

The page's own controls order ids with JavaScript's ``localeCompare``:
the ID column's sorter, and the tree view.  The rows the page is
published with have to be in the same order, or what a reader sees on
arrival and what one click on the ID header hands them disagree with
nothing having changed (iossifovlab/gain#1351) -- and the published
order is all a reader has until the search index loads, or for good if
it never does.

``localeCompare`` cannot be called from here, and it is not one thing:
with no locale named it follows the *browser's*, and a Danish one sorts
``aa`` after ``z``, a Czech one ``chr1`` after ``hg38``.  What this key
reproduces is the *root* collation on the alphabet a resource id is
scanned from, ``[a-zA-Z0-9/._-]`` -- what a browser in English and most
Western locales gives: punctuation ahead of digits ahead of letters,
letters compared without regard to case first, and lowercase ahead of
uppercase only where nothing else separates two ids.  A browser in a
locale that tailors the alphabet will still see the published order
and the clicked order disagree, exactly as the tree and the sorter
already do between themselves there.  It is deliberately not
``locale.strxfrm``, which would make the published page depend on the
build host's locale, and a rebuild of an unchanged repository is meant
to be byte-identical.
"""
from __future__ import annotations

import string

#: Digits and lowercase letters already sit in code-point order behind
#: these four marks once case is folded; only the marks have to be moved
#: below the digits, in the order the root collation ranks them.
_ROOT = str.maketrans(
    string.ascii_uppercase + "_-./",
    string.ascii_lowercase + "\0\1\2\3",
)


def resource_id_collation_key(resource_id: str) -> tuple[str, str]:
    """Sort key ordering ids the way the index page's controls do.

    Punctuation before digits before letters, letters without regard
    to case; where two ids differ only in case, the one with the
    lowercase letter at the first difference sorts first, as
    ``localeCompare`` has it (``alpha`` before ``Alpha``) -- which is
    what swapping case and comparing does.  Total over ``str``: a
    character outside the alphabet keeps its code point.
    """
    return (resource_id.translate(_ROOT), resource_id.swapcase())
