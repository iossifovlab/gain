#!/usr/bin/env bash
# drive-binning-tool.sh <run id>
#
# Drive features/binning-tool.md: bin the mini_positionscore_bw score over
# mini_genome in bins of 5 bases with the checkout's
# .venv/bin/binning_tool, then open the HDF5 output a second time with
# readback-binning-tool.sh.
#
# Evidence lands in .verify/<run id>/evidence/binning-tool/:
#   command.txt  stdout.txt  stderr.txt  exit_code.txt  bin.yaml
#   bins.h5  work_dir_left.txt  readback.txt  readback_exit_code.txt
# Exits 0 only when binning_tool exits 0 and the read-back passes.

source "$(dirname "${BASH_SOURCE[0]}")/app.sh"

run_dir="$(verify_run_dir "${1:-}")"
scratch="$run_dir/scratch"
evidence="$run_dir/evidence/binning-tool"
[[ -f "$scratch/grr.yaml" ]] || verify_die "drive: $scratch/grr.yaml is missing (run launch.sh)"
[[ ! -e "$evidence" ]] || verify_die "drive: $evidence already exists; launch a new run"
mkdir -p "$evidence"

cat > "$scratch/bin.yaml" <<'YAML'
input_reference_genome: mini_genome
bins:
  bin_size: 5
binners:
- position_score_binner:
    resource_query: mini_positionscore_bw
YAML
cp "$scratch/bin.yaml" "$evidence/bin.yaml"

# binning_tool writes the HDF5 file beside the run definition and its work
# directory beside the output by default. Pass both inside scratch.
verify_drive "$scratch" "$evidence" \
    "$APP_VENV_BIN/binning_tool" "$scratch/bin.yaml" -g "$scratch/grr.yaml" \
    -o "$scratch/bins.h5" -w "$scratch/bin-work" -j 1
[[ -f "$scratch/bins.h5" ]] && cp "$scratch/bins.h5" "$evidence/bins.h5"
# A work directory the tool created is removed on success.
if [[ -e "$scratch/bin-work" ]]; then
    echo "left: $scratch/bin-work" > "$evidence/work_dir_left.txt"
else
    echo "removed: $scratch/bin-work" > "$evidence/work_dir_left.txt"
fi
if [[ "$VERIFY_RC" -ne 0 ]]; then
    tail -n 20 "$evidence/stderr.txt" >&2 || true
    verify_die "drive: binning_tool exited $VERIFY_RC; evidence in $evidence; run doctor.sh"
fi
[[ -f "$evidence/bins.h5" ]] || verify_die "drive: binning_tool exited 0 but wrote no $scratch/bins.h5"

# Second, independent read of the mutation: the kept HDF5 file.
verify_readback "$evidence" "$VERIFY_SCRIPTS/readback-binning-tool.sh" "$evidence/bins.h5"
echo "drive: PASS: evidence in $evidence"
