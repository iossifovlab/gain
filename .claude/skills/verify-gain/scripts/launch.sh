#!/usr/bin/env bash
# launch.sh [run id]
#
# Create .verify/<run id>/scratch/ and .verify/<run id>/evidence/ in the
# checkout and write scratch/grr.yaml: a directory GRR over
# test_fixtures/mini-GRR. Prints the run id on stdout (the last line).
# ~/.grr_definition.yaml is neither read nor changed.

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

run_id="${1:-$(date +%Y%m%dT%H%M%S)-$$}"
[[ "$run_id" =~ ^[A-Za-z0-9._-]+$ ]] \
    || vg_die "run id '$run_id' may hold only letters, digits, '.', '_' and '-'"
run_dir="$VG_VERIFY/$run_id"
[[ ! -e "$run_dir" ]] || vg_die "run $run_dir already exists; pick another run id"

mkdir -p "$run_dir/scratch" "$run_dir/evidence"
cat > "$run_dir/scratch/grr.yaml" <<YAML
id: mini
type: directory
directory: $VG_CHECKOUT/test_fixtures/mini-GRR
YAML

echo "verify-gain: launched run at $run_dir" >&2
echo "$run_id"
