# The point read goes through the segment and run layers

`PositionScore.get_scores_at_position` reads a one-position region
through the same three layers as every other read of the position
plane, and it stays that way:

1. `_score_segments` yields one `(begin, end, values)` segment for each
   record, at the full extent of the record.
2. `_runs_from_segments` clips each segment against the region and
   yields `(values, run_length)` runs. A gap in coverage becomes a
   `None` run.
3. The point read unpacks the one run that a one-position region
   always gives.

There is no fast path that reads `fetch_records` and
`get_score_values_from_record` directly. There is also no point-read
branch that skips the run encoder and takes the first covering segment.

## Why this is out of scope

gain#1298 measured the cost of the layers at about 2 µs for each point
read. The layers give nothing to a one-position region. We measured the
cost again after gain#1173, gain#1282 and gain#1304 removed the other
costs of the point read. The table holds the numbers.

| Arm, tabix, 10 000 rows, 5 `float` scores, sequential | Run 1    | Run 2    |
| ------------------------------------------------------ | -------- | -------- |
| `get_scores_at_position`, as shipped                    | 11.17 µs | 11.77 µs |
| `_plane_read_defs`, then `fetch_records` + values       | 9.29 µs  | 9.85 µs  |
| `_plane_read_defs`, then the segments, drained          | 9.67 µs  | 10.40 µs |
| `_plane_read_defs` alone                                | 0.71 µs  | 0.72 µs  |

(Measured 2026-11-17 at gain `7aaa61705`, with `a_position_score()`
and `.with_tabix()`. The arms ran interleaved, and each number is the
best of 7 passes over the 10 000 positions.) The second row is the
lower bound for any special point read: the same refusals and the
first record, with no segments and no runs. The gap is about 1.9 µs.
The segments cost about 0.5 µs of the gap. The run encoder and the
unpack cost about 1.4 µs.

The saving is too small for the cost.

- **The absolute size is small.** 1.9 µs for each substitution is
  about 2 seconds for each million substitutions, for each
  `position_score` annotator. The 17% share is for a local file,
  sequential positions and a warm `LineBuffer`. A GRR table is
  block-gunzipped for each seek and is usually remote. There the read
  takes hundreds of µs and the share is less than 1%.
- **A fast path is a second body.** `_score_segments` is the one
  per-kind body of the segment stream. The fragment plane reads through
  it for that reason (gain#1272). A point read over `fetch_records`
  spells the reading of a record a second time. That is the drift that
  gain#1272 and gain#1282 closed.
- **The clip belongs to the consumer.** ADR 0008 makes each segment
  report the full extent of its record. The consumer decides what a
  partial overlap means. A producer that clips early gives that up.
- **The run is the semantics of the plane.** "A gap in coverage is a
  run of `None` positions, and it counts" is what the run encoder
  expresses. The aggregating reads answer an absent contig with that
  run and with no shape of their own (gain#1211). A point read that
  takes the first covering segment states the "first record answers"
  rule a second time, beside `_runs_from_segments`.

## What is still in scope

- **A cut inside a layer.** A change that makes `_score_segments` or
  `_runs_from_segments` cheaper for every read, and keeps what the
  layers mean, is a different request. Measure it on
  `a_position_score().with_tabix()`, interleaved, as above.
- **A real backend.** If a measurement on a real GRR table shows the
  layers above 5% of the point read, open a new issue with the numbers.

See also `point-read-pre-resolution.md`, which refuses the resolution
half of the same read.

## Prior requests

- iossifovlab/gain#1298 -- "The position plane's segment-and-run
  machinery costs ~2 us per point read, before any of it is used"
