# 13. Symlink resolution inside a resource is not contained

- **Status:** accepted
- **Date:** 2026-08-05
- **Issues:** [gain#483](https://github.com/iossifovlab/gain/issues/483)
  (closed `wontfix`), [gain#572](https://github.com/iossifovlab/gain/issues/572),
  [seqpipe/infra#83](https://github.com/seqpipe/infra/issues/83)

## Context

[0010](0010-resource-file-name-containment.md) contained the resource file
*name*, and recorded a gap it deliberately left open: a symlink moves the
escape out of the name and into the **resolution**. A resource containing
`sneak.txt -> /outside/secret.txt` carries a perfectly contained name and
still reads outside the GRR root.

The gap is real, and larger than the original report claimed. Confirmed by
execution against a local directory GRR:

- `uncontained_resource_file_name_reason("sneak.txt")` returned `None`, and
  reading it returned the outside file's content. `file_exists` agreed.
- The same through a symlinked *directory* — `up -> /outside`, then
  `up/secret.txt`.
- **It is a write, not only a read.** `open_raw_file(res, "sneak.txt",
  "wt")` followed the link and truncated the outside file, leaving the link
  intact. Every ordinary `grr_manage` write into a resource carrying a
  poisoned link — repair, statistics, manifest update — is an arbitrary
  out-of-root overwrite.
- `delete_resource_file(res, "up/victim.txt")` deleted the outside file.
  `.MANIFEST` diffing feeds `delete_resource_file` (0010), so no name need
  be typed by hand.
- A symlinked directory at the repository root was enumerated as a
  resource, pulling an entire outside tree into the repository.

It is reachable rather than theoretical: the mirrors grr-sync maintains are
git clones, git carries symlinks, and `grr_sync/publish.py` reproduces a
symlink as a symlink in the published snapshot — never dereferenced,
deliberately, so DVC's symlink cache survives the copy.

A fix was designed, implemented, reviewed and abandoned. It is preserved,
unmerged, at [PR#504](https://github.com/iossifovlab/gain/pull/504)
(`da6bda63f`, `e905451ce`; recoverable from `refs/pull/504/head` after the
branch is deleted). It refused symlinked directory components, allowed
symlinked leaf files to be read from anywhere, and refused writes through a
symlinked leaf.

## Decision

**Symlink resolution is not contained, and symlinks inside a resource are
allowed in every shape** — leaf files and directories alike, on read, write,
delete and scan. GAIn does not check where a resource file resolves. PR#504
is closed unmerged and gain#483 is closed `wontfix`.

The reason is the trust boundary, not convenience: **a GRR resource is
trusted by authorship**, and containment here would defend a side door while
the front door stands deliberately open.

### Why the trust boundary settles it

gain#572 established the boundary, closing `wontfix` a confirmed arbitrary
code execution: a histogram's `plot_function` names a Python file inside the
resource, whose source is `exec`'d with full builtins by six `grr_manage`
commands, on dask workers under a cluster. Its closing note states the
consequence plainly: *running `grr_manage` statistics/info/repair or
`draw_score_histograms` over a GRR you do not control is equivalent to
running that repository's code.*

Against that, the symlink escape is:

- **Strictly weaker.** Anything a link can read, overwrite or delete,
  `exec`'d Python can do too, and more. It grants no capability that is not
  already granted.
- **Narrower in reach.** `plot_function` fires on `http` and `s3`
  repositories — 0572 records that the module "may be fetched from a remote
  repository" — while symlinks exist only on the local `file` protocol and
  require the poisoned tree to be on local disk already.

Enforcing resolution containment while `plot_function` remains would be
incoherent: it hardens the narrower path against an adversary who is
assumed, one function away, to be running arbitrary code.

### This overrides a distinction gain#572 drew

0572's closing comment explicitly placed gain#483 on the other side of its
line, calling it *"a genuine containment defect rather than an intended
extension point."* That is recorded here rather than quietly dropped,
because a reader who finds it will otherwise conclude this ADR misread the
precedent.

The distinction is real but not decision-relevant. It classifies by
**intent** — a feature versus a defect — while the trust model 0572 accepted
is about **capability**, and capability is what an attacker gets. Once
running `grr_manage` over an untrusted GRR is conceded to be equivalent to
running its code, a symlink in that same repository adds nothing to the
adversary's reach.

It has also become less true since. A symlinked resource file is a
*supported workflow*, not merely an attack shape: large source files are
routinely parked on another mount and linked into a resource, and whole
resource directories are assembled the same way. That is an intended
extension point by the same standard 0572 applied to `plot_function`.

### Why not keep just the write-through refusal

PR#504's rule 3 — never write through a symlinked leaf — looks free.
Measured, GAIn writes only derived artefacts into a resource (`stats_hash`,
`statistics/*`, histogram images, `.MANIFEST`, `.CONTENTS`, `.grr/*`) and
never writes the bulk `.gz`/`.bw`/`.tbi` files anyone would link, so the
rule would cost the motivating workflow nothing while blocking
`statistics/x.json -> ~/.ssh/authorized_keys`.

It was rejected because it does not hold the property it appears to. Once
symlinked *directories* are allowed — which this decision requires, since
linking a resource directory in from another mount is half the motivating
workflow — the same attack walks around it untouched:

```
statistics -> /home/lubo/.ssh      # a symlinked DIRECTORY
grr_manage repo-repair             # writes statistics/foo.json -> creates ~/.ssh/foo.json
```

The leaf `statistics/foo.json` is an ordinary file; a leaf-shaped guard
never sees a link. The only variant that closes it is `realpath`
containment anchored at the *resolved* resource root, and that fails for the
reason PR#504 already recorded: if the resource directory is itself the
link, the anchor becomes whatever the poisoned repository points at, so
`copy_resource_file` writing remote content under a remote-chosen name into
a resource linked at `~/.ssh` is "contained" by construction.

A guard that names a boundary it does not hold is worse than no guard — that
is the lesson gain#467 already paid for. Partial containment was therefore
rejected in favour of none.

### The measurements the decision rests on

Taken against the four production GRRs served from `/data/grr` on
2026-07-29, and re-checked against the three local development trees on
2026-08-05. They are recorded here because they outlived the code, and they
are what makes this liveable rather than merely defensible:

- **Zero symlinks** anywhere inside `grr`, `grr_encode`, `grr_sfari` or
  `grr_seqpipe`, and zero inside the local `grr`, `grr_encode` and
  `grr_sfari` trees. No served or checked-out resource carries one today.
- **Zero DVC directory outputs** across all **24,165** `.dvc` pointer files:
  every one is a single-file `outs:` entry. DVC's symlink cache fallback can
  therefore only ever produce a symlinked *file*, never a symlinked
  directory.
- **Zero `.dvc` files under `statistics/`.** DVC tracks only bulk source
  data — 16,006 `.gz`, 7,964 `.tbi`, 150 `.bw`, and a handful of
  `.bgz/.fa/.vcf/.obo/.csv/.xlsx` — which are exactly the files GAIn reads
  and never writes. **The set GAIn writes and the set that is legitimately a
  link are disjoint in practice**, which is why the overwrite primitive has
  never fired in normal operation.

## Consequences

The escapes above are **accepted, not fixed**. A poisoned symlink in a GRR
tree on local disk is an out-of-root read, an out-of-root overwrite, an
out-of-root delete, and — through a symlinked directory at the repository
root — a way to enumerate an outside tree as resources. Anyone deploying
automated `grr_manage` runs over repositories of mixed provenance should
read that alongside 0572's list as the scope of what a resource author can
do, and the answer is the same one 0572 gave: vet the source.

What the project gets in exchange is that a resource file, or a whole
resource directory, may live anywhere on the filesystem and be linked into a
GRR. For large source files that is the difference between a workable
development tree and a duplicated one.

`get_resource_file_url` stays a pure string join on every protocol. PR#504
would have made it touch the filesystem — an `lstat` per path component on
every resource-file access on the local protocol — and no benchmark was ever
run on it. That cost is now not incurred.

### What would reopen this

The decision is contingent, and these are the triggers:

- **gain#572 is revisited.** If `plot_function` is ever sandboxed, gated
  behind an opt-in, or dropped, the front door closes and the argument above
  loses its premise. gain#483 becomes live again and should be reopened,
  with PR#504 as the starting point rather than a fresh design.
- **GRR content stops being trusted by authorship** — for example if
  resources become user-uploadable through the web tier, where the author is
  not the operator.
- **A symlink appears in a served tree.** The measurements above are a
  snapshot, and the second and third are what make the write escape
  theoretical rather than live. A periodic `find -type l` over the served
  roots is cheap and is the only monitoring this decision implies.

### Not closed here: the mirrors are served by nginx, not by GAIn

Nothing in this ADR protects the public GRR mirrors, and closing gain#483
arguably makes nginx the only remaining control. The four public vhosts
serve the mirrored trees as plain static roots with no `disable_symlinks` —
the default is `off`, "symbolic links are not checked" — so a link committed
to a mirrored content repo is a public, unauthenticated out-of-root read
with GAIn nowhere in the loop. Their `.git`/`.dvc` `deny` does not help: it
matches the request *URI*, so a link named anything else is served.

That is a different trust boundary — anonymous internet rather than
authenticated resource authors — and the reasoning above does **not** extend
to it. It remains open as
[seqpipe/infra#83](https://github.com/seqpipe/infra/issues/83).
