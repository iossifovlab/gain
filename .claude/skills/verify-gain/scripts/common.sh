# shellcheck shell=bash
# Shared helpers for the verify-gain scripts. Sourced, not executed.

set -euo pipefail

VG_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# The checkout is the git worktree that holds this skill.
VG_CHECKOUT="$(git -C "$VG_SCRIPTS" rev-parse --show-toplevel)"
VG_VENV_BIN="$VG_CHECKOUT/.venv/bin"
VG_VERIFY="$VG_CHECKOUT/.verify"

vg_die() {
    echo "verify-gain: $*" >&2
    exit 1
}

# vg_check_run_id <run id>: fails unless the id is a safe directory name
# and a valid docker compose project suffix. Compose takes only lowercase
# letters, digits, '-' and '_' in a project name. The leading character
# rules out '.', '..', '-x' and '_x'.
vg_check_run_id() {
    [[ "$1" =~ ^[a-z0-9][a-z0-9_-]*$ ]] \
        || vg_die "run id '$1' is not valid: use lowercase letters, digits, '_' and '-', and start with a letter or a digit (it names the compose project verify-gain-<run id>)"
}

# vg_run_dir <run id> -> prints .verify/<run id>, fails if it is not a run.
vg_run_dir() {
    local run_id="${1:-}"
    [[ -n "$run_id" ]] || vg_die "missing <run id> argument"
    vg_check_run_id "$run_id"
    local run_dir="$VG_VERIFY/$run_id"
    [[ -d "$run_dir/evidence" ]] \
        || vg_die "no run at $run_dir (run launch.sh first)"
    echo "$run_dir"
}

# vg_isolated_env <scratch dir>: set VG_ENV to the NAME=value assignments
# vg_isolated runs a command under. The one list both vg_isolated and the
# command.txt evidence read, so the recorded command replays the same run.
vg_isolated_env() {
    local scratch="$1"
    VG_ENV=(
        HOME="$scratch/home"
        MPLCONFIGDIR="$scratch/home/.config/matplotlib"
        XDG_CACHE_HOME="$scratch/home/.cache"
        XDG_CONFIG_HOME="$scratch/home/.config"
        TMPDIR="$scratch/tmp"
        GRR_DEFINITION_FILE="$scratch/grr.yaml"
        http_proxy=http://127.0.0.1:9 https_proxy=http://127.0.0.1:9
        HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9
        no_proxy= NO_PROXY=
    )
}

# vg_isolated <scratch dir> <cmd...>: run a command with HOME inside the
# scratch directory (so ~/.grr_definition.yaml is never read and nothing is
# written under the real home) and GRR_DEFINITION_FILE pointing at the run's
# directory-GRR definition. TMPDIR is inside the scratch directory too:
# grr_cache_repo and the task graph write gain-annotation-work-* there,
# not in /tmp. A drive of the HTTP GRR passes -g with the run's HTTP
# definition, which names the run's own httpd on 127.0.0.1.
#
# The http(s)_proxy variables point at a closed local port. This is only a
# backstop for clients that honour the proxy environment (curl, urllib,
# requests). It does NOT cover gain's HTTP GRR path: fsspec's HTTPFileSystem
# runs on aiohttp without trust_env and ignores these variables. Do not rely
# on it to make network use fail; keep each definition a directory GRR or
# the run's own 127.0.0.1 HTTP GRR.
vg_isolated() {
    local scratch="$1"
    shift
    mkdir -p "$scratch/home" "$scratch/tmp"
    vg_isolated_env "$scratch"
    env "${VG_ENV[@]}" "$@"
}

# --- The run-scoped HTTP GRR (launch-http-grr.sh) ---------------------------

# The httpd image the apache service runs. VERIFY_GAIN_HTTPD_IMAGE overrides
# it, so the missing-image failure of doctor.sh can be proved without
# removing httpd:latest. The image is never pulled: `up` runs --pull never.
VG_HTTPD_IMAGE="${VERIFY_GAIN_HTTPD_IMAGE:-httpd:latest}"

# vg_http_dir <run dir> -> prints the scratch directory of the HTTP GRR.
vg_http_dir() {
    echo "$1/scratch/http"
}

# vg_compose_override <run dir> -> prints the run's scratch compose
# override file (written by launch-http-grr.sh).
vg_compose_override() {
    echo "$(vg_http_dir "$1")/compose.override.yaml"
}

# vg_compose_project <run dir> -> prints verify-gain-<run id>.
vg_compose_project() {
    echo "verify-gain-$(basename "$1")"
}

# vg_compose <run dir> <compose args...>: the one place that builds the
# docker compose arguments. Every call (up, port, ps, down) passes the same
# -f files, the same project directory and the same -p name, so no call
# can reach another compose project or load docker-compose.override.yaml
# with its fixed host ports.
vg_compose() {
    local run_dir="$1"
    shift
    local override
    override="$(vg_compose_override "$run_dir")"
    [[ -f "$override" ]] \
        || vg_die "$override is missing (run launch-http-grr.sh)"
    docker compose \
        --project-directory "$VG_CHECKOUT" \
        -f "$VG_CHECKOUT/docker-compose.yaml" \
        -f "$override" \
        -p "$(vg_compose_project "$run_dir")" \
        "$@"
}

