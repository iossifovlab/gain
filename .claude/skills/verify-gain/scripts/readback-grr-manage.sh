#!/usr/bin/env bash
# readback-grr-manage.sh <evidence dir>
#
# Read the evidence of drive-grr-manage.sh back:
#   - 01-resource-manifest-dry exits 1 and names the added file;
#   - 02-repo-manifest-dry exits non-zero, names the added file, and its
#     exit code equals the number of resources it reports as stale
#     (grr_manage -n exits with that count);
#   - 03-repo-manifest exits 0;
#   - 04-repo-manifest-dry-again exits 0 and reports no stale resource;
#   - manifest.txt (the manifest of the resource after step 03) lists the
#     added file;
#   - 05-list (grr_manage list) lists exactly the ids of resources.txt;
#   - 06-grr-browse lists exactly the ids of resources.txt, all from the
#     repository mini_copy.
# Exits 0 when every check passes, 1 otherwise.

set -euo pipefail

ev="${1:-}"
[[ -n "$ev" && -f "$ev/added_file.txt" ]] \
    || { echo "readback: usage: readback-grr-manage.sh <evidence dir of grr-manage>" >&2; exit 1; }

read -r resource added < "$ev/added_file.txt"
fail=0
bad() { echo "readback: FAIL: $*"; fail=1; }
rc() { cat "$ev/$1/exit_code.txt" 2> /dev/null || echo missing; }
# names_added <stderr file>: true when a "should be updated" line of the
# resource names the added file. The line lists every stale entry of the
# resource, so it can name other files too.
names_added() {
    grep -F "manifest of <$resource> should be updated; entries to update in manifest [" "$1" 2> /dev/null \
        | grep -qF "'$added'"
}
# rows <stdout file>: the resource rows of a listing. A row reads
# <type> <version> <files> <size> <unit> <repository id> <resource id>;
# grr_browse prints the GRR definition above the rows.
rows() {
    awk '$1 ~ /^[a-z_]+$/ && $2 ~ /^[0-9.]+$/ && $3 ~ /^[0-9]+$/ && NF >= 6' "$1" 2> /dev/null
}

# 01: the resource-scoped dry run.
r="$(rc 01-resource-manifest-dry)"
echo "readback: 01-resource-manifest-dry exit $r"
[[ "$r" == 1 ]] || bad "resource-manifest -n exited $r, not 1"
names_added "$ev/01-resource-manifest-dry/stderr.txt" \
    || bad "resource-manifest -n does not name $added in $resource"

# 02: the repository dry run.
r="$(rc 02-repo-manifest-dry)"
stale="$(grep -c 'should be updated; entries to update in manifest' \
    "$ev/02-repo-manifest-dry/stderr.txt" 2> /dev/null || true)"
echo "readback: 02-repo-manifest-dry exit $r, ${stale:-0} stale resources reported"
[[ "$r" =~ ^[0-9]+$ && "$r" -ne 0 ]] || bad "repo-manifest -n exited $r, not non-zero"
[[ "$r" == "${stale:-0}" ]] || bad "repo-manifest -n exited $r, but reported ${stale:-0} stale resources"
names_added "$ev/02-repo-manifest-dry/stderr.txt" \
    || bad "repo-manifest -n does not name $added in $resource"

# 03: the real run.
r="$(rc 03-repo-manifest)"
echo "readback: 03-repo-manifest exit $r"
[[ "$r" == 0 ]] || bad "repo-manifest exited $r, not 0"
grep -qx "  name: $added" "$ev/manifest.txt" 2> /dev/null \
    || grep -qx -- "- name: $added" "$ev/manifest.txt" 2> /dev/null \
    || bad "the manifest of $resource after repo-manifest does not list $added"

# 04: the second dry run.
r="$(rc 04-repo-manifest-dry-again)"
echo "readback: 04-repo-manifest-dry-again exit $r"
[[ "$r" == 0 ]] || bad "the second repo-manifest -n exited $r, not 0"
if grep -q 'should be updated' "$ev/04-repo-manifest-dry-again/stderr.txt" 2> /dev/null; then
    bad "the second repo-manifest -n still reports a stale resource"
fi

# 05 and 06: the listings against the resources of the copy.
expected="$(sort "$ev/resources.txt")"
n="$(wc -l < "$ev/resources.txt")"
r="$(rc 05-list)"
listed="$(rows "$ev/05-list/stdout.txt" | awk '{ print $NF }' | sort)"
echo "readback: 05-list exit $r, $(grep -c . <<<"$listed") of $n resources listed"
[[ "$r" == 0 ]] || bad "grr_manage list exited $r, not 0"
if [[ "$listed" != "$expected" ]]; then
    bad "grr_manage list does not list exactly the resources of the copy"
    diff <(echo "$expected") <(echo "$listed") || true
fi

r="$(rc 06-grr-browse)"
browsed="$(rows "$ev/06-grr-browse/stdout.txt" | awk '$(NF - 1) == "mini_copy" { print $NF }' | sort)"
other="$(rows "$ev/06-grr-browse/stdout.txt" | awk '$(NF - 1) != "mini_copy"')"
echo "readback: 06-grr-browse exit $r, $(grep -c . <<<"$browsed") of $n resources listed from mini_copy"
[[ "$r" == 0 ]] || bad "grr_browse exited $r, not 0"
[[ -z "$other" ]] || bad "grr_browse lists a row that is not from mini_copy: $other"
if [[ "$browsed" != "$expected" ]]; then
    bad "grr_browse does not list exactly the resources of the copy"
    diff <(echo "$expected") <(echo "$browsed") || true
fi

[[ "$fail" -eq 0 ]] || exit 1
echo "readback: PASS: -n found $added in $resource (exit 1), repo-manifest fixed it (exit 0), a second -n exits 0, grr_manage list and grr_browse list the $n resources of the copy"
