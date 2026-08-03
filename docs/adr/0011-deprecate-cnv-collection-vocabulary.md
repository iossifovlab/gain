# 11. The `cnv_collection` configuration vocabulary is deprecated, removed in 2027.1.0

- **Status:** accepted
- **Date:** 2026-08-03
- **Issues:** [gain#538](https://github.com/iossifovlab/gain/issues/538);
  supersedes [0003-fragment-score-vocabulary.md](0003-fragment-score-vocabulary.md)
  ([gain#471](https://github.com/iossifovlab/gain/issues/471)); the removal
  itself is [gain#539](https://github.com/iossifovlab/gain/issues/539)

## Context

### The premise that has expired

`0003-fragment-score-vocabulary` decided that four configuration surfaces
would accept two spellings each *permanently, with no removal timeline, and
silently*. It rested that permanence on one specific claim:

> An unrecognised resource type is not a soft failure:
> `build_resource_implementation` raises
> `ValueError: unsupported resource implementation type`, and repository-wide
> tooling builds an implementation per resource — so one resource carrying a
> type an older client does not know can abort a whole `grr_manage` run for
> that client.

That claim was true when it was written and is now false.
[gain#364](https://github.com/iossifovlab/gain/issues/364) introduced
`report_resource_failure` in `genomic_resources/cli_errors.py` and wrapped
every repository-wide loop in `genomic_resources/cli.py` in a per-resource
handler: each one reports the failing resource by id and continues.
`draw_score_histograms` was the last caller left outside that treatment and
was brought inside it by
[gain#537](https://github.com/iossifovlab/gain/issues/537).

So an unrecognised `type:` now costs a *named resource*, not a run. Removal
is therefore a ramp with a legible failure at the end of it, not a cliff —
which is precisely the condition `0003` said was missing.

### What does *not* justify revisiting it

**Not** an argument that the affected repositories are enumerable. They are
not, and the earlier record was right to say so. GAIn installs from a public
conda channel, and `docs/source/gain_getting_started_grr.rst` teaches outside
readers to author their own GRRs; the set of resources declaring
`type: cnv_collection` is open and always will be. A deprecation is honest
about that: it announces to every holder rather than claiming to know who
they are.

### Precondition: our own data first

The first-party migration landed before this record
(`iossifovlab/grr`#19). Warning about a spelling our own published GRR still
declares would have trained every user to ignore the warning, since the
loudest offender would have been us.

## Decision

**The four legacy spellings are deprecated. They keep working unchanged
until GAIn `2027.1.0`, and every use of one logs a warning.**

| surface | preferred | deprecated | warned from |
| --- | --- | --- | --- |
| resource `type:` | `fragment_score` | `cnv_collection` | `FragmentScore.__init__` |
| annotator name, short | `fragment_score` | `cnv_collection` | `FragmentScoreAnnotator.__init__` |
| annotator name, long | `fragment_score_annotator` | `cnv_collection_annotator` | `FragmentScoreAnnotator.__init__` |
| annotator filter parameter | `fragment_filter` | `cnv_filter` | `FragmentScoreAnnotator.__init__` |

Nothing resolves differently. No pipeline that worked before this change
stops working, and none annotates differently. The whole of the change is
that each use announces itself.

### `logger.warning`, not `DeprecationWarning`

`DeprecationWarning` is ignored by default outside `__main__`. The audience
here runs `annotate_vcf`, `annotate_tabular` and `grr_manage` from a shell,
and the spelling is recognised inside GAIn's config parsing — never in
`__main__` — so a `DeprecationWarning` would reach nobody and the entire
ramp would elapse in silence.

The two pre-existing deprecations in this repository (`annotate_columns`,
`GeneScore._to_dict`) use `warnings.warn` because they target *Python API*
consumers. That is a different audience, and even there `annotate_columns`
had to add a separate stderr banner for exactly this reason.

`stacklevel` is useless here for the same structural reason: the stack at
the point of recognition runs through GAIn's own parser, not through the
user's YAML. **The message must therefore carry the location itself**, and
each one names the offending resource id, or the annotator within the
pipeline, alongside the replacement spelling and `2027.1.0`.

### Where the warnings are *not* emitted

**Not from `FRAGMENT_SCORE_TYPES` membership tests.** That tuple is used as
a predicate inside the repository layer's SQL and inside
`AnnotationConfigParser.query_resources`' wildcard resolution. Warning there
would fire per *query* — over candidate resources the caller never opened —
rather than per open, which is both wrong and extremely loud on a repository
with thousands of resources.

**Not per annotated record.** `FragmentScoreAnnotator.__init__` runs once per
pipeline build; `FragmentScore.__init__` runs once per resource open.

### Volume is the design constraint

`0003` refused to warn at all on noise grounds, and that objection is
answered by *shape*, not by ignoring it. The target is **one warning per
distinct offending resource or pipeline annotator, per run**:

- Not one per run in total — that hides every offender after the first, so a
  repository with fifty legacy resources yields one actionable id and
  forty-nine invisible ones.
- Emphatically not one per record — that out-volumes the annotation output
  itself, which is the noise `0003` was right to refuse.

A repository-wide sweep over N legacy resources emits exactly N warnings,
each naming its own resource. Annotating M records through one pipeline
emits a count that does not depend on M. Both are pinned in
`core/tests/small/genomic_resources/test_fragment_score_config_surface.py`.

### What the web API advertises does not change

The resource-types endpoint still reports both spellings — it advertises
what the API can *read*, and unmigrated repositories really do contain the
legacy type. The editor's annotator menu still lists only the preferred
spelling, and its config template still emits the preferred vocabulary, so
re-saving a legacy pipeline from the editor is how a user clears the
warning.

## Consequences

**GRR resource paths do not change, here or ever as part of this work.**
`hg38/cnv_collections/DGV` and its siblings are public URLs, linked from the
published documentation, and appear as `resource_id` in user pipelines. The
directory name is not the vocabulary.

**The removal is a separate release and a separate change.** A warning
shipped in the same version that removes the thing is not a deprecation, so
this ships in a `2026.8.x` release and `2027.1.0` carries gain#539.

**`test_fragment_score_config_surface` now pins two things per surface** —
what the legacy spelling still does, and the warning it emits. Its
companion, `test_fragment_score_vocabulary`, pins the preferred half and
must stay silent. If a later change makes the preferred spelling warn, the
silence assertions there and in `test_preferred_spellings_emit_no_deprecation_warning`
are what catch it.

**The published documentation now asserts the opposite of what it did.**
Two notes — in `docs/source/grr.rst` and
`docs/source/annotation_infrastructure.rst` — said in bold that the legacy
names were *not* deprecated. They now name the deprecation and the release.

## Alternatives considered

**Leave `0003` standing.** Rejected: it is not that the earlier decision was
badly reasoned, it is that its single load-bearing premise stopped being
true. Left alone it would keep two vocabularies alive indefinitely, and
every new config written from a copied example would keep picking the wrong
one at random.

**Remove the spellings now.** Rejected outright — that is gain#539, and
doing it without a shipped warning first is a break, not a deprecation.

**Warn once per run rather than once per offender.** Cheaper, and initially
tempting because it caps the volume absolutely. Rejected: the whole value of
the warning is the *id* it carries. One warning per run over a repository of
thousands names one resource and hides the rest, which converts an
actionable notice into a vague one.

**Deduplicate warnings through a process-wide seen-set.** Rejected as
bought-and-paid-for complexity: the seams already chosen — resource open and
pipeline build — are naturally once-per-offender, so the set would suppress
nothing that is actually emitted while adding cross-test state that makes an
assertion's outcome depend on what ran before it.
