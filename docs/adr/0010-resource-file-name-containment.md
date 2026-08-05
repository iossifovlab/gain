# 10. Resource file names are contained by construction

- **Status:** accepted
- **Date:** 2026-07-29
- **Issues:** [gain#467](https://github.com/iossifovlab/gain/issues/467)

## Context

`ReadOnlyRepositoryProtocol.get_resource_file_url` joined a caller-supplied
file name onto the resource url with `os.path.join` and no checking:

```python
return os.path.join(self.get_resource_url(resource), filename)
```

That name is not a code constant. It comes from GRR *content* — the resource's
`genomic_resource.yaml` (`table.filename`, `gene_mapping`, …) and, separately,
from its `.MANIFEST`, whose `ManifestEntry.name` was parsed verbatim from
remote YAML. GRR content is fetched from remote repositories, so it is
attacker-controlled input.

Every sink downstream of that join was therefore reachable with an arbitrary
path. Reproduced against a local directory GRR before the fix:

- `open_raw_file("../../secret.txt")` read a file above the resource
  directory, and `file_exists` agreed it was there.
- The same name in write mode created the parent directory chain *outside the
  GRR root* (`mkdir(parent, create_parents=True)`) and wrote into it.
- `copy_resource` on a resource whose `.MANIFEST` carried
  `name: ../../evil.txt` wrote outside the destination resource **and
  completed without raising**.
- An absolute name was worse than a traversal: `os.path.join` discards the
  left operand entirely, so `/etc/passwd` dropped both the resource url and
  the `file://` scheme.
- `local_manifest.names() - remote_manifest.names()` feeds
  `delete_resource_file`, so a manifest that once carried a traversing name
  and later dropped it was an arbitrary-delete primitive.

## Decision

Containment is enforced in `validate_resource_file_name`, called from the
shared join in `get_resource_file_url` — so every protocol, every scheme and
every sink inherits it — plus the two internal paths that build a location by
joining the resource url *themselves* and so would not have inherited
anything: `_get_resource_file_state_path` (`.grr/<name>.state`) and
`_get_resource_file_lockfile_path` (`.grr/<name>.lockfile`).

A name is contained when it is relative — by POSIX *and* Windows rules — and
carries no `..`, `.` or empty segment. Rejection raises `ValueError` naming
both the resource and the name; it never skips, clamps or normalises the name
into something safe.

The resource **id** is contained by the same construction, because it is the
*other* operand of the very same join: `get_resource_url` joins
`resource.get_genomic_resource_id_version()` onto the repository url, and on
the remote path that id is read verbatim out of the repository's
`.CONTENTS.json.gz`. Containing only the file name left every consequence
listed above reachable through its sibling — a `.CONTENTS` carrying
`id: ../../ESCAPED/evil` read outside the GRR root and, through the caching
repository, wrote two levels *above* the cache root with the directory chain
`mkdir`'d on the way. `validate_resource_id` is therefore called from
`get_resource_url` — in the base class and in the `FsspecReadOnlyProtocol`
override that joins the credential-bearing `_fetch_url` itself — and the
`.CONTENTS` loader drops a poisoned entry with a warning rather than serving
it.

The **local** scan path was never exposed: `_scan_path_for_resources` skips
any directory whose name starts with `.`, so `..` cannot enter an id that
way. Note that the id validators the codebase already had do **not** supply
this property — `is_gr_id_token("..")` and `parse_gr_id_version_token("..")`
both accept, because the token charset includes `.`, and
`parse_gr_id_version_token` even accepts `/etc/passwd`. They answer "is this
well-formed", not "is this contained".

`""` and `"."` are contained ids: both name the repository root, which is a
resource in its own right — `proto_builder` addresses it as `""` and
`build_local_resource` as `"."`.

### Why the rule is shaped this way

- **Normalise-then-compare, not "no separators".** Nested names are ordinary
  here: every resource's statistics live under `statistics/`. A rule that
  rejected any name containing a separator would break the repository.
- **`..` is rejected even when it stays inside** (`sub/../other.txt`). The
  joined url is handed to fsspec *unnormalised*, and the three backends GAIn
  speaks to then do three different things with it — measured, not assumed:
  `yarl`/aiohttp **normalises** it away client-side before the request is
  sent (`http://h/res/sub/../other.txt` → `http://h/res/other.txt`), minio
  **rejects** the key outright (`XMinioInvalidResourceName`), and a local
  `file` filesystem **resolves** it. One name, three outcomes. That is a
  stronger reason to reject than the "object stores treat `..` literally"
  claim this ADR made first time round, which was simply false.

  The same measurement settles a question the issue left open: because yarl
  collapses `..` *before* the request goes out, the http traversal in #467
  was **live**, not hypothetical — the escaped path was requested directly
  and the server never saw a `..` to refuse.
- **The check is url-shaped, not `os.path`-shaped.** The name is tested both
  as written and percent-decoded, because an http(s) server decodes the path
  before resolving it — `%2e%2e` is a traversal there and a literal name on a
  local filesystem. One decoding pass is the right depth: `%252e%252e`
  decodes to the literal text `%2e%2e`, which no server resolves further.
- **Backslash counts as a separator** when scanning for `..`, since it is one
  on Windows and in several fsspec backends. Only `..` segments are rejected,
  so a stray backslash inside an ordinary name still works.
- **Absoluteness is tested the Windows way too**, for the same reason. A
  POSIX-only `startswith("/")` accepted `C:/windows/system32/x`,
  `C:\windows\x`, `\\srv\share\x`, `\windows\x` and even the
  drive-relative `x:y` — every one of which discards the base under
  `ntpath.join`, exactly as `/etc/passwd` does under `posixpath.join`. A rule
  that treats a backslash as a separator while ignoring Windows absoluteness
  is incoherent, so the check adds a leading `\`, `ntpath.isabs` and a
  drive-letter prefix.
- **Degenerate names are rejected**: empty, whitespace-only, `.`, and any
  name carrying a `.` or empty segment (`./x`, `sub/./x`, `x/`, `a//b`).
  `open_raw_file("")` yielded the resource *directory* itself. This stays
  proportionate — nested, dotted and spaced names (`statistics/hist.json`,
  `a.b/c-d/e_f.txt`, `odd name.txt`) are untouched.
- **A resource id is held to the containment half only** — relative, no `..`.
  It is joined once, at the repository root, so a `.` segment in it is a
  no-op rather than a way to address something else, and `.` is an id the
  codebase already issues.

## Consequences

The rule admits no exemptions, and one caller was relying on the old
behaviour. `build_gene_models_from_file` built a synthetic resource rooted at
`.` and passed whole local paths as the file name, leaning on `os.path.join`
discarding the root for an absolute path — 110 tests failed on the first run
of the fix. Its two siblings (`build_reference_genome_from_file`,
`build_gene_set_collection_from_file`) already split `dirname`/`basename`, but
that split does not fit here: the models, the gene mapping and the chromosome
mapping are three independent paths that need not share a directory. It now
roots the synthetic resource at `/` and makes each path relative to it, which
keeps the API's semantics and the containment rule at once.

A published resource that carries a traversing name in its config or manifest
now fails loudly instead of silently reaching outside itself. Auditing
existing published resources for such names was deliberately left out of
scope.

### Rejected: failing a poisoned `.MANIFEST` at parse time

The first version of this fix also raised from `ManifestEntry.__post_init__`,
billed as defence in depth. It was withdrawn, because a `.MANIFEST` is parsed
while *enumerating* a repository: `collect_all_resources` reads every one of
them before yielding anything, so a single poisoned entry killed the
generator before the first resource came out. Measured on a repository with
three healthy resources and one poisoned: `grr_manage list`, `repo-repair`
**and** `resource-repair --resource good_one` all died with a raw traceback
that named no resource — repairing an unrelated healthy resource had become
impossible. That is precisely the failure gain#464 filed, where one bad
`meta.labels` key cost the whole repository its FTS index.

It was also redundant. With `__post_init__` neutralised and the choke point
intact, the whole security suite still passed except the three tests that
asserted `__post_init__` itself — including the worst attack in the issue,
`copy_resource` from a resource whose `.MANIFEST` carries `../../evil.txt`.
The choke point alone covers it.

What survives is the *attribution* the raise was really buying:
`report_uncontained_manifest_entries` logs a warning naming both the resource
and the offending entry, from `build_genomic_resource` and `get_manifest` —
the first points at which a parsed manifest is paired with the resource it
belongs to, which a `ManifestEntry` never is. The repository stays
enumerable and repairable; the poisoned name fails loudly when something
tries to use it.

One knock-on: the FTS index is a *separate* artefact published by the same
GRR, so it can name a resource the `.CONTENTS` loader refused to build.
Resolving such a hit through the resource dict raised `KeyError` and took
search down for the whole repository — the same shape again — so
`search_resources` now skips an unresolvable row with a warning.

### Accepted gap: symlinks

Containment is enforced on the *name*, and a symlink moves the escape into
the *resolution*. A resource containing `sneak.txt -> /outside/secret.txt`
has a perfectly contained name and still reads — and writes, and deletes —
out of the GRR root, on a local `file` protocol. Confirmed by execution.

Nothing here addresses that, and this ADR should not be read as claiming
otherwise. Nothing is going to: the gap was **accepted** in
[0013](0013-symlink-resolution-is-not-contained.md), on the ground that a
GRR resource is trusted by authorship and the escape is strictly weaker than
the code execution gain#572 already accepts by design.

Read 0013 before proposing a resolution check here. One was designed,
implemented, reviewed and abandoned, and it records why — including why
partial containment is worse than none.
