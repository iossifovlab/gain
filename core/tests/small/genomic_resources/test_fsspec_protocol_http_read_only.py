# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""An http(s) protocol answers a ``read_only`` request instead of dropping it.

``build_fsspec_protocol`` dispatches on the url scheme.  On the local, s3 and
memory schemes ``read_only`` selects the class; on ``http``/``https`` it used
to be popped and then never consulted, so a caller asking for a writable http
protocol was handed a read-only one with no diagnostic and found out through
an absent write method somewhere downstream (#528).

A read-write http protocol is not implementable -- there is nothing to create
and ``obtain_resource_file_lock`` raises ``NotImplementedError`` (#473) -- so
the object that was returned is the only correct one.  What was wrong is that
the request was discarded rather than answered, which is the same
silent-wrong-mode shape #514 fixed for the memo rebuild.  Asking for the
impossible is now refused where it is asked for.

The distinction the refusal rests on is *explicit* ``read_only=False`` versus
an omitted ``read_only``: every http caller in the codebase omits it, and
omitting it must stay silent.
"""
import pathlib

import pytest
from gain.genomic_resources.fsspec_protocol import (
    build_fsspec_protocol,
    build_inmemory_protocol,
)
from gain.genomic_resources.repository import Mode


def test_an_explicitly_read_write_http_protocol_is_refused() -> None:
    """``read_only=False`` on an http url is a request that cannot be served.

    It used to be discarded: the caller got a read-only protocol back and no
    indication that the mode it asked for was not the mode it received.
    """
    with pytest.raises(ValueError, match="read-only"):
        build_fsspec_protocol(
            "http-rw-refused", "https://grr.example.com", read_only=False)


def test_an_http_protocol_built_without_read_only_is_silently_read_only(
) -> None:
    """Omitting ``read_only`` is not a request, so it is not refused.

    This is what every http caller in the codebase does, and what the default
    GRR definition produces.  It must stay silent -- the refusal above is for
    a caller that asked for something else, not for the common case.
    """
    proto = build_fsspec_protocol(
        "http-default-ro", "https://grr.example.com")

    assert proto.mode() == Mode.READONLY


def test_an_explicitly_read_only_http_protocol_is_honoured() -> None:
    """``read_only=True`` asks for what the scheme can serve, so it is served.

    Only the *unserviceable* request is refused; the refusal is about the
    mismatch, not about passing the keyword.
    """
    proto = build_fsspec_protocol(
        "http-explicit-ro", "https://grr.example.com", read_only=True)

    assert proto.mode() == Mode.READONLY


def test_the_refusal_names_the_protocol_and_strips_the_credential() -> None:
    """A refusal is a diagnostic, so it must be readable and secret-free.

    The url can embed ``user:pass@host`` userinfo, and this message is raised
    at the caller and lands in tracebacks and logs -- the same reason every
    other message in this module reports a stripped url.
    """
    with pytest.raises(ValueError) as excinfo:
        build_fsspec_protocol(
            "http-credentialed",
            "https://grr-user:s3cr3t@grr.example.com",
            read_only=False)

    message = str(excinfo.value)
    assert "http-credentialed" in message
    assert "s3cr3t" not in message
    assert "grr.example.com" in message


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        pytest.param({}, Mode.READWRITE, id="omitted"),
        pytest.param(
            {"read_only": False}, Mode.READWRITE, id="explicitly-read-write"),
        pytest.param(
            {"read_only": True}, Mode.READONLY, id="explicitly-read-only"),
    ],
)
def test_a_local_protocol_reads_read_only_exactly_as_before(
    tmp_path: pathlib.Path,
    kwargs: dict[str, bool],
    expected: Mode,
) -> None:
    """The schemes that *can* serve both modes are untouched by the refusal.

    ``read_only`` defaults to absent rather than ``False`` so the http branch
    can tell the two apart, and absent has to keep meaning read-write here --
    which is what a caller that passes nothing has always got.
    """
    proto = build_fsspec_protocol(
        f"local-{expected.name}-{len(kwargs)}",
        f"file://{tmp_path}", **kwargs)

    assert proto.mode() == expected


def test_the_inmemory_builder_never_returns_a_read_only_protocol(
    tmp_path: pathlib.Path,
) -> None:
    """The mirror-image half of #528, on the memory scheme.

    ``build_inmemory_protocol`` promises a read-write protocol in its return
    type and used to make that true by ``cast`` alone -- an assertion to the
    type checker that nothing checked at runtime.  The memory scheme does
    build read-write, so the promise held; what was missing was anything to
    keep it holding.

    The reachable way to break it is the memo: ids are per-process and never
    evicted, so an id already held by a read-only protocol over the same root
    is the one case where the builder can be handed something other than what
    it asked for.  It must fail, with a message about the conflict, rather
    than return a protocol whose write methods do not exist.
    """
    build_fsspec_protocol(
        "inmemory-collision", f"memory://{tmp_path}", read_only=True)

    with pytest.raises((ValueError, TypeError)) as excinfo:
        build_inmemory_protocol("inmemory-collision", str(tmp_path), {})

    assert "inmemory-collision" in str(excinfo.value)
