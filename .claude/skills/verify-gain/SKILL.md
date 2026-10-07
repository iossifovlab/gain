---
name: verify-gain
description: Drive the real gain command-line tools (annotate_tabular, grr_browse, grr_cache_repo) from this checkout's .venv against test_fixtures/mini-GRR, as a directory GRR or as an HTTP GRR served by the run's own verify-gain-<run id> compose project, and keep the evidence (command, stdout, stderr, exit code, output file, read-back) under .verify/<run id>/evidence/. Use it to prove a gain CLI change works end to end, beyond unit tests, with no network and no shared container touched.
---

# verify-gain

Runs the real `gain` CLI against a known GRR (`test_fixtures/mini-GRR`) and
keeps proof of the result. Every path below is relative to the checkout root
(the git worktree that holds this skill); every helper finds the checkout on
its own, so they can be called from any directory.

A run is one directory, `.verify/<run id>/`:

- `.verify/<run id>/scratch/` — the GRR definitions, inputs, work dirs, a
  private `HOME` and `TMPDIR`, and for an HTTP run the served GRR copy, the
  compose override and the cache. Removed by Cleanup.
- `.verify/<run id>/evidence/` — one subdirectory per drive. Kept.

`.verify/` is git-ignored, so no run output is ever committed, and every
write stays inside the worktree.

Feature files live in `features/`; `features/README.md` holds the baseline
preconditions, the driving conventions, the proof rules and the feature
index.

## 1. Launch

Create the run and its GRR definition:

```bash
RUN_ID=$(.claude/skills/verify-gain/scripts/launch.sh)     # or: launch.sh my-run-id
RUN=.verify/$RUN_ID
cat "$RUN/scratch/grr.yaml"
```

A run id holds only lowercase letters, digits, `_` and `-`, and starts with
a letter or a digit (`^[a-z0-9][a-z0-9_-]*$`), because it also names the
compose project `verify-gain-<run id>`. `launch.sh` rejects `.`, `..` and
`Run1` and creates nothing. The default id is `<date>-<time>-<pid>`.

`launch.sh` writes `.verify/<run id>/scratch/grr.yaml`:

```yaml
id: mini
type: directory
directory: '<absolute checkout path>/test_fixtures/mini-GRR'
```

The drives pass this file as `GRR_DEFINITION_FILE` (or `-g`). They also set
`HOME` to `$RUN/scratch/home`, so `~/.grr_definition.yaml` is never read or
changed. The definition is what keeps a drive off the network: it names a
local directory GRR, so no repository request leaves the host. The scripts
also point `http(s)_proxy` at a closed local port, but that only catches
clients that honour the proxy environment (curl, urllib, requests). gain's
HTTP GRR path (fsspec `HTTPFileSystem` over aiohttp) ignores it, so a
definition that named an `http(s)` GRR would reach the network and the drive
would still pass. Keep a definition either a `type: directory` GRR or the
run's own `127.0.0.1` HTTP GRR below.

### HTTP GRR (features/http-grr.md)

For the HTTP features, start the run's own `httpd` after `launch.sh`:

```bash
.claude/skills/verify-gain/scripts/launch-http-grr.sh "$RUN_ID"
cat "$RUN/scratch/grr-http.yaml"
```

It does four steps:

1. Copies `test_fixtures/mini-GRR`, without its `.git` file, to
   `$RUN/scratch/http/grr`. The submodule is never written. It already
   commits `.CONTENTS*`, so no `grr_manage` step is needed.
2. Writes `$RUN/scratch/http/compose.override.yaml`. The override mounts
   the copy (an absolute path) as `htdocs`, read-only, and publishes port
   80 on an ephemeral port (`127.0.0.1::80`).
3. Runs `docker compose -f docker-compose.yaml -f <override>
   -p verify-gain-<run id> up -d --pull never apache`. The image must be
   local. It is never pulled.
4. Reads the host port with `docker compose ... port apache 80` and writes
   `$RUN/scratch/grr-http.yaml`:

```yaml
id: mini_http
type: url
url: 'http://127.0.0.1:<port>/'
cache_dir: '<absolute run path>/scratch/http/cache'
```

Every compose call (`up`, `port`, `ps`, `down`) goes through `vg_compose`
in `common.sh`, with the same `-f` files, project directory and `-p` name.
`docker-compose.override.yaml` and its fixed ports `28080` and `29000` are
never loaded. No helper stops, restarts or removes a container by name,
and no helper touches another compose project.

`VERIFY_GAIN_HTTPD_IMAGE=<image>` replaces `httpd:latest` in the override
and in the image check.

## 2. Doctor

Read-only. Run it before the first drive and again after any failed drive:

```bash
PATH="$PWD/.venv/bin:$PATH" .claude/skills/verify-gain/scripts/doctor.sh "$RUN_ID"
```

It fails (exit 1) with a message naming the fix when:

