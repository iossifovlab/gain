"""A test-only fsspec filesystem that can be scripted to fail.

The repository protocol's failure paths used to be reachable only by
patching protocol methods -- ``mocker.patch.object(res, "open_raw_file")``
and friends. That seam pins internal method names, skips the code between
the public API and the patch site, and can only fault the *read* side: a
publish write, a ``.state`` write or a cleanup ``rm`` had no injection
point at all.

The protocol's real contract boundary is the fsspec ``AbstractFileSystem``
it is handed (``FsspecReadWriteProtocol(..., filesystem=...)``), so that is
where faults belong. See ``docs/adr/0021-protocol-fault-tests-inject-at-
the-filesystem-and-tier-by-observability.md`` and #874.
"""
from __future__ import annotations

import fnmatch
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import fsspec
from fsspec.callbacks import DEFAULT_CALLBACK as _DEFAULT_CALLBACK
from fsspec.exceptions import FSTimeoutError
from fsspec.implementations.memory import MemoryFileSystem


@dataclass
class _ScriptedFault:
    """One line of the script: what fails, where, and on which call.

    ``on_call`` is ``None`` for a fault that fires on *every* matching call
    -- the shape a test wants when it needs an operation to fail however
    many times the protocol retries it. An integer selects one ordinal, so
    "the first open fails, the second succeeds" is expressible too.

    ``pattern`` is matched with :mod:`fnmatch` against the whole path,
    because the path a test wants to fault is often not one it can name:
    the protocol downloads into ``<resource>/.grr/<file>.<uuid>.part``,
    minting the uuid itself.

    Matching and firing are two steps, and every fault on the call is
    matched even after one of them has fired. Counting only up to the
    first firing fault would make an ordinal mean different things
    depending on what else is scripted: two faults on one operation and
    path, at ``on_call`` 1 and 2, would fire on calls one and *three*.
    """

    operation: str
    pattern: str
    error: BaseException | None = None
    after_bytes: int | None = None
    on_call: int | None = None
    matched: int = 0

    def matches(self, operation: str, path: str) -> bool:
        """Count a call against this fault, and answer whether it matched."""
        if operation != self.operation:
            return False
        if not fnmatch.fnmatch(path, self.pattern):
            return False
        self.matched += 1
        return True

    def fires(self) -> bool:
        """Answer whether the call just matched is the one that fails."""
        return self.on_call is None or self.on_call == self.matched

    def raise_it(self) -> None:
        """Raise the error this fault was scripted with."""
        assert self.error is not None
        raise self.error


class _FaultyFile:
    """A file handle that consults the script on read, write and close.

    Wraps whatever the inner filesystem returned, in whichever mode it was
    opened -- a binary ``AbstractBufferedFile`` or the ``TextIOWrapper``
    fsspec puts on top of one for a text-mode open.
    """

    def __init__(
        self, inner: Any, filesystem: FaultyFileSystem, path: str,
    ) -> None:
        self._inner = inner
        self._filesystem = filesystem
        self._path = path

    def __enter__(self) -> _FaultyFile:
        return self

    def __exit__(self, *_exc: object) -> None:
        # Deliberately this wrapper's ``close``, not the inner handle's
        # ``__exit__``: a scripted close fault has to surface out of a
        # ``with`` block, which is how the protocol writes every file.
        # Returns ``None``, so an exception from the block propagates.
        self.close()

    def _under_script(self, deliver: Callable[[], Any]) -> Any:
        """Run one read-shaped call against the script."""
        fault = self._filesystem.consume_fault("read", self._path)
        if fault is None:
            return deliver()
        if fault.error is not None:
            fault.raise_it()
        data = deliver()
        if fault.after_bytes is not None:
            # A silent short read: hand back a prefix and then end the
            # stream, the shape that produced #292.
            return data[:fault.after_bytes]
        # Corruption: full length, wrong bytes.
        return corrupt_same_length(data)

    def read(self, *args: Any, **kwargs: Any) -> Any:
        """Read through the inner handle, under the script."""
        return self._under_script(lambda: self._inner.read(*args, **kwargs))

    def readline(self, *args: Any, **kwargs: Any) -> Any:
        """Read a line through the inner handle, under the script."""
        return self._under_script(
            lambda: self._inner.readline(*args, **kwargs))

    def __iter__(self) -> _FaultyFile:
        # Special methods are looked up on the type, so ``__getattr__``
        # never sees them: without these two, ``for line in handle`` --
        # how the tabular readers consume a resource file -- raises
        # ``TypeError: not iterable`` rather than delegating.
        return self

    def __next__(self) -> Any:
        return self._under_script(lambda: next(self._inner))

    def write(self, data: Any) -> Any:
        """Write through the inner handle, under the script."""
        fault = self._filesystem.consume_fault("write", self._path)
        if fault is not None:
            fault.raise_it()
        return self._inner.write(data)

    def close(self) -> None:
        """Close the inner handle, under the script."""
        fault = self._filesystem.consume_fault("close", self._path)
        if fault is not None:
            # Closed anyway: a store that fails a close still releases the
            # handle, and leaving it open would leak into the next test.
            self._inner.close()
            fault.raise_it()
        self._inner.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def corrupt_same_length(data: Any) -> Any:
    """Return ``data`` of the same length with different content."""
    if isinstance(data, bytes):
        return bytes((byte + 1) % 256 for byte in data)
    return "".join(chr((ord(char) + 1) % 128) for char in data)


