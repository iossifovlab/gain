#!/usr/bin/env bash
# readback-binning-tool-view.sh <evidence dir>
#
# Read the evidence of drive-binning-tool-view.sh back. For each case in
# <evidence dir>/<case>/, compare the kept exit_code.txt with the expected
# exit code and the kept stdout.txt with the exact expected text:
#   tracks         exit 0, the /tracks header and its one row
#   bins           exit 0, the four bins
#   joined         exit 0, the four bins and their values
#   region         exit 0, the joined header and the two chr2 rows
#   separators     exit 0, only chr1 6-10 (chr1:1_0-2,0 reads as 10-20)
#   index-header   exit 0, the joined rows under the header "0"
#   start-below-1  exit 2, empty stdout, stderr names the start
#   axes-differ    exit 2, empty stdout, stderr names the axes
# /values is float64 and holds the float32 values of the bigWig, and
# binning_tool_view prints each value with repr, so the text is the same
# on each run: the comparison is exact, not within a tolerance.
# Exits 0 when every check passes, 1 otherwise.

set -euo pipefail

ev="${1:-}"
[[ -n "$ev" && -d "$ev/tracks" ]] \
    || { echo "readback: usage: readback-binning-tool-view.sh <evidence dir of binning-tool-view>" >&2; exit 1; }

fail=0
bad() { echo "readback: FAIL: $*"; fail=1; }
rc() { cat "$ev/$1/exit_code.txt" 2> /dev/null || echo missing; }

label=mini_positionscore_bw:mean
bins_header=$'chrom\tstart\tend'
v1=$'chr1\t1\t5\t0.0'
v2=$'chr1\t6\t10\t0.10000000149011612'
v3=$'chr2\t1\t5\t0.20000000298023224'
v4=$'chr2\t6\t10\t0.30000001192092896'

# expect <case> <exit code> <line>...: the case exits with the code, and
# its stdout is exactly the lines, each ended by a newline.
expect() {
    local name="$1" want_rc="$2" got want
    shift 2
    got="$(rc "$name")"
    echo "readback: $name exit $got"
    [[ "$got" == "$want_rc" ]] || bad "$name exited $got, not $want_rc"
    if (($#)); then
        want="$(printf '%s\n' "$@")"
    else
        want=""
    fi
    if [[ ! -f "$ev/$name/stdout.txt" ]]; then
        bad "$name has no stdout.txt"
    elif ! cmp -s <(printf '%s' "$want${want:+$'\n'}") "$ev/$name/stdout.txt"; then
        bad "$name stdout differs from the expected text"
        diff <(printf '%s' "$want${want:+$'\n'}") "$ev/$name/stdout.txt" || true
    fi
}

# expect_error <case> <message>: the case exits 2, prints nothing on
# stdout, and its stderr has the message.
expect_error() {
    local name="$1" message="$2"
    expect "$name" 2
    grep -qF -- "$message" "$ev/$name/stderr.txt" 2> /dev/null \
        || bad "$name stderr does not have '$message'"
}

expect tracks 0 \
    $'label\tbinner_type\tbinner_index\ttrack_index\tresource_ids\tselector\tbinner_def' \
    "$label"$'\tposition_score\t0\t0\tmini_positionscore_bw\tpos_bw_0\t{"aggregator":"mean","name":null,"none_value_replacement":null}'
expect bins 0 \
    "$bins_header" $'chr1\t1\t5' $'chr1\t6\t10' $'chr2\t1\t5' $'chr2\t6\t10'
expect joined 0 "$bins_header"$'\t'"$label" "$v1" "$v2" "$v3" "$v4"
expect region 0 "$bins_header"$'\t'"$label" "$v3" "$v4"
expect separators 0 "$bins_header" $'chr1\t6\t10'
expect index-header 0 "$bins_header"$'\t0' "$v1" "$v2" "$v3" "$v4"
expect_error start-below-1 "the start must be 1 or more"
expect_error axes-differ "the axes differ"

[[ "$fail" -eq 0 ]] || exit 1
echo "readback: PASS: the 6 exit-0 cases print the exact expected text, the 2 exit-2 cases name their error, in $ev"
