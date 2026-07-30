"""How `grr_manage` reports a failure that belongs to ONE resource.

Its own module because more than one command needs it and they must agree:
a repository-wide command that dies on the first bad resource reports half
a repository and hides the rest (gain#364, gain#503).
"""
from __future__ import annotations

from cerberus.schema import SchemaError

from gain import logging

logger = logging.getLogger("grr_manage")

# `OSError` subsumes `FileNotFoundError`: a resource file is undescribable
# for more reasons than being absent -- a symlink loop, a target whose
# parent is not a directory, a DVC cache this run may not traverse. Each is
# a fault of the RESOURCE, and each used to abort the whole run (gain#503).
RESOURCE_ERRORS = (ValueError, SchemaError, OSError)


def report_resource_failure(
    err: Exception, action: str, resource_id: str,
) -> None:
    """Report a failed operation on one resource, at the right tier.

    ``action`` names what could not be done -- never the phase the failure
    happened in.  A handler that wraps several operations cannot know which
    one raised, and naming the wrong one sends the reader looking in the
    wrong place (gain#364); the cause, which is always carried, says it.
    """
    # LOG014 is suppressed rather than obeyed: every caller is an exception
    # handler, which is exactly what makes `exc_info` meaningful here -- the
    # linter cannot see that through the call.
    if isinstance(err, RESOURCE_ERRORS):
        # `str(err)` is empty for an exception raised without a message --
        # `raise ValueError()`, or a bare `assert` under `python -O`. The
        # class name is then the only thing left that says anything about
        # the cause, and this issue is exactly about losing it (gain#364).
        logger.error(
            "%s <%s>: %s", action, resource_id,
            str(err) or type(err).__name__)
        logger.debug(
            "%s <%s> failed", action, resource_id,
            exc_info=True)  # noqa: LOG014
        return
    logger.error(
        "%s <%s>: unexpected internal error", action, resource_id,
        exc_info=True)  # noqa: LOG014