# vg_project_containers <run dir> -> prints the IDs of every container,
# running or not, that carries the label of the run's compose project.
vg_project_containers() {
    docker ps -aq --filter "label=com.docker.compose.project=$(vg_compose_project "$1")"
}

# vg_check_project_free <run dir>: fails unless no container carries the
# label of the run's compose project. The project name is global to the
# host, but the run id is only unique in one checkout: another worktree,
# or a leftover of a run whose .verify/ was deleted, can use the same name.
# A second `up` on that name would recreate the other run's container.
vg_check_project_free() {
    local project ids
    project="$(vg_compose_project "$1")"
    ids="$(vg_project_containers "$1")" \
        || vg_die "FAIL: docker ps failed for compose project $project"
    [[ -z "$ids" ]] || vg_die "FAIL: compose project $project already has containers: $(echo $ids).
    Another checkout or an old run uses this run id. Launch a new run with another run id."
}

# vg_check_project_owned <run dir>: fails unless every container of the
# run's compose project was started by this run. The project label alone
# proves nothing (docker compose -p P ps already filters on it). The
# check reads two things that only this run has:
#   - the com.docker.compose.project.config_files label names this run's
#     scratch override file;
#   - the htdocs bind mount source is this run's scratch copy of mini-GRR.
# A project with no containers passes.
vg_check_project_owned() {
    local run_dir="$1" project override grr_copy ids id files source
    project="$(vg_compose_project "$run_dir")"
    override="$(vg_compose_override "$run_dir")"
    grr_copy="$(cd "$(vg_http_dir "$run_dir")/grr" 2> /dev/null && pwd -P)" \
        || vg_die "FAIL: $(vg_http_dir "$run_dir")/grr is missing; cannot prove that $project is this run's"
    ids="$(vg_project_containers "$run_dir")" \
        || vg_die "FAIL: docker ps failed for compose project $project"
    for id in $ids; do
        files="$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.project.config_files" }}' "$id")" \
            || vg_die "FAIL: docker inspect $id failed"
        [[ ",$files," == *",$override,"* ]] \
            || vg_die "FAIL: container $id of $project was not started from this run's $override (config_files: $files).
    Another checkout uses this run id. Do not act on $project from this run."
        source="$(docker inspect -f '{{ range .Mounts }}{{ if eq .Destination "/usr/local/apache2/htdocs" }}{{ .Source }}{{ end }}{{ end }}' "$id")" \
            || vg_die "FAIL: docker inspect $id failed"
        [[ "$source" == "$grr_copy" ]] \
            || vg_die "FAIL: container $id of $project serves '$source', not this run's $grr_copy.
    Another checkout uses this run id. Do not act on $project from this run."
    done
}

# vg_check_httpd_image: fails, naming the fix, unless the httpd image is
# local. It never pulls.
vg_check_httpd_image() {
    command -v docker > /dev/null \
        || vg_die "FAIL: docker is not on PATH; the HTTP GRR needs docker compose"
    if ! docker image inspect "$VG_HTTPD_IMAGE" > /dev/null 2>&1; then
        vg_die "FAIL: the image $VG_HTTPD_IMAGE is not local, and verify-gain never pulls it (up --pull never).
    Fix: docker pull $VG_HTTPD_IMAGE"
    fi
}

# vg_drive_cli <scratch dir> <evidence dir> <cmd...>: run a CLI from the
# scratch directory under vg_isolated and keep command.txt, stdout.txt,
# stderr.txt and exit_code.txt in the evidence directory. Sets VG_RC to
# the CLI's exit code; never fails on it.
vg_drive_cli() {
    local scratch="$1" evidence="$2"
    shift 2
    vg_isolated_env "$scratch"
    {
        printf 'cd %q &&\n' "$scratch"
        printf 'env'
        printf ' %q' "${VG_ENV[@]}"
        printf ' \\\n'
        printf '%q ' "$@"
        printf '\n'
    } > "$evidence/command.txt"
    VG_RC=0
    (cd "$scratch" && vg_isolated "$scratch" "$@") \
        > "$evidence/stdout.txt" 2> "$evidence/stderr.txt" || VG_RC=$?
    echo "$VG_RC" > "$evidence/exit_code.txt"
}

# vg_readback <evidence dir> <cmd...>: run the read-back command, keep
# readback.txt and readback_exit_code.txt, print the read-back and fail
# unless it exits 0.
vg_readback() {
    local evidence="$1"
    shift
    local rb=0
    "$@" > "$evidence/readback.txt" 2>&1 || rb=$?
    echo "$rb" > "$evidence/readback_exit_code.txt"
    cat "$evidence/readback.txt"
    [[ "$rb" -eq 0 ]] || vg_die "drive: read-back failed; evidence in $evidence"
}
