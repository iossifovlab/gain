#!/usr/bin/env bash
# cleanup.sh <run id>
#
# Remove .verify/<run id>/scratch/ and nothing else; the evidence directory
# .verify/<run id>/evidence/ is kept.

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

run_dir="$(vg_run_dir "${1:-}")"
rm -rf -- "$run_dir/scratch"
echo "cleanup: removed $run_dir/scratch; evidence kept in $run_dir/evidence"