- the `annotate_tabular` or `grr_browse` on `PATH` is not this checkout's
  `.venv/bin/` one (on a workstation a conda env can shadow it). Fix:
  `export PATH="$PWD/.venv/bin:$PATH"`. If `.venv/bin/annotate_tabular` is
  missing, run `uv sync` first.
- `test_fixtures/mini-GRR/mini_pipeline/genomic_resource.yaml` is missing:
  the submodule is not initialised. Fix:
  `git submodule update --init test_fixtures/mini-GRR`.
- `grr_browse -g $RUN/scratch/grr.yaml` fails or does not list
  `mini_pipeline`.

For a run with an HTTP GRR, it also fails when:

- the httpd image is not local. Fix: `docker pull httpd:latest`. Doctor
  never pulls.
- the compose project `verify-gain-<run id>` does not own exactly one
  `apache` container (`docker compose -p verify-gain-<run id> ps -q apache`).
- `grr_browse -g $RUN/scratch/grr-http.yaml` fails or does not list
  `mini_pipeline` from `mini_http`. Doctor does not use a bare request for
  `.CONTENTS.json`: a GRR can ship only `.CONTENTS.json.gz`.

To prove the missing-image message without removing `httpd:latest`, name
a tag that is not local:

```bash
VERIFY_GAIN_HTTPD_IMAGE=httpd:verify-gain-no-such-tag PATH="$PWD/.venv/bin:$PATH" \
    .claude/skills/verify-gain/scripts/doctor.sh "$RUN_ID"    # exits 1, names docker pull
```

`VERIFY_GAIN_MINI_GRR=<dir>` makes check 2 look at `<dir>` instead of
`test_fixtures/mini-GRR`, to prove the empty-submodule message without
deinitialising the submodule:

```bash
mkdir -p "$RUN/scratch/empty-grr"
VERIFY_GAIN_MINI_GRR="$PWD/$RUN/scratch/empty-grr" PATH="$PWD/.venv/bin:$PATH" \
    .claude/skills/verify-gain/scripts/doctor.sh "$RUN_ID"    # exits 1
```

## 3. Drive

One recipe per feature file. Drives always call the checkout CLI as
`.venv/bin/<tool>`, never the bare name from `PATH`.

**`features/annotate-tabular.md`** — `annotate_tabular` with the
`mini_pipeline` pipeline:

```bash
.claude/skills/verify-gain/scripts/drive-annotate-tabular.sh "$RUN_ID"
```

which writes `$RUN/scratch/in.tsv`

```
chrom	pos
chr1	6
chr2	3
chr2	8
```

and runs, from `$RUN/scratch/` with `HOME=$RUN/scratch/home`:

```bash
GRR_DEFINITION_FILE=$RUN/scratch/grr.yaml .venv/bin/annotate_tabular \
    $RUN/scratch/in.tsv mini_pipeline -o $RUN/scratch/out.tsv -w $RUN/scratch/work -j 1
```

The drive exits 0 only when `annotate_tabular` exits 0 and the read-back
check passes. On a non-zero exit, run Doctor before anything else.

**`features/http-grr.md`** — `grr_browse` and `grr_cache_repo` against the
run's HTTP GRR (after `launch-http-grr.sh`):

```bash
.claude/skills/verify-gain/scripts/drive-grr-browse.sh "$RUN_ID"
.claude/skills/verify-gain/scripts/drive-grr-cache-repo.sh "$RUN_ID"
```

which run, from `$RUN/scratch/` in the isolated environment:

```bash
.venv/bin/grr_browse -g $RUN/scratch/grr-http.yaml
.venv/bin/grr_cache_repo -g $RUN/scratch/grr-http.yaml --no-progress mini_pipeline
```

Run `drive-grr-browse.sh` first: its read-back expects no cached file.

## 4. Evidence

Each drive keeps, in `$RUN/evidence/<feature>/`:

| File | Content |
| --- | --- |
| `command.txt` | the exact command, with its working directory and environment |
| `stdout.txt` / `stderr.txt` | the CLI's output streams |
| `exit_code.txt` | the CLI's exit code |
| `input.tsv` | `annotate-tabular`: the input the drive fed in |
| `output.tsv` | `annotate-tabular`: the annotated output file |
| `grr-http.yaml` | `grr-browse`, `grr-cache-repo`: the HTTP GRR definition, with the run's port |
| `contents.json` | `grr-browse`: the served `.CONTENTS.json` |
| `cached_files.txt` | `grr-cache-repo`: the files in `cache_dir` after the drive |
| `readback.txt` / `readback_exit_code.txt` | the second, independent read of the output |

