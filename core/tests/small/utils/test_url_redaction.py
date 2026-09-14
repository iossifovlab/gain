# pylint: disable=W0621,C0114,C0116
"""Url userinfo is redacted at the log-record seam (gain#1363).

The seam is process-global: every ``LogRecord`` created in the process,
by any logger, renders its message with ``scheme://user:pass@host``
reduced to ``scheme://host``.  These tests observe it the only way a
reader of the log does -- a logger, a stdlib ``Formatter`` -- and never
reach for the mechanism.
"""
from __future__ import annotations

import importlib
import logging
import subprocess
import sys
from collections.abc import Callable, Iterator
from typing import Any

import gain
import gain.utils.url_redaction
import pytest
from gain.utils.url_redaction import redact_url_userinfo_in_log_records

CREDENTIALED_URL = "https://alice:s3cret@grr.example.org/repo/f.gz"
REDACTED_URL = "https://grr.example.org/repo/f.gz"


class _FormattedLines(logging.Handler):
    """Collect what a reader of the log would see: the formatted lines."""

    def __init__(self) -> None:
        super().__init__()
        self.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


IsolatedLogger = Callable[
    [str | None], tuple[logging.Logger, _FormattedLines]]


@pytest.fixture
def isolated_logger() -> Iterator[IsolatedLogger]:
    """A logger whose records reach exactly one handler, ours.

    Loggers are process-global -- the root logger doubly so, since pytest
    hangs its own capture off it -- so every property touched here is put
    back at teardown.
    """
    isolated: list[logging.Logger] = []
    saved: list[tuple[list[logging.Handler], bool, int]] = []

    def isolate(name: str | None) -> tuple[logging.Logger, _FormattedLines]:
        logger = logging.getLogger(name)
        isolated.append(logger)
        saved.append((list(logger.handlers), logger.propagate, logger.level))
        logger.handlers.clear()
        logger.propagate = False
        logger.setLevel(logging.DEBUG)
        lines = _FormattedLines()
        logger.addHandler(lines)
        return logger, lines

    yield isolate

    for logger, (handlers, propagate, level) in zip(
            isolated, saved, strict=True):
        logger.handlers[:] = handlers
        logger.propagate = propagate
        logger.setLevel(level)


#: The three ways a url reaches a log call in this tree: as a ``%s`` arg,
#: baked into the ``msg`` itself, or interpolated by the caller before the
#: call -- which is what fsspec's ``logger.debug(url)`` and
#: ``logger.debug(f"{self.url} : ...")`` do.
@pytest.mark.parametrize("emit", [
    pytest.param(
        lambda logger: logger.error("cannot open %s", CREDENTIALED_URL),
        id="percent-arg"),
    # The lint that forbids these two spellings is the point: gain never
    # writes them, fsspec does, and the seam has to catch what the lint
    # cannot reach.
    pytest.param(
        lambda logger: logger.error(
            "cannot open " + CREDENTIALED_URL),  # ruff: ignore[logging-string-concat]
        id="in-msg"),
    pytest.param(
        lambda logger: logger.error(
            f"cannot open {CREDENTIALED_URL}"),  # ruff: ignore[logging-f-string]
        id="f-string"),
])
#: Loggers gain does not own are the point of a seam over a fence: fsspec's
#: http logger writes the fetch url at DEBUG on every read, and the root
#: logger is where a host with no logger of its own lands.
@pytest.mark.parametrize("logger_name", [
    "gain.tests.url_redaction",
    "fsspec.implementations.http",
    None,
])
def test_userinfo_is_stripped_however_and_wherever_the_url_arrives(
    isolated_logger: IsolatedLogger,
    emit: Callable[[logging.Logger], None],
    logger_name: str | None,
) -> None:
    logger, lines = isolated_logger(logger_name)

    emit(logger)

    assert lines.lines == [f"ERROR cannot open {REDACTED_URL}"]


