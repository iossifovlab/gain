# The annotator's contig screen and the read's contig refusal stay separate

`PositionScoreAnnotator._do_annotate` asks `score.has_chromosome(chrom)`
and answers `_empty_result()` when the score does not carry the contig;
the point read it then makes, `get_scores_at_position`, asks the same
question again inside `_region_read_defs` and *raises* when the answer is
no. The allele annotator and the allele reads are the same pair. Both
pairs stay: there will be no "plane read that answers uncovered for an
absent contig" for the annotators to read through so that the screen can
be asked once.

```python
# core/gain/annotation/position_score_annotator.py
if not self.score.has_chromosome(annotatable.chromosome):
    return self._empty_result()
...
point_scores = self.position_score.get_scores_at_position(
    annotatable.chromosome, annotatable.position,
    self.simple_score_queries)

# core/gain/genomic_resources/genomic_scores/base.py
def _require_open_and_known_chrom(self, chrom: str) -> None:
    self._require_open()
    if not self.has_chromosome(chrom):
        raise ValueError(
            f"{chrom} is not among the available chromosomes.")
```

## Why this is out of scope

The proposal (gain#1315, filed from the implementation of gain#1282 and
earlier, for the allele annotator, as gain#832) was to collapse the pair:
give the score a read that composes an absent contig as "uncovered" -- the
shape `_read_defs_for_any_contig` gives the two aggregating reads
(gain#1211) -- and have the annotator read through it without a screen of
its own. The issue deferred the decision to a re-measurement after
gain#1173 removed the O(contigs) list rebuild both halves used to pay.

That re-measurement exists, and it says no.

**The remaining cost is 0.135 us per substitution.** gain#1304 rewrote
every contig screen on the read path, this pair included, from
`chrom not in get_all_chromosomes()` to `has_chromosome()`, a lookup in a
set derived once per open. Measured on a real tabix table at hg38-shaped
contig counts (`timeit` best-of-5 over 20k calls; the table is in the
`has_chromosome` docstring in `genomic_position_table/table.py`), one
screen costs 0.135 us on the score, flat in the contig count and in the
contig's index. The duplicate this proposal would remove is one of those.
Against the point read it precedes:

| Backend, point read                      | Read     | One screen | Share  |
| ---------------------------------------- | -------- | ---------- | ------ |
| Tabix, hg38-shaped contig count          | 5.3 us   | 0.135 us   | ~2.5%  |
| Tabix, 10k rows, sequential positions    | 31 us    | 0.135 us   | ~0.4%  |
| Tabix, 10k rows, random positions        | ~500 us  | 0.135 us   | ~0.03% |

(The read costs are the ones already recorded in
`point-read-pre-resolution.md` and in the `has_chromosome` docstring;
the screen cost is the same 0.135 us in all three rows.) A sorted VCF is
the sequential row. This is a third of the 0.4 us that
`point-read-pre-resolution.md` refused on the same read for the same
reason: below what the annotation pipeline's other costs per variant let
anyone observe.

**The two halves are not the same question, and the read's is the one
that must not change.** The annotator's screen answers "does this score
have anything to say about this contig" -- `_empty_result()` is the
right answer, and it is how a variant on an alt contig the resource never
mentions annotates to nothing rather than failing the run. The read's
refusal answers "can this score serve this request at all", and it raises
on purpose. `_region_read_defs` says why the refusal is the default and
why only the two aggregating reads are exempt: they answer a question
about a *window*, where a genome-wide fold skipping a chromosome is the
normal case; every read that *materialises* a position or a contig's
positions keeps refusing, because a typo'd contig answering `None` where
a populated one answers a value is exactly the quiet failure the
eagerness exists to prevent. A point read that answered "uncovered" for
an absent contig would extend that exemption to the read every direct
caller of the API uses, for the benefit of one annotator branch. The
annotator can afford the quiet answer because it screened first; the
read cannot, because it has no way to know whether its caller did.

**Both remedies cost more than they buy.** A second, non-refusing point
read on `PositionScore` (and its twins on the allele score) grows the
declared API surface -- `test_score_resource_api.py` pins it -- with an
entry whose only caller would be an annotator branch. Dropping the
annotator's screen and catching the read's `ValueError` instead turns a
0.135 us lookup into an exception on every uncovered contig, and reads a
refusal that also fires for "not open" and for an unknown score id as if
it meant "no such contig". Either way the screen's cost moves rather than
disappears.

## What is still in scope

**A read that pays for the screen more than once in a way that grows.**
The refusal is of *this* saving at *this* size. If a measurement on a
tabix or bigWig table shows a screen that is not the flat 0.135 us --
because a new backend answers `has_chromosome` by walking something, say
-- that is a `has_chromosome` bug on that backend (gain#1304's contract),
not a reason for a seam here.

**A genuinely new reader that needs the aggregating exemption.** A read
that answers a question about a window, where an absent contig is a
normal input, may take `_read_defs_for_any_contig` as the aggregating
reads do. The point read is not that reader.

**Measuring on the right denominator.** As in
`point-read-pre-resolution.md`: efficiency-review percentages for the
score reads are re-measured on `a_position_score().with_tabix()` before
they become follow-up issues.

## Prior requests

- iossifovlab/gain#832 -- "Memoise the mapped contig list: a region
  annotation scans it twice per annotatable" (allele annotator; closed as
  a duplicate, its body holds the pre-gain#1304 measurements)
- iossifovlab/gain#1315 -- "PositionScoreAnnotator scans the contig list
  twice per substitution: its own guard, then the point read's"
