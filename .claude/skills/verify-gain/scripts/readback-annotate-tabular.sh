#!/usr/bin/env bash
# readback-annotate-tabular.sh <output.tsv>
#
# Read an annotate_tabular output back and compare pos_bw_0 with the exact
# values the mini_positionscore_bw bedGraph gives at the drive's positions:
#   chr1:6 -> 0.1   chr2:3 -> 0.2   chr2:8 -> 0.3
# Exits 0 on an exact match, 1 otherwise (an all-zero column fails).

set -euo pipefail

out="${1:-}"
[[ -n "$out" ]] || { echo "readback: missing <output.tsv> argument" >&2; exit 1; }
[[ -f "$out" ]] || { echo "readback: FAIL: $out does not exist" >&2; exit 1; }

expected=$'chr1\t6\t0.1\nchr2\t3\t0.2\nchr2\t8\t0.3'
actual="$(awk -F'\t' '
    NR == 1 {
        for (i = 1; i <= NF; i++) col[$i] = i
        if (!("chrom" in col) || !("pos" in col) || !("pos_bw_0" in col)) {
            print "missing chrom/pos/pos_bw_0 header: " $0 > "/dev/stderr"
            exit 2
        }
        next
    }
    { print $col["chrom"] "\t" $col["pos"] "\t" $col["pos_bw_0"] }
' "$out")" || { echo "readback: FAIL: cannot parse $out" >&2; exit 1; }

if [[ "$actual" != "$expected" ]]; then
    echo "readback: FAIL: pos_bw_0 in $out does not match"
    echo "--- expected (chrom pos pos_bw_0)"
    printf '%s\n' "$expected"
    echo "--- actual"
    printf '%s\n' "$actual"
    exit 1
fi
echo "readback: PASS: pos_bw_0 = 0.1, 0.2, 0.3 at chr1:6, chr2:3, chr2:8 in $out"
