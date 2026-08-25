# 21. Protocol fault tests inject at the filesystem, and tier by observability

**Status:** accepted
**Date:** 2026-08-25
**Issues:** [#862](https://github.com/iossifovlab/gain/issues/862),
[#874](https://github.com/iossifovlab/gain/issues/874),
[#875](https://github.com/iossifovlab/gain/issues/875)

## Context

The fsspec protocol's failure paths — the download retry loop, checksum
verification, partial-download discard, `on_bytes` rollback — are covered by
some twenty tests in `test_fsspec_protocol_update_resource_file.py`. They
inject their faults by patching protocol methods:

```python
mocker.patch.object(src_res, "open_raw_file", side_effect=flaky_open)
```

and they carry `@pytest.mark.grr_rw`, so each runs `[file]`, `[inmemory]` and
`[s3]`.

Two things are wrong with that picture, pulling in opposite directions.

First, the seam. Patching `open_raw_file` pins an internal method name, skips
the code between the public API and the patch site, and can only fault the
*read* side. Destination-side failures — a publish write failing mid-stream, a
`.state` write failing after a successful publish, `rm` failing during cleanup
— have no injection point at all, so those paths are untested.

Second, the parametrization. For a test whose fault is injected at the Python
level *and* whose assertions observe only Python-side effects (an exception,
`sleep`'s call schedule, callback sums), the `[s3]` arm exercises nothing
s3-specific — it pays a MinIO fixture population (~114 s3 round trips each,
after #862) to run a test that never meaningfully touches the store.

But the obvious fix — "these look scheme-independent, drop their `[s3]` arms"
— was demonstrated to be untrustworthy *during the work that raised this
decision*. #862's first draft of the bulk population looked purely logical:
stage locally, upload, write states. Its bug was that MinIO reports
`LastModified` truncated to whole seconds on `head_object` but with
milliseconds on `list_objects_v2`, and s3fs serves `modified()` from whichever
call last filled its listing cache — so a cold resource memo changed which
timestamp a later read saw, and every file in a freshly published repository
read as drifted (10/12 files, against 0/12 on master). No `file` or
`inmemory` arm could ever have shown this. The `[s3]` arm of a
store-observing test was the only thing that caught it.

So: some `[s3]` arms are pure waste, and some are the only line of defense —
and intuition about which is which is exactly what failed.

### Rejected: pyfakefs (measured, not just argued)

pyfakefs was considered twice — first as the protocol-test mocking
mechanism, then again as a speed-up for the test-data builders' realize
step — and rejected both times. The structural argument: gain's protocol
code reaches storage through fsspec's `AbstractFileSystem`, never through
raw `os`/`io` — the layer pyfakefs patches — so it would intercept almost
nothing the code under test does; and the heavy readers are C extensions
that receive a URL and do their own I/O syscalls (already why
`open_tabix_file` refuses the `memory://` scheme).

The second round was settled by experiment rather than theory
(pyfakefs 6.2.0, pysam 0.24.0, pyBigWig 0.3.25, Python 3.12):

- **pysam fails in both directions, incoherently.** A file written into
  the fake fs is invisible to htslib (`tabix_compress` cannot open it).
  Handed a *real* file that patched Python cannot see, `tabix_compress`
  (pure C) succeeds — and `tabix_index` then fails "not found", because
  pysam's Cython layer runs Python-level checks against the fake fs while
  its C core does real I/O. Under pyfakefs, pysam straddles the two
  worlds; no workaround makes that coherent.
- **pyBigWig segfaults.** libBigWig's C `fopen` fails on a fake path and
  the failure path takes the whole pytest process down
  (`Fatal Python error: Segmentation fault`) — one bigwig builder call
  kills the xdist worker, not the test.
- **The speed intuition is inverted.** For a builder-shaped tree
  (5 directories, 12 small files, a stat-and-read-back pass; best of 3,
  300 iterations): real disk 1.09 ms, tmpfs (`/dev/shm`) 1.07 ms,
  pyfakefs **12.08 ms**. The tmpfs arm is the control: it bounds what
  *any* filesystem-avoidance could save at ~2%, because builder-scale
  writes land in the page cache and never touch the device during a test
  run. There is no disk win to capture, and pyfakefs then pays
  Python-level interception on every `os` call — 11x slower than the
  real filesystem it replaces.

So pyfakefs would fake only the pure-Python slice of test setup, break or
crash every tabix/VCF/bigwig realize step, and slow the slice it does
cover by an order of magnitude: cost without coverage, measured.

## Decision

**Faults are injected at the fsspec filesystem, not at protocol methods.** A
test-only, fault-injecting `AbstractFileSystem` wrapper (#874) delegates to
any inner filesystem — `MemoryFileSystem` by default — and is scripted per
path and per call ordinal. It is handed to a directly-constructed
`FsspecReadWriteProtocol(proto_id, url, filesystem=…)` under a unique
`(proto_id, url)` key, because the protocol memo re-runs `__init__` on the
live instance and would otherwise leak a scripted filesystem across tests.
The filesystem is the protocol's actual contract boundary, so the whole
protocol path runs for real, the tests survive internal refactors, and the
destination side becomes faultable for the first time.

**Tests tier by a two-condition observability rule.** A test belongs in the
mocked unit tier — and may drop its paid `[s3]`/`[http]` arms — only when
**both**:

1. its injected fault sits above the filesystem boundary, **and**
2. its assertions observe nothing on the destination store: no `.grr`
   contents, no published bytes, no states or timestamps.

Either condition failing keeps at least one real remote arm. Both conditions
are established by reading the test — never assumed from the module name or
from what the test happens to mock. A de-tiering change carries a per-test
justification table (fault site, assertion targets) that a reviewer can
falsify by reading the Then section (#875).

**Migration is staged, and rewrites are lazy.** The wrapper lands purely
additively with new destination-fault tests (#874); de-tiering is mark
surgery only, leaving test bodies untouched (#875); an existing
`mocker.patch` test is rewritten onto the filesystem seam only when it is
already being edited for other reasons — the same treatment this repository
gives docstrings that predate their rule. No standalone rewrite sweep:
those tests are green and hardened, and a wholesale rewrite risks more than
it buys.

## Why scoped this way

The rule is deliberately conservative — two conditions, both required,
bias toward keeping remote arms. A single-condition rule ("the fault is
mocked anyway") would have de-tiered store-observing tests like
`…leaves_no_temp_file`, whose `[s3]` arm is load-bearing: `mv` is
copy+delete on an object store, a genuinely different code path from the
local rename. The #862 near-miss is the standing evidence that
"looks scheme-independent" is not a reviewable property, while "what does
the Then section read" is.

The de-tiered tests keep the free default schemes (`inmemory` + `file`)
through the existing `grr_scheme` machinery rather than collapsing to a
single plain test — that collapse is part of the lazy rewrite, not of mark
surgery, so #875 stays mechanically reviewable.

The scheme parametrization machinery itself (`grr_schemes_for_marks`, the
`--enable-s3-testing`/`--enable-http-testing` gates) is untouched by all of
this: the rule governs which tests *carry* the marks, not what the marks do.

## Consequences

- Failure-path coverage stops being coupled to internal method names, and
  destination-side failures become testable at all.
- The `[s3]` cost of the suite is paid only by tests whose assertions can
  actually be influenced by a real store. The arms that remain are the ones
  with a documented reason to exist — the justification table doubles as
  that documentation.
- Two seams coexist for as long as the lazy rewrite takes — new tests use
  the filesystem wrapper while old ones still patch methods. That is
  accepted: the alternative (a sweep) rewrites twenty hardened tests in one
  diff, where a rewrite bug and a de-tier loss can mask each other.
- The rule adds a review obligation: every future fault test must state, or
  make obvious, which tier it belongs to and why. A test that mocks at the
  Python level *and* asserts on store state is legal — it just cannot drop
  its remote arms.
- `memory://`-backed unit tests still cannot touch tabix/VCF/bigwig opens;
  htslib-facing behavior stays in the real-scheme tier by construction, and
  nothing in this decision pretends otherwise.
