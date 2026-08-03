# 3. The fragment score speaks two configuration vocabularies, permanently

Date: 2026-07-29

## Status

Superseded by
[0011-deprecate-cnv-collection-vocabulary.md](0011-deprecate-cnv-collection-vocabulary.md)
(iossifovlab/gain#538), which deprecates the legacy vocabulary this record
made permanent and silent, for removal in `2027.1.0`.

The body below is left exactly as written and is **not** a description of
today's behaviour. It is an accurate record of what was decided in July
2026, on a premise — that one resource with an unrecognised `type:` can
abort a whole repository-wide tooling run — that gain#364 has since made
false. Read `0010` for what supersedes it, and why.

Originally: accepted. Implements iossifovlab/gain#471, the second half of
the rename begun in #470. The deployed-data migration it defers is #469.

## Context

The genomic score type modelling intervals-with-attributes was called
`CnvCollection`, and its configuration surface said `cnv_collection`
throughout. Nothing about the type is copy-number specific: a record is
`(chrom, pos_begin, pos_end, attributes)` with a `size` property, and there
is no copy-number field, no duplication/deletion semantics, anywhere in it.
Copy-number variants are the most common thing stored this way, not the only
thing. The name also sat oddly in a family named after the unit a score
attaches to — `PositionScore`, `AlleleScore`, `NPScore` — where "collection"
names a container instead.

#470 renamed the Python surface to `FragmentScore` and deliberately changed
**no** configuration string, so that the rename could be reviewed as "nothing
happened except names". This ADR covers what #471 then did to the strings
users and GRRs actually write.

The constraint that shapes everything here: **those strings live in data this
repository does not own.** Six deployed resources declare
`type: cnv_collection`, and two GRR-hosted annotation pipelines
(`GPF_SFARI_annotation`, `hg38_autism_annotation`) name the `cnv_collection`
annotator and pass `cnv_filter:`. Those GRRs are mirrored publicly by
`grr-sync` and read by every deployed GAIn version at once, which is not a set
we can enumerate.

An unrecognised resource type is not a soft failure:
`build_resource_implementation` raises
`ValueError: unsupported resource implementation type`, and repository-wide
tooling builds an implementation per resource — so one resource carrying a
type an older client does not know can abort a whole `grr_manage` run for that
client.

## Decision

**Four configuration surfaces accept two spellings each, permanently and
silently.**

| surface | preferred | also accepted |
| --- | --- | --- |
| resource `type:` | `fragment_score` | `cnv_collection` |
| annotator name, short | `fragment_score` | `cnv_collection` |
| annotator name, long | `fragment_score_annotator` | `cnv_collection_annotator` |
| annotator filter parameter | `fragment_filter` | `cnv_filter` |

Four consequences worth stating outright, because each one was a choice:

**Silently, with no deprecation warning.** A warning would fire on every open
of every deployed resource — precisely the log noise removed in #466. A
message that fires for nearly every resource trains its reader to ignore the
level.

**Permanently, with no removal timeline.** The legacy spellings are not a
migration ramp. Even after #469 flips the deployed data, third-party and
private GRRs we never see will still declare the old type.

**The Python names got no such courtesy.** #470 deleted `CnvCollection`,
`CNV`, `fetch_cnvs` and the rest outright, with no aliases. The asymmetry is
deliberate and is the whole point: a Python name lives in code we control and
can grep exhaustively; a configuration string lives in YAML we cannot see.
Aliasing the former would have protected code we had no evidence existed,
while ensuring new code kept writing the old name and the vocabulary never
converged.

**Configuring both filter spellings at once is refused**, naming both. They
are two spellings of one parameter, so honouring one would apply a filter the
configuration did not ask for — and a fragment filter decides which fragments
are counted, so the wrong choice is a wrong annotation rather than an error.

### Resource-type equality is not enough

Accepting a second spelling breaks every place that compared a resource type
with `==`. Two are subtle enough to record:

- `AnnotationConfigParser.query_resources` maps an annotator name to the
  resource type it consumes. It now maps to a **set**, and all four annotator
  names resolve against both resource types — a pipeline written with the new
  name will point at GRRs still declaring the old type, which is exactly the
  state the deployed GRRs are in until #469. A wildcard that matches nothing
  annotates nothing rather than failing, so this would have been silent.

- The web API's resource picker filters by an exact `type` string, and the
  annotator's config template tells it which type to ask for. Emitting
  `fragment_score` there while every deployed resource says `cnv_collection`
  would have handed the user an **empty picker** — indistinguishable from "this
  GRR has none". `equivalent_resource_types` exists for this: it expands a
  requested type to every spelling denoting the same kind, and returns a
  one-element tuple for every other type so callers need no special case.

### What the editor advertises

The editor's annotator menu lists **one** spelling — the new one. Listing both
would show two menu entries for one annotator. The legacy name is still
accepted when a saved pipeline names it, and both names resolve to the same
config template; that template emits the new vocabulary, so anything saved
from the editor is written the new way.

The resource-types endpoint is the opposite case and advertises **both**: it
reports which types the API understands, and a deployed GRR really does
contain the legacy one.

## Consequences

**No statistics recompute.** `calc_statistics_hash` folds histograms, table
config, `files_md5` and `score_config` — not the resource `type:`. Verified
while scoping #470. So #469, when it happens, is a pure YAML edit.

**Deployed data is untouched by this change**, and #469 carries the
precondition that every deployed client must be at or above the release
containing #471 before the flip.

**GRR resource paths do not change.** `hg38/cnv_collections/DGV` and its
siblings are public URLs, linked from the published documentation, and appear
as `resource_id` in user pipelines. Renaming directories is a much larger and
separate decision, and is not on the table here.

**The cost is a permanent `in`-check in four places** where an `==` used to
do. Someone will eventually find `("fragment_score", "cnv_collection")` and
try to simplify it. That is what this ADR is for. The same applies to the two
test modules — `test_fragment_score_config_surface` pins the legacy half and
`test_fragment_score_vocabulary` the new half; both must pass, and each would
go green if the other's spellings were dropped.

**One documented example deliberately still says `cnv_collection`:**
`docs/source/python_interface.rst` counts resources by type in the public GRR,
and its documented output would become a row of zeros if the type string were
modernised ahead of the data.

## Alternatives considered

**Replace the strings outright** (no aliases). Rejected: it breaks six
deployed resources and two GRR pipelines for every client that has not
upgraded, with a hard failure that can abort repository-wide tooling.

**Add the aliases but keep the documentation on the old names.** Rejected as
self-defeating — if every document still says `cnv`, the vocabulary never
changes and the churn buys nothing.

**Register the annotator alias only, and withhold the resource-type alias**
until the data migration. Genuinely tempting, because the resource type is the
sharper edge: the day someone authors `type: fragment_score` in the shared
GRR, older clients hard-fail on it. Rejected because it produces an incoherent
half-state — a class called `FragmentScore` that can never read a resource
*called* a fragment score — and because the people who author resources in the
public GRR are the same people who control the client rollout. That is a
scheduling problem, not a guardrail the type registry should enforce.
