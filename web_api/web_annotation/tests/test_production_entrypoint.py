# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Config-text guard for ``Dockerfile.production``'s daphne command line.

**The defect this pins (iossifovlab/gain#795).**  The confirmation and
reset mails carry a single-use code in the query string, and daphne's
access log records the full request target -- query included.  Started
with neither ``--access-log`` nor ``-v``, daphne's CLI turns that log on
at verbosity 1 and writes it to *stdout*, so every redemption wrote a
live code into the container's stdout and from there into Docker's
``json-file`` store on the deployed host.  gain#754 closed the same leak
in the frontend image's Apache log; this is the backend's copy of it.

**What is asserted is the destination, not the spelling.**  Any command
line that keeps the access record out of a retained sink passes -- the
log turned off entirely, or pointed at a discarding one.  What fails is
stdout, stderr, and any file the container keeps, because a code sitting
in a container-local log file is still a live credential at rest.

Silencing the log by lowering verbosity instead would take the root
logger down to WARN with it, so the verbosity the image ships is
asserted separately.  The two tests together describe the choice
recorded on gain#795 without naming the flag that implements it.

**The flags are read by daphne's own parser**, not by a second one
written here: the defaults are the whole point (verbosity defaults to 1,
which is what enabled the log), and a guard that restated them would
keep passing across the daphne upgrade that changed one.  Only the six
lines of ``cli.run`` that turn parsed arguments into a stream are
modelled, in ``_access_log_destination`` below.

That model is the one thing here that can rot silently, so it was
checked against a real server rather than only against the source:
daphne **4.2.1**, driven with the shipped command line, logged nothing
for a request to ``/api/reset_password?code=<token>`` while still
printing its INFO ``Listening on TCP address`` line -- and, run without
``--access-log`` as a control, logged the token.  A daphne upgrade that
moves the verbosity threshold means re-running that check, not just
re-reading this file.

The guard refuses what it cannot model rather than passing: an
entrypoint that is not a JSON array, or one that does not invoke
``daphne`` directly, fails loudly.  Both are legitimate things to do --
starting daphne from a Python entrypoint is one of the fixes gain#795
weighed -- but neither is something this file can still make a statement
about, and a guard that shrugged at them would be green while the codes
came back.
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import shlex

import pytest

# daphne ships no py.typed marker, so its parser is untyped here; the
# annotations in _parse are what give the flags their shape back.  This
# import is at module scope on purpose: it pulls in ``daphne.server``,
# which installs a Twisted reactor as an import side effect, but
# ``daphne`` is in INSTALLED_APPS and its AppConfig imports the same
# module, so every web_api pytest process has already paid that before
# this file is collected.  Deferring it would buy nothing and read as
# though it did.
from daphne.cli import CommandLineInterface  # type: ignore[import-untyped]

#: The ``web_api`` project root: this file is ``web_annotation/tests/``.
#: Resolved from the module, never from the working directory -- CI runs
#: pytest from the project directory, not the repo root.
WEB_API_SRC = pathlib.Path(__file__).resolve().parents[2]

#: The image the deployed backend runs.
PRODUCTION_DOCKERFILE = WEB_API_SRC / "Dockerfile.production"

#: What ``_access_log_destination`` returns for the container's own
#: stdout -- the sink Docker's logging driver retains.
STDOUT = "<stdout>"

#: Destinations that keep nothing.  ``None`` is "daphne writes no access
#: log at all"; the device discards what it is given.
DISCARDING = frozenset({None, "/dev/null"})


def _entrypoint_argv(dockerfile: str) -> list[str]:
    """The ``ENTRYPOINT`` command line, as an argument list.

    Only the JSON *exec* form is understood.  The shell form starts a
    shell whose own word-splitting decides what daphne receives, which
    this file has no business guessing at, so it raises instead.
    """
    joined = dockerfile.replace("\\\n", " ")
    entrypoints = [
        line.split(maxsplit=1)[1]
        for line in joined.splitlines()
        if line.split(maxsplit=1)[:1] == ["ENTRYPOINT"]
    ]
    if len(entrypoints) != 1:
        raise ValueError(
            f"expected exactly one ENTRYPOINT, found {len(entrypoints)}",
        )
    try:
        argv = json.loads(entrypoints[0])
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"ENTRYPOINT is not the JSON exec form: {entrypoints[0]!r}",
        ) from exc
    if not isinstance(argv, list) or not all(
            isinstance(word, str) for word in argv):
        raise ValueError(f"ENTRYPOINT is not a list of strings: {argv!r}")
    return argv


