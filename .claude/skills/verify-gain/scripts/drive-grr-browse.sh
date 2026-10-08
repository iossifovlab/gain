#!/usr/bin/env bash
# drive-grr-browse.sh <run id>
#
# Drive the grr_browse half of features/http-grr.md: list the run's HTTP
# GRR (scratch/grr-http.yaml, from launch-http-grr.sh) with the checkout's
# .venv/bin/grr_browse, then read the listing back with
# readback-grr-browse.sh against the .CONTENTS.json of the served copy.
#
# Evidence lands in .verify/<run id>/evidence/grr-browse/:
#   command.txt  stdout.txt  stderr.txt  exit_code.txt  grr-http.yaml
#   contents.json  readback.txt  readback_exit_code.txt
# Exits 0 only when grr_browse exits 0 and the read-back passes.

source "$(dirname "${BASH_SOURCE[0]}")/app.sh"

run_dir="$(verify_run_dir "${1:-}")"
scratch="$run_dir/scratch"
http_dir="$(app_http_dir "$run_dir")"
evidence="$run_dir/evidence/grr-browse"
[[ -f "$scratch/grr-http.yaml" ]] || verify_die "drive: $scratch/grr-http.yaml is missing (run launch-http-grr.sh)"
[[ ! -e "$evidence" ]] || verify_die "drive: $evidence already exists; launch a new run"
mkdir -p "$evidence"
cp "$scratch/grr-http.yaml" "$evidence/grr-http.yaml"
cp "$http_dir/grr/.CONTENTS.json" "$evidence/contents.json"

verify_drive "$scratch" "$evidence" \
    "$APP_VENV_BIN/grr_browse" -g "$scratch/grr-http.yaml"
if [[ "$VERIFY_RC" -ne 0 ]]; then
    tail -n 20 "$evidence/stderr.txt" >&2 || true
    verify_die "drive: grr_browse exited $VERIFY_RC; evidence in $evidence; run doctor.sh"
fi

# Second, independent read: the listing against the served .CONTENTS.json.
verify_readback "$evidence" "$VERIFY_SCRIPTS/readback-grr-browse.sh" \
    "$evidence/stdout.txt" "$evidence/contents.json"
echo "drive: PASS: evidence in $evidence"
