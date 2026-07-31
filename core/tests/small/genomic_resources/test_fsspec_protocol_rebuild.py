# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Rebuilding a memoized fsspec protocol must not reconfigure it (#514).

``FsspecReadOnlyProtocol.__new__`` memoizes one instance per
``(proto_id, url)`` for the life of the process, and Python still runs
``__init__`` on the instance it hands back.  So a second build over a key that
already has a protocol used to be a silent reconfiguration of the object every
existing holder is using: it rebound the mode, the public url, the credential
kwargs, the resource memo and -- worst -- the ``Lock`` that guards that memo.

Two consequences, both covered here:

- the *caller* gets a protocol it did not ask for.  The memo is keyed on the id
  and the url alone, and ``FsspecReadWriteProtocol`` subclasses the read-only
  one, so a read-only build over a read-write key was answered with a writable
  protocol, and a read-write build over a read-only key was answered with an
  instance whose ``__init__`` Python skipped entirely (it is not an instance of
  the requested class) -- stale filesystem, stale kwargs and no write methods.
- the *incumbent* loses its lock.  Swapping ``_all_resources_lock`` while a
  reader is inside the guard leaves two threads holding two different locks for
  one memo, which makes the mutual exclusion #458 established decorative.

A rebuild whose configuration matches is still a memo refresh -- that is the
established contract of building a repository twice (``grr_manage`` re-reads a
changed ``.CONTENTS`` that way) -- so what changes is only that the refresh now
happens under the incumbent's own lock.
"""
import inspect
import pathlib
import pickle  # noqa: S403
import re
import threading
from typing import Any

import pytest
from gain.genomic_resources import fsspec_protocol
from gain.genomic_resources.fsspec_protocol import (
    _FILESYSTEM_KWARGS,
    _build_filesystem,
    build_fsspec_protocol,
)
from gain.genomic_resources.repository import GR_CONF_FILE_NAME, Mode
from gain.genomic_resources.testing import setup_directories

#: Every way a keyword can be read out of a ``**kwargs`` dict: ``.get`` with or
#: without a default, ``.pop``, or a subscript. The lookbehind keeps it to the
#: bare ``kwargs`` name -- without it the pattern also matches the tail of
#: ``client_kwargs["auth"]``, which is a *write* to a different dict.
_KWARGS_READ_RE = (
    r"""(?<![\w.])kwargs(?:\.(?:get|pop)\(|\[)\s*["']([^"']+)["']"""
)


def test_a_read_only_rebuild_of_a_read_write_protocol_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    """A read-only request must not be answered with a writable protocol.

    The read-write protocol is a subclass of the read-only one, so the memo
    hit satisfied ``isinstance`` and Python re-ran the *read-write*
    ``__init__``: the caller asked for a protocol that cannot write and got
    one that can, with no error anywhere.
    """
    url = f"file://{tmp_path}/grr"
    read_write = build_fsspec_protocol("rebuild", url)
    assert read_write.mode() == Mode.READWRITE

    with pytest.raises(ValueError) as excinfo:
        build_fsspec_protocol("rebuild", url, read_only=True)

    message = str(excinfo.value)
    assert "rebuild" in message
    assert "READONLY" in message


def test_a_read_write_rebuild_of_a_read_only_protocol_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    """The other order, and the worse of the two failure modes.

    A read-only instance is not an instance of the read-write class, so Python
    skipped ``__init__`` altogether: the caller got the incumbent read-only
    protocol -- built against a different filesystem object, and missing every
    write method it was about to call.
    """
    url = f"file://{tmp_path}/grr"
    read_only = build_fsspec_protocol("rebuild", url, read_only=True)
    assert read_only.mode() == Mode.READONLY

    with pytest.raises(ValueError) as excinfo:
        build_fsspec_protocol("rebuild", url)

    message = str(excinfo.value)
    assert "rebuild" in message
    assert "READWRITE" in message


def test_a_rebuild_with_a_different_public_url_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    """A rebuild must not repoint the public url under its holders.

    ``public_url`` is the identity a repository is published under -- it is
    serialized into web responses and persisted docs. A second build used to
    rebind it on the live instance, so a holder that had already read
    ``get_public_url()`` and one reading it afterwards disagreed about the same
    protocol object.
    """
    url = f"file://{tmp_path}/grr"
    first = build_fsspec_protocol("rebuild", url)
    assert first.get_public_url() == url

    with pytest.raises(ValueError) as excinfo:
        build_fsspec_protocol(
            "rebuild", url, public_url="https://public.example.com")

    assert "public url" in str(excinfo.value)
    assert first.get_public_url() == url


def test_a_public_url_that_differs_only_in_spelling_is_honoured(
    tmp_path: pathlib.Path,
) -> None:
    """Two spellings of one location are not two public urls.

    The incumbent's ``public_url`` is whatever a caller passed, while a
    rebuild that passes none defaults to the url's own display form -- so a
    trailing slash, or a bare path against its ``file://`` spelling, made a
    rebuild that asks for exactly the incumbent's url look like a request to
    repoint it somewhere else.
    """
    root = f"{tmp_path}/grr"
    first = build_fsspec_protocol(
        "rebuild", f"file://{root}/", public_url=f"file://{root}")

    second = build_fsspec_protocol("rebuild", f"file://{root}/")

    assert second is first


def test_a_rebuild_with_different_credentials_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    """Different credentials for one id and url cannot share a protocol.

    The memo key keeps the url's own ``user:pass@`` userinfo precisely so a
    second build with other credentials does not authenticate with the first
    one's. Credentials passed as *keywords* were never covered by that: they
    landed in ``self.kwargs``, which a rebuild rebound -- so the incumbent's
    next filesystem rebuild would authenticate as whoever built it last.
    """
    url = f"file://{tmp_path}/grr"
    build_fsspec_protocol("rebuild", url, user="alice", password="s3cret")

    with pytest.raises(ValueError) as excinfo:
        build_fsspec_protocol("rebuild", url, user="bob", password="hunter2")

    message = str(excinfo.value)
    assert "rebuild" in message
    # The refusal names the keywords that disagree and none of their values:
    # this is the one new error message on a path whose whole subject is
    # credentials.
    assert "user" in message
    assert "password" in message
    assert "s3cret" not in message
    assert "hunter2" not in message
    assert "alice" not in message
    assert "bob" not in message


def test_a_rebuild_whose_keywords_differ_only_by_omission_is_honoured(
    tmp_path: pathlib.Path,
) -> None:
    """An omitted keyword and an explicit ``None`` configure the same protocol.

    ``_build_filesystem`` reads every keyword with ``.get``, so
    ``user=None`` and no ``user`` at all build the identical filesystem. The
    repository factory reaches the same url both ways -- a ``url``-type
    definition passes neither credential keyword, an ``http``-type one passes
    both as ``None`` -- and refusing that pair would break a rebuild that asks
    for exactly what already exists.
    """
    url = f"file://{tmp_path}/grr"
    first = build_fsspec_protocol("rebuild", url)

    second = build_fsspec_protocol("rebuild", url, user=None, password=None)

    assert second is first


def test_a_rebuild_differing_only_in_a_non_filesystem_keyword_is_honoured(
    tmp_path: pathlib.Path,
) -> None:
    """A keyword the protocol never reads cannot make two builds disagree.

    The repository factory hands the protocol builder the whole definition,
    ``cache_dir`` included -- and that keyword configures the cache wrapped
    *around* the protocol, not the protocol. Two definitions over one
    directory that cache in different places still name one protocol.
    """
    url = f"file://{tmp_path}/grr"
    first = build_fsspec_protocol("rebuild", url)

    second = build_fsspec_protocol(
        "rebuild", url, cache_dir=str(tmp_path / "cache"))

    assert second is first


def test_a_build_racing_the_very_first_one_waits_and_is_not_refused(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A racing build waits for the first one and shares its protocol.

    ``__new__`` used to publish an instance into the memo *before*
    ``__init__`` configured it, so a thread racing the very first build of a
    key found an incumbent with no ``public_url`` and no ``kwargs`` yet. The
    refusal had to *tolerate* that rather than discover it (#514) -- reading
    those attributes anyway turned a race that merely ran ``__init__`` twice
    into an ``AttributeError`` raised out of ``__new__``.

    #527 closed the window instead: nothing is published until the whole
    construction has returned, so what a racing build meets is an in-flight
    construction and it waits for it. Still not refused, and now three
    stronger things hold -- it blocks rather than seeing a half-built object,
    both callers end up with the SAME instance, and only ONE instance is ever
    constructed for the key.

    The stall substitutes a module function called from inside ``__init__``,
    because no public seam of the protocol runs in that window; a stress test
    could only make it likely, and a test that is merely likely to fail is no
    guard at all.
    """
    url = f"file://{tmp_path}/grr"
    inside_init = threading.Event()
    release = threading.Event()
    original_display_url = fsspec_protocol._display_url
    stalled = threading.Lock()
    has_stalled = False

    def stalling_display_url(target: str) -> str:
        nonlocal has_stalled
        with stalled:
            first = not has_stalled
            has_stalled = True
        if first:
            inside_init.set()
            assert release.wait(10.0), "the stalled build was never released"
        return original_display_url(target)

    monkeypatch.setattr(
        fsspec_protocol, "_display_url", stalling_display_url)

    # Every instance ``__init__`` runs on, in order, so the test can tell one
    # construction from the rebuild-refresh that follows it.
    initialised: list[int] = []
    original_init = fsspec_protocol.FsspecReadWriteProtocol.__init__

    def counting_init(self: Any, *args: Any, **kwargs: Any) -> None:
        with stalled:
            initialised.append(id(self))
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(
        fsspec_protocol.FsspecReadWriteProtocol, "__init__", counting_init)

    built: dict[str, Any] = {}
    errors: list[BaseException] = []
    second_done = threading.Event()

    def build(name: str) -> None:
        # pylint: disable=broad-exception-caught
        try:
            protocol = build_fsspec_protocol("rebuild", url)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        else:
            built[name] = protocol

    def build_second() -> None:
        build("second")
        second_done.set()

    # Daemon threads: a build left waiting by a lock-ordering regression must
    # not keep the interpreter alive after this test has already failed.
    first = threading.Thread(
        target=build, args=("first",), daemon=True)
    second = threading.Thread(target=build_second, daemon=True)

    first.start()
    assert inside_init.wait(10.0), "the first build never reached __init__"
    second.start()
    assert not second_done.wait(0.2), \
        "a racing build was answered before the first one was configured"

    release.set()
    for thread in (first, second):
        thread.join(timeout=10.0)
        assert not thread.is_alive(), "a build never finished -- deadlock?"

    assert not errors
    assert built["first"] is built["second"]
    assert built["second"].get_public_url() == url
    # ONE instance was constructed for the key. The second ``__init__`` is the
    # racing build's own, re-run on the instance it was handed -- the rebuild
    # -as-refresh contract of ADR 0005, which closing this window must not
    # cost. Pinned as a count so that an "already initialised" no-op, the fix
    # that ADR rejected, cannot creep back in unnoticed.
    assert len(set(initialised)) == 1
    assert len(initialised) == 2


def test_a_matching_rebuild_keeps_the_lock_that_guards_the_memo(
    tmp_path: pathlib.Path,
) -> None:
    """The memo lock must outlive a rebuild of its protocol.

    ``__init__`` re-runs on the memoized instance and used to rebind
    ``_all_resources_lock`` to a brand new ``Lock``. A reader already inside
    the guard holds the old object, so it and the rebuild then serialise on
    two different locks -- and the mutual exclusion #458 established over this
    memo becomes decorative.
    """
    url = f"file://{tmp_path}/grr"
    proto = build_fsspec_protocol("rebuild", url)
    lock = proto._all_resources_lock

    rebuilt = build_fsspec_protocol("rebuild", url)

    assert rebuilt is proto
    assert rebuilt._all_resources_lock is lock


def test_a_rebuild_waits_for_the_memo_lock(tmp_path: pathlib.Path) -> None:
    """Keeping the lock is only half of it -- the rebuild must take it.

    A rebuild clears the resource memo, which is exactly what ``invalidate``
    does and exactly what an unsynchronised ``invalidate`` was not allowed to
    do in the middle of a populating read (#458). Reaching the same clear
    through ``__init__`` has to obey the same rule (#514).
    """
    url = f"file://{tmp_path}/grr"
    proto = build_fsspec_protocol("rebuild", url)
    lock = proto._all_resources_lock
    proto.get_all_resources_dict()
    assert proto._all_resources is not None

    running = threading.Event()
    rebuilt = threading.Event()

    def rebuild() -> None:
        running.set()
        build_fsspec_protocol("rebuild", url)
        rebuilt.set()

    # Daemon: a rebuild left blocked by a lock-ordering regression must not
    # keep the interpreter alive after this test has already failed.
    thread = threading.Thread(target=rebuild, daemon=True)
    with lock:
        thread.start()
        # Wait for the thread to really be running before timing it, so a
        # loaded machine cannot turn this into a silent pass.
        assert running.wait(10.0), "the rebuilding thread never started"
        assert not rebuilt.wait(0.2), \
            "a rebuild cleared the memo while the memo lock was held"
        assert proto._all_resources is not None
        assert proto._all_resources_lock is lock

    thread.join(timeout=10.0)
    assert not thread.is_alive()
    assert rebuilt.is_set()
    # And once the reader is out, the rebuild's refresh did land.
    assert proto._all_resources is None


def test_unpickling_a_memoized_protocol_keeps_its_memo_lock(
    tmp_path: pathlib.Path,
) -> None:
    """Unpickling is the second way into a live protocol, with the same bug.

    ``__getnewargs_ex__`` sends the protocol back through ``__new__``, so in a
    process that already has this ``(proto_id, url)`` -- a dask worker handed
    two resources of one repository, or any round trip inside one process --
    the state lands on the live instance. ``__setstate__`` rebound the lock
    there just as ``__init__`` did (#514).
    """
    url = f"file://{tmp_path}/grr"
    proto = build_fsspec_protocol("rebuild", url)
    lock = proto._all_resources_lock

    restored = pickle.loads(pickle.dumps(proto))

    assert restored is proto
    assert restored._all_resources_lock is lock


def test_a_scheme_less_url_names_the_same_protocol_as_its_file_form(
    tmp_path: pathlib.Path,
) -> None:
    """One repository, one protocol, however its url is spelled.

    ``build_local_resource``, ``grr_manage``'s ``_create_proto`` and the
    filesystem test builder all pass a bare absolute path, while ``__init__``
    derives a ``file://`` url from it -- so the memo held two entries for one
    directory, and the two could disagree about mode or credentials.
    """
    first = build_fsspec_protocol("rebuild", str(tmp_path / "grr"))

    second = build_fsspec_protocol("rebuild", f"file://{tmp_path}/grr")

    assert second is first


def test_unpickling_a_protocol_built_from_a_bare_path_hits_the_memo(
    tmp_path: pathlib.Path,
) -> None:
    """The pickle round trip has to land on the instance it came from.

    ``__getnewargs_ex__`` sends ``_fetch_url`` -- always ``file://``-spelled --
    back through ``__new__``, so for the builders that pass a bare path the
    round trip used to miss the memo and mint a second protocol over one
    directory. Two protocols for one url is the state whose halves can
    disagree, and unpickling was the one path that created it silently.
    """
    proto = build_fsspec_protocol("rebuild", str(tmp_path / "grr"))

    restored = pickle.loads(pickle.dumps(proto))

    assert restored is proto


def test_a_matching_rebuild_still_refreshes_the_resource_memo(
    tmp_path: pathlib.Path,
) -> None:
    """The refresh a rebuild performs is the contract, and it is kept.

    Building a repository twice is how ``grr_manage`` sees a repository it has
    just changed: the second build drops the resource memo, so the next read
    re-scans. Fixing the lock must not cost that -- an ``__init__`` guarded by
    an "already initialised" flag would have.
    """
    root_path = tmp_path / "grr"
    setup_directories(root_path, {"one": {GR_CONF_FILE_NAME: "type: basic\n"}})
    url = f"file://{root_path}"
    proto = build_fsspec_protocol("rebuild", url)
    assert sorted(proto.get_all_resources_dict()) == ["one"]

    setup_directories(root_path, {"two": {GR_CONF_FILE_NAME: "type: basic\n"}})
    rebuilt = build_fsspec_protocol("rebuild", url)

    assert sorted(rebuilt.get_all_resources_dict()) == ["one", "two"]


def test_every_filesystem_keyword_is_compared_on_a_rebuild() -> None:
    """A keyword the filesystem reads must be one a rebuild cannot change.

    The refusal compares a fixed set of keywords, so a keyword
    ``_build_filesystem`` learns to read without joining that set is one a
    rebuild would go on silently reconfiguring -- and these are the keywords
    that decide who the protocol authenticates as and which endpoint it talks
    to. Reading the source is the only way to pin a set against the function
    it has to mirror; the alternative is discovering the drift as the next
    credential bug.
    """
    source = inspect.getsource(_build_filesystem)
    # ``getsource`` slices the file on disk by the loaded code object's line
    # numbers, so an edit to the module while the suite is running hands back
    # some unrelated stretch of the file -- and the assertion below then fails
    # as an empty set, blaming a drift that is not there. Say so instead.
    assert source.lstrip().startswith("def _build_filesystem"), (
        "inspect.getsource did not return _build_filesystem -- was "
        "fsspec_protocol.py edited while the module was loaded?")

    read_by_the_filesystem = set(re.findall(_KWARGS_READ_RE, source))

    assert read_by_the_filesystem == set(_FILESYSTEM_KWARGS)


def test_the_drift_guard_sees_every_way_a_keyword_can_be_read() -> None:
    """The guard above is only as good as the shapes its pattern matches.

    A pattern that only understood ``kwargs.get("x")`` -- no default, no
    subscript, no ``pop`` -- would stay green through exactly the drift it
    exists to catch, so the shapes are pinned against a sample rather than
    trusted.
    """
    sample = """
        token = kwargs.get("token", None)
        secret = kwargs["secret"]
        other = kwargs.pop('other')
        plain = kwargs.get("plain")
        client_kwargs["auth"] = make_auth(user, password)
    """

    # ``client_kwargs["auth"]`` is a write to another dict, not a keyword this
    # function reads -- counting it would refuse every rebuild of an authed
    # http protocol.
    assert set(re.findall(_KWARGS_READ_RE, sample)) == {
        "token", "secret", "other", "plain"}
