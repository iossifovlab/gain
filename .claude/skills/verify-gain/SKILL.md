---
name: verify-gain
description: Drive the real gain command-line tools (annotate_tabular, grr_browse) from this checkout's .venv against the test_fixtures/mini-GRR directory GRR and keep the evidence (command, stdout, stderr, exit code, output file, read-back) under .verify/<run id>/evidence/. Use it to prove a gain CLI change works end to end, beyond unit tests, with no docker and no network.
---

# verify-gain

Runs the real `gain` CLI against a known GRR (`test_fixtures/mini-GRR`) and
keeps proof of the result. Every path below is relative to the checkout root
(the git worktree that holds this skill); every helper finds the checkout on
its own, so they can be called from any directory.

A run is one directory, `.verify/<run id>/`:

- `.verify/<run id>/scratch/` — the GRR definition, inputs, work dirs and a
  private `HOME`. Removed by Cleanup.
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

`launch.sh` writes `.verify/<run id>/scratch/grr.yaml`:

```yaml
id: mini
type: directory
directory: <absolute checkout path>/test_fixtures/mini-GRR
```

The drives pass this file as `GRR_DEFINITION_FILE` (or `-g`). They also set
`HOME` to `$RUN/scratch/home`, so `~/.grr_definition.yaml` is never read or
changed, and point `http(s)_proxy` at a closed local port, so any network
access fails instead of passing quietly.

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

## 4. Evidence

Each drive keeps, in `$RUN/evidence/<feature>/`:

| File | Content |
| --- | --- |
| `command.txt` | the exact command, with its working directory and environment |
| `stdout.txt` / `stderr.txt` | the CLI's output streams |
| `exit_code.txt` | the CLI's exit code |
| `input.tsv` | the input the drive fed in |
| `output.tsv` | the annotated output file |
| `readback.txt` / `readback_exit_code.txt` | the second, independent read of the output |

The read-back reads the mutation a second time, with a separate command over
the kept output file. For `annotate-tabular` it compares `pos_bw_0` with the
exact values `0.1`, `0.2`, `0.3` at `chr1:6`, `chr2:3`, `chr2:8`; an output
whose `pos_bw_0` column is all `0` fails it. Re-run it at any time:

```bash
.claude/skills/verify-gain/scripts/readback-annotate-tabular.sh "$RUN/evidence/annotate-tabular/output.tsv"
cat "$RUN/evidence/annotate-tabular/exit_code.txt"
```

Cite the evidence directory, not a pasted summary, as the proof.

## 5. Cleanup

Remove the scratch directory and nothing else:

```bash
.claude/skills/verify-gain/scripts/cleanup.sh "$RUN_ID"
ls "$RUN"                     # evidence only
git status --porcelain        # shows no run output
```

## 6. Helpers

All in `.claude/skills/verify-gain/scripts/`, all executable:

| Script | Invocation | Does |
| --- | --- | --- |
| `launch.sh` | `launch.sh [run id]` | creates `.verify/<run id>/{scratch,evidence}` and `scratch/grr.yaml`; prints the run id |
| `doctor.sh` | `doctor.sh <run id>` | read-only preflight (CLI on `PATH`, mini-GRR initialised, `grr_browse` lists `mini_pipeline`) |
| `drive-annotate-tabular.sh` | `drive-annotate-tabular.sh <run id>` | drives `annotate_tabular` + `mini_pipeline`, keeps the evidence, runs the read-back |
| `readback-annotate-tabular.sh` | `readback-annotate-tabular.sh <output.tsv>` | checks `pos_bw_0` = 0.1, 0.2, 0.3 at the drive's positions |
| `cleanup.sh` | `cleanup.sh <run id>` | removes `.verify/<run id>/scratch/` only |
| `common.sh` | sourced by the others | checkout discovery, run-id validation, the isolated environment (`HOME`, `GRR_DEFINITION_FILE`, closed proxy) |

A full run, from the checkout root:

```bash
S=.claude/skills/verify-gain/scripts
RUN_ID=$($S/launch.sh)
PATH="$PWD/.venv/bin:$PATH" $S/doctor.sh "$RUN_ID"
$S/drive-annotate-tabular.sh "$RUN_ID"
$S/cleanup.sh "$RUN_ID"
ls .verify/$RUN_ID/evidence/annotate-tabular
```
