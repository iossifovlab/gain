#!/usr/bin/env bash
# drive-annotate-vcf.sh <run id>
#
# Drive features/annotate-vcf.md: annotate a three-record VCF with the
# mini_pipeline pipeline through the checkout's .venv/bin/annotate_vcf,
# then read the output back with readback-annotate-vcf.sh.
#
# Evidence lands in .verify/<run id>/evidence/annotate-vcf/:
#   command.txt  stdout.txt  stderr.txt  exit_code.txt  input.vcf
#   output.vcf   readback.txt  readback_exit_code.txt
# Exits 0 only when annotate_vcf exits 0 and the read-back passes.

source "$(dirname "${BASH_SOURCE[0]}")/app.sh"

run_dir="$(verify_run_dir "${1:-}")"
scratch="$run_dir/scratch"
evidence="$run_dir/evidence/annotate-vcf"
[[ -f "$scratch/grr.yaml" ]] || verify_die "drive: $scratch/grr.yaml is missing (run launch.sh)"
[[ ! -e "$evidence" ]] || verify_die "drive: $evidence already exists; launch a new run"
mkdir -p "$evidence"

# Positions with distinct non-zero scores (chr1:1-5 scores 0, which would
# let an all-zero output pass by accident).
printf '%s\n' \
    '##fileformat=VCFv4.2' \
    '##contig=<ID=chr1>' \
    '##contig=<ID=chr2>' \
    $'#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO' \
    $'chr1\t6\t.\tA\tC\t.\t.\t.' \
    $'chr2\t3\t.\tA\tC\t.\t.\t.' \
    $'chr2\t8\t.\tA\tC\t.\t.\t.' \
    > "$scratch/in.vcf"
cp "$scratch/in.vcf" "$evidence/input.vcf"

verify_drive "$scratch" "$evidence" \
    "$APP_VENV_BIN/annotate_vcf" "$scratch/in.vcf" mini_pipeline \
    -g "$scratch/grr.yaml" -o "$scratch/out.vcf" -w "$scratch/work" -j 1
[[ -f "$scratch/out.vcf" ]] && cp "$scratch/out.vcf" "$evidence/output.vcf"
if [[ "$VERIFY_RC" -ne 0 ]]; then
    tail -n 20 "$evidence/stderr.txt" >&2 || true
    verify_die "drive: annotate_vcf exited $VERIFY_RC; evidence in $evidence; run doctor.sh"
fi
[[ -f "$evidence/output.vcf" ]] || verify_die "drive: annotate_vcf exited 0 but wrote no $scratch/out.vcf"

# Second, independent read of the mutation: the kept output file.
verify_readback "$evidence" "$VERIFY_SCRIPTS/readback-annotate-vcf.sh" "$evidence/output.vcf"
echo "drive: PASS: evidence in $evidence"
