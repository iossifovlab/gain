# 4. A hung `core` test is diagnosed by faulthandler, not pytest-timeout

- **Status:** accepted
- **Date:** 2026-07-29
- **Issues:** [gain#494](https://github.com/iossifovlab/gain/issues/494) (this record), split out of [gain#480](https://github.com/iossifovlab/gain/issues/480) (the hang that motivated it)

## Context

A test that *hangs* is worse than a test that fails. It costs the enclosing
CI job its entire timeout — one hour for the root `Jenkinsfile`, two for
`core/Jenkinsfile.integration` — and reports nothing usable: no failing test
name, no stack, no clue which thread is parked. #480 is exactly that shape, a
dask task-graph test that parks under load, and diagnosing it was blocked on
the absence of the very diagnostic that would explain it.

Before this change `core/pytest.ini` set no per-test time limit and no
faulthandler timeout, and `pytest-timeout` was not a dev dependency. Nothing
in the suite could turn a park into a report.

Two mechanisms were available, and they are not interchangeable. Both were
run against a deliberately hanging test shaped like #480 — the interesting
stack belonging to a *background worker* thread, not to the main thread —
serially and under `pytest -n 2`:

| | serial | under `-n 2` |
| --- | --- | --- |
| `faulthandler_timeout` alone | dumps all threads, **run keeps hanging** | — |
| `faulthandler_timeout` + `faulthandler_exit_on_timeout` | dumps all threads, exits in 5s | **dumps all threads**, xdist names the test, run continues and finishes |
| `pytest-timeout --timeout-method=thread` | dumps all threads, exits in 5s | **no dump at all** — worker just dies |
| `pytest-timeout --timeout-method=signal` | main thread only | — |

The two results that decided it:

**`faulthandler_timeout` on its own does not stop anything.** As of pytest 9
the dump and the exit are separate settings: `dump_traceback_later` is armed
with `exit=faulthandler_exit_on_timeout`, which defaults to *false*. A config
that sets only the timeout dumps a perfectly good stack and then goes right on
hanging until the job is killed — the diagnostic appears, the wasted hour does
too. `faulthandler_exit_on_timeout` was added in pytest 9.0.0; it does not
exist in 8.3 or 8.4, where setting it produces `PytestConfigWarning: Unknown
config option` and nothing else. That warning is the only signal, and it scrolls
past in CI output like any other — a suite can look instrumented and be inert.

**`pytest-timeout`'s dump does not survive xdist.** It writes through
`item.config.get_terminal_writer()` (`pytest_timeout.dump_stacks`). In an
xdist worker that writer is not connected to the controller's terminal, so
when the thread method calls `os._exit` the buffered dump dies with the
worker. The observed output under `-n 2` is `node down: Not properly
terminated` and nothing else. Since CI runs `core` with `-n 5`, that is the
only configuration that matters — and it is precisely the case with no
diagnostic. faulthandler survives because it writes to a *real file
descriptor* rather than through pytest's reporting machinery. The load-bearing
detail is `get_stderr_fileno()` in pytest's plugin: xdist monkeypatches
`sys.stderr` with an object that is not a file, so the lookup falls back to
`sys.__stderr__.fileno()` — the fd the worker inherited from the controller —
and dumps there. The stack lands straight in the CI log even though the worker
then dies mid-`os._exit`, and xdist reports `worker 'gw1' crashed while
running '<nodeid>'`, naming the test and failing the run.

## Decision

**`core/pytest.ini` sets `faulthandler_timeout = 600` and
`faulthandler_exit_on_timeout = true`. `pytest-timeout` is not adopted.**

The dev dependency floor moves from `pytest` to `pytest>=9` in
`core/pyproject.toml` and `dev-environment.yml`, because on pytest 8 the exit
setting is an unrecognised key that pytest ignores after one easily-missed
warning — the instrument would look configured and do nothing.

`tests/test_hang_diagnostic.py` guards both halves: that the ini keys are set
and recognised, and — by running a real hanging test in a subprocess, serially
and under `-n 2` — that the run actually terminates with an all-thread dump.

### Why 600 seconds

The threshold has to clear the slowest *legitimate* item by a healthy margin,
and `faulthandler_timeout` budgets each item's whole protocol — setup plus
call plus teardown — not just the call.

- The slowest item in the CI `core` stage is **7.86s** (master #704, 4083
  tests under `-n 5` with `--enable-http-testing --enable-s3-testing`, so the
  marker-gated `grr_http`/`grr_full`/`grr_tabix` suites are included in that
  number). 600s is ~76x that.
- That figure is measured on an unloaded CI agent, and per-test wall clock is
  what the timer bounds, so contention matters more than the idle number. Run
  on a 32-core box at `-n 32`, the same suite's worst item stretched to
  **47.55s** — 6x its CI time — and at `-n 64` the whole suite still passed
  (4006 passed, 29 skipped, no failures at either width). 600s keeps ~13x
  headroom over that loaded worst case, which is the margin that stops this
  from becoming a flake generator on a busy agent.
- The two probe tests this change adds cost 5.35s and 6.53s under `-n 5`, so
  the instrument does not become the thing it measures.
- `tests/integration` is the real constraint. Its session-scoped
  `grr_seqpipe` fixtures resolve the hg19 genome and refGene gene models from
  a remote http GRR, and on a **cold cache** the ~787MB genome is downloaded
  during one item's setup and charged entirely to that item. Warm builds
  finish the whole job in ~45-60s; a cold one must not be shot in the head
  halfway through the download.
- 600s still cuts a genuine hang short well inside every job timeout it runs
  under (1h root, 2h integration, 3h python-matrix, 4h nightly), which is the
  entire point.

### Why not a per-test override

The issue anticipated a generous global default *plus* per-test overrides for
known-slow cases. faulthandler has no marker: the timeout is a session-wide
ini value and there is no `@pytest.mark`-shaped escape hatch, which is the one
real capability `pytest-timeout` has that this does not. It did not change the
decision, because a value generous enough for a cold-cache integration
download is generous enough for everything else in the suite — no test needs
an override at 600s. If one ever does, that is the point to re-open this: the
answer would be a *second* mechanism layered on for the specific test, not a
switch to `pytest-timeout`, whose xdist blindness would still be there.

## Consequences

- A hang is now a failing test with a full thread dump in the CI log, inside
  10 minutes, instead of a silent hour.
- **The cover is not total, and the gaps are where the timer is not armed.**
  pytest arms it inside `pytest_runtest_protocol`, so it bounds one item's
  setup, call and teardown and nothing outside that. A deadlock during
  *collection* or at import time happens before any item runs and is not
  caught. Neither is **session-scoped fixture teardown**, which runs from
  `pytest_sessionfinish` — after the last item's protocol ended and the timer
  was cancelled. That gap lands on this ADR's own example: the `genome_2013`
  and `genome_2019` yield fixtures in
  `tests/integration/effect_annotation/conftest.py` close their reference
  genome in exactly that window. pytest also cancels the timer in
  `pytest_exception_interact`, so the teardown of an already-failing item is
  unprotected too. A hang in any of these still burns the job. Closing them
  needs a different instrument (a job-level watchdog), which #494 scoped out.
- Under `-n`, a hang kills one worker; xdist replaces it and the rest of the
  suite still runs, so one hang no longer costs the whole run's results.
- Serially, the exit is `os._exit` from faulthandler: it is abrupt by design.
  No teardown runs, no JUnit XML is written for that run, and any temporary
  directories or containers the test owned are left behind. This is the cost
  of the mechanism and is accepted — the alternative on the table lost the
  diagnostic entirely, and a hung run leaks those resources anyway.
- The suite now requires pytest 9. Anyone on an older pytest gets a failing
  `tests/test_hang_diagnostic.py` rather than a silently disarmed instrument.
- 600s is deliberately loose. It catches parks and deadlocks, not a test that
  is merely slow. It is not a performance guard and must not be tightened into
  one without re-measuring `tests/integration` on a cold cache.
