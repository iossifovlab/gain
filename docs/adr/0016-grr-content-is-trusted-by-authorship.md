# 16. GRR content is trusted by authorship

- **Status:** accepted
- **Date:** 2026-08-10
- **Issues:** [gain#572](https://github.com/iossifovlab/gain/issues/572)
  (closed `wontfix`; the decision this record generalizes),
  [gain#483](https://github.com/iossifovlab/gain/issues/483) (closed `wontfix`,
  [PR#504](https://github.com/iossifovlab/gain/pull/504) unmerged),
  [gain#623](https://github.com/iossifovlab/gain/issues/623) /
  [gain#699](https://github.com/iossifovlab/gain/issues/699) (closed `wontfix`,
  [PR#691](https://github.com/iossifovlab/gain/pull/691) unmerged),
  [gain#728](https://github.com/iossifovlab/gain/issues/728) (this record),
  [gain#731](https://github.com/iossifovlab/gain/issues/731) (the characterization test),
  [gain#684](https://github.com/iossifovlab/gain/issues/684) (closed `wontfix`;
  the published search index)
- **Related:** [0013](0013-symlink-resolution-is-not-contained.md), which applied this
  boundary to symlink resolution before it had a record of its own, and cited gain#572's
  closing comment as the source. 0013's "Why the trust boundary settles it" section is the
  first half of this argument.

## Context

Three separate defects have now been closed `wontfix` on one shared premise, and a fourth
surface was hardened, reviewed and abandoned on it. The premise had never been written
down as a decision. It existed as a paragraph in gain#572's closing comment and,
second-hand, inside an ADR about symlinks.

The cost of that omission is measurable, and it is why this record exists. gain#623 —
GRR-supplied documentation rendered through `markdown(...)|safe` — was triaged
`ready-for-agent` and ran a full three-round automated implementation loop, producing a
228-line hand-rolled HTML sanitizer, before anyone asked whether the threat model held. It
did not. The loop's own review rounds never raised the question either: every round found
real defects *in the sanitizer* and none asked what the sanitizer was for.

That is the failure mode this ADR is written against. `markdown(...)|safe`, `exec(source)`
and an unchecked path join are each individually indistinguishable from an oversight. A
reviewer, a scanner, or an agent encountering one cold will read it as a bug, and the
effort of proving otherwise is paid again every time.

### What "GRR content" means here

Everything a genomic resource carries: its `genomic_resource.yaml` — `desc`, `meta`,
histogram configuration, score definitions — and every file in the resource directory,
reaching GAIn through any of the GRR protocols. Its author is whoever has commit access to
the repository the GRR is built from.

The repository-level artefacts are the same content with the same author:
`.CONTENTS.json.gz` and the `.CONTENTS.sqlite3.gz` search index are built by `grr_manage`
and published from the repository by whoever controls it. A served index is exactly as
trustworthy as the resources it indexes — no more, and no less.

## Decision

**A GRR resource is trusted by authorship. GAIn does not defend against a hostile GRR.**

Not as code, not as filesystem reach, not as markup. Concretely, all three of the
following are conceded, not defects:

| exposure | mechanism | decided in |
| --- | --- | --- |
| **arbitrary code execution** | a histogram's `plot_function` names a Python file inside the resource; its source is `exec`'d with full builtins (`histogram.py:1152`) by six `grr_manage` commands and by `draw_score_histograms`, on dask workers under a cluster | gain#572 |
| **arbitrary filesystem reach** | a symlink inside a resource resolves anywhere — out-of-root read, overwrite, delete, and enumeration of an outside tree as resources | gain#483, [0013](0013-symlink-resolution-is-not-contained.md) |
| **arbitrary markup** | resource-supplied prose rendered `markdown(...)\|safe` into generated pages — script tags, `on*` handlers, `javascript:` URLs | gain#623, gain#699 |

gain#572's closing note states the operational consequence, and it is the sentence to
quote when this comes up: *running `grr_manage` statistics/info/repair or
`draw_score_histograms` over a GRR you do not control is equivalent to running that
repository's code.*

The remedy for an untrusted GRR is to vet its source, not to harden GAIn against it.

### Why capability is the test, and intent is not

The argument that makes this coherent rather than merely convenient is that **capability
is what an attacker gets**. Once `exec` of resource-supplied Python is conceded, every
narrower escape by the same author adds nothing to their reach:

- Anything a symlink can read, overwrite or delete, `exec`'d Python can do too, and more.
- Anything injected markup can do in a rendered page, `exec`'d Python can do at build
  time, before the page exists.

Hardening one of them defends a side door while the front door stands deliberately open.
Worse, it defends the *narrower* door: `plot_function` fires on `http` and `s3`
repositories, while symlinks require the poisoned tree to be on local disk already, and
the markup sinks require someone to open a generated page.

0013 already worked this out and, in doing so, explicitly overrode a distinction gain#572
had drawn — 0572's closing comment placed gain#483 on the other side of its line, calling
it "a genuine containment defect rather than an intended extension point." That
classification is by **intent**, feature versus defect. The trust model is about
**capability**. gain#623 and gain#699 fall the same way for the same reason, and this
record generalizes the rule so the next surface does not have to re-derive it.

### Why partial hardening is rejected

Not on cost. On the property it fails to hold.

0013 measured the tempting middle position — keep only the write-through refusal, which
looked free — and rejected it: once symlinked *directories* are allowed, a leaf-shaped
guard never sees a link, and `statistics -> ~/.ssh` walks around it untouched. The same
shape recurs in markup. PR#691's sanitizer was a 228-line allowlist parser whose first two
review rounds each found a containment breach *in the sanitizer itself* — a self-closing
allowed tag that was never closed, and an attribute allowlist no test reached.

**A guard that names a boundary it does not hold is worse than no guard.** That is the
lesson gain#467 already paid for, and it is the reason this decision is "none" rather than
"some".

### A lying published index is conceded; a stale one is not the same question (gain#684)

gain#684 is the surface that makes the repository-level sentence in the definition worth
its place. `search_resources` answers the id glob and the `type` filter out of the index's
recorded columns and resolves each row back to a real resource through its `full_id`, so a
fabricated row whose `full_id` is truthful and whose `id` lies returns a resource the glob
did not match. Conceded, by the capability test above: an author who can serve a lying
`.CONTENTS.sqlite3.gz` can serve a lying resource outright, and already holds the `exec`
capability in the first row of the table. Re-checking the glob against the resolved
resource would harden the narrower door again.

What keeps this from swallowing gain#646's label re-check: that fix is not hardening
against a hostile index, it is correctness under a **trusted** one. A curator edits a
label and no rebuild runs; index and resource then disagree with nobody lying, and both
search routes must still agree on the live value. The `id`/`full_id` split has no such
innocent route — `build_content_file` writes both columns from the same resource object,
so they agree in every index GAIn builds, however stale, and a resource renamed or
re-versioned since the build leaves a row whose `full_id` resolves nothing at all, which
the read loop already tolerates (gain#467). Divergence between the two columns of one row
is a forgery, not a lag — and forgery is what this record declines to defend against. How
the filters still answered from the index behave when it merely lags its resources is
[ADR 0007](0007-resource-query-pushdown.md)'s subject, not this one's.

### Where the boundary actually sits

The decision rests on the author being trusted, so a later reader needs to be able to
check whether that is still true. These are the facts that make it true today; each is
cheap to re-verify, and any one of them changing is a trigger below.

- **Pipeline YAML cannot supply documentation text.** `AttributeConfig` — the dataclass
  parsed from pipeline YAML — carries `name`, `source`, `internal`, `aggregator`,
  `parameters`, and no `description`. `AttributeInfo.documentation` falls back to
  `spec.description`, and every `AttributeSpec` is constructed in annotator code.
  `AnnotatorInfo.documentation` defaults to `""` and is assigned in one place in tree
  (`gene_set_annotator.py:76`). So a web request cannot inject into the string; only a
  resource can.
- **gainweb's repository is pinned server-side.**
  `GRR = build_genomic_resource_repository(file_name=settings.GRR_DEFINITION_PATH)`,
  built once at import (`web_api/web_annotation/annotation_base_view.py:48`). A request
  names a `resource_id` *within* that repository and cannot point at an external GRR.
- **Every mirror grr-sync serves is in-org** — `iossifovlab/grr_encode`,
  `iossifovlab/grr_sfari`, `iossifovlab/grr_gpf` (`grr-sync/grr_sync/config.py`). No
  third-party content is mirrored today.
- **The markup sinks are not authenticated application origins.** `annotate_doc.py`
  writes a `file://` page on the operator's own disk, and the operator chose both the
  pipeline and the GRR. `annotation_pipeline_impl.py` and `resource_template.jinja`
  produce static pages under `grr-*.iossifovlab.com`, where the content author *is* the
  site publisher. `PipelineDoc` serves its page as
  `Content-Disposition: attachment` (`web_api/web_annotation/pipelines/views.py:409`), so
  it downloads rather than executing in the gainweb origin.

### The cost of learning this on gain#623

Recorded because the README asks for the cost honestly, and because the shape repeats.

PR#691 is the near-exact analogue of PR#504: designed, implemented, reviewed, abandoned.
Beyond the three review rounds, two things are worth carrying forward.

**The prescribed fix was silently swapped mid-loop.** The issue's agent brief specified a
one-line `markdown2(..., safe_mode="escape")`. Round 1 implemented it; round 2 discarded it
for the hand-rolled sanitizer, and rounds 2 and 3 were then spent debugging the sanitizer
rather than the original issue. The substitution was never approved.

**The reason round 2 gave for the swap is itself an argument for this ADR.**
`safe_mode="escape"` escapes the whole string — including markup *this project authored*,
since every built-in annotator concatenates its own `<a href="…" target="_blank">More
info</a>` onto the resource's prose before the sink sees it. Trusted and untrusted markup
are already indistinguishable at the sink by construction. So the escape broke the
project's own links, the sanitizer was written to tell the two apart, and it could only do
so by an allowlist that **restricted the trusted author**: plain Markdown `![alt](url)`
degraded to visible tag text — though `resource_template.jinja` ships a
`<style> img { max-width: … }` block precisely for images in rendered documentation — and
`<div>`, `<table>` or `<img>` in an out-of-tree plugin's `info.documentation` rendered as
tag soup.

The end state: **doing nothing was cheaper than either proposed fix.** The brief's escape
costs the annotators' links and a cross-surface follow-up; the sanitizer costs 228 lines of
security-critical code plus what it takes from resource authors. Leaving the sink alone
costs nothing and loses nothing.

One factual correction, since it is recorded in gain#623's triage and is wrong: the claim
that a browser today eats `p < 0.05 are …` as a bogus tag does not hold. markdown2 escapes
a bare `<` that is not followed by a letter, before and after any change.

## Consequences

The exposures above are **accepted, not fixed**. Anyone running automated `grr_manage`
over repositories of mixed provenance should read the table in the Decision as the scope
of what a resource author can do, and 0013's Consequences for the filesystem detail.

What the project gets in exchange is a resource format that is not second-guessed:

- `plot_function` remains a working extension point for resource-specific plots.
- A resource file, or a whole resource directory, may be a symlink from another mount —
  half the motivating development workflow for large source files.
- Documentation prose renders as its author wrote it. Markdown images, tables, inline
  HTML, and whatever an out-of-tree annotator plugin puts in `info.documentation` all
  reach the page intact. No allowlist has to be extended when a curator wants a figure.

The monitoring this implies is small and is already stated in 0013: a periodic
`find -type l` over the served roots, because the "no symlinks in a served tree"
measurement is a snapshot rather than an invariant.

**The decision is enforced by a test, not only by this record** (gain#731). A
characterization test asserts that GRR-supplied HTML in a score `desc` reaches the
generated page *live* — deliberately. A future "security fix" to that sink turns CI red and
lands the reader here. This is the cheap half of what PR#691 built, inverted, and it exists
because the ADR alone only helps the reader who thinks to look for one.

## What would reopen this

The decision is contingent. Any of these triggers a re-read, and the surfaces closed under
it should be reopened as filed rather than redesigned from scratch:

- **gain#572 is revisited.** If `plot_function` is sandboxed, gated behind an opt-in, or
  dropped, the front door closes and the capability argument loses its premise. gain#483,
  gain#623 and gain#699 all become live again — with PR#504 and PR#691 as starting points,
  not blank pages.
- **GRR content stops being trusted by authorship.** A community-contributed GRR, a
  third-party repository added to grr-sync's mirror list, or resources becoming
  user-uploadable through the web tier — anywhere the author is not the operator.
- **A user-supplied GRR reaches the web tier.** A gainweb feature accepting a GRR URL or a
  repository definition from a request breaks the server-side pin recorded above, and with
  it the reachability argument for the markup sinks.
- **GRR content is rendered into an authenticated application origin.** The sinks closed
  here are a local file, a static host, and an attachment download. A session-bearing
  origin is a different question — see below.

## Not closed here

- **gain#688 is deliberately open.** Markdown-exempt score help templates emit
  `meta.summary` unescaped into the help string GPF serves. It was *not* closed alongside
  gain#623 and gain#699, because its sink is GPF's help pane — an authenticated
  application origin with a session, not a static docs host. The trust argument is about
  the author and would apply, but the blast radius differs enough that it deserves its own
  judgement rather than inheriting this one. Note also that escaping there collides with
  the guard test added alongside gain#558, which asserts a help pane's Markdown must reach
  GPF unescaped.
- **`about.md` is unfiled.** `fsspec_protocol.py` renders a GRR's root `about.md` with raw
  `markdown2` into `<body>` via `{{about_contents|safe}}` (around line 2183). Same class,
  never filed, closed by the same reasoning if it ever is.
- **The mirrors are served by nginx, not by GAIn.** 0013 records this and it is not
  improved by anything here: the public GRR vhosts serve mirrored trees as static roots
  with no `disable_symlinks`, so a link committed to a mirrored content repo is a public
  out-of-root read with GAIn nowhere in the loop.
