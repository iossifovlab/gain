# annotate-tabular

`annotate_tabular` annotates a tab-separated file of variants or positions
with an annotation pipeline from a GRR.

## Sub-features

- Positional `input` (a TSV with a header row) and `pipeline` (a resource id
  in the GRR, here `mini_pipeline`, or a pipeline file).
- `-o OUTPUT`: the annotated TSV; the input columns plus one column per
  pipeline attribute.
- `-w WORK_DIR`: the task-graph work directory; removed on success unless
  `--keep-work-dir`.
- `-j JOBS`: parallel jobs.
- `-g GRR_FILENAME` or `GRR_DEFINITION_FILE`: the GRR definition.
- Column detection: `chrom` and `pos` columns are found by name by default
  (`--col-chrom`, `--col-pos` override them).
- `mini_pipeline` has one annotator, `position_score: mini_positionscore_bw`,
  which adds the `pos_bw_0` column.

## How to get to it (user POV)

A user with a TSV of positions and a GRR definition runs:

```bash
GRR_DEFINITION_FILE=grr.yaml annotate_tabular in.tsv mini_pipeline -o out.tsv -w work -j 1
```

and opens `out.tsv`, which carries a `pos_bw_0` column next to `chrom` and
`pos`.

## Driving it with verify-gain

```bash
S=.claude/skills/verify-gain/scripts
RUN_ID=$($S/launch.sh)
PATH="$PWD/.venv/bin:$PATH" $S/doctor.sh "$RUN_ID"
$S/drive-annotate-tabular.sh "$RUN_ID"
$S/cleanup.sh "$RUN_ID"
```

Input (`evidence/annotate-tabular/input.tsv`) and the expected output
(`evidence/annotate-tabular/output.tsv`):

| chrom | pos | pos_bw_0 |
| --- | --- | --- |
| chr1 | 6 | 0.1 |
| chr2 | 3 | 0.2 |
| chr2 | 8 | 0.3 |

Pass: `exit_code.txt` is `0` and
`scripts/readback-annotate-tabular.sh evidence/annotate-tabular/output.tsv`
exits 0.

## Gotchas

- **Zero-valued positions.** The score source is a 0-based bedGraph:
  `chr1 0 5 0`, `chr1 5 10 0.1`, `chr2 0 5 0.2`, `chr2 5 10 0.3`. At 1-based
  `chr1:1`..`chr1:5` the score is `0`, so an all-zero output would pass a
  check that used those positions. The drive uses `chr1:6`, `chr2:3`,
  `chr2:8` and the read-back compares exact values.
- **CLI on `PATH`.** On a workstation, `annotate_tabular` on `PATH` can come
  from a conda env, not from the checkout. Doctor rejects that; the drive
  calls `.venv/bin/annotate_tabular` regardless.
- **Empty submodule.** A fresh worktree has an empty
  `test_fixtures/mini-GRR`, and every drive then fails. Doctor names the fix:
  `git submodule update --init test_fixtures/mini-GRR`.
- **`~/.grr_definition.yaml`.** Without `GRR_DEFINITION_FILE` or `-g`, the
  CLI falls back to the user's default GRR (possibly remote). The drive sets
  both `GRR_DEFINITION_FILE` and a scratch `HOME`.
- **Pipeline id vs file.** The `pipeline` argument is read as a file path
  first (`load_pipeline_from_file_or_resource`) and as a GRR resource id only
  when no such path exists. A file or directory named `mini_pipeline` in the
  working directory would win; the drive runs from the scratch directory,
  which holds none.
