# 3. Resource file names are contained by construction

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

A name is contained when it is relative and contains no `..` segment.
Rejection raises `ValueError` naming both the resource and the name; it never
skips, clamps or normalises the name into something safe.

`ManifestEntry.__post_init__` applies the same rule at manifest-parse time.
That is defence in depth, not the load-bearing check — it just puts the
diagnostic next to the poisoned input.

### Why the rule is shaped this way

- **Normalise-then-compare, not "no separators".** Nested names are ordinary
  here: every resource's statistics live under `statistics/`. A rule that
  rejected any name containing a separator would break the repository.
- **`..` is rejected even when it stays inside** (`sub/../other.txt`). The
  joined url is handed to fsspec *unnormalised*, and an object store treats
  `..` as a literal key segment instead of resolving it, so such a name
  addresses one object on `file` and a different one on `s3`/`http`. There is
  no legitimate use for it, and rejecting keeps the behaviour uniform across
  schemes.
- **The check is url-shaped, not `os.path`-shaped.** The name is tested both
  as written and percent-decoded, because an http(s) server decodes the path
  before resolving it — `%2e%2e` is a traversal there and a literal name on a
  local filesystem. One decoding pass is the right depth: `%252e%252e`
  decodes to the literal text `%2e%2e`, which no server resolves further.
- **Backslash counts as a separator** when scanning for `..`, since it is one
  on Windows and in several fsspec backends. Only `..` segments are rejected,
  so a stray backslash inside an ordinary name still works.

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