#: Messages with no userinfo, each carrying one of the characters the
#: redactor keys on, so that the seam's promise -- invisible to every
#: deployment with nothing to protect -- is pinned per character rather
#: than on one bland string.
@pytest.mark.parametrize("message", [
    pytest.param("cannot open https://grr.example.org/repo/f.gz", id="url"),
    pytest.param("notify alice@example.org", id="at-sign-no-scheme"),
    pytest.param("clone ssh://github.com/iossifovlab/gain", id="no-at"),
    pytest.param(
        "fetched https://grr.example.org/f.gz?version=3&format=tsv",
        id="query-string-kept"),
    pytest.param("100% of 3 shards @ 2 MB/s", id="percent-and-at"),
])
def test_a_message_without_userinfo_renders_exactly_as_stdlib_would(
    isolated_logger: IsolatedLogger, message: str,
) -> None:
    logger, lines = isolated_logger("gain.tests.url_redaction.clean")

    logger.warning(message)

    assert lines.lines == [f"WARNING {message}"]


#: The two presigned spellings botocore emits (ADR 0023, gain#1339). Both
#: are bearer credentials, and both pass this seam untouched: the query
#: string is NOT a credential in its vocabulary. Decided at triage --
#: stripping it here would strip the query of every url in every host log
#: line -- and pinned so the decision cannot drift into the union redactor.
@pytest.mark.parametrize("presigned", [
    pytest.param(
        "https://minio.local/bucket/f.gz"
        "?AWSAccessKeyId=AKIA123&Signature=abc%3D&Expires=1700000000",
        id="sigv2"),
    pytest.param(
        "https://minio.local/bucket/f.gz"
        "?X-Amz-Algorithm=AWS4-HMAC-SHA256"
        "&X-Amz-Credential=AKIA123%2F20260914%2Fus-east-1%2Fs3%2Faws4_request"
        "&X-Amz-Signature=deadbeef",
        id="sigv4"),
])
def test_a_presigned_query_string_passes_through_the_seam(
    isolated_logger: IsolatedLogger, presigned: str,
) -> None:
    logger, lines = isolated_logger("gain.tests.url_redaction.presigned")

    logger.error("cannot open %s", presigned)

    assert lines.lines == [f"ERROR cannot open {presigned}"]


class _CountsRenderings:
    """A log argument that knows whether anything ever stringified it."""

    def __init__(self) -> None:
        self.renderings = 0

    def __str__(self) -> str:
        self.renderings += 1
        return CREDENTIALED_URL


def test_a_record_no_handler_formats_is_never_interpolated(
    isolated_logger: IsolatedLogger,
) -> None:
    """The seam is lazy: it costs nothing for a record nobody reads.

    stdlib interpolates ``args`` only when a handler formats the record.
    A seam that redacted at *emission* would have to interpolate first,
    which is the eager cost the issue names -- so the proof is a record
    the logger accepts and the handler then drops on level.
    """
    logger, lines = isolated_logger("gain.tests.url_redaction.lazy")
    lines.setLevel(logging.CRITICAL)
    url = _CountsRenderings()

    logger.error("cannot open %s", url)

    assert (lines.lines, url.renderings) == ([], 0)


