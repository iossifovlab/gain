# annotate-vcf

`annotate_vcf` annotates the records of a VCF file with an annotation
pipeline from a GRR, and writes each pipeline attribute as an `INFO` field.

## Sub-features

- Positional `input` (a VCF; `.gz` and `.bgz` inputs are read compressed)
  and `pipeline` (a resource id in the GRR, here `mini_pipeline`, or a
  pipeline file).
- `-o OUTPUT`: the annotated VCF. The header gains one `##INFO` line per
  pipeline attribute, and each record gains the attribute in its `INFO`
  column.
- `-w WORK_DIR`: the task-graph work directory; removed on success unless
  `--keep-work-dir`.
- `-j JOBS`: parallel jobs.
- `-g GRR_FILENAME` or `GRR_DEFINITION_FILE`: the GRR definition.
- `mini_pipeline` has one annotator, `position_score: mini_positionscore_bw`,
  which adds the `pos_bw_0` attribute.

## How to get to it (user POV)

A user with a VCF and a GRR definition runs:

```bash
annotate_vcf in.vcf mini_pipeline -g grr.yaml -o out.vcf -w work -j 1
```

and opens `out.vcf`. The header holds
`##INFO=<ID=pos_bw_0,Number=A,Type=String,...>`, and each record carries
`pos_bw_0=<value>` in its `INFO` column.

## Driving it with verify-gain

```bash
S=.claude/skills/verify-gain/scripts
RUN_ID=$($S/launch.sh)
PATH="$PWD/.venv/bin:$PATH" $S/doctor.sh "$RUN_ID"
$S/drive-annotate-vcf.sh "$RUN_ID"
$S/cleanup.sh "$RUN_ID"
```

The input (`evidence/annotate-vcf/input.vcf`) has the header lines
`##contig=<ID=chr1>` and `##contig=<ID=chr2>` and three records, each
`A>C`. The expected output (`evidence/annotate-vcf/output.vcf`):

| CHROM | POS | INFO |
| --- | --- | --- |
| chr1 | 6 | `pos_bw_0=0.1` |
| chr2 | 3 | `pos_bw_0=0.2` |
| chr2 | 8 | `pos_bw_0=0.3` |

Pass: `exit_code.txt` is `0` and
`scripts/readback-annotate-vcf.sh evidence/annotate-vcf/output.vcf` exits
0. The read-back finds the `##INFO=<ID=pos_bw_0,` header line and the
three values.

## Gotchas

- **String attribute.** `annotate_vcf` declares `pos_bw_0` as
  `Type=String`, and writes the values as `0.1`, `0.2` and `0.3`. The
  read-back compares them as strings, and checks the header line too.
- **Zero-valued positions.** At `chr1:1`..`chr1:5` the score is `0`, so an
  all-zero output would pass a check that used those positions. The drive
  uses `chr1:6`, `chr2:3` and `chr2:8`.
- **Compressed output.** An `-o` name that ends in `.gz` or `.bgz` writes a
  bgzip-compressed, tabix-indexed VCF. The read-back reads plain text, so
  the drive writes `out.vcf`.
- **`-n` and `-R`.** In `annotate_vcf`, `-n` is `--dry-run` (no output
  file), and `-R` names a reference genome resource. In `grr_manage`, `-R`
  names the repository. Do not copy a `grr_manage` option to
  `annotate_vcf`.
- **Warnings on stderr.** `mini_genome` sets the retired `chrom_prefix` key,
  so each run writes a deprecation warning to stderr. An empty stderr is
  not a pass condition.
- **Temporary files.** The task graph writes `gain-annotation-work-*`
  under `TMPDIR`. The isolated environment sets `TMPDIR` to `scratch/tmp`,
  so nothing lands in `/tmp`.
