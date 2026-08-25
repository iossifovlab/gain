# Refusing a duplicate `(chrom, pos, ref, alt)` key

An allele score will **not** be refused for carrying more than one record with
the same `(chrom, pos, ref, alt)` key. A repeated key is normal data, not a
malformed resource.

## Why this is out of scope

The proposal was to give `AlleleScore.validate_records` a second rule alongside
the ordering one: a `(chrom, pos, ref, alt)` key must not repeat, on the
reasoning that "an allele score promises one value per ref/alt pair at a site,
so two rows for one pair means the table cannot say what value that pair has."

That premise does not hold for the resources we actually publish. A full-table
sweep of every `allele_score` in the GRR — 15.25 billion records, 184 GB
compressed, no sampling, at `iossifovlab/grr` commit `c4acd5cc` — found repeated
keys in 7 of 30 resources:

| resource | records | repeated keys |
|---|---:|---:|
| `hg38/scores/dbSNP` | 710,172,836 | 2,044,931 |
| `hg38/scores/dbNSFP4.9a` | 84,013,093 | 719,513 |
| `hg38/scores/AlphaMissense` | 71,697,556 | 663,287 |
| `hg19/scores/AlphaMissense` | 69,716,655 | 592,465 |
| `hg19/scores/MPC` | 66,939,306 | 323,457 |
| `.../gnomAD_v2.1.1_liftover/genomes` | 261,769,081 | 75,311 |
| `.../gnomAD_v2.1.1_liftover/exomes` | 17,201,296 | 3,489 |

The dominant cause is a **per-transcript dimension**. AlphaMissense, MPC and
dbNSFP are per-transcript predictions; their natural key is
`(pos, ref, alt, transcript)`, and the allele-score config declares only
`(pos, ref, alt)`. At `hg38/scores/AlphaMissense` chr1:23010904:

```
chr1  23010904  G  T  hg38  H3BTG2  ENST00000566855.4  S121R  0.3781  ambiguous
chr1  23010904  G  T  hg38  H3BTG2  ENST00000622840.1  S121R  0.3781  ambiguous
```

Neither row is corrupt. Both are true statements about the same substitution
under different transcripts. dbNSFP is the same shape one step aggregated: it
packs agreeing transcripts into one row and splits the row when they disagree.

So the repeat is a property of the source data's shape, not damage to it. A rule
that called it malformed would refuse seven of our flagship annotation resources
— AlphaMissense, dbNSFP, MPC and dbSNP among them — in `repo-stats`, over data
that annotates correctly today.

`AlleleScore.fetch_allele_record` returns the **first** exact match, and that
first-wins resolution is the accepted behaviour. Callers that need the
transcript dimension must read the underlying table, not ask the allele score to
adjudicate between rows it was never given the key to distinguish.

## What is still in scope

The **ordering** rule is unaffected: an allele score's records must not move
backwards, stated in `AlleleScore.validate_records` and, since gain#591, in
`AlleleScore.validate_record_arrays` on the vectorized path. Several records at
one position remain exactly what an allele score is made of — which is the same
fact that makes a repeated key unremarkable.

Nothing here says the underlying data is beyond question. If a specific resource
is found to carry genuinely contradictory rows for one allele, that is a content
question for `iossifovlab/grr` about that resource, not a reason for gain to
refuse the kind.

## The knock-on: carrying `ref`/`alt` in the column-array read

Refusing the duplicate rule also settles gain#665, which existed only to serve
it. That issue asked for two things, and the rejection above removes the ground
under both.

It asked to carry `ref`/`alt` through `fetch_region_value_arrays`, so that
`AlleleScore.validate_record_arrays` could see the key columns and state the
duplicate rule on the vectorized path. With no duplicate rule, nothing on that
path needs them: the ordering rule reads the begins and nothing else, so
`validate_record_arrays` already states the whole of what an allele score
promises, and already agrees with `validate_records` — which is what ADR 0008
asks of a pair of validators. The batch shape therefore stays
`(pos_begin, pos_end, {score_id: values})`.

