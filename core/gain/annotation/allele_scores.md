# Allele Score Annotator — Changes

## `genomic_scores.py`

### Public API

- `GenomicScore._fetch_lines` renamed to `fetch_lines` (public). Callers inside
  `PositionScore`, `AlleleScore`, and `CnvCollection` updated accordingly.

- `AlleleScore.fetch_allele_scores` return type changed from `list[ScoreValue]` to
  `dict[str, ScoreValue]` (score_id → value). This makes it easier for callers
  to look up individual scores by name without maintaining a parallel index list.

- `AlleleScore._build_scores_agg` was briefly made public as `build_scores_agg`
  for the annotator to reuse. **It no longer exists** (#267): aggregation moved
  entirely up into the annotators, and the whole in-resource aggregation engine
  — `build_scores_agg`, `fetch_scores_agg` and their query / aggregate-holder
  types — was removed. A score resource fetches records; it does not aggregate.

- That last sentence stopped being true with #1163. `AlleleScore` grew a
  folding read, `get_allele_scores_in_region_agg`, which reduces a region
  itself in one streaming walk and answers an `AlleleAggregate` — one value
  per `ScoreAggregationQuery`, plus the allele keys when asked — or `None`
  for a region no record overlaps. The annotator's region mode reads through
  it and materialises nothing (design: `2026-09-03-gain-allele-folding-read-
  design.html` in the meta-repo's `docs/`).

---

## `score_annotator.py`

### `GenomicScoreAnnotatorBase`

- `simple_score_queries` is now filtered to only include attribute sources that
  exist in the resource's `score_definitions`. Virtual attributes (like `"allele"`)
  are excluded, preventing a `KeyError` when `fetch_allele_scores` is called.

### `AlleleScoreAnnotator` — modes

The annotator has two modes selected by the `mode` parameter:

- **`region`** (**default**): the score reduces all allele lines overlapping
  the annotatable's span, streaming them through
  `AlleleScore.get_allele_scores_in_region_agg`. Works with any `Annotatable`.
  Each score attribute must have an aggregator defined either in the attribute
  config or as the resource's default; an attribute with neither — only a
  `bool` score has no default — is refused when the pipeline loads, in
  **either** mode, because a CNV or a `Region` takes the region path whatever
  the mode.

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

Collects the distinct allele strings of the lines in the region, in the
order the lines were first met — the resource's own genomic order (#1163;
it was an arbitrary set order before). The `include_attributes` suffix is
part of a string's identity, so two lines at one allele that differ in a
suffixed score are two strings. Every `include_attributes` id is resolved
against the resource when the pipeline loads; an unknown one is refused
there, naming the valid ids.

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

### `allele_filter`

`allele_filter` is an annotator-level parameter (not an attribute parameter).
The annotator only resolves the parameter and reports configuration errors
under it; the expression language, its compiler and its semantics belong to
the score — see `genomic_resources/score_filter.py`,
`GenomicScore.compile_filter`,
`docs/adr/0017-score-filtering-is-a-score-capability.md` for why the
capability sits on the score and
`docs/adr/0018-score-filter-grammar-extension.md` for the language's
precedence and character rules. The user-facing syntax is documented in
`docs/source/annotation_infrastructure.rst`.

### Methods

| Method | Visibility | Description |
|---|---|---|
| `AlleleScoreAnnotator.get_all_attribute_descriptions` | override | Extends the parent implementation to add the virtual `"allele"` attribute with `default=False`. |
| `AlleleScoreAnnotator._annotate_allele` | private | Exact chrom/pos/ref/alt lookup; used in `allele` mode. |
| `AlleleScoreAnnotator._annotate_region` | private | Asks the score's folding read for the region already reduced and answers an `AggregatedValues` keyed by attribute name; used in `region` mode. |
| `AlleleScoreAnnotator.annotate` | public | Dispatches to `_annotate_allele` or `_annotate_region` based on `self.mode`. |
