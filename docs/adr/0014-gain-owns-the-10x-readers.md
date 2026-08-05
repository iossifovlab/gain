# 14. gain owns the 10x readers, and the statistics build never reads the data matrix

- **Status:** proposed
- **Date:** 2026-08-05
- **Issues:** to be filed — see [Follow-ups](#follow-ups)

## Context

`grr_manage resource-repair` cannot build the statistics of a large
`ann_data` resource. On a 10x matrix-market resource of 884,736 cells ×
38,705 genes the dask workers exceed their memory budget and the nanny
restarts them in a loop:

```
WARNING:distributed.nanny.memory:Worker ... exceeded 95% memory budget. Restarting...
```

The resource declares **320,534,319** non-zero entries. `anndata.read_mtx`
reaches them through `scipy.io.mmread`, and the dtypes it produces were
measured rather than assumed (scipy 1.18.0, anndata 0.13.2, scanpy 1.12.3):

| step | layout | size |
| --- | --- | --- |
| `scipy.io.mmread` | COO: `float64` + 2 × `int32` = 16 B/nnz | 5.13 GB |
| `.astype("float32")` | COO: 12 B/nnz, original still alive | +3.85 GB → **~9.0 GB peak** |
| `csr_matrix(...)` | 8 B/nnz | 2.56 GB |
| `.T` in `_read_10x_mtx` | transposed copy | 2.56 GB |
| `adata[:, gex_rows].copy()` | `gex_only`, always taken for v3 | another full copy |

The worker limit is 9.31 GiB. Nothing about this is recoverable by tuning.

**What the statistics build actually writes is 221 bytes.** Three files —
`describe_obs.csv`, `describe_var.csv`, `describe_ann_data.txt` — summarising
the two **axis tables** and the resource's shape. `AnnData._gen_repr`
explicitly skips `X`; `describe` reads `obs`/`var`. **No statistic reads the
data matrix.** For the resource above, ~10 GB of RAM is spent to describe
2.5 MB of sidecars.

`h5ad` does not have this problem: the loader passes `backed="r"` and `X`
stays on disk. The two 10x formats have no backed mode — `read_10x_mtx`
materialises through `mmread`, and `read_10x_h5`'s `_collect_datasets` does
`dsets[k] = v[()]` on every dataset — so the format that fails is precisely
the one the loader's memory guarantee never covered.

### scanpy contributes no algorithms here

Reading the two 10x formats is the only thing gain uses scanpy for, and every
part of that work belongs to libraries gain already depends on:

| piece | actually from |
| --- | --- |
| matrix read | `anndata.io.read_mtx` |
| sidecars | `pandas.read_csv` |
| `make_unique` | `anndata.utils.make_index_unique` |
| `gex_only` | a pandas comparison plus an anndata subset |
| `read_10x_h5` | `h5py` + `scipy.sparse` + `AnnData` |

scanpy's own contribution is the `is_legacy` probe, the `.gz` suffix
assembly, and the `gex_only` branch. For that it pulls **numba,
scikit-learn, umap-learn, pynndescent, statsmodels, patsy, seaborn, joblib,
fast-array-utils** and more. gain already depends on `anndata`, which already
pulls `h5py` and `scipy` transitively.

The project had already reached half of this conclusion. `core/Dockerfile`
keeps scanpy out of the main CI image — *"scanpy is the ann_data_10x extra
and pulls in numba, llvmlite, scikit-learn and statsmodels for two readers
most GRRs never touch"* — and only `core/Jenkinsfile.integration` installs it.

### Two defects found while surveying real resources

A survey of the eight `ann_data` resources in the benchmark GRR (four
`h5ad`, two `10x_mtx`, two `10x_h5`) turned up two problems that are not
about memory, and that any change here has to decide about:

**`gex_only=True` silently discards most of a multiome resource.** It is
scanpy's default, it filters `var` by **feature type**, and the two
CellRanger-ARC resources carry both kinds:

| resource | features in the file | statistic reports | dropped |
| --- | --- | --- | --- |
| `zemke2024Epigenetic/hc5551` | 151,832 (`Peaks` 115,231 + `Gene Expression` 36,601) | 36,601 | **115,231 (76%)** |
| `zemke2024Epigenetic/hc73` | 163,427 (`Peaks` 126,826 + `Gene Expression` 36,601) | 36,601 | **126,826 (78%)** |

**`describe_ann_data.txt` is not a pure function of the resource.**
`AnnData._gen_repr` appends `backed at '<filename>'` whenever `isbacked`, so
every `h5ad` statistic embeds the absolute path of the machine that built it.
In this GRR two resources carry `/data/lubo/...` and two carry
`/gpfs/commons/groups/iossifov_lab/...` — the same statistic, built on two
machines, byte-different, and published.

## Decision

**gain implements the two 10x readers itself, on `anndata` + `pandas` +
`h5py` + `scipy`. scanpy stops being a runtime dependency.**

Because gain owns the readers, the statistics build gets a **matrix-free
read**: the shared sidecar, `var` and feature-type-filter construction is
used by both the full read and the statistics read, and only the `X` strategy
differs — the real matrix for the loader, nothing at all for statistics,
whose shape comes from the matrix-market header or the h5 `shape` dataset.

This is deliberately *not* two implementations of one computation. ADR
[0001](0001-bulk-read-path-for-statistics.md) had to settle for *enforced
equivalence* between a scalar and a vectorized parser because they could not
literally be the same code. Here they can be: there is one construction of
the axis tables, and the reads differ only in whether they build `X`.

### The parameter surface

`parameters:` stops being an unrestricted passthrough to somebody else's
function and becomes a surface gain defines.

- **Reproduced**, because each changes an axis table or the shape:
  `var_names`, `make_unique`, `gex_only`, and `genome` (h5 only).
- **Derived, not accepted:** `prefix`, which gain already computes from
  `file:`; and compression, which gain resolves from the **manifest**.
  scanpy's `compressed` boolean is subsumed — and an existing gap closes with
  it, because `_CURRENT_SIDECARS` hardcodes `.gz` today and therefore cannot
  express an uncompressed v3 (STARsolo) layout at all.
- **Refused, with a config error naming the resource:** `cache` and
  `cache_compression`, which are scanpy's own h5ad caching machinery, and
  **`backup_url`**, which downloads the file from an arbitrary URL when it is
  not on disk. That last one is refused on principle: it would read bytes
  that are not in the manifest, not hashed, and not served by the repository.
  A resource that can silently read off-repository data is not a resource.
- An unrecognised key raises rather than being forwarded.

`h5ad` keeps its unrestricted passthrough to `anndata.read_h5ad`. gain is
replacing scanpy's wrappers, not anndata's own reader.

### `gex_only` keeps scanpy's default, and says so

The default stays `True`, so the change is byte-identical on every existing
resource. **The defect is the silence, not the default:** a read that drops
features now logs a warning naming the feature types and counts. Whether
`hc5551` is a gene-expression resource or a multiome resource is a curation
judgement about what that resource *is*, and it belongs in its
`genomic_resource.yaml` — those two configs get `gex_only: false` as a
separate, deliberate change.

### `h5ad` keeps `backed="r"`

Only the leaked path is removed, by deleting the exact
` backed at '<filename>'` clause — exact, because `ann_data.filename` is in
hand, so a future change to anndata's repr format makes the removal a no-op
that a test catches, rather than mangling the output.

A matrix-free read for `h5ad` was considered and rejected. The repr
enumerates eight key-sets (`obs`, `var`, `uns`, `obsm`, `varm`, `obsp`,
`varp`, `layers`); for 10x all but two are trivially empty, but for `h5ad`
they are arbitrary, so reproducing them means reimplementing a chunk of
anndata's own reader — for **zero** memory benefit, since `backed="r"`
already keeps `X` off the heap.

### How equivalence is held, and by what

Two tests that answer different questions, and conflating them is the trap:

- **Golden fixtures, small lane, every commit, no scanpy.** These are gain's
  contract with its own resources. They must never change without a
  deliberate statistics rebuild.
- **Live comparison against scanpy, integration lane only.** A red here means
  *upstream moved, decide deliberately* — never *gain regressed*. It runs
  where `test_ann_data_10x_layout.py` already runs, in the one tier that
  installs the extra.

Acceptance is a **one-time run against the eight real resources** — 0.19 GB
to 3.79 GB, far too large to be fixtures — byte-comparing all three
statistics files between gain's reader and scanpy's, recorded in the PR body.

### How it lands, and the one ordering constraint

Three changes, in order:

1. **The two statistics defects alone** — the leaked `backed at` path, and
   `calc_statistics_hash` recording the hardcoded `"h5ad"` fallback instead
   of the format the loader actually derives from the file suffix. Small,
   independent of the reader work, and correcting a hash that is wrong for
   every resource whose config omits `format:` — which, in the surveyed GRR,
   is all eight.
2. **The `10x_mtx` reader and the matrix-free read.** This is what unblocks
   the resource that cannot currently be repaired at all.
3. **The `10x_h5` reader, its test fixture, and the removal of scanpy.**

**scanpy does not leave before the golden fixtures and the drift test
exist.** That is the whole of ADR 0001's lesson — *"a second implementation
of an existing computation needs its equivalence mechanized **before** it is
optimized, because the failure mode is silent and the test suite you already
have will not see it"* — and here the oracle itself is what is being removed,
so there is no recovering it afterwards by re-reading the old code.

## Why it is scoped this way

**No version in the statistics hash.** `calc_statistics_hash` covers inputs
— config plus input md5s — and none of the five implementations carries a
version of the *computation*. ADR 0001 could ignore that because it
guaranteed identical output; this decision deliberately changes output, which
that design cannot express. Recording the real format instead of the
hardcoded `"h5ad"` fallback does make the four non-`h5ad` resources rebuild
on their own, but the `h5ad` path-leak fix changes output **without**
changing any input, so it would never fire. Rather than introduce a
versioning mechanism as a side effect of a memory fix, existing statistics
are regenerated with `grr_manage -f`. The general gap is real and is filed
separately.

**`10x_h5` is in scope, unlike the `np_score` precedent.** ADR 0001 leaves
kinds out when they are not exercised in production. That reasoning does not
transfer: two `10x_h5` resources exist, they have the identical eager-read
defect, and excluding them would leave a known out-of-memory failure in
place rather than leaving a safe slow path in place. gain's test builder
cannot realize a `10x_h5` fixture today (`_ANN_DATA_FORMATS = ("h5ad",
"10x_mtx")`); teaching it to is part of this work, not a reason to defer.

## Consequences

- **gain owns 10x format semantics permanently** — `var_names`,
  `make_unique`, `gex_only`, the CellRanger v2 legacy layouts, and for h5 the
  probe-barcode vs feature-barcode `var` branch and the legacy `genome`
  selector. Some of that is years of accumulated upstream edge-case handling,
  and the golden fixtures are the whole of what keeps it honest.
- **The `ann_data_10x` extra stops gating a runtime capability.** A 10x
  resource becomes readable with plain `gain-core`, and the
  "install the `gain-core[ann_data_10x]` extra" error disappears. scanpy is
  retained only as a test-only dependency of the integration image.
- **Existing statistics do not self-heal.** Every deployed GRR with `h5ad`
  `ann_data` resources keeps serving a machine-local path until someone runs
  a forced rebuild, and nothing warns them. This is the accepted cost of not
  versioning the hash.
- **Statistics output changes for `h5ad` resources**, by design — the leaked
  path is removed. It is unchanged for 10x resources.
- Reading a 10x resource for annotation is unaffected: the full read still
  builds `X`.

## What the prototype measured

A matrix-free read was prototyped against the real 1.30 GB resource before
this was written, by handing `read_10x_mtx` a matrix declaring the true
dimensions with zero entries:

```
header: 38705 x 884736, nnz=320534319   (0.00s)
scanpy read done (1.49s)  shape=(884736, 38705) nnz=0
describe_var.csv       IDENTICAL: True
describe_ann_data.txt  IDENTICAL: True
Maximum resident set size: 460 MB
```

Byte-identical to the statistics the full load had written weeks earlier —
1.5 s and 460 MB against ~10 GB and a nanny kill. That prototype delegated to
scanpy; the decision above does not, but the measurement is what established
that the statistics are derivable without the data matrix at all.

One detail from it is load-bearing and easy to get wrong: the repr's
`layers: None (.X)` line requires `X` to be a real sparse matrix of the right
shape with zero non-zeros. With `X=None` the line disappears and the
statistic is not byte-identical.

## Follow-ups

- Add `gex_only: false` to the two `zemke2024Epigenetic` configs, so the
  peaks are restored on purpose and the record says so.
- File the package-wide gap: `calc_statistics_hash` cannot express a change
  in the code that computes the statistics, only in their inputs.
- Teach the test builder to realize a `10x_h5` resource.
