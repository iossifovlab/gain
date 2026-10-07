#!/usr/bin/env bash
# doctor.sh <run id>
#
# Read-only preflight. Run it before the first drive and after any failed
# drive. Checks:
#   1. the annotate_tabular and grr_browse on PATH are the checkout's
#      .venv/bin ones (a conda env can shadow them);
#   2. test_fixtures/mini-GRR is initialised (it is a git submodule);
#   3. grr_browse with the run's GRR definition lists mini_pipeline.
# For a run with an HTTP GRR (launch-http-grr.sh wrote
# scratch/http/compose.override.yaml), also:
#   4. the httpd image is local (it is never pulled);
#   5. the run's compose project verify-gain-<run id> has exactly one
#      apache container (docker compose -p ... ps -q apache), and every
#      container of the project was started from this run's override and
#      serves this run's copy (vg_check_project_owned);
#   6. grr_browse -g scratch/grr-http.yaml exits 0 and lists mini_pipeline.
#      A bare request for .CONTENTS.json is not the check: a GRR can ship
#      only .CONTENTS.json.gz.
# Exits 0 when every check passes, 1 on the first failure.
#
# VERIFY_GAIN_MINI_GRR=<dir> overrides the mini-GRR directory checked in
# step 2 (default: <checkout>/test_fixtures/mini-GRR); used to prove the
# empty-submodule failure without deinitialising the submodule.
# VERIFY_GAIN_HTTPD_IMAGE=<image> overrides the image checked in step 4
# (default: httpd:latest); a tag that is not local proves the
# missing-image failure without removing httpd:latest.

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

run_dir="$(vg_run_dir "${1:-}")"
scratch="$run_dir/scratch"
[[ -f "$scratch/grr.yaml" ]] || vg_die "doctor: $scratch/grr.yaml is missing (run launch.sh)"

# 1. CLI on PATH comes from this checkout.
for cli in annotate_tabular grr_browse grr_cache_repo; do
    expected="$VG_VENV_BIN/$cli"
    [[ -x "$expected" ]] || vg_die "doctor: FAIL: $expected is missing; run 'uv sync' in $VG_CHECKOUT"
    found="$(command -v "$cli" || true)"
    if [[ -z "$found" ]]; then
        vg_die "doctor: FAIL: $cli is not on PATH; expected $expected.
    Fix: export PATH=\"$VG_VENV_BIN:\$PATH\""
    fi
    if [[ "$(realpath "$found")" != "$(realpath "$expected")" ]]; then
        vg_die "doctor: FAIL: $cli on PATH is $found, not this checkout's $expected.
    Fix: export PATH=\"$VG_VENV_BIN:\$PATH\" (the drives call $VG_VENV_BIN explicitly, but a shadowing CLI means an ad-hoc command would test the wrong code)"
    fi
    echo "doctor: ok: $cli -> $found"
done

# 2. mini-GRR submodule is initialised.
mini_grr="${VERIFY_GAIN_MINI_GRR:-$VG_CHECKOUT/test_fixtures/mini-GRR}"
if [[ ! -f "$mini_grr/mini_pipeline/genomic_resource.yaml" ]]; then
    vg_die "doctor: FAIL: $mini_grr/mini_pipeline/genomic_resource.yaml is missing; the mini-GRR submodule is not initialised.
    Fix: cd $VG_CHECKOUT && git submodule update --init test_fixtures/mini-GRR"
fi
echo "doctor: ok: mini-GRR initialised at $mini_grr"

# 3. The run's GRR definition resolves and lists mini_pipeline.
if ! listing="$(vg_isolated "$scratch" "$VG_VENV_BIN/grr_browse" -g "$scratch/grr.yaml" 2>&1)"; then
    printf '%s\n' "$listing" >&2
    vg_die "doctor: FAIL: grr_browse -g $scratch/grr.yaml failed (output above)"
fi
if ! grep -qE '[[:space:]]mini_pipeline$' <<<"$listing"; then
    printf '%s\n' "$listing" >&2
    vg_die "doctor: FAIL: grr_browse -g $scratch/grr.yaml does not list mini_pipeline (output above)"
fi
echo "doctor: ok: grr_browse -g $scratch/grr.yaml lists mini_pipeline"

if [[ -f "$(vg_compose_override "$run_dir")" ]]; then
    project="$(vg_compose_project "$run_dir")"
    # 4. The image is local.
    vg_check_httpd_image
    echo "doctor: ok: image $VG_HTTPD_IMAGE is local"

    # 5. The run's compose project owns the apache container.
    ids="$(vg_compose "$run_dir" ps -q apache)" \
        || vg_die "doctor: FAIL: docker compose -p $project ps failed"
    if [[ "$(wc -w <<<"$ids")" -ne 1 ]]; then
        vg_die "doctor: FAIL: compose project $project runs $(wc -w <<<"$ids") apache containers, not 1.
    Fix: cleanup.sh $(basename "$run_dir"), then launch a new run"
    fi
    # The project label proves nothing: `ps -p` filters on it. Check that
    # every container of the project runs this run's override and copy.
    vg_check_project_owned "$run_dir"
    echo "doctor: ok: compose project $project runs this run's apache container $ids (this run's override and copy)"

    # 6. The HTTP GRR definition answers.
    [[ -f "$scratch/grr-http.yaml" ]] \
        || vg_die "doctor: FAIL: $scratch/grr-http.yaml is missing; launch-http-grr.sh did not finish"
    if ! listing="$(vg_isolated "$scratch" "$VG_VENV_BIN/grr_browse" -g "$scratch/grr-http.yaml" 2>&1)"; then
        printf '%s\n' "$listing" >&2
        vg_die "doctor: FAIL: grr_browse -g $scratch/grr-http.yaml failed (output above)"
    fi
    if ! grep -qE '[[:space:]]mini_http[[:space:]]+mini_pipeline$' <<<"$listing"; then
        printf '%s\n' "$listing" >&2
        vg_die "doctor: FAIL: grr_browse -g $scratch/grr-http.yaml does not list mini_pipeline (output above)"
    fi
    echo "doctor: ok: grr_browse -g $scratch/grr-http.yaml lists mini_pipeline from $(grep -m1 '^url:' "$scratch/grr-http.yaml")"
fi
echo "doctor: PASS"