def _access_log_destination(argv: list[str]) -> str | None:
    """Where the daphne command line ``argv`` sends its access log.

    ``None`` when it writes none, :data:`STDOUT` for the process's own
    stdout, otherwise the path it opens.  This is ``daphne.cli``'s rule::

        if args.access_log:
            access_log_stream = (
                sys.stdout if args.access_log == "-"
                else open(args.access_log, "a", 1))
        elif args.verbosity >= 1:
            access_log_stream = sys.stdout

    An explicit ``--access-log`` therefore wins over verbosity, and the
    absence of both is what enables the log -- daphne's ``--verbosity``
    defaults to 1.
    """
    args = _parse(argv)
    if args.access_log:
        return STDOUT if args.access_log == "-" else args.access_log
    return STDOUT if args.verbosity >= 1 else None


def _root_log_level(argv: list[str]) -> int:
    """The level ``argv`` gives the root logger, as ``logging`` spells it.

    Only the 0-versus-anything-higher boundary is load-bearing -- the
    caller asks whether INFO survives -- but the level itself is what
    makes a failure message readable.  Unlike the flags, this table is
    transcribed from ``daphne.cli`` rather than read from it, because it
    is an inline dict inside ``run`` with nothing importable to ask::

        logging.basicConfig(level={0: WARN, 1: INFO, 2: DEBUG, 3: DEBUG}[
            args.verbosity], ...)
    """
    return {
        0: logging.WARNING,
        1: logging.INFO,
        2: logging.DEBUG,
        3: logging.DEBUG,
    }[_parse(argv).verbosity]


def _parse(argv: list[str]) -> argparse.Namespace:
    """Parse a daphne command line with daphne's own argument parser."""
    if not argv or argv[0] != "daphne":
        raise ValueError(
            f"not a daphne command line: {argv!r}. This guard reads the "
            f"flags with daphne's own parser, so it can only speak about "
            f"an entrypoint that runs daphne directly",
        )
    parser: argparse.ArgumentParser = CommandLineInterface().parser
    return parser.parse_args(argv[1:])


def test_the_entrypoint_is_read_across_its_continuation_lines() -> None:
    """The shipped ENTRYPOINT is a JSON array split over several lines.

    Read line by line it is not JSON at all, so a guard that missed the
    backslash continuations would find no command line to police.
    """
    argv = _entrypoint_argv(
        'FROM python:3.12-slim\n'
        'ENTRYPOINT ["daphne", \\\n'
        '            "--port", "9001", \\\n'
        '            "web_annotation.asgi:application"]\n',
    )

    assert argv == [
        "daphne", "--port", "9001", "web_annotation.asgi:application"]


@pytest.mark.parametrize("dockerfile", [
    # A shell does the word-splitting, and what daphne ends up with
    # depends on quoting this file cannot see.
    pytest.param(
        "ENTRYPOINT daphne --access-log /dev/null app:app",
        id="shell-form"),
    # Nothing to police.
    pytest.param('CMD ["daphne"]', id="no-entrypoint"),
    # Docker honours the last, and a guard that read the first would
    # police a line the container never runs.
    pytest.param(
        'ENTRYPOINT ["daphne", "app:app"]\n'
        'ENTRYPOINT ["daphne", "--access-log", "-", "app:app"]',
        id="two-entrypoints"),
    # Valid JSON, and not a command line.
    pytest.param('ENTRYPOINT "daphne"', id="not-a-list"),
])
def test_an_entrypoint_this_guard_cannot_read_is_refused(
    dockerfile: str,
) -> None:
    """Refuse rather than shrug -- a silent pass is the failure mode."""
    with pytest.raises(ValueError):
        _entrypoint_argv(dockerfile)


def test_an_entrypoint_that_does_not_run_daphne_is_refused() -> None:
    """The flags only mean what daphne's parser says they mean.

    An interpreter's arguments are not daphne's, and every assertion
    below would be talking about the wrong ones.
    """
    with pytest.raises(ValueError, match="not a daphne command line"):
        _access_log_destination(
            ["python", "-m", "web_annotation.serve", "--access-log", "-"])


