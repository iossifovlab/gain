# Allele Score Annotator — Changes

## `genomic_scores.py`

### Public API

- `GenomicScore._fetch_lines` renamed to `fetch_lines` (public). Callers inside
  `PositionScore`, `AlleleScore`, and `CnvCollection` updated accordingly.

- `AlleleScore.fetch_scores` return type changed from `list[ScoreValue]` to
  `dict[str, ScoreValue]` (score_id → value). This makes it easier for callers
  to look up individual scores by name without maintaining a parallel index list.

- `AlleleScore._build_scores_agg` renamed to `build_scores_agg` (public) and
  return type changed from `list[AlleleScoreAggr]` to `dict[str, AlleleScoreAggr]`
  (score_id → aggregator). Used by the new `aggregate_scores` method in the
  annotator.

---

## `score_annotator.py`

### `GenomicScoreAnnotatorBase`

- `simple_score_queries` is now filtered to only include attribute sources that
  exist in the resource's `score_definitions`. Virtual attributes (like `"allele"`)
  are excluded, preventing a `KeyError` when `fetch_scores` is called.

### `AlleleScoreAnnotator` — modes

The annotator has two modes selected by the `mode` parameter:

- **`region`** (**default**): iterates all allele lines overlapping the
  annotatable's span and aggregates their scores. Works with any `Annotatable`.
  Each score attribute must have an aggregator defined either in the attribute
  config or as the resource's `allele_aggregator` default.

- **`allele`**: performs an exact chrom/pos/ref/alt lookup. The annotatable must
  be a `VCFAllele`; any other type produces an empty result.

```yaml
- allele_score:
    resource_id: my_score
    # mode: region   # default — omit for region behaviour
    attributes:
    - source: freq
      aggregator: max
```

```yaml
- allele_score:
    resource_id: my_score
    mode: allele     # exact-match only; VCFAllele required
    attributes:
    - source: freq
```

### `AlleleScoreAnnotator` — `allele` virtual attribute

A virtual attribute `allele` (source `"allele"`, `default=False`) is available
on all allele score annotators. It is not a column in the underlying data file;
its value is synthesised from the matched line(s).

#### `allele` mode (exact match)

Returns `["chrom:pos:ref:alt"]` for the single matched line.

Optionally append score values by setting `include_attributes`:

```yaml
- allele_score:
    resource_id: my_score
    mode: allele
    attributes:
    - source: allele
      include_attributes: freq       # single score id
    - source: freq
```

```yaml
- allele_score:
    resource_id: my_score
    mode: allele
    attributes:
    - source: allele
      include_attributes:
        - freq
        - id
    - source: freq
    - source: id
```

#### `region` mode (default)

Collects allele strings from all lines in the region.

- **No `allele_filter`**: every allele in the region is collected.
- **With `allele_filter`**: only alleles whose scores satisfy the expression are
  collected.

```yaml
- allele_score:
    resource_id: my_score
    allele_filter: "freq > 0.05"   # optional; omit to collect all alleles
    attributes:
    - source: allele
```

`include_attributes` works the same way as for exact match.

### `allele_filter` — Lark grammar

`allele_filter` is an annotator-level parameter (not an attribute parameter).
Supported syntax:

| Form | Example |
|---|---|
| `variable > number` | `freq > 0.05` |
| `variable < number` | `freq < 0.05` |
| `variable == number` | `freq == 0.05` |
| `variable == "string"` | `type == "SNV"` |
| `variable in variable` | (right-hand variable is also a score lookup) |
| `expr and expr` | `freq > 0.01 and freq < 0.1` |
| `expr or expr` | `freq < 0.01 or freq > 0.9` |

Variables resolve to score values via `ScoreLineBase.get_score(name)`.

### Methods

| Method | Visibility | Description |
|---|---|---|
| `AlleleScoreAnnotator._build_allele_filter_func` | class method | Recursively compiles a Lark parse tree into a `ScoreLineBase → bool` callable. |
| `AlleleScoreAnnotator.get_all_attribute_descriptions` | override | Extends the parent implementation to add the virtual `"allele"` attribute with `default=False`. |
| `AlleleScoreAnnotator._annotate_allele` | private | Exact chrom/pos/ref/alt lookup; used in `allele` mode. |
| `AlleleScoreAnnotator._annotate_region` | private | Aggregates scores for all allele lines overlapping the annotatable span; used in `region` mode. |
| `AlleleScoreAnnotator.annotate` | public | Dispatches to `_annotate_allele` or `_annotate_region` based on `self.mode`. |
