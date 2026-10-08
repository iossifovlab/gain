#!/usr/bin/env bash
# drive-annotate-tabular.sh <run id>
#
# Drive features/annotate-tabular.md: annotate three positions with the
# mini_pipeline pipeline through the checkout's .venv/bin/annotate_tabular,
# then read the output back with readback-annotate-tabular.sh.
#
# Evidence lands in .verify/<run id>/evidence/annotate-tabular/:
#   command.txt  stdout.txt  stderr.txt  exit_code.txt  input.tsv
#   output.tsv   readback.txt  readback_exit_code.txt
# Exits 0 only when annotate_tabular exits 0 and the read-back passes.

source "$(dirname "${BASH_SOURCE[0]}")/app.sh"

run_dir="$(verify_run_dir "${1:-}")"
scratch="$run_dir/scratch"
evidence="$run_dir/evidence/annotate-tabular"
[[ -f "$scratch/grr.yaml" ]] || verify_die "drive: $scratch/grr.yaml is missing (run launch.sh)"
[[ ! -e "$evidence" ]] || verify_die "drive: $evidence already exists; launch a new run"
mkdir -p "$evidence"

# Positions with distinct non-zero scores (chr1:1-5 scores 0, which would
# let an all-zero output pass by accident).
printf 'chrom\tpos\nchr1\t6\nchr2\t3\nchr2\t8\n' > "$scratch/in.tsv"
cp "$scratch/in.tsv" "$evidence/input.tsv"

verify_drive "$scratch" "$evidence" \
    "$APP_VENV_BIN/annotate_tabular" "$scratch/in.tsv" mini_pipeline \
    -o "$scratch/out.tsv" -w "$scratch/work" -j 1
[[ -f "$scratch/out.tsv" ]] && cp "$scratch/out.tsv" "$evidence/output.tsv"
if [[ "$VERIFY_RC" -ne 0 ]]; then
    tail -n 20 "$evidence/stderr.txt" >&2 || true
    verify_die "drive: annotate_tabular exited $VERIFY_RC; evidence in $evidence; run doctor.sh"
fi
[[ -f "$evidence/output.tsv" ]] || verify_die "drive: annotate_tabular exited 0 but wrote no $scratch/out.tsv"

# Second, independent read of the mutation: the kept output file.
verify_readback "$evidence" "$VERIFY_SCRIPTS/readback-annotate-tabular.sh" "$evidence/output.tsv"
echo "drive: PASS: evidence in $evidence"
