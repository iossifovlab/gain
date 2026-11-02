# binning-tool-view

`binning_tool_view` prints the `/tracks`, `/bins` and `/values` tables of a
`binning_tool` HDF5 file as tab-separated text.

## Sub-features

- Positional `file`: the HDF5 file that `binning_tool` wrote.
- `--tracks`: the `/tracks` table, one row per track, headed `label
  binner_type binner_index track_index resource_ids selector binner_def`.
- `--bins`: the `/bins` table, headed `chrom start end`.
- `--matrix`: the `/values` matrix, one column per track. With `--bins`,
  one joined table: the bin columns, then one column per track.
- `--region REGION`: only the bins that overlap `REGION`: `chrom`,
  `chrom:start` or `chrom:start-end`, 1-based inclusive. Repeatable. `_`
  and `,` in a number are separators: `chr1:1_0-2,0` reads as `chr1:10-20`.
- `--header {label,index}`: head the matrix columns by track label (the
  default) or by track position.
- `--no-header`: no header line.
- `--track LABEL`: only the track with this exact label. Repeatable. Not
  driven: see the gotchas.
- Usage errors exit 2 with a `binning_tool_view: error:` line on stderr:
  a start below 1, or `--tracks` with `--bins` or `--matrix` (the axes
  differ).

## How to get to it (user POV)

A user bins a score with `binning_tool` (see `binning-tool.md`) and then
reads the output:

```bash
binning_tool bin.yaml -g grr.yaml -o bins.h5 -w bin-work -j 1
binning_tool_view bins.h5 --tracks
binning_tool_view bins.h5 --bins --matrix
binning_tool_view bins.h5 --bins --matrix --region chr2
```

## Driving it with verify-gain

```bash
S=.claude/skills/verify-gain/scripts
RUN_ID=$($S/launch.sh)
PATH="$PWD/.venv/bin:$PATH" $S/doctor.sh "$RUN_ID"
$S/drive-binning-tool-view.sh "$RUN_ID"
$S/cleanup.sh "$RUN_ID"
```

The drive makes its own HDF5 file. It runs `.venv/bin/binning_tool` with
the run definition of `drive-binning-tool.sh` (`mini_positionscore_bw` over
`mini_genome`, `bin_size: 5`), with `-o` and `-w` inside scratch. Then it
runs `.venv/bin/binning_tool_view` on that file once for each case below.
Each step keeps `command.txt`, `stdout.txt`, `stderr.txt` and
`exit_code.txt` in its own subdirectory of `evidence/binning-tool-view/`.
The evidence directory also keeps `bin.yaml` and the HDF5 file
`view-bins.h5`.

The expected values. Columns are separated by one tab:

| Case | Arguments | Exit | Expected output |
| --- | --- | --- | --- |
| `tracks` | `--tracks` | 0 | header `label binner_type binner_index track_index resource_ids selector binner_def`, and one row `mini_positionscore_bw:mean position_score 0 0 mini_positionscore_bw pos_bw_0 {"aggregator":"mean","name":null,"none_value_replacement":null}` |
| `bins` | `--bins` | 0 | header `chrom start end`, then `chr1 1 5`, `chr1 6 10`, `chr2 1 5`, `chr2 6 10` |
| `joined` | `--bins --matrix` | 0 | header `chrom start end mini_positionscore_bw:mean`, then the four bins with the values `0.0`, `0.10000000149011612`, `0.20000000298023224`, `0.30000001192092896` |
| `region` | `--bins --matrix --region chr2` | 0 | the header, then only the two `chr2` rows |
| `separators` | `--bins --region chr1:1_0-2,0` | 0 | header `chrom start end`, then only `chr1 6 10` (the region reads as 10 to 20) |
| `index-header` | `--bins --matrix --header index` | 0 | header `chrom start end 0`, then the same four rows as `joined` |
| `start-below-1` | `--bins --region chr1:0-5` | 2 | empty stdout, stderr has `the start must be 1 or more` |
| `axes-differ` | `--tracks --bins` | 2 | empty stdout, stderr has `the axes differ` |

Pass: the drive prints `drive: PASS`, and
`scripts/readback-binning-tool-view.sh evidence/binning-tool-view` exits 0.
The read-back compares the kept stdout of each case with the exact text,
and the kept exit code with the expected code. A case that exits 2 is an
expected result, not a drive failure.

## Gotchas

- **Exact strings.** `/values` is float64 and holds the float32 values of
  the bigWig, and `binning_tool_view` prints each value with `repr`. So
  the text is the same on each run, and the read-back compares strings
  exactly, not within a tolerance. A value `0.1` in place of
  `0.10000000149011612` points at a changed value type or format.
- **`--track` is not driven.** The fixture has one track only, so
  `--track` cannot show that it drops other tracks. A multi-track fixture
  is out of scope.
- **Its own HDF5 file.** The drive does not read the evidence of
  `binning-tool`, so each drive passes alone in a fresh run.
- **A zero bin.** The `chr1 1 5` bin is `0.0`, the same as a broken run
  could give. The other three bins differ from `0` and from each other.
- **Warnings on stderr.** `mini_genome` sets the retired `chrom_prefix` key,
  so the `binning-tool` step writes a deprecation warning to stderr. The
  read-back checks stderr only for the two exit-2 cases.
- **An old `.venv`.** A `.venv` from before the tool was added has no
  `.venv/bin/binning_tool_view`. Doctor names that file. Fix: `uv sync`.
