# 4. Symlinks inside a resource are allowed; symlinked directories are not

- **Status:** accepted
- **Date:** 2026-07-29
- **Issues:** [gain#483](https://github.com/iossifovlab/gain/issues/483)

## Context

[0003](0003-resource-file-name-containment.md) contained the resource file
*name*. It recorded, and deliberately left open, the gap this ADR closes: a
symlink moves the escape out of the name and into the **resolution**. A
resource containing `sneak.txt -> /outside/secret.txt` carries a perfectly
contained name and still reads outside the GRR root.

Confirmed by execution against a local directory GRR, before any fix:

- `uncontained_resource_file_name_reason("sneak.txt")` returned `None`, and
  reading it returned the outside file's content. `file_exists` agreed.
- The same through a symlinked *directory* — `up -> /outside`, then
  `up/secret.txt` — read outside too.
- The issue scoped this as read-only. It is not. `open_raw_file(res,
  "sneak.txt", "wt")` **followed the link and truncated the outside file**,
  leaving the link intact. Every ordinary `grr_manage` write into a resource
  carrying a poisoned link — repair, statistics, manifest update — was an
  arbitrary out-of-root overwrite.
- `delete_resource_file(res, "up/victim.txt")` **deleted the outside file**.
  Deleting a symlinked *leaf*, by contrast, removed only the link. The
  dangerous case is the intermediate directory, not the leaf — the reverse
  of what an `O_NOFOLLOW`-shaped guard would protect. `.MANIFEST` diffing
  feeds `delete_resource_file` (0003), so no name need be typed by hand.
- A symlinked directory at the repository root was **enumerated as a
  resource**, pulling an entire outside tree into the repository.

It is reachable in principle rather than theoretical: the mirrors grr-sync
maintains are git clones, git carries symlinks, and `grr_sync/publish.py`
reproduces a symlink as a symlink in the published snapshot — never
dereferenced, deliberately, so DVC's symlink cache survives the copy.

## Decision

**Symlinks are allowed.** The rule is about the *shape* of the path, not
about where it resolves. On the local `file` protocol only — `http` and `s3`
have no symlinks, so the question does not arise and they are untouched:

1. **No symlinked directory component below the repository root**, on read,
   write, delete or scan.
2. **A symlinked leaf file may be read, and may resolve anywhere**,
   including outside the repository root.
3. **A symlinked leaf file is never written through.** Deleting the link
   itself stays allowed: it removes the link, not the target.

A scan that meets a symlinked directory **warns and skips** it.

Rule 1 lives at the same join 0003 used, `get_resource_file_url`, so every
read sink inherits it, plus the two internal joins that build a location
themselves and so inherit nothing — `_get_resource_file_state_path` and
`_get_resource_file_lockfile_path`, where a symlinked `.grr` redirects the
write just as surely as a traversing name did. Rule 3 sits in
`open_raw_file`'s write branch. The scans get the check inline, next to the
dot-directory skip they already had.

### Why symlinks are allowed at all

Because a resource file that resolves outside the repository is a
**supported configuration**, not only an attack. grr-sync sets DVC's cache
type per publish mode: `hardlink` under `publish: atomic`, but
`hardlink,symlink` in in-place mode, with the cache relocated to a shared
per-repo `cache_dir` placed deliberately *outside* any swapped tree. In that
mode a DVC-materialized resource file is *correctly* a symlink pointing out
of the repository. Refusing symlinks, or requiring every resolution to stay
inside the resource, would break it.

That is why the obvious framings were rejected:

- **Refuse to serve any symlinked entry** — simplest to reason about, and
  incompatible with the above unless DVC's symlink fallback is also dropped,
  which is a grr-sync decision, not a gain one.
- **`realpath` containment, anchored at the resource root** — the first
  design, and it survived two rounds before collapsing. It needed a second
  check that the resource root itself stay inside the repository root,
  because otherwise the anchor is whatever a poisoned repository points at:
  `copy_resource_file` writes *remote* content under a *remote-chosen* name,
  so a resource directory linked at `~/.ssh` is an arbitrary write with
  attacker-controlled content. Once symlinked directories are refused
  outright, both checks collapse into rule 1 — a symlinked resource
  directory simply *is* a symlinked component. What is left is an `lstat`
  per component and no `realpath` at all.

### Why the rule is shaped this way

- **The walk starts AT the repository root and only tests components below
  it.** The root's own linkness is never examined, which is what keeps the
  atomic-flip publish layout working — on kowalski every served GRR root
  *is* a symlink (`/repo/<name> -> <name>.<sha>`), re-pointed on each flip.
  This gives "resolve the root once" semantics without a `realpath` call.
- **Classification is `islink` AND `isdir`, never `isdir` alone.** `isdir`
  follows links, so a symlink *pointing at* a directory would otherwise be
  traversed as though it were a real one.
- **The walk splits on `/` only**, unlike the name rule in 0003, which also
  treats a backslash as a separator. This walks a real local filesystem,
  where a backslash is an ordinary character in a file name; splitting on it
  would test components that do not exist and miss the one that does.
- **Writing through a leaf link is refused wherever it points**, including
  back inside the resource. Nothing GAIn writes is legitimately a symlink,
  and the DVC-materialized files that *are* links are ones GAIn only reads —
  see the measurement below.
- **A scan warns and skips; it never raises.** Raising during enumeration is
  the gain#464 shape 0003 explicitly refused: a manifest is parsed while
  *enumerating*, so one bad entry kills the generator before a single
  resource is yielded and takes `list`, `repo-repair` and even
  `resource-repair` on an unrelated healthy resource down with it. Skipping
  silently, like a dot-directory, was rejected in turn: someone created that
  link deliberately, so a silent disappearance becomes a debugging session.

### The measurement the scoping rests on

Taken against the four production GRRs served from `/data/grr` on
2026-07-29:

- **Zero symlinks** anywhere inside `grr`, `grr_encode`, `grr_sfari` or
  `grr_seqpipe`. No published resource carries one today — the audit 0003
  left out of scope.
- **Zero DVC directory outputs** across all **24,165** `.dvc` pointer files:
  every one is a single-file `outs:` entry, none carries `nfiles:`. DVC's
  symlink fallback can therefore only ever produce a symlinked *file*, never
  a symlinked directory, so rule 1 cannot collide with it.
- **Zero `.dvc` files under `statistics/`.** DVC tracks only bulk source
  data — 16,006 `.gz`, 7,964 `.tbi`, 150 `.bw`, and a handful of
  `.bgz/.fa/.vcf/.obo/.csv/.xlsx` — which are exactly the files GAIn reads
  and never writes. Everything `grr_manage` writes (`statistics/`,
  `.MANIFEST`, `.grr/`) is untracked. Rules 2 and 3 are disjoint in
  practice, not merely by argument.
- Nothing on kowalski exercises the symlink cache *today*: the four DVC
  repos publish `atomic` (`hardlink`), and the two `in-place` repos are
  git-only with no DVC blobs. The shared-cache case is a developer and
  future-host pattern, so no current deployment regressed either way.

## Consequences

A resource directory reached through a symlink is no longer served: it is
skipped with a warning and does not appear in the repository. Assembling a
GRR by symlinking in resources that live elsewhere therefore stops working,
which is a real cost to a developer workflow and was accepted knowingly —
symlinking in a heavy *file* still works, which covers the case that
motivated allowing symlinks at all.

Rule 1 runs on every resource-file access on the local protocol, turning
`get_resource_file_url` from a pure string join into one that touches the
filesystem: an `lstat` per path component. That is a real change in kind, on
a hot-ish path, accepted because the walk is short and the alternative is
leaving the choke point 0003 established with only half of containment on
it.

**TOCTOU is not addressed.** The check is `lstat`-then-open, so a component
could in principle be swapped in between. The threat model here is poisoned
repository *content*; an attacker who can swap a path component mid-operation
already has local write access to the checkout.

Hardlinks, bind mounts and other filesystem aliasing are not addressed
either, and would not have been caught by a `realpath` check.

### Not closed here: the mirrors are served by nginx, not by GAIn

Nothing in this ADR protects the public GRR mirrors, and it should not be
read as claiming otherwise. The four public vhosts serve the mirrored trees
as plain nginx static roots with no `disable_symlinks` — the default is
`off`, "symbolic links are not checked" — so a link committed to a mirrored
content repo is already a public, unauthenticated out-of-root read with GAIn
nowhere in the loop. That default is currently load-bearing, because the
vhost root is itself the atomic-flip symlink. The vhosts' `.git`/`.dvc`
`deny` does not help: it matches the request *URI*, so a link named anything
else is served — the same name-versus-resolution defect as this ADR, one
layer down. Tracked as
[seqpipe/infra#83](https://github.com/seqpipe/infra/issues/83).