It also asked to "restore" `AlleleScore`'s bulk eligibility, which gain#663
would have surrendered by returning `False` from `_bulk_scan_eligible` rather
than let a completed scan mean two different things depending on which path
ran. That trade was never made — gain#663 did not ship — so nothing was lost. A
tabix-backed allele score is bulk-eligible on master today, pinned by
`test_allele_score_is_bulk_scan_eligible`, and keeps the vectorized histogram
and min/max win.

This is not a standing refusal to ever carry the key columns. It says there is
no consumer for them, so a future proposal has to arrive with its own
motivation — and pay for itself against the cost gain#665 already identified:
`ref`/`alt` are `str`, so they would ride as `object` arrays, which is the
per-row memory the array path exists to avoid.

### That proposal arrived: gain#780 (2026-08-24)

**The two sentences above about the batch shape no longer describe the code**,
and are kept because they record why the shape held until something paid for
changing it. What is still true is everything this document is actually about:
the duplicate-key rule stays refused, `validate_record_arrays` still states the
ordering rule and nothing more, and no validator reads the key columns.

gain#780 carries `ref`/`alt` for a consumer that did not exist when gain#665
was written: the allele statistics of gain#770/gain#777, which tally
substitutions, insertions, deletions and complex alleles and therefore have to
see the nucleotides. Being a *statistic* rather than a *validator* is the whole
difference — it does not adjudicate between rows, so it needs no rule about
repeated keys, and the duplicate rows this document defends are simply counted,
each on its own.

It pays the memory cost rather than dodging it, and the measurement confirms
gain#665 was right about the cost. On 200,000 rows of a realistic tabix allele
fixture, against the fastest honest per-record read:

- throughput is only about **1.1x** — the win is modest, not a multiple;
- peak memory goes from effectively **zero** (the per-record read streams) to
  **31.7 MB** at the default 100,000-row batch, because `ref`/`alt` ride as
  `object` arrays of `str` exactly as gain#665 said they would.

The memory is a knob rather than a price — it tracks batch size linearly, and
at a 10,000-row batch it is 3.2 MB with throughput unchanged — but it is a real
cost that the streaming read does not pay.

So what carried gain#780 was **not** speed. A tabix-backed `allele_score` is
bulk-scan-eligible today, so a statistic that only knew how to collect
per-record would render "not computed" for the resources that matter while
their `np_score` twins got numbers. The key columns are carried so that one
statistic can be collected on whichever path the scan takes. That is a
different argument from the one gain#665 made and lost, which is why it
succeeded where that one did not.

Two things in that issue's shape are worth keeping here, because they are why
this is an addition rather than a reversal:

- The columns ride a **separate `AlleleScore.fetch_region_allele_arrays`**, not
  a flag on `fetch_region_value_arrays`. `RecordArrays` is unchanged and every
  existing consumer of the shared read is untouched — so the shape quoted above
  still holds *for that read*. ADR 0008 turned down a mode flag on a read path,
  and this respects it.
- The key columns are handed back **raw**, where the score columns beside them
  are parsed. `fetch_records` reads them verbatim, so the two reads hand a
  caller the same strings rather than two dialects — which is what lets a
  statistic collected on either path come out identical.

## Prior requests

- iossifovlab/gain#663 — "AlleleScore.validate_records: refuse a duplicate (pos, ref, alt) key"
- iossifovlab/grr#23 — "Duplicate (chrom, pos, ref, alt) keys in 7 published allele scores"
- iossifovlab/gain#665 — "Carry ref/alt in the column-array read; restore AlleleScore bulk eligibility" (downstream of gain#663; see the knock-on section above)
- iossifovlab/gain#780 — "Opt-in raw ref/alt column arrays on the AlleleScore bulk read" (**shipped**; the motivated proposal gain#665's rejection invited — see the knock-on section above)
