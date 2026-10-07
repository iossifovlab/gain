#!/usr/bin/env bash
# readback-grr-browse.sh <grr_browse stdout> <.CONTENTS.json>
#
# Compare a grr_browse listing of the mini_http GRR with the resources the
# served copy publishes in its .CONTENTS.json:
#   - the listing holds exactly the resource ids of .CONTENTS.json, each
#     once, all from the repository mini_http;
#   - no resource is cached yet (each row reads 0/<files>);
#   - mini_pipeline and mini_positionscore_bw are listed.
# Exits 0 on a match, 1 otherwise.

set -euo pipefail

listing="${1:-}"
contents="${2:-}"
[[ -f "$listing" && -f "$contents" ]] \
    || { echo "readback: usage: readback-grr-browse.sh <stdout.txt> <.CONTENTS.json>" >&2; exit 1; }

# Rows: <type> <version> <cached>/<files> <size> <unit> mini_http <id>.
# The cached count and the file count can be split by padding ("0/ 7").
rows="$(sed -nE 's/^[a-z_]+ +[0-9.]+ +([0-9]+)\/ *([0-9]+) +.* mini_http ([^ ]+)$/\1 \2 \3/p' "$listing")"
listed="$(awk '{ print $3 }' <<<"$rows" | sort)"
expected="$(python3 -I -c '
import json, sys
for entry in json.load(open(sys.argv[1])):
    print(entry["id"])
' "$contents" | sort)"

fail=0
if [[ -z "$listed" || "$listed" != "$expected" ]]; then
    echo "readback: FAIL: the listed resource ids differ from .CONTENTS.json"
    diff <(echo "$expected") <(echo "$listed") || true
    fail=1
fi
if [[ -n "$(uniq -d <<<"$listed")" ]]; then
    echo "readback: FAIL: a resource id is listed twice"
    fail=1
fi
if awk '$1 != 0 { bad = 1 } END { exit !bad }' <<<"$rows"; then
    echo "readback: FAIL: a resource is cached before grr_cache_repo ran"
    awk '$1 != 0' <<<"$rows"
    fail=1
fi
for id in mini_pipeline mini_positionscore_bw; do
    grep -qx "$id" <<<"$listed" || { echo "readback: FAIL: $id is not listed"; fail=1; }
done
[[ "$fail" -eq 0 ]] || exit 1
echo "readback: PASS: grr_browse lists the $(wc -l <<<"$listed") resources of .CONTENTS.json from mini_http, 0 cached"
