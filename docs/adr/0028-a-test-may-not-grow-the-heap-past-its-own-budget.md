# 28. A test may not grow the heap past its own budget

- **Status:** accepted
- **Date:** 2026-09-10
- **Issues:** [gain#1366](https://github.com/iossifovlab/gain/issues/1366)

## Context

A test that allocates without bound takes the whole machine with it. On
2026-09-10 one grew at ~1GB/s to 121GB and was kernel-OOM-killed four times in
a single morning on `piglet`, each time taking sshd and the developer's session
down with it (`Out of memory: Killed process ... (python) anon-rss:121550104kB`).
The cause was a `MagicMock()` standing in for an fsspec filesystem: the
unindexed VCF open consulted the manifest, `load_manifest` handed the mock file
object to `yaml.safe_load`, and yaml's encoding sniffer looped on
`stream.read()` forever, because a mock read never returns an empty chunk.

Nothing in the suite bounded it. This is the memory sibling of ADR 0004: there,
a test that stopped making progress destroyed its own evidence by consuming the
job timeout in silence; here, a test that allocates destroys its evidence by
being killed from outside the process — no traceback, no nodeid, and usually no
session left to read the log in.

A host-side cgroup cap now exists on `piglet` and `pooh`, but it protects those
two hosts only, and it still kills rather than reports. A guard that travels
with the repository protects every developer machine and CI — where, notably,
the `docker run` in the root `Jenkinsfile` passes no `--memory`, so a runaway in
CI grows until the *agent host* dies, not a bounded container.

### A flat ceiling cannot work

`RLIMIT_DATA` bounds `VmData`: the data segment plus private anonymous
mappings. That is address space, not resident memory, and the two diverge
enormously at import time. On a 32-core box:

| after | `VmData` | `VmRSS` | threads |
| --- | --- | --- | --- |
| interpreter start | 5 MB | 11 MB | 1 |
| `import numpy` | **1319 MB** | 27 MB | **32** |
| `import numpy`, `OPENBLAS_NUM_THREADS=1` | 79 MB | 27 MB | 1 |

numpy starts one OpenBLAS thread per core and each reserves ~40MB of address
space it never makes resident, so a flat 2GiB budget is two-thirds spent before
the first test runs — and the amount spent **scales with the core count of
whatever machine the suite lands on**. Armed that way the suite does not fail on
a runaway; it fails during *collection*, with `MemoryError` at 291MB of actual
resident memory. (`MALLOC_ARENA_MAX=2` was measured and changes nothing: these
are OpenBLAS's own reservations, not glibc arenas.)

### Measuring once at startup makes it a lifetime budget

`VmData` largely ratchets: lazily-created thread pools are never given back, and
what an allocator keeps in its arenas is not returned. Measured across
`core/tests/small` at `-n 5`:

| worker | tests | `VmData` at start | at end | growth |
| --- | --- | --- | --- | --- |
| gw0 | 1228 | 1452 MB | 4025 MB | 2573 MB |
| gw1 | 1088 | 1453 MB | 4019 MB | 2566 MB |
| gw2 | 1857 | 1452 MB | 3581 MB | 2129 MB |
| gw3 | 1840 | 1452 MB | 3599 MB | 2147 MB |
| gw4 | 1771 | 1452 MB | 4074 MB | 2622 MB |

Every worker grows 2.1-2.6GB over its life, so a 2GiB budget measured once at
startup is exhausted by ordinary work — and gets tighter with every test added.

The growth is not attributable to greedy tests. Charged per test protocol, the
largest single growth on each worker is 1024-1064MB, and it lands on a
different, unrelated test on each one — the signature of a one-time lazy import
or thread pool, not of the test that happened to trigger it. The runner-up on
every worker is 120MB or less, and the peak *resident* set of a worker over the
whole run is only 512-547MB.

## Decision

**`core/tests/memory_guard.py` re-arms `RLIMIT_DATA` before every test at
`VmData_now + budget`, where `budget` defaults to 2GiB and is overridable
through `GAIN_PYTEST_MEMORY_LIMIT_GB` (`0` disables). It is registered from
`pytest.ini`'s `addopts` as `-p tests.memory_guard`, beside the dask guard.**

The budget is therefore *what one test may add*, not a ceiling on the process.
That is both the intent — no unit test should need much memory — and the shape
of the failure being guarded, which was a single test growing without bound. It
is also the only version that stays correct as the suite grows: a lifetime
budget would have to be raised periodically by whoever happened to trip it.

2GiB is roughly double the largest growth any single test legitimately shows
(1064MB), and the whole of `core/tests/small` plus the top-level suites — 7896
tests at `-n 5` — passes under it with no `MemoryError`.

### The inheritance trap

Getting this right needs one non-obvious thing, and it is the part a later
reader is most likely to undo.

The guard refuses to *raise* a soft limit it does not recognise, so that a
deliberately stricter outer bound — a container, a cgroup, an outer test run —
is always respected. But rlimits are inherited by child processes, and an xdist
worker is a child of the controller. The controller imports almost nothing (it
collects, it does not run tests), so it arms a limit from a much smaller
`VmData` than any worker will have. Every worker then starts life under the
controller's ceiling, finds its own target above it, and — treating it as a
stricter outer bound — declines to move it.

The result is silent and total: the guard reports itself armed, while every
process that actually runs a test is pinned under a limit computed for a
process that does no work. In this suite that meant workers running under a
~3.5GB ceiling they exceed by ~600MB in normal operation, which surfaced as the
xdist controller dying with `INTERNALERROR ... assert not crashitem` (exit 3)
and *no failing test named*, because the `MemoryError` struck while pytest was
formatting the report for it.

So the guard publishes the limit it armed in an environment variable, and a
child treats an inherited soft limit matching that value as its own to move.
Anything else stays untouched. `tests/test_memory_guard.py` covers both halves
— the inherited-from-parent bound being raised, and a foreign bound still being
respected — because a token that is checked too loosely turns the whole refusal
into a no-op.

`setrlimit` failing, a non-Linux platform, and an unreadable `/proc` are all
silent no-ops — a best-effort guard must not be the thing that takes a run down.

### Why not the alternatives

- **`RLIMIT_AS`.** Strictly worse: it bounds all address space, so it breaks
  anything that reserves large virtual ranges without touching it — the JVM,
  torch, onnxruntime — and the numpy effect above would be larger still.
- **Pinning `OPENBLAS_NUM_THREADS=1`.** This does make a flat budget workable
  (79MB baseline, measured above), but it silently changes how every numeric
  test in the suite executes, to buy a number that measuring gives us for free.
  It also depends on winning an import race: the variable must be set before
  numpy loads, which is true at plugin *module import* but already false by
  `pytest_configure`.
- **An RSS watchdog thread.** Resident memory is what actually OOMs the host, so
  this is the theoretically better instrument, and no rlimit provides it
  (`RLIMIT_RSS` is unenforced on modern Linux). Not adopted because it costs a
  sampling thread in every worker and can only kill, not raise — which puts it
  back in the class of thing that destroys its own evidence.

## Consequences

- A runaway test is now a named failing test with a `MemoryError` traceback,
  serially and under `-n`, instead of an OOM kill. `tests/test_memory_guard.py`
  guards that by running a genuinely runaway test in a subprocess both ways.
- **Cumulative growth across a worker is deliberately not bounded.** A slow leak
  spread over many tests, each staying inside its own budget, is invisible here.
  Bounding it is what the ratchet measurement above rejects. The host-side
  cgroup cap remains the thing that bounds the machine.
- The ceiling floats upward with `VmData` rather than being fixed, and it is per
  process, so the machine-wide worst case is `(VmData_at_the_time + budget) ×
  workers`. Late in a `core/tests/small` run that is ~4.0GB + 2GiB per worker:
  ~30GB at CI's `-n 5`, ~195GB at a local `-n 32`.
- **Several runaway tests in one session still cascade.** The failing frame's
  locals stay pinned by the traceback pytest retains, so the process can sit at
  the ceiling: later items then error in both setup and teardown, and pytest
  itself can raise `MemoryError` inside its own capture teardown. Under `-n`
  that can kill the controller with `INTERNALERROR` and name no test at all —
  the same signature the inheritance bug produced, which is worth remembering
  when diagnosing one. A single runaway among healthy tests recovers cleanly,
  which is measured and asserted; a shared broken fixture hitting many tests
  does not. A 256MB ballast released on `MemoryError` was tried and does not fix
  it. Accepted: the alternative on the table was the OOM kill, which loses the
  diagnostic and the host both.
- Re-arming costs one `/proc/self/status` read and one `setrlimit` per test.
  Against ~7900 items it is not visible in the suite's wall time (74s at `-n 5`).
- Child processes inherit the limit. That is usually right — a runaway in a
  task-graph subprocess is still a runaway — but a test that deliberately shells
  out to something memory-hungry inherits a bound it did not ask for. None exist
  today; the escape hatch is the environment variable.
- The window before the first `pytest_configure` — importing the root
  `conftest.py` itself — is not covered. An import-time runaway is still an OOM
  kill.
- The guard is `core`-only, matching ADR 0004's scope. `web_api` and
  `spliceai_annotator` are unbounded and un-faulthandled; that gap is filed
  separately.