The read-back reads the mutation a second time, with a separate command over
the kept output file. For `annotate-tabular` it compares `pos_bw_0` with the
exact values `0.1`, `0.2`, `0.3` at `chr1:6`, `chr2:3`, `chr2:8`; an output
whose `pos_bw_0` column is all `0` fails it. For `grr-browse` it compares
the listed resource ids with the ids in `contents.json`. For
`grr-cache-repo` it `cmp`s the cached `mini_pipeline.yaml`,
`genomic_resource.yaml` and `mini_positionscore_bw_0.bw` with the served
copy. Re-run the `annotate-tabular` and `grr-browse` read-backs at any time
(the `grr-cache-repo` one needs the scratch directory):

```bash
.claude/skills/verify-gain/scripts/readback-annotate-tabular.sh "$RUN/evidence/annotate-tabular/output.tsv"
cat "$RUN/evidence/annotate-tabular/exit_code.txt"
.claude/skills/verify-gain/scripts/readback-grr-browse.sh \
    "$RUN/evidence/grr-browse/stdout.txt" "$RUN/evidence/grr-browse/contents.json"
```

Cite the evidence directory, not a pasted summary, as the proof.

## 5. Cleanup

For an HTTP run, `cleanup.sh` first runs `docker compose -p
verify-gain-<run id> down` through `vg_compose`. That removes the
project's container and network, and nothing of another project. Then it
removes the scratch directory. It keeps the evidence directory:

```bash
.claude/skills/verify-gain/scripts/cleanup.sh "$RUN_ID"
ls "$RUN"                     # evidence only
git status --porcelain        # shows no run output
docker ps -a --filter label=com.docker.compose.project=verify-gain-$RUN_ID       # empty
docker network ls --filter label=com.docker.compose.project=verify-gain-$RUN_ID  # empty
```

## 6. Helpers

All in `.claude/skills/verify-gain/scripts/`, all executable:

| Script | Invocation | Does |
| --- | --- | --- |
| `launch.sh` | `launch.sh [run id]` | creates `.verify/<run id>/{scratch,evidence}` and `scratch/grr.yaml`; prints the run id |
| `launch-http-grr.sh` | `launch-http-grr.sh <run id>` | copies mini-GRR to scratch, starts `apache` in `verify-gain-<run id>` on an ephemeral port, writes `scratch/grr-http.yaml` |
| `doctor.sh` | `doctor.sh <run id>` | read-only preflight (CLI on `PATH`, mini-GRR initialised, `grr_browse` lists `mini_pipeline`; for an HTTP run also the image, the project's container and the HTTP definition) |
| `drive-annotate-tabular.sh` | `drive-annotate-tabular.sh <run id>` | drives `annotate_tabular` + `mini_pipeline`, keeps the evidence, runs the read-back |
| `readback-annotate-tabular.sh` | `readback-annotate-tabular.sh <output.tsv>` | checks `pos_bw_0` = 0.1, 0.2, 0.3 at the drive's positions |
| `drive-grr-browse.sh` | `drive-grr-browse.sh <run id>` | drives `grr_browse` against the HTTP GRR, keeps the evidence, runs the read-back |
| `readback-grr-browse.sh` | `readback-grr-browse.sh <stdout.txt> <contents.json>` | checks the listing holds exactly the `.CONTENTS.json` ids, from `mini_http`, none cached |
| `drive-grr-cache-repo.sh` | `drive-grr-cache-repo.sh <run id>` | drives `grr_cache_repo mini_pipeline` against the HTTP GRR, keeps the evidence, runs the read-back |
| `readback-grr-cache-repo.sh` | `readback-grr-cache-repo.sh <cache dir> <GRR copy>` | `cmp`s the cached `mini_pipeline` and `mini_positionscore_bw` files with the copy |
| `cleanup.sh` | `cleanup.sh <run id>` | `docker compose -p verify-gain-<run id> down` for an HTTP run, then removes `.verify/<run id>/scratch/` |
| `common.sh` | sourced by the others | checkout discovery, run-id validation, the isolated environment (`HOME`, `TMPDIR`, `GRR_DEFINITION_FILE`, a closed proxy for proxy-honouring clients only), `vg_compose` and the image check |

A full run, from the checkout root:

```bash
S=.claude/skills/verify-gain/scripts
RUN_ID=$($S/launch.sh)
PATH="$PWD/.venv/bin:$PATH" $S/doctor.sh "$RUN_ID"
$S/drive-annotate-tabular.sh "$RUN_ID"
$S/cleanup.sh "$RUN_ID"
ls .verify/$RUN_ID/evidence/annotate-tabular
```

A full HTTP run:

```bash
S=.claude/skills/verify-gain/scripts
RUN_ID=$($S/launch.sh)
$S/launch-http-grr.sh "$RUN_ID"
PATH="$PWD/.venv/bin:$PATH" $S/doctor.sh "$RUN_ID"
$S/drive-grr-browse.sh "$RUN_ID"
$S/drive-grr-cache-repo.sh "$RUN_ID"
$S/cleanup.sh "$RUN_ID"
ls .verify/$RUN_ID/evidence/{grr-browse,grr-cache-repo}
```
