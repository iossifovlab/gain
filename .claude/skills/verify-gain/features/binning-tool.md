# binning-tool

`binning_tool` bins position and fragment scores from a GRR into a fixed
grid over a reference genome, and writes one HDF5 file with a
bins x tracks matrix.

## Sub-features

- Positional `run_definition`: a YAML file with `input_reference_genome`,
  `bins` (here `bin_size: 5`) and a list of `binners` (here one
  `position_score_binner` over `mini_positionscore_bw`).
- `-o OUTPUT`: the HDF5 file. Default: the run definition with an `.h5`
  suffix, beside it.
- `-w WORK_DIR`: the per-chunk intermediate files. Default: a sibling of
  the output. A work directory that the tool created is removed on success
  unless `--keep-work-dir`.
- `-j JOBS`: parallel jobs.
- `-g GRR_FILENAME`: the GRR definition.
- The HDF5 file holds the datasets `bins` (`chrom`, `start`, `end`),
  `tracks` (one row per track: `name`, `aggregator` and more) and `values`
  (bins x tracks), and the file attribute `coordinates`.

## How to get to it (user POV)

A user writes `bin.yaml`:

```yaml
input_reference_genome: mini_genome
bins:
  bin_size: 5
binners:
- position_score_binner:
    resource_query: mini_positionscore_bw
```

and runs:

```bash
binning_tool bin.yaml -g grr.yaml -o bins.h5 -w bin-work -j 1
python -c 'import h5py; f = h5py.File("bins.h5"); print(f["bins"][()], f["values"][()])'
```

## Driving it with verify-gain

```bash
S=.claude/skills/verify-gain/scripts
RUN_ID=$($S/launch.sh)
PATH="$PWD/.venv/bin:$PATH" $S/doctor.sh "$RUN_ID"
$S/drive-binning-tool.sh "$RUN_ID"
$S/cleanup.sh "$RUN_ID"
```

The drive keeps `bin.yaml`, the output `bins.h5` and
`work_dir_left.txt` (whether the work directory is gone) in
`evidence/binning-tool/`. The expected content of `bins.h5`:

| bin | `values[:, 0]` |
| --- | --- |
| chr1 1-5 | 0 |
| chr1 6-10 | 0.1 |
| chr2 1-5 | 0.2 |
| chr2 6-10 | 0.3 |

`coordinates` is `1-based-inclusive`, and `tracks` holds one track,
`mini_positionscore_bw`, with the aggregator `mean`.

Pass: `exit_code.txt` is `0` and
`scripts/readback-binning-tool.sh evidence/binning-tool/bins.h5` exits 0.
The read-back opens the file with `h5py` from the checkout `.venv`.

## Gotchas

- **Float32 values.** The bigWig stores float32, so `values` holds
  `0.30000001`, not `0.3`. The read-back compares each value within `1e-6`.
- **Output location.** Without `-o`, the HDF5 file lands beside the run
  definition. Without `-w`, the work directory lands beside the output. The
  drive passes both inside scratch.
- **A zero bin.** The score source is `0` over `chr1:1-5`, so that bin is
  `0`, the same as a broken run could give. The other three bins are `0.1`, `0.2` and `0.3`, so an all-zero output
  fails the read-back.
- **Warnings on stderr.** `mini_genome` sets the retired `chrom_prefix` key,
  so each run writes a deprecation warning to stderr. An empty stderr is
  not a pass condition.
- **`h5py`.** The read-back needs `h5py` in the checkout `.venv`. Doctor
  checks that `.venv/bin/python` imports it.