class FaultyFileSystem(fsspec.AbstractFileSystem):
    """An ``AbstractFileSystem`` that delegates, and fails where told to.

    Generic over the filesystem it wraps -- ``MemoryFileSystem`` by default,
    matching the ``inmemory`` scheme the protocol tests already use, but any
    ``AbstractFileSystem`` will do.

    ``cachable = False`` because fsspec otherwise memoizes filesystem
    instances by constructor arguments and would hand a scripted filesystem
    to an unrelated test.
    """

    cachable = False

    def __init__(
        self, inner: fsspec.AbstractFileSystem | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.inner = inner if inner is not None else MemoryFileSystem()
        self.script: list[_ScriptedFault] = []

    # -- scripting ------------------------------------------------------
    #
    # Eight named ways to write one line of the script. They are separate
    # public names rather than one ``script(operation, ...)`` because the
    # name is what a test reads as its arrangement; they are one line each
    # because the shape of a script line is not eight different things.

    def _add(
        self, operation: str, pattern: str, *,
        error: BaseException | None = None,
        after_bytes: int | None = None,
        on_call: int | None = None,
    ) -> None:
        self.script.append(_ScriptedFault(
            operation, pattern, error=error, after_bytes=after_bytes,
            on_call=on_call))

    def fail_open(
        self, pattern: str, error: BaseException, *,
        on_call: int | None = None,
    ) -> None:
        """Fail opening any path matching ``pattern``."""
        self._add("open", pattern, error=error, on_call=on_call)

    def fail_write(
        self, pattern: str, error: BaseException, *,
        on_call: int | None = None,
    ) -> None:
        """Fail writing to any path matching ``pattern``."""
        self._add("write", pattern, error=error, on_call=on_call)

    def fail_close(
        self, pattern: str, error: BaseException, *,
        on_call: int | None = None,
    ) -> None:
        """Fail closing any path matching ``pattern``."""
        self._add("close", pattern, error=error, on_call=on_call)

    def fail_rm(
        self, pattern: str, error: BaseException, *,
        on_call: int | None = None,
    ) -> None:
        """Fail removing any path matching ``pattern``."""
        self._add("rm", pattern, error=error, on_call=on_call)

    def fail_read(
        self, pattern: str, error: BaseException, *,
        on_call: int | None = None,
    ) -> None:
        """Fail reading from any path matching ``pattern``.

        ``stall_read``'s general form: the caller names the error rather
        than taking the timeout that models a dropped link. What needs it
        is a read that fails the way a remote store fails -- an
        ``aiohttp`` error, whose message carries the fetch url -- which no
        scripted *open* can stand in for, because the protocol redacts the
        open and not the reads on the handle it returns (gain#620).
        """
        self._add("read", pattern, error=error, on_call=on_call)

    def stall_read(
        self, pattern: str, *, on_call: int | None = None,
    ) -> None:
        """Stall reads of ``pattern`` the way a dropped remote link does."""
        self.fail_read(
            pattern, FSTimeoutError("scripted stalled read"),
            on_call=on_call)

    def short_read(
        self, pattern: str, *, after_bytes: int,
        on_call: int | None = None,
    ) -> None:
        """End the stream of ``pattern`` early, silently (the #292 shape)."""
        self._add(
            "read", pattern, after_bytes=after_bytes, on_call=on_call)

    def corrupt_read(
        self, pattern: str, *, on_call: int | None = None,
    ) -> None:
        """Deliver the full length of ``pattern``, with the wrong bytes."""
        self._add("read", pattern, on_call=on_call)

    def consume_fault(
        self, operation: str, path: str,
    ) -> _ScriptedFault | None:
        """Return the first scripted fault firing on this call, if any.

        Every matching fault is counted, not just the one that fires, so
        one fault's ordinals do not shift because another was scripted
        over the same operation and path.
        """
        firing = None
        for fault in self.script:
            if fault.matches(operation, path) and firing is None:
                firing = fault if fault.fires() else None
        return firing

    # -- delegation -----------------------------------------------------

    def open(
        self, path: str, mode: str = "rb",
        block_size: int | None = None,
        cache_options: dict[str, Any] | None = None,
        compression: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Open through the inner filesystem, under the script.

        The whole call is delegated rather than routed through ``_open``,
        so path handling stays the inner filesystem's -- this wrapper never
        strips a scheme prefix of its own, and the protocol hands it
        fully-qualified urls.
        """
        fault = self.consume_fault("open", path)
        if fault is not None:
            fault.raise_it()
        return _FaultyFile(
            self.inner.open(
                path, mode, block_size=block_size,
                cache_options=cache_options, compression=compression,
                **kwargs),
            self, path)

    def rm(
        self, path: str, recursive: bool = False,  # noqa: FBT001, FBT002
        maxdepth: int | None = None,
    ) -> Any:
        """Remove through the inner filesystem, under the script."""
        fault = self.consume_fault("rm", path)
        if fault is not None:
            fault.raise_it()
        return self.inner.rm(path, recursive=recursive, maxdepth=maxdepth)

    # Below: plain delegation, one method per line of the protocol's
    # filesystem vocabulary. Written out rather than installed in a loop
    # so that mypy, pylint and a reader all see the same surface.
    #
    # A method the protocol starts calling MUST be added here. Falling
    # through to ``AbstractFileSystem`` is not a safe default: this class
    # inherits the base ``protocol``/``root_marker``, so the inherited
    # ``_strip_protocol`` mangles the scheme-qualified urls the protocol
    # passes. ``glob`` on a populated wrapper answers ``[]`` for that
    # reason, and ``unstrip_protocol`` answers ``abstract://memory://...``.
    # Nothing calls either today; the ones that do work by composition
    # (``cat``, ``walk``, ``du``) do so incidentally, not by design.

    def exists(self, path: str, **kwargs: Any) -> Any:
        """Delegate to the wrapped filesystem."""
        return self.inner.exists(path, **kwargs)

    def isdir(self, path: str) -> Any:
        """Delegate to the wrapped filesystem."""
        return self.inner.isdir(path)

    def ls(
        self, path: str, detail: bool = True,  # noqa: FBT001, FBT002
        **kwargs: Any,
    ) -> Any:
        """Delegate to the wrapped filesystem."""
        return self.inner.ls(path, detail=detail, **kwargs)

    def info(self, path: str, **kwargs: Any) -> Any:
        """Delegate to the wrapped filesystem."""
        return self.inner.info(path, **kwargs)

    def find(
        self, path: str, maxdepth: int | None = None,
        withdirs: bool = False,  # noqa: FBT001, FBT002
        detail: bool = False,  # noqa: FBT001, FBT002
        **kwargs: Any,
    ) -> Any:
        """Delegate to the wrapped filesystem."""
        return self.inner.find(
            path, maxdepth=maxdepth, withdirs=withdirs, detail=detail,
            **kwargs)

    def makedirs(
        self, path: str, exist_ok: bool = False,  # noqa: FBT001, FBT002
    ) -> Any:
        """Delegate to the wrapped filesystem."""
        return self.inner.makedirs(path, exist_ok=exist_ok)

    def mkdir(
        self, path: str, create_parents: bool = True,  # noqa: FBT001, FBT002
        **kwargs: Any,
    ) -> Any:
        """Delegate to the wrapped filesystem."""
        return self.inner.mkdir(path, create_parents=create_parents, **kwargs)

    def mv(
        self, path1: str, path2: str,
        recursive: bool = False,  # noqa: FBT001, FBT002
        maxdepth: int | None = None, **kwargs: Any,
    ) -> Any:
        """Delegate to the wrapped filesystem."""
        return self.inner.mv(
            path1, path2, recursive=recursive, maxdepth=maxdepth, **kwargs)

    def cp_file(self, path1: str, path2: str, **kwargs: Any) -> Any:
        """Delegate to the wrapped filesystem."""
        return self.inner.cp_file(path1, path2, **kwargs)

    def delete(
        self, path: str, recursive: bool = False,  # noqa: FBT001, FBT002
        maxdepth: int | None = None,
    ) -> Any:
        """Delegate to the wrapped filesystem."""
        return self.inner.delete(path, recursive=recursive, maxdepth=maxdepth)

    def modified(self, path: str) -> Any:
        """Delegate to the wrapped filesystem."""
        return self.inner.modified(path)

    def created(self, path: str) -> Any:
        """Delegate to the wrapped filesystem."""
        return self.inner.created(path)

    def sign(self, path: str, expiration: int = 100, **kwargs: Any) -> Any:
        """Delegate to the wrapped filesystem."""
        return self.inner.sign(path, expiration=expiration, **kwargs)

    def put(
        self, lpath: str, rpath: str,
        recursive: bool = False,  # noqa: FBT001, FBT002
        callback: Any = _DEFAULT_CALLBACK,
        maxdepth: int | None = None, **kwargs: Any,
    ) -> Any:
        """Delegate to the wrapped filesystem."""
        return self.inner.put(
            lpath, rpath, recursive=recursive, callback=callback,
            maxdepth=maxdepth, **kwargs)

    def invalidate_cache(self, path: str | None = None) -> Any:
        """Delegate to the wrapped filesystem."""
        return self.inner.invalidate_cache(path)

    @property
    def fsid(self) -> str:
        """Delegate to the wrapped filesystem."""
        return str(self.inner.fsid)

    def _rm(self, path: str) -> Any:
        return self.inner._rm(path)  # noqa: SLF001
