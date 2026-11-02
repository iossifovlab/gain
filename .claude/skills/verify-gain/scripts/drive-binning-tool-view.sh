#!/usr/bin/env bash
# drive-binning-tool-view.sh <run id>
#
# Drive features/binning-tool-view.md. First make an HDF5 file of our own:
# bin the mini_positionscore_bw score over mini_genome in bins of 5 bases
# with the checkout's .venv/bin/binning_tool (the run definition of
# drive-binning-tool.sh). Then run .venv/bin/binning_tool_view on that file
# once for each case, each step with its own evidence subdirectory:
#   binning-tool   binning_tool bin.yaml -o scratch/view-bins.h5 ...
#   tracks         binning_tool_view --tracks
#   bins           binning_tool_view --bins
#   joined         binning_tool_view --bins --matrix
#   region         binning_tool_view --bins --matrix --region chr2
#   separators     binning_tool_view --bins --region chr1:1_0-2,0
#   index-header   binning_tool_view --bins --matrix --header index
#   start-below-1  binning_tool_view --bins --region chr1:0-5   (exits 2)
#   axes-differ    binning_tool_view --tracks --bins            (exits 2)
# and reads the cases back with readback-binning-tool-view.sh. A case that
# exits 2 is an expected result, not a drive failure: the read-back judges
# every exit code.
#
# Evidence lands in .verify/<run id>/evidence/binning-tool-view/:
#   <step>/{command.txt,stdout.txt,stderr.txt,exit_code.txt}
#   bin.yaml  view-bins.h5  readback.txt  readback_exit_code.txt
# Exits 0 only when binning_tool exits 0 and the read-back passes.

source "$(dirname "${BASH_SOURCE[0]}")/app.sh"

run_dir="$(verify_run_dir "${1:-}")"
scratch="$run_dir/scratch"
evidence="$run_dir/evidence/binning-tool-view"
h5="$scratch/view-bins.h5"
[[ -f "$scratch/grr.yaml" ]] || verify_die "drive: $scratch/grr.yaml is missing (run launch.sh)"
[[ ! -e "$evidence" ]] || verify_die "drive: $evidence already exists; launch a new run"
[[ ! -e "$h5" ]] || verify_die "drive: $h5 already exists; launch a new run"
mkdir -p "$evidence"

cat > "$scratch/view-bin.yaml" <<'YAML'
reference_genome: mini_genome
bins:
  bin_size: 5
binners:
- position_score_binner:
    resource_query: mini_positionscore_bw
YAML
cp "$scratch/view-bin.yaml" "$evidence/bin.yaml"

step() {
    local name="$1"
    shift
    mkdir -p "$evidence/$name"
    verify_drive "$scratch" "$evidence/$name" "$@"
    echo "drive: $name exited $VERIFY_RC"
}

# The HDF5 file of this drive. binning_tool writes the output beside the
# run definition and its work directory beside the output by default.
# Pass both inside scratch.
step binning-tool \
    "$APP_VENV_BIN/binning_tool" "$scratch/view-bin.yaml" -g "$scratch/grr.yaml" \
    -o "$h5" -w "$scratch/view-bin-work" -j 1
if [[ "$VERIFY_RC" -ne 0 ]]; then
    tail -n 20 "$evidence/binning-tool/stderr.txt" >&2 || true
    verify_die "drive: binning_tool exited $VERIFY_RC; evidence in $evidence/binning-tool; run doctor.sh"
fi
[[ -f "$h5" ]] || verify_die "drive: binning_tool exited 0 but wrote no $h5"
cp "$h5" "$evidence/view-bins.h5"

view="$APP_VENV_BIN/binning_tool_view"
step tracks        "$view" "$h5" --tracks
step bins          "$view" "$h5" --bins
step joined        "$view" "$h5" --bins --matrix
step region        "$view" "$h5" --bins --matrix --region chr2
step separators    "$view" "$h5" --bins --region chr1:1_0-2,0
step index-header  "$view" "$h5" --bins --matrix --header index
step start-below-1 "$view" "$h5" --bins --region chr1:0-5
step axes-differ   "$view" "$h5" --tracks --bins

# Second, independent read: the kept stdout, stderr and exit code of each
# case against the exact expected text.
verify_readback "$evidence" "$VERIFY_SCRIPTS/readback-binning-tool-view.sh" "$evidence"
echo "drive: PASS: evidence in $evidence"
