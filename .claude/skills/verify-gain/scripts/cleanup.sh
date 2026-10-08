#!/usr/bin/env bash
# cleanup.sh <run id>
#
# Remove what the run started and keep its evidence:
#   1. For a run with an HTTP GRR, `docker compose -p verify-gain-<run id>
#      down` (through verify_compose, with the run's own -f files). It removes
#      that project's containers and network only. No container is stopped
#      by name. Before `down`, app_check_project_owned proves that every
#      container of the project runs this run's override and copy; if one
#      does not, cleanup refuses and keeps scratch.
#   2. Remove .verify/<run id>/scratch/.
# The evidence directory .verify/<run id>/evidence/ is kept.

source "$(dirname "${BASH_SOURCE[0]}")/app.sh"

run_dir="$(verify_run_dir "${1:-}")"
project="$(verify_compose_project "$run_dir")"
if [[ -f "$(app_compose_override "$run_dir")" ]]; then
    # Act only on a project this run started: another checkout can use the
    # same run id, and so the same project name.
    ( app_check_project_owned "$run_dir" ) \
        || verify_die "cleanup: compose project $project is not this run's; no down, scratch kept"
    verify_compose "$run_dir" down --remove-orphans >&2 \
        || verify_die "cleanup: docker compose -p $project down failed; scratch kept"
    left="$(docker ps -aq --filter "label=com.docker.compose.project=$project")"
    [[ -z "$left" ]] || verify_die "cleanup: containers of $project remain: $left; scratch kept"
    echo "cleanup: removed compose project $project"
fi
rm -rf -- "$run_dir/scratch"
echo "cleanup: removed $run_dir/scratch; evidence kept in $run_dir/evidence"
