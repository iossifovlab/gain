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

# vg_check_run_id <run id>: fails unless the id is a safe directory name.
vg_check_run_id() {
    [[ "$1" =~ ^[A-Za-z0-9._-]+$ ]] \
        || vg_die "run id '$1' may hold only letters, digits, '.', '_' and '-'"
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

# vg_isolated <scratch dir> <cmd...>: run a command with HOME inside the
# scratch directory (so ~/.grr_definition.yaml is never read and nothing is
# written under the real home) and GRR_DEFINITION_FILE pointing at the run's
# directory-GRR definition. That definition is what keeps a drive off the
# network: the repository it names is a local directory.
#
# The http(s)_proxy variables point at a closed local port. This is only a
# backstop for clients that honour the proxy environment (curl, urllib,
# requests). It does NOT cover gain's HTTP GRR path: fsspec's HTTPFileSystem
# runs on aiohttp without trust_env and ignores these variables. Do not rely
# on it to make network use fail; keep the definition a directory GRR.
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
        GRR_DEFINITION_FILE="$scratch/grr.yaml"
        http_proxy=http://127.0.0.1:9 https_proxy=http://127.0.0.1:9
        HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9
        no_proxy= NO_PROXY=
    )
}

vg_isolated() {
    local scratch="$1"
    shift
    mkdir -p "$scratch/home"
    vg_isolated_env "$scratch"
    env "${VG_ENV[@]}" "$@"
}
