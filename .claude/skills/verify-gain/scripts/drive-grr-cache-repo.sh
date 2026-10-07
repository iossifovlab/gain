#!/usr/bin/env bash
# drive-grr-cache-repo.sh <run id>
#
# Drive the grr_cache_repo half of features/http-grr.md: cache the
# mini_pipeline resource and its dependency mini_positionscore_bw from the
# run's HTTP GRR into the definition's cache_dir, then read the cache back
# with readback-grr-cache-repo.sh against the served copy.
#
# Evidence lands in .verify/<run id>/evidence/grr-cache-repo/:
#   command.txt  stdout.txt  stderr.txt  exit_code.txt  grr-http.yaml
#   cached_files.txt  readback.txt  readback_exit_code.txt
# Exits 0 only when grr_cache_repo exits 0 and the read-back passes.

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

run_dir="$(vg_run_dir "${1:-}")"
scratch="$run_dir/scratch"
http_dir="$(vg_http_dir "$run_dir")"
evidence="$run_dir/evidence/grr-cache-repo"
[[ -f "$scratch/grr-http.yaml" ]] || vg_die "drive: $scratch/grr-http.yaml is missing (run launch-http-grr.sh)"
[[ ! -e "$evidence" ]] || vg_die "drive: $evidence already exists; launch a new run"
mkdir -p "$evidence"
cp "$scratch/grr-http.yaml" "$evidence/grr-http.yaml"

vg_drive_cli "$scratch" "$evidence" \
    "$VG_VENV_BIN/grr_cache_repo" -g "$scratch/grr-http.yaml" --no-progress mini_pipeline
(cd "$http_dir/cache" && find . -type f | sort) > "$evidence/cached_files.txt"
if [[ "$VG_RC" -ne 0 ]]; then
    tail -n 20 "$evidence/stderr.txt" >&2 || true
    vg_die "drive: grr_cache_repo exited $VG_RC; evidence in $evidence; run doctor.sh"
fi

# Second, independent read: the cached files against the served copy.
vg_readback "$evidence" "$VG_SCRIPTS/readback-grr-cache-repo.sh" \
    "$http_dir/cache/mini_http" "$http_dir/grr"
echo "drive: PASS: evidence in $evidence"
