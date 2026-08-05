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

## Prior requests

- iossifovlab/gain#663 — "AlleleScore.validate_records: refuse a duplicate (pos, ref, alt) key"
- iossifovlab/grr#23 — "Duplicate (chrom, pos, ref, alt) keys in 7 published allele scores"
