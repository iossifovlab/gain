#!/usr/bin/env bash
# readback-annotate-vcf.sh <output.vcf>
#
# Read an annotate_vcf output back:
#   - the header declares the attribute: a line that starts with
#     ##INFO=<ID=pos_bw_0,
#   - the records carry pos_bw_0 with the exact strings the
#     mini_positionscore_bw bedGraph gives at the drive's positions:
#       chr1:6 -> 0.1   chr2:3 -> 0.2   chr2:8 -> 0.3
# annotate_vcf declares pos_bw_0 as Type=String, so the values are
# compared as strings. Exits 0 on a match, 1 otherwise.

set -euo pipefail

out="${1:-}"
[[ -n "$out" ]] || { echo "readback: missing <output.vcf> argument" >&2; exit 1; }
[[ -f "$out" ]] || { echo "readback: FAIL: $out does not exist" >&2; exit 1; }

fail=0
header="$(grep -m1 '^##INFO=<ID=pos_bw_0,' "$out" || true)"
if [[ -z "$header" ]]; then
    echo "readback: FAIL: $out has no ##INFO=<ID=pos_bw_0, header line"
    fail=1
else
    echo "readback: header: $header"
fi

expected=$'chr1\t6\t0.1\nchr2\t3\t0.2\nchr2\t8\t0.3'
# One line per record: chrom, pos and the value of the pos_bw_0 INFO
# field ("-" when the record has none).
actual="$(awk -F'\t' '
    /^#/ { next }
    {
        v = "-"
        n = split($8, kv, ";")
        for (i = 1; i <= n; i++)
            if (index(kv[i], "pos_bw_0=") == 1) v = substr(kv[i], 10)
        print $1 "\t" $2 "\t" v
    }
' "$out")"
if [[ "$actual" != "$expected" ]]; then
    echo "readback: FAIL: pos_bw_0 in the records of $out does not match"
    echo "--- expected (chrom pos pos_bw_0)"
    printf '%s\n' "$expected"
    echo "--- actual"
    printf '%s\n' "$actual"
    fail=1
fi
[[ "$fail" -eq 0 ]] || exit 1
echo "readback: PASS: pos_bw_0 = 0.1, 0.2, 0.3 at chr1:6, chr2:3, chr2:8 in $out"
