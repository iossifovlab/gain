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

### `AlleleScoreAnnotator` — new `allele` attribute

A new virtual attribute `allele` (source `"allele"`, `default=False`) is
available on all allele score annotators. It is not a column in the underlying
data file; its value is synthesised from the matched line(s).

#### Exact-match path (`VCFAllele`)

Returns a string `"chrom:pos:ref:alt"` for the matched allele.

Optionally, one or more score values can be appended by setting
`include_attributes` on the attribute. Score values also have to be present
in the annotator's attributes and references by source.

```yaml
- allele_score_annotator:
    resource_id: my_score
    attributes:
    - source: allele
      include_attributes: freq       # single score id
    - source: freq
```

```yaml
- allele_score_annotator:
    resource_id: my_score
    attributes:
    - source: allele
      include_attributes:
        - freq 
        - id
    - source: freq
    - source: id
```

#### Aggregated path (`Region`)

Collects allele strings from lines in the region and joins them with `,`.

- **No `allele_filter`**: every allele in the region is collected.
- **With `allele_filter`**: only alleles whose scores satisfy the expression are collected.

```yaml
- allele_score_annotator:
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
| `variable in variable` | (variable on right is also a score lookup) |
| `expr and expr` | `freq > 0.01 and freq < 0.1` |
| `expr or expr` | `freq < 0.01 or freq > 0.9` |

Variables resolve to score values via `ScoreLine.get_score(name)`. These map
to the sources of the annotator's attributes.

### New / renamed methods

| Method | Visibility | Description |
|---|---|---|
| `AlleleScoreAnnotator._annotate_exact_match` | private | Replaces `_fetch_substitution_scores` + `_fetch_vcf_allele_score`; handles `allele` attribute for `VCFAllele`. |
| `AlleleScoreAnnotator.aggregate_scores` | **public** | Replaces old `_fetch_aggregated_scores` logic; handles position/allele aggregation and allele string collection. |
| `AlleleScoreAnnotator._annotate_aggregated` | private | Wraps `aggregate_scores` for `Region` inputs. |
| `AlleleScoreAnnotator._build_allele_filter_func` | class method | Recursively compiles a Lark parse tree into a `ScoreLine → bool` callable. |
| `AlleleScoreAnnotator.get_all_attribute_descriptions` | override | Adds `"allele"` to the attribute description map with `default=False`. |
