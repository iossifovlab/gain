#!/usr/bin/env bash
# launch.sh [run id]
#
# Create .verify/<run id>/scratch/ and .verify/<run id>/evidence/ in the
# checkout and write scratch/grr.yaml: a directory GRR over
# test_fixtures/mini-GRR. Prints the run id on stdout (the last line).
# ~/.grr_definition.yaml is neither read nor changed.

source "$(dirname "${BASH_SOURCE[0]}")/app.sh"

run_id="${1-$(date +%Y%m%d-%H%M%S)-$$}"
verify_check_run_id "$run_id"
run_dir="$VERIFY_ROOT/$run_id"
[[ ! -e "$run_dir" ]] || verify_die "run $run_dir already exists; pick another run id"

mkdir -p "$run_dir/scratch" "$run_dir/evidence"
# A YAML single-quoted scalar (embedded ' doubled), so a checkout path
# holding ': ', ' #' or a leading indicator character stays one value.
grr_dir="$VERIFY_CHECKOUT/test_fixtures/mini-GRR"
q="'"
cat > "$run_dir/scratch/grr.yaml" <<YAML
id: mini
type: directory
directory: '${grr_dir//$q/$q$q}'
YAML

echo "verify-gain: launched run at $run_dir" >&2
echo "$run_id"
