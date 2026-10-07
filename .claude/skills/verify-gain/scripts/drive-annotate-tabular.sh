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

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

run_dir="$(vg_run_dir "${1:-}")"
scratch="$run_dir/scratch"
evidence="$run_dir/evidence/annotate-tabular"
[[ -f "$scratch/grr.yaml" ]] || vg_die "drive: $scratch/grr.yaml is missing (run launch.sh)"
[[ ! -e "$evidence" ]] || vg_die "drive: $evidence already exists; launch a new run"
mkdir -p "$evidence"

# Positions with distinct non-zero scores (chr1:1-5 scores 0, which would
# let an all-zero output pass by accident).
printf 'chrom\tpos\nchr1\t6\nchr2\t3\nchr2\t8\n' > "$scratch/in.tsv"
cp "$scratch/in.tsv" "$evidence/input.tsv"

cmd=("$VG_VENV_BIN/annotate_tabular" "$scratch/in.tsv" mini_pipeline
     -o "$scratch/out.tsv" -w "$scratch/work" -j 1)
vg_isolated_env "$scratch"
{
    printf 'cd %q &&\n' "$scratch"
    printf 'env'
    printf ' %q' "${VG_ENV[@]}"
    printf ' \\\n'
    printf '%q ' "${cmd[@]}"
    printf '\n'
} > "$evidence/command.txt"

rc=0
(cd "$scratch" && vg_isolated "$scratch" "${cmd[@]}") \
    > "$evidence/stdout.txt" 2> "$evidence/stderr.txt" || rc=$?
echo "$rc" > "$evidence/exit_code.txt"
[[ -f "$scratch/out.tsv" ]] && cp "$scratch/out.tsv" "$evidence/output.tsv"

if [[ "$rc" -ne 0 ]]; then
    tail -n 20 "$evidence/stderr.txt" >&2 || true
    vg_die "drive: annotate_tabular exited $rc; evidence in $evidence; run doctor.sh"
fi
[[ -f "$evidence/output.tsv" ]] || vg_die "drive: annotate_tabular exited 0 but wrote no $scratch/out.tsv"

# Second, independent read of the mutation: the kept output file.
rb=0
"$VG_SCRIPTS/readback-annotate-tabular.sh" "$evidence/output.tsv" \
    > "$evidence/readback.txt" 2>&1 || rb=$?
echo "$rb" > "$evidence/readback_exit_code.txt"
cat "$evidence/readback.txt"
[[ "$rb" -eq 0 ]] || vg_die "drive: read-back failed; evidence in $evidence"
echo "drive: PASS: evidence in $evidence"