#: A daphne command line, minus the flags each case is about.  Only the
#: application is required; the bind address and port say nothing about
#: where the access log goes.
_BASE = ["daphne"]
_APP = ["web_annotation.asgi:application"]


@pytest.mark.parametrize(("flags", "destination"), [
    # The command line this image shipped before gain#795: no
    # --access-log and no -v, which is exactly what put the codes on
    # stdout.  Verbosity defaults to 1, and daphne enables the log at 1.
    pytest.param([], STDOUT, id="defaults"),
    # Raising verbosity does not change where the log goes.
    pytest.param(["-v", "2"], STDOUT, id="verbose"),
    # Turning the log off through verbosity keeps nothing -- it is
    # rejected by the *other* test, for taking the root logger with it.
    pytest.param(["-v", "0"], None, id="quiet"),
    # The fix.
    pytest.param(["--access-log", "/dev/null"], "/dev/null", id="discard"),
    # "-" is daphne's spelling of stdout.  An explicit --access-log wins
    # over verbosity in both directions, so this re-opens the defect
    # even at verbosity 0 ...
    pytest.param(["-v", "0", "--access-log", "-"], STDOUT, id="quiet-stdout"),
    # ... and this keeps it off without touching verbosity.
    pytest.param(
        ["-v", "0", "--access-log", "/dev/null"], "/dev/null",
        id="quiet-discard"),
])
def test_where_a_daphne_command_line_sends_its_access_log(
    flags: list[str], destination: str | None,
) -> None:
    """The rule the guard applies, stated on its own.

    Table-driven rather than by shipping variant Dockerfiles: the guard's
    job is to read one command line, this table's job is to say what each
    command line means, and a bug in the meaning is invisible in a
    single passing assertion about the file we ship.
    """
    assert _access_log_destination([*_BASE, *flags, *_APP]) == destination


@pytest.mark.parametrize(("destination", "discarding"), [
    (STDOUT, False),
    ("/dev/null", True),
    (None, True),
    # Out of the docker log is not out of reach: a file in the running
    # container keeps the codes on disk just the same.
    ("/var/log/daphne-access.log", False),
    # The container log by another name.
    ("/dev/stdout", False),
    ("/dev/stderr", False),
])
def test_which_destinations_keep_nothing(
    destination: str | None, *, discarding: bool,
) -> None:
    """Which destinations the guard is willing to accept."""
    assert (destination in DISCARDING) is discarding


@pytest.fixture
def production_argv() -> list[str]:
    """The command line the deployed backend container actually runs."""
    return _entrypoint_argv(PRODUCTION_DOCKERFILE.read_text(encoding="utf-8"))


def test_the_guard_can_see_the_file_it_polices() -> None:
    """The tests below must not pass by reading something else.

    They resolve the Dockerfile from this module's location, so a moved
    tests directory would take them with it -- and an assertion about a
    file that is not there is no assertion at all.
    """
    assert PRODUCTION_DOCKERFILE.is_file(), (
        f"{PRODUCTION_DOCKERFILE} is not there: WEB_API_SRC no longer "
        f"resolves to the web_api project root, so nothing below is "
        f"policing the shipped entrypoint"
    )


def test_the_backend_keeps_no_access_log_of_the_codes_it_redeems(
    production_argv: list[str],
) -> None:
    """No request target the backend serves is written to a kept sink.

    The reset and confirmation codes travel in the query string, and the
    access log records the target whole, so a retained access log is a
    retained credential (gain#795).
    """
    destination = _access_log_destination(production_argv)

    assert destination in DISCARDING, (
        f"the production entrypoint writes daphne's access log to "
        f"{destination!r}, which is kept. Every redemption of a reset or "
        f"confirmation code logs the live code with it, because the "
        f"access log records the query string (gain#795). Command line: "
        f"{shlex.join(production_argv)}"
    )


def test_the_backend_still_logs_at_info(
    production_argv: list[str],
) -> None:
    """Silencing the access log did not silence the application."""
    level = _root_log_level(production_argv)

    assert level <= logging.INFO, (
        f"the production entrypoint starts the root logger at "
        f"{logging.getLevelName(level)}, so the container's INFO lines "
        f"are gone. Keep daphne's access log off with --access-log "
        f"rather than by lowering --verbosity (gain#795). Command line: "
        f"{shlex.join(production_argv)}"
    )