@pytest.fixture
def host_record_factory() -> Iterator[None]:
    """A host that tags every record, the way the stdlib docs show.

    Installed the idiomatic way -- wrapping whatever factory was there --
    and put back afterwards, because the factory slot is process-global.
    """
    previous = logging.getLogRecordFactory()

    def tagging(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        record.tenant = "acme"
        return record

    logging.setLogRecordFactory(tagging)
    yield
    logging.setLogRecordFactory(previous)


def test_a_host_record_factory_keeps_its_attributes_and_is_redacted(
    isolated_logger: IsolatedLogger,
    host_record_factory: None,
) -> None:
    logger, lines = isolated_logger("gain.tests.url_redaction.host")
    lines.setFormatter(logging.Formatter("%(tenant)s %(message)s"))

    logger.error("cannot open %s", CREDENTIALED_URL)

    assert lines.lines == [f"acme cannot open {REDACTED_URL}"]


#: A shape the userinfo redactor is NOT idempotent on: one pass strips
#: ``a@`` and leaves ``https://b@host/``, a second pass strips ``b@`` too.
#: That makes the number of redaction layers observable, which a
#: well-formed url cannot show.
DOUBLE_AT_URL = "https://a@b@host/f.gz"
ONCE_REDACTED_DOUBLE_AT_URL = "https://b@host/f.gz"


def _reload_the_bootstrap() -> None:
    importlib.reload(gain.utils.url_redaction)
    importlib.reload(gain)


#: The two ways the install runs again in one process: called outright, and
#: re-executed by a module reload, which also resets any module-level flag
#: an implementation might have kept its "already installed" state in.
@pytest.mark.parametrize("reinstall", [
    pytest.param(redact_url_userinfo_in_log_records, id="called-again"),
    pytest.param(_reload_the_bootstrap, id="module-reloaded"),
])
def test_installing_the_seam_again_does_not_stack_a_second_layer(
    isolated_logger: IsolatedLogger, reinstall: Callable[[], None],
) -> None:
    logger, lines = isolated_logger("gain.tests.url_redaction.again")
    installed_by_importing_gain = logging.LogRecord.getMessage

    reinstall()
    logger.error("cannot open %s", DOUBLE_AT_URL)

    # The identity half says the bootstrap had already installed it; the
    # rendered half says a second install would have been visible.
    assert (
        logging.LogRecord.getMessage is installed_by_importing_gain,
        lines.lines,
    ) == (True, [f"ERROR cannot open {ONCE_REDACTED_DOUBLE_AT_URL}"])


#: A host in miniature: import gain, apply a Django-shaped ``LOGGING``
#: (non-incremental, ``disable_existing_loggers: False``, its own handler
#: and formatter on the root), then log a credentialed url through a logger
#: of its own.  Run in a fresh interpreter because a non-incremental
#: ``dictConfig`` shuts down every handler in the process -- pytest's
#: included -- which is not state a test may leak into the suite.
HOST_APPLYING_DICTCONFIG = f"""
import logging, logging.config
import gain
logging.config.dictConfig({{
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {{"plain": {{"format": "%(levelname)s %(message)s"}}}},
    "handlers": {{"console": {{
        "class": "logging.StreamHandler", "formatter": "plain",
        "stream": "ext://sys.stdout"}}}},
    "loggers": {{"fsspec": {{"level": "WARNING"}}}},
    "root": {{"handlers": ["console"], "level": "INFO"}},
}})
logging.getLogger("host.web").error("cannot open %s", {CREDENTIALED_URL!r})
"""


def test_the_seam_survives_a_host_applying_dictconfig_after_import(
) -> None:
    """web_api's and gpf's Django ``LOGGING`` land as ``dictConfig`` long
    after ``gain`` was imported; a seam that configuration could undo
    would be one every deployment silently lacks."""
    host = subprocess.run(
        [sys.executable, "-c", HOST_APPLYING_DICTCONFIG],
        capture_output=True, text=True, check=True, timeout=60,
    )

    assert host.stdout == f"ERROR cannot open {REDACTED_URL}\n"


def _open_forgetting_to_redact() -> None:
    """The seventeenth site: a gain-composed refusal with the url raw."""
    raise OSError(f"cannot open {CREDENTIALED_URL}")


def test_the_exc_info_traceback_tail_is_the_documented_residual(
    isolated_logger: IsolatedLogger,
) -> None:
    """What this seam does NOT reach, pinned rather than left to inference.

    ``Formatter.formatException`` renders the traceback from ``exc_info``,
    not from the record's message, and its last line is ``str(exc)``.  A
    gain-composed ``raise`` that forgot its redaction and is then logged
    with ``exc_info=True`` still shows the credential there -- which is
    why the #1318 fence keeps policing ``raise`` (ADR 0023, gain#1363).
    """
    logger, lines = isolated_logger("gain.tests.url_redaction.exc-info")
    try:
        _open_forgetting_to_redact()
    except OSError:
        logger.exception("annotating %s failed", CREDENTIALED_URL)

    [line] = lines.lines
    message_line, _, traceback_text = line.partition("\n")

    assert (message_line, CREDENTIALED_URL in traceback_text) == (
        f"ERROR annotating {REDACTED_URL} failed", True)
