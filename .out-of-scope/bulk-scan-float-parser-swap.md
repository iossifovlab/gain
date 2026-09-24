# Swapping the bulk scan's float parser (pyarrow, future pandas)

GAIn will **not** replace the bulk statistics scan's float parse —
`ndarray.astype(np.float64)` in `score_def.py` — with pyarrow's
`cast(utf8 -> float64)`, nor track new pandas releases in the hope that
`pd.to_numeric` becomes usable there.

## Why this is out of scope

The maintainer declined the evaluation at triage (2026-09-24): it is not
something they plan to use. The trade-off on record:

- **The current parser is correct.** It is correctly rounded and agrees
  bit for bit with the per-record path's `float()`, which is the reference
  the whole bit-exactness contract is defined against (#387).
- **The win is marginal.** pyarrow measured ~61.5 ms vs ~75.3 ms per
  million cells (starting from the `dtype=object` array the tabix reader
  produces) — ~14 ms per million, against a whole bulk scan of ~4.9 s per
  10 Mbp of tabix phastCons.
- **It would cost a new hot-path dependency and a re-proof.** pyarrow would
  have to re-clear the bar numpy already clears: PEP-515 underscores and
  Unicode digits agreeing with `float()`, the NA-sentinel substitution and
  unparseable-cell fallback in `_coerce_values`, and the bulk-vs-per-record
  equivalence tests.
- **`pd.to_numeric` is the slowest option even if fixed** (~381.7 ms per
  million), so waiting on pandas buys nothing.

## Findings worth keeping (measured in #404, pandas 3.0.2 / numpy 2.4.4 / pyarrow 24.0.0)

Do not rediscover these:

- **`pd.to_numeric` is not correctly rounded** — and not by an ULP: it
  truncates to ~10 significant digits (`0.00000071009127180852` →
  `7.100912718e-07`) and misparses scientific notation ~8–10% of the time.
  1060 of 6004 adversarial tokens mismatched `float()`.
  `dtype_backend="pyarrow"` does not help.
- It is `pd.to_numeric` specifically: `pd.Series.astype(np.float64)`,
  `ndarray.astype(np.float64)` and pyarrow's cast all had **0** mismatches.
- Benchmark pyarrow from the object array, not from a pre-built `pa.array`
  — the latter flatters it to ~6x.

## Prior requests

- iossifovlab/gain#404 — "Evaluate pyarrow (and future pandas) for the bulk scan's float parse"
