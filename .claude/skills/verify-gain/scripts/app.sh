# shellcheck shell=bash
# The gain code of the verify-gain scripts. Sourced, not executed. The
# launch, drive, doctor and cleanup scripts source this file only; the
# readback-*.sh scripts are standalone. It sources verify-lib.sh, the copy
# of the canonical contract code (do not edit that copy).

VERIFY_APP=gain
# shellcheck source=verify-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/verify-lib.sh"

# shellcheck disable=SC2034  # read by the scripts that source app.sh
APP_VENV_BIN="$VERIFY_CHECKOUT/.venv/bin"

# app_env <scratch dir>: called by verify_isolated_env. Append the gain
# entries to VERIFY_ENV, so a drive and its command.txt use the same ones:
#   - MPLCONFIGDIR inside the scratch home;
#   - GRR_DEFINITION_FILE pointing at the run's directory-GRR definition
#     (HOME is inside scratch too, so ~/.grr_definition.yaml is never
#     read). A drive of the HTTP GRR passes -g with the run's HTTP
#     definition, which names the run's own httpd on 127.0.0.1.
#   - the http(s)_proxy variables, pointing at a closed local port.
#
# The proxy variables are only a backstop for clients that honour the
# proxy environment (curl, urllib, requests). They do NOT cover gain's
# HTTP GRR path: fsspec's HTTPFileSystem runs on aiohttp without trust_env
# and ignores these variables. Do not rely on them to make network use
# fail; keep each definition a directory GRR or the run's own 127.0.0.1
# HTTP GRR.
app_env() {
    local scratch="$1"
    VERIFY_ENV+=(
        MPLCONFIGDIR="$scratch/home/.config/matplotlib"
        GRR_DEFINITION_FILE="$scratch/grr.yaml"
        http_proxy=http://127.0.0.1:9 https_proxy=http://127.0.0.1:9
        HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9
        no_proxy= NO_PROXY=
    )
}

# --- The run-scoped HTTP GRR (launch-http-grr.sh) ---------------------------

# The compose files of verify_compose, relative to the checkout. Only
# docker-compose.yaml: never docker-compose.override.yaml with its fixed
# host ports.
# shellcheck disable=SC2034  # read by verify_compose in verify-lib.sh
VERIFY_COMPOSE_FILES=(docker-compose.yaml)

# The httpd image the apache service runs. VERIFY_GAIN_HTTPD_IMAGE overrides
# it, so the missing-image failure of doctor.sh can be proved without
# removing httpd:latest. The image is never pulled: `up` runs --pull never.
APP_HTTPD_IMAGE="${VERIFY_GAIN_HTTPD_IMAGE:-httpd:latest}"

# app_http_dir <run dir> -> prints the scratch directory of the HTTP GRR.
app_http_dir() {
    echo "$1/scratch/http"
}

# app_compose_override <run dir> -> prints the run's scratch compose
# override file (written by launch-http-grr.sh). verify_compose reads it.
app_compose_override() {
    echo "$(app_http_dir "$1")/compose.override.yaml"
}

# app_check_project_owned <run dir>: fails unless every container of the
# run's compose project was started by this run. The project label alone
# proves nothing (docker compose -p P ps already filters on it). The
# check reads two things that only this run has:
#   - the com.docker.compose.project.config_files label names this run's
#     scratch override file;
#   - the htdocs bind mount source is this run's scratch copy of mini-GRR.
# A project with no containers passes.
app_check_project_owned() {
    local run_dir="$1" project override grr_copy ids id files source
    project="$(verify_compose_project "$run_dir")"
    override="$(app_compose_override "$run_dir")"
    grr_copy="$(cd "$(app_http_dir "$run_dir")/grr" 2> /dev/null && pwd -P)" \
        || verify_die "FAIL: $(app_http_dir "$run_dir")/grr is missing; cannot prove that $project is this run's"
    ids="$(verify_project_containers "$run_dir")" \
        || verify_die "FAIL: docker ps failed for compose project $project"
    for id in $ids; do
        files="$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.project.config_files" }}' "$id")" \
            || verify_die "FAIL: docker inspect $id failed"
        [[ ",$files," == *",$override,"* ]] \
            || verify_die "FAIL: container $id of $project was not started from this run's $override (config_files: $files).
    Another checkout uses this run id. Do not act on $project from this run."
        source="$(docker inspect -f '{{ range .Mounts }}{{ if eq .Destination "/usr/local/apache2/htdocs" }}{{ .Source }}{{ end }}{{ end }}' "$id")" \
            || verify_die "FAIL: docker inspect $id failed"
        [[ "$source" == "$grr_copy" ]] \
            || verify_die "FAIL: container $id of $project serves '$source', not this run's $grr_copy.
    Another checkout uses this run id. Do not act on $project from this run."
    done
}

# app_check_httpd_image: fails, naming the fix, unless the httpd image is
# local. It never pulls.
app_check_httpd_image() {
    command -v docker > /dev/null \
        || verify_die "FAIL: docker is not on PATH; the HTTP GRR needs docker compose"
    if ! docker image inspect "$APP_HTTPD_IMAGE" > /dev/null 2>&1; then
        verify_die "FAIL: the image $APP_HTTPD_IMAGE is not local, and verify-gain never pulls it (up --pull never).
    Fix: docker pull $APP_HTTPD_IMAGE"
    fi
}
