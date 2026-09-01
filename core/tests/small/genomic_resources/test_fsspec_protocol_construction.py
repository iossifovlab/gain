# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Building a memoized fsspec protocol is one atomic step (#527).

``FsspecReadOnlyProtocol.__new__`` memoizes one instance per
``(proto_id, canonical url)`` and never evicts, so that pair is supposed to
name ONE protocol for the life of the process.  It used to reach that memo
without any mutual exclusion at all, and two threads building the same **new**
key concurrently broke the guarantee in two different ways:

- ``__new__`` published the fresh instance and returned, and only *then* did
  Python run ``__init__`` on it -- so the memo held a protocol with
  ``filesystem``, ``url``, ``public_url``, ``kwargs`` and ``proto_id`` still
  unbound, and a reader in that window got ``AttributeError``;
- the memo read, the instance creation and the publication were a
  check-then-set, so two threads that both missed both built, the second
  publication overwrote the first, and one caller walked away holding an
  orphan protocol the memo does not know about -- with its own resource memo,
  its own lock, and no share in the refusal that keeps one key from serving
  two configurations.

Every test here steps the race with an ``Event`` rather than repeating it: a
stress loop over this bug class reproduces nothing.  The seams are the module
globals construction calls, because the protocol exposes no public seam inside
the window itself -- ``Lock`` (bound to the fresh instance, inside ``__new__``
and before anything is reachable), ``_display_url`` (inside ``__init__``,
before the filesystem and the kwargs are bound), ``_fetch_url_form`` (the key
derivation, reached ahead of any mutual exclusion, so it marks a second
thread's arrival in both the racing and the serialised code) and
``_build_filesystem`` (the remote work ``__setstate__`` can fail on).

Isolation is by key uniqueness, as everywhere else over this memo: nothing
clears ``_FSSPEC_PROTOCOLS``, so each test picks a ``proto_id`` of its own
over its own ``tmp_path``.
"""
import pathlib
import pickle  # ruff: ignore[suspicious-pickle-import]
import pickletools
import threading
from typing import Any

import pytest
from gain.genomic_resources import fsspec_protocol
from gain.genomic_resources.fsspec_protocol import (
    FsspecReadWriteProtocol,
    build_fsspec_protocol,
)

from .conftest import RunInThreads

#: Every attribute ``__init__`` binds and a caller may read straight away. A
#: protocol reachable through the memo must have all of them.
_CONFIGURED_ATTRIBUTES = (
    "filesystem", "url", "public_url", "kwargs", "proto_id")


class _CallCounter:
    """A thread-safe count of how many times a seam has been reached."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._count = 0

    def take(self) -> int:
        with self._lock:
            self._count += 1
            return self._count


def _stall_the_first_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[threading.Event, threading.Event]:
    """Hold the first thread that creates an instance inside ``__new__``.

    ``Lock`` is bound to the fresh instance as its resource-memo lock, between
    the memo read and the publication -- the one module global construction
    touches inside the window this issue is about.  Returns the event that
    fires when a thread is stalled there, and the event that releases it.
    """
    inside = threading.Event()
    release = threading.Event()
    original_lock = fsspec_protocol.Lock
    counter = _CallCounter()

    def stalling_lock() -> threading.Lock:
        if counter.take() == 1:
            inside.set()
            assert release.wait(10.0), \
                "the stalled construction was never released"
        return original_lock()

    monkeypatch.setattr(fsspec_protocol, "Lock", stalling_lock)
    return inside, release


def _stall_the_first_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[threading.Event, threading.Event]:
    """Hold the first thread that reaches ``__init__``'s configuration.

    ``_display_url`` derives ``self.url``, so a thread stalled there has left
    ``__new__`` behind and has not yet bound the filesystem, the public url or
    the kwargs.  That is the whole of the window this issue is about, and the
    protocol exposes no seam of its own inside it.
    """
    inside = threading.Event()
    release = threading.Event()
    original_display = fsspec_protocol._display_url
    counter = _CallCounter()

    def stalling_display_url(url: str) -> str:
        if counter.take() == 1:
            inside.set()
            assert release.wait(10.0), \
                "the stalled configuration was never released"
        return original_display(url)

    monkeypatch.setattr(
        fsspec_protocol, "_display_url", stalling_display_url)
    return inside, release


def _unbound_attributes(protocol: Any) -> list[str]:
    return [
        name for name in _CONFIGURED_ATTRIBUTES if not hasattr(protocol, name)
    ]


def _signal_the_second_arrival(
    monkeypatch: pytest.MonkeyPatch,
) -> threading.Event:
    """Fire when a second thread reaches the memo key derivation.

    ``_fetch_url_form`` is the first thing ``__new__`` does, ahead of any
    mutual exclusion, so it is a point both the racing code and the serialised
    code reach.  That is what lets one test assert an outcome against both:
    once this has fired, the second thread is committed to the memo read that
    the first thread -- stalled mid-publication -- has not yet answered.
    """
    arrived = threading.Event()
    original_form = fsspec_protocol._fetch_url_form
    counter = _CallCounter()

    def signalling_fetch_url_form(url: str) -> str:
        if counter.take() == 2:
            arrived.set()
        return original_form(url)

    monkeypatch.setattr(
        fsspec_protocol, "_fetch_url_form", signalling_fetch_url_form)
    return arrived


def test_two_threads_that_both_miss_the_memo_build_one_protocol(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One key names one instance, however many threads build it at once.

    ``__new__`` read the memo, constructed on a miss and published, with
    nothing serialising the three.  Two threads that both missed both built,
    and the second publication silently replaced the first: the loser walked
    away with an orphan protocol the memo does not hold, carrying a resource
    memo and a lock of its own, so the mutual exclusion of #458 and the
    rebuild refusal of #514 both stopped covering half the process.
    """
    proto_id = "construction-double-miss"
    url = f"file://{tmp_path}/grr"
    key = (proto_id, url)
    inside, release = _stall_the_first_construction(monkeypatch)
    arrived = _signal_the_second_arrival(monkeypatch)

    built: dict[str, Any] = {}
    errors: list[BaseException] = []
    guard = threading.Lock()

    def build(name: str) -> None:
        # pylint: disable=broad-exception-caught
        try:
            protocol = build_fsspec_protocol(proto_id, url)
        except BaseException as exc:  # ruff: ignore[blind-except]
            with guard:
                errors.append(exc)
        else:
            with guard:
                built[name] = protocol

    # Daemon threads: a construction left waiting by a regression must not
    # keep the interpreter alive after this test has already failed.
    first = threading.Thread(target=build, args=("first",), daemon=True)
    second = threading.Thread(target=build, args=("second",), daemon=True)

    first.start()
    assert inside.wait(10.0), "no build reached the instance creation"
    second.start()
    assert arrived.wait(10.0), "the second build never reached the memo"
    release.set()

    for thread in (first, second):
        thread.join(timeout=10.0)
        assert not thread.is_alive(), "a build never finished -- deadlock?"

    assert not errors
    assert built["first"] is built["second"]
    assert fsspec_protocol._FSSPEC_PROTOCOLS[key] is built["first"]


def test_the_memo_never_holds_a_protocol_that_is_not_configured(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Publication is the promise that an instance is usable.

    ``__new__`` published the fresh instance and returned, and Python only ran
    ``__init__`` on it afterwards -- so between the two the memo handed out a
    protocol with no ``filesystem``, no ``url``, no ``public_url`` and no
    ``kwargs``, and every reader of one got ``AttributeError`` out of a
    method that cannot fail.
    """
    proto_id = "construction-unconfigured"
    url = f"file://{tmp_path}/grr"
    key = (proto_id, url)
    inside, release = _stall_the_first_configuration(monkeypatch)

    errors: list[BaseException] = []

    def build() -> None:
        # pylint: disable=broad-exception-caught
        try:
            build_fsspec_protocol(proto_id, url)
        except BaseException as exc:  # ruff: ignore[blind-except]
            errors.append(exc)

    thread = threading.Thread(target=build, daemon=True)
    thread.start()
    assert inside.wait(10.0), "the build never reached its configuration"

    observed = fsspec_protocol._FSSPEC_PROTOCOLS.get(key)
    # Sampled here and asserted after the thread is released, so a failure
    # cannot leave the stalled builder pinned on a dead event.
    unbound = [] if observed is None else _unbound_attributes(observed)

    release.set()
    thread.join(timeout=10.0)
    assert not thread.is_alive(), "the build never finished -- deadlock?"
    assert not errors

    assert unbound == [], \
        "the memo published a protocol before __init__ configured it"
    assert fsspec_protocol._FSSPEC_PROTOCOLS[key].get_url() == url


def _build_off_the_main_thread(
    run_in_threads: RunInThreads, proto_id: str, url: str,
) -> Any:
    """Build a protocol on a joined-with-timeout thread, not here.

    A key left stranded in flight makes a build block forever, so building it
    on the main thread would hang the suite instead of reporting the
    regression. ``run_in_threads`` already joins with a timeout and asserts
    liveness, so one thread through it says exactly that.
    """
    results, errors = run_in_threads(
        lambda: build_fsspec_protocol(proto_id, url), 1, timeout=10.0)
    assert not errors, f"building {proto_id!r} raised {errors[0]!r}"
    return results[0]


def test_unpickling_into_a_cold_memo_completes_and_registers_the_protocol(
    tmp_path: pathlib.Path,
    run_in_threads: RunInThreads,
) -> None:
    """The deserialize path has to finish its own construction.

    Unpickling runs ``__getnewargs_ex__`` -> ``__new__`` -> ``__setstate__``
    and NEVER calls ``__init__``, so in another process -- a dask worker, the
    case the whole memo-in-``__new__`` arrangement exists for -- ``__init__``
    is not what configures the protocol. A construction that is only ever
    finished by ``__init__`` therefore leaves the key in flight forever: this
    unpickle would hang, and so would every later build of that key.
    """
    proto_id = "construction-cold-unpickle"
    url = f"file://{tmp_path}/grr"
    key = (proto_id, url)
    payload = pickle.dumps(build_fsspec_protocol(proto_id, url))
    # A cold memo, scoped to this key alone -- nothing here clears the memo
    # wholesale, and no other test can name this id.
    del fsspec_protocol._FSSPEC_PROTOCOLS[key]

    restored = pickle.loads(payload)

    assert _unbound_attributes(restored) == []
    assert restored.get_url() == url
    assert fsspec_protocol._FSSPEC_PROTOCOLS[key] is restored
    # And the key is not left in flight: a later build answers from the memo.
    assert _build_off_the_main_thread(
        run_in_threads, proto_id, url) is restored


def test_a_construction_that_raises_leaves_its_key_buildable(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed build must release its key, and everyone waiting on it.

    Serialising construction means a second thread over a new key waits for
    the first. If a construction that raises kept its record, that wait would
    never end and the key would be permanently unbuildable -- a far worse
    outcome than the duplicated ``__init__`` this replaced.
    """
    proto_id = "construction-failing-init"
    url = f"file://{tmp_path}/grr"
    key = (proto_id, url)

    inside = threading.Event()
    release = threading.Event()
    original_display = fsspec_protocol._display_url
    counter = _CallCounter()

    def failing_display_url(target: str) -> str:
        if counter.take() == 1:
            inside.set()
            assert release.wait(10.0), "the failing build was never released"
            raise OSError("the repository url could not be resolved")
        return original_display(target)

    monkeypatch.setattr(
        fsspec_protocol, "_display_url", failing_display_url)

    failure: list[BaseException] = []
    waiter: dict[str, Any] = {}
    waiter_done = threading.Event()

    def build_and_fail() -> None:
        # pylint: disable=broad-exception-caught
        try:
            build_fsspec_protocol(proto_id, url)
        except BaseException as exc:  # ruff: ignore[blind-except]
            failure.append(exc)

    def build_and_wait() -> None:
        # pylint: disable=broad-exception-caught
        try:
            waiter["protocol"] = build_fsspec_protocol(proto_id, url)
        except BaseException as exc:  # ruff: ignore[blind-except]
            waiter["error"] = exc
        waiter_done.set()

    failing = threading.Thread(target=build_and_fail, daemon=True)
    waiting = threading.Thread(target=build_and_wait, daemon=True)

    failing.start()
    assert inside.wait(10.0), "the failing build never reached __init__"
    waiting.start()
    assert not waiter_done.wait(0.2), \
        "a build was answered while the key was still being constructed"

    release.set()
    for thread in (failing, waiting):
        thread.join(timeout=10.0)
        assert not thread.is_alive(), \
            "a build never finished -- a failed construction kept its key?"

    assert isinstance(failure[0], OSError)
    assert "error" not in waiter
    # The waiter took the abandoned construction on and built the protocol.
    assert _unbound_attributes(waiter["protocol"]) == []
    assert fsspec_protocol._FSSPEC_PROTOCOLS[key] is waiter["protocol"]


def test_an_unpickle_that_raises_leaves_its_key_buildable(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    run_in_threads: RunInThreads,
) -> None:
    """``__setstate__`` is a construction too, and can fail like one.

    It rebuilds the filesystem from the pickled keywords, which is remote work
    on every scheme but ``file`` -- so it is the deserialize path's own way of
    failing after ``__new__`` has already taken the key.
    """
    proto_id = "construction-failing-setstate"
    url = f"file://{tmp_path}/grr"
    key = (proto_id, url)
    payload = pickle.dumps(build_fsspec_protocol(proto_id, url))
    del fsspec_protocol._FSSPEC_PROTOCOLS[key]

    original_build_filesystem = fsspec_protocol._build_filesystem
    counter = _CallCounter()

    def failing_build_filesystem(target: str, **kwargs: Any) -> Any:
        if counter.take() == 1:
            raise OSError("the filesystem is unavailable")
        return original_build_filesystem(target, **kwargs)

    monkeypatch.setattr(
        fsspec_protocol, "_build_filesystem", failing_build_filesystem)

    with pytest.raises(OSError, match="filesystem is unavailable"):
        pickle.loads(payload)

    rebuilt = _build_off_the_main_thread(run_in_threads, proto_id, url)

    assert _unbound_attributes(rebuilt) == []
    assert fsspec_protocol._FSSPEC_PROTOCOLS[key] is rebuilt


def _cut_before_the_state_is_applied(payload: bytes) -> bytes:
    """Truncate a pickled protocol between ``__new__`` and ``__setstate__``.

    The last ``BUILD`` in the stream is the one that applies the outermost
    object's state, so everything before it has already created the protocol
    -- through ``__getnewargs_ex__`` -> ``__new__``, which takes the key --
    while the deserialize now runs out of input before ``__setstate__``, the
    only place the deserialize path itself guards. Protocol 2 on purpose: 5
    frames the stream and rejects a short one before executing any opcode, so
    it cannot express this window at all.
    """
    build_positions = [
        pos for opcode, _arg, pos in pickletools.genops(payload)
        if opcode.name == "BUILD"
    ]
    assert build_positions, "the pickled protocol carries no BUILD opcode"
    return payload[:build_positions[-1]]


def test_a_deserialize_that_dies_before_setstate_leaves_its_key_buildable(
    tmp_path: pathlib.Path,
    run_in_threads: RunInThreads,
) -> None:
    """Taking the key is ``__new__``'s; finishing it may never happen.

    A corrupt or truncated frame carrying a protocol fails *between*
    ``__new__`` and ``__setstate__``, so neither of the two guards that
    release a key is reached -- not the metaclass's, which the deserialize
    path does not go through, and not ``__setstate__``'s own, which never
    runs. The record then sits in flight forever and every later build of
    that key blocks in an untimed wait, with no log line: on a dask worker
    -- the case the memo-in-``__new__`` arrangement exists for -- distributed
    reports the task error, the worker survives, and it never touches that
    GRR again.
    """
    proto_id = "construction-truncated-unpickle"
    url = f"file://{tmp_path}/grr"
    key = (proto_id, url)
    payload = pickle.dumps(build_fsspec_protocol(proto_id, url), protocol=2)
    del fsspec_protocol._FSSPEC_PROTOCOLS[key]

    with pytest.raises(EOFError):
        pickle.loads(_cut_before_the_state_is_applied(payload))

    rebuilt = _build_off_the_main_thread(run_in_threads, proto_id, url)

    assert _unbound_attributes(rebuilt) == []
    assert fsspec_protocol._FSSPEC_PROTOCOLS[key] is rebuilt


def test_a_construction_dropped_mid_flight_wakes_the_threads_waiting_on_it(
    tmp_path: pathlib.Path,
) -> None:
    """A key nobody can finish must not hold its waiters forever.

    The deserialize above is one way for a caller to take a key through
    ``__new__`` and then never come back for it; a ``KeyboardInterrupt``
    delivered in the same window is another. Either way the half-built
    instance is dropped, and what tells that apart from a construction still
    running is that nothing holds the instance any more.

    Taken here through ``__new__`` directly rather than through a pickle,
    because a thread already waiting is the half a truncated deserialize
    cannot express: it fails too fast to let another thread arrive.
    """
    proto_id = "construction-dropped-mid-flight"
    url = f"file://{tmp_path}/grr"
    key = (proto_id, url)

    taken = FsspecReadWriteProtocol.__new__(
        FsspecReadWriteProtocol, proto_id, url)

    waiter: dict[str, Any] = {}
    waiter_done = threading.Event()

    def build_and_wait() -> None:
        # pylint: disable=broad-exception-caught
        try:
            waiter["protocol"] = build_fsspec_protocol(proto_id, url)
        except BaseException as exc:  # ruff: ignore[blind-except]
            waiter["error"] = exc
        waiter_done.set()

    waiting = threading.Thread(target=build_and_wait, daemon=True)
    waiting.start()
    assert not waiter_done.wait(0.2), \
        "a build was answered while the key was still being constructed"

    # The moment the deserialize dies: the last reference to the half-built
    # instance goes, and the construction holding the key can never finish.
    del taken

    assert waiter_done.wait(10.0), \
        "the waiter was never woken -- the key is stranded in flight"
    assert "error" not in waiter
    assert _unbound_attributes(waiter["protocol"]) == []
    assert fsspec_protocol._FSSPEC_PROTOCOLS[key] is waiter["protocol"]
