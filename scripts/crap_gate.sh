#!/usr/bin/env bash
# Fails a branch build on a CRAP score above 10.0 in a changed file (#2123).
#
# Usage: crap_gate.sh <list file> <crapper root>
#
# The core stage of the root Jenkinsfile runs this script in the core CI
# image, after the whole-package crapper call. The Prepare workspace
# stage writes the list file: one repo-relative path per line, from
# `git diff --name-only` between the merge base with origin/master and
# HEAD. The crapper root is the core project directory, the --root of
# the whole-package call, where the LCOV report already is.
#
# Steps:
#   1. The filter keeps a path only when it is a .py file under
#      core/gain that still exists. It drops _version.py, conftest.py
#      and the test files test_*.py and *_test.py, because crapper
#      scores each file that a caller names.
#   2. Each kept path goes to crapper as an absolute path under the
#      crapper root. crapper turns a path that does not exist under
#      --root into a substring filter.
#   3. crapper runs with --threshold 10. It compares the score rounded
#      to 4 decimals and exits 2 when the worst score is above 10.
#   4. On exit 2, the gate line names each function above 10.0 with
#      its namespace and score, from the .metrics/crap.edn that this
#      call writes under the crapper root. The table prints 1 decimal,
#      so a score of 10.036 shows there as 10.0.
#
# The gate prints one line that starts with "crap gate: ". The crapper
# table goes to stderr.
#
# Exit codes:
#   0  the gate passed, or it skipped. The gate skips when the list file
#      is missing, which means that the fetch or the merge base failed.
#      With no kept path, crapper does not run, because crapper with no
#      path scores the whole package. A crapper exit other than 0 and 2
#      is a tool fault, not a CRAP finding, so the gate passes.
#   1  a function in a changed file has a CRAP score above 10.0.
set -u

list_file="$1"
crap_root="$2"

if [ ! -f "$list_file" ]; then
    echo "crap gate: skipped, no list of changed files at $list_file"
    exit 0
fi

changed=()
while IFS= read -r path; do
    case "$path" in
        core/gain/*.py) ;;
        *) continue ;;
    esac
    case "${path##*/}" in
        _version.py | conftest.py | test_*.py | *_test.py) continue ;;
    esac
    source_file="$crap_root/${path#core/}"
    if [ -f "$source_file" ]; then
        changed+=("$source_file")
    fi
done < "$list_file"

if [ "${#changed[@]}" -eq 0 ]; then
    echo "crap gate: pass, 0 changed files"
    exit 0
fi

crapper --use-existing-coverage --root "$crap_root" --threshold 10 \
    "${changed[@]}" >&2
crapper_exit=$?

case "$crapper_exit" in
    0)
        echo "crap gate: pass, ${#changed[@]} changed files"
        exit 0
        ;;
    2) ;;
    *)
        echo "crap gate: pass, crapper exited $crapper_exit, a tool fault"
        exit 0
        ;;
esac

# Each row of crap.edn is one map:
#   {:name "f", :namespace "gain.m", :complexity 4, :coverage 50.0, :crap 10.036}
above=$(LC_ALL=C sed -n \
    's/.*:name "\([^"]*\)", :namespace "\([^"]*\)",.*:crap \([0-9.]*\)}.*/\3 \2 \1/p' \
    "$crap_root/.metrics/crap.edn" \
    | LC_ALL=C awk '$1 > 10' | LC_ALL=C sort -rn)
count=$(printf '%s\n' "$above" | grep -c .)
names=$(printf '%s\n' "$above" \
    | LC_ALL=C awk 'NF { printf "%s%s %s %s", sep, $2, $3, $1; sep = ", " }')
echo "crap gate: fail, ${#changed[@]} changed files," \
    "$count functions above 10.0: $names"
exit 1
