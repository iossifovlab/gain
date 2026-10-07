# grr-manage

`grr_manage` maintains a directory GRR: it writes the manifest of each
resource and the repository index, and it checks that the manifests match
the files on disk. `grr_browse` then reads the GRR through a definition.

## Sub-features

- `-R REPOSITORY`: the repository, an absolute directory. Without `-R`,
  `grr_manage` looks for `.CONTENTS.json.gz` from the working directory up.
- `repo-manifest`: updates the `.MANIFEST` of every resource and the
  repository index (`.CONTENTS.json.gz`).
- `resource-manifest -r <resource id>`: the same for one resource.
- `-n` (`--dry-run`): writes nothing. It prints one `manifest of <id>
  should be updated; entries to update in manifest [...]` warning per stale
  resource, and exits with the number of stale resources (0 when all
  manifests are current).
- `list`: one row per resource: type, version, file count, size,
  repository id (`manage`) and resource id.
- `grr_browse -g <definition>`: the same resources, through a `type:
  directory` definition over the repository.

## How to get to it (user POV)

A user who added a file to a resource of a directory GRR runs:

```bash
grr_manage repo-manifest -n -R /abs/path/grr    # exits 1, names the file
grr_manage repo-manifest -R /abs/path/grr       # exits 0, updates .MANIFEST
grr_manage repo-manifest -n -R /abs/path/grr    # exits 0, prints nothing
grr_manage list -R /abs/path/grr
grr_browse -g grr-copy.yaml
```

where `grr-copy.yaml` is:

```yaml
id: mini_copy
type: directory
directory: '/abs/path/grr'
```

## Driving it with verify-gain

```bash
S=.claude/skills/verify-gain/scripts
RUN_ID=$($S/launch.sh)
PATH="$PWD/.venv/bin:$PATH" $S/doctor.sh "$RUN_ID"
$S/drive-grr-manage.sh "$RUN_ID"
$S/cleanup.sh "$RUN_ID"
```

The drive copies `test_fixtures/mini-GRR`, without its `.git` file, to
`scratch/grr-copy`. It adds `verify_gain_added.txt` to the `mini_vcf_plot`
resource of the copy. Then it runs six steps, each with its own evidence
subdirectory in `evidence/grr-manage/`:

| Step | Command | Expected |
| --- | --- | --- |
| `01-resource-manifest-dry` | `grr_manage resource-manifest -n -R <copy> -r mini_vcf_plot` | exit 1, names `verify_gain_added.txt` |
| `02-repo-manifest-dry` | `grr_manage repo-manifest -n -R <copy>` | exit 1 (the number of stale resources), names `verify_gain_added.txt` |
| `03-repo-manifest` | `grr_manage repo-manifest -R <copy>` | exit 0 |
| `04-repo-manifest-dry-again` | `grr_manage repo-manifest -n -R <copy>` | exit 0, no stale resource |
| `05-list` | `grr_manage list -R <copy>` | the 26 resources of the copy |
| `06-grr-browse` | `grr_browse -g scratch/grr-copy.yaml` | the 26 resources of the copy, from `mini_copy` |

The evidence also keeps `added_file.txt`, `grr-copy.yaml`,
`resources.txt` (each directory of the copy with a
`genomic_resource.yaml`) and `manifest.txt` (the `.MANIFEST` of
`mini_vcf_plot` after step 03).

Pass: `readback_exit_code.txt` is `0`. Re-run the read-back at any time:

```bash
.claude/skills/verify-gain/scripts/readback-grr-manage.sh "$RUN/evidence/grr-manage"
```

## Gotchas

- **Stale manifest in mini-GRR.** The committed `.MANIFEST` of
  `mini_vcf_plot` does not list `customplot.py`. So `repo-manifest` on the
  submodule itself writes into the submodule. The drive runs `grr_manage`
  only on the scratch copy.
- **Do not depend on that drift.** A later fix of the mini-GRR manifest
  would make the first `-n` exit 0. The drive adds its own file, and the
  read-back checks that the warning names that file.
- **Exit code is a count.** `-n` exits with the number of stale resources,
  not with 1. The drive adds its file to `mini_vcf_plot`, the resource that
  already has the drift, so the count is 1 with or without the drift. The
  read-back checks that the exit code of step 02 equals the number of
  stale-resource warnings.
- **Absolute `-R`.** `grr_manage -R grr-copy` fails with `ValueError: not
  an absolute directory name`. The drive passes the absolute path.
- **The warning lists all stale entries.** The warning for `mini_vcf_plot`
  reads `['customplot.py', 'verify_gain_added.txt']`. The read-back looks
  for the added file in the list, not for the whole line.
- **Files that `repo-manifest` adds.** A real `repo-manifest` writes a
  `.grr` directory in the copy and in each resource, and it warns that the
  old `.CONTENTS.json` is stale. Neither changes the result.
- **`grr_browse` header.** `grr_browse` prints the GRR definition above the
  rows. The read-back counts only the resource rows.
