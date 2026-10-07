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

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

run_dir="$(vg_run_dir "${1:-}")"
scratch="$run_dir/scratch"
http_dir="$(vg_http_dir "$run_dir")"
evidence="$run_dir/evidence/grr-browse"
[[ -f "$scratch/grr-http.yaml" ]] || vg_die "drive: $scratch/grr-http.yaml is missing (run launch-http-grr.sh)"
[[ ! -e "$evidence" ]] || vg_die "drive: $evidence already exists; launch a new run"
mkdir -p "$evidence"
cp "$scratch/grr-http.yaml" "$evidence/grr-http.yaml"
cp "$http_dir/grr/.CONTENTS.json" "$evidence/contents.json"

vg_drive_cli "$scratch" "$evidence" \
    "$VG_VENV_BIN/grr_browse" -g "$scratch/grr-http.yaml"
if [[ "$VG_RC" -ne 0 ]]; then
    tail -n 20 "$evidence/stderr.txt" >&2 || true
    vg_die "drive: grr_browse exited $VG_RC; evidence in $evidence; run doctor.sh"
fi

# Second, independent read: the listing against the served .CONTENTS.json.
vg_readback "$evidence" "$VG_SCRIPTS/readback-grr-browse.sh" \
    "$evidence/stdout.txt" "$evidence/contents.json"
echo "drive: PASS: evidence in $evidence"
