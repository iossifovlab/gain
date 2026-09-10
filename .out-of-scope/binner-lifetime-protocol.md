# Binner resource lifetime: `bind`/`bin_region` instead of the generator

GAIn keeps the generator-shaped binner protocol. `Binner.bin_track(track,
regions, bin_size, grr)` stays a generator that holds its resource open
across its yields; it will **not** be replaced, as standalone work, by a
`bind` / `bin_region` pair that moves the lifetime into a context manager
owned by the caller's frame.

The shape that was proposed:

```python
class Binner(Protocol):
    @staticmethod
    def bind(
        track: Track, grr: GenomicResourceRepo,
    ) -> AbstractContextManager[BoundBinner]: ...


class BoundBinner(Protocol):
    def bin_region(
        self, region: BedRegion, bin_size: int,
    ) -> npt.NDArray[np.float64]: ...
```

It is a good analysis of a real asymmetry — it is rejected as a *piece of
work*, not as a *conclusion*.

## Why this is out of scope

The maintainer declined it at triage (2026-09-10). The trade-off on record:

- **It re-expresses a working design rather than fixing a defect.** The
  generator shape is what #1301 bought: one resource open per task however
  many regions a bundle holds, with the caller saving each array as it
  arrives so peak memory stays one region. Any replacement has to preserve
  both properties, and the proposal does — which is the point. There is no
  behaviour to recover, only a different place to put the same invariant.

- **The obligation is guarded at the only place it can be broken.**
  `gain.binning.cli._bin_chunks` is the sole caller. It wraps the call in
  `contextlib.closing`, because a suspended generator runs its `finally`
  only when exhausted or closed and a failed save keeps the exception,
  whose traceback keeps the frame and the generator with it. That failure
  path is pinned by
  `test_a_task_closes_the_binner_when_saving_a_chunk_fails`. A leak is not
  a live failure mode; it is a hazard for a caller that does not exist.

- **Protocol shape is decided with the second implementation, not ahead of
  it.** Every cost the proposal names is borne by whoever writes a second
  binner kind: the `Generator[...]` return type forbids a custom iterator
  class with its own `close()` (a type error) and a plain iterator without
  one (an `AttributeError` at the end of the `with`); the lifetime rule has
  to be re-derived from a docstring paragraph; nothing structural stops
  yielding out of turn. `PositionScoreBinner` is still the only member of
  the `gain.binning.binners` entry-point group, so choosing now would be
  speculative design against an implementation nobody has written. Choosing
  when the second kind arrives costs one extra migration of one existing
  implementation and is informed by what that kind actually needs.

- **The ordering hazard is narrower than it reads.** `_bin_chunks` pairs
  `zip(regions, arrays, strict=True)`, so `strict` counts rather than
  orders — but since #1301 the region names its own chunk through
  `_chunk_path`, so the graph and the task cannot disagree about naming.
  What remains is one in-repo implementation, reviewed together with its
  one caller, promising to yield in order. Restructuring the protocol to
  make that promise unnecessary is worth doing when there is more than one
  promise to keep.

## Where the decision actually lives now

The one durable ask of #1325 — *record the choice so the next reviewer does
not re-derive it* — was folded into
[#1203](https://github.com/iossifovlab/gain/issues/1203)
(`fragment_score_binner`, the planned second binner kind) as an acceptance
criterion, rather than written speculatively ahead of it.

## Findings worth keeping (verified during the #1325 triage)

These hold independently of the rejection — do not rediscover them:

- ADR 0025's #1301 amendment records *that* `bin_track` is bundle-level and
  yields one array per region, and why that is affordable. It does **not**
  record *why the return type is `Generator` and not `Iterator`*. That
  rationale exists only in two docstring paragraphs, on `Binner.bin_track`
  and on `_bin_chunks`.
- The narrowing to `Generator[...]` is not stylistic: `contextlib.closing`
  calls `.close()` unconditionally, so the caller's guard is what forces
  the type.
- `core/pyproject.toml` registers exactly one member of
  `gain.binning.binners`: `position_score_binner`.

## Reconsider when

A second binner kind is written — #1203 is the concrete one. If the answer
reached there is `bind`/`bin_region`, delete this file; the analysis on
#1325 is the design note to start from.

## Prior requests

- iossifovlab/gain#1325 — "Binner: bind/bin_region instead of a
  resource-holding generator (follow-up from #1301)"
