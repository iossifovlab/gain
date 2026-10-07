#!/usr/bin/env bash
# doctor.sh <run id>
#
# Read-only preflight. Run it before the first drive and after any failed
# drive. Checks:
#   1. the annotate_tabular and grr_browse on PATH are the checkout's
#      .venv/bin ones (a conda env can shadow them);
#   2. test_fixtures/mini-GRR is initialised (it is a git submodule);
#   3. grr_browse with the run's GRR definition lists mini_pipeline.
# Exits 0 when every check passes, 1 on the first failure.
#
# VERIFY_GAIN_MINI_GRR=<dir> overrides the mini-GRR directory checked in
# step 2 (default: <checkout>/test_fixtures/mini-GRR); used to prove the
# empty-submodule failure without deinitialising the submodule.

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

run_dir="$(vg_run_dir "${1:-}")"
scratch="$run_dir/scratch"
[[ -f "$scratch/grr.yaml" ]] || vg_die "doctor: $scratch/grr.yaml is missing (run launch.sh)"

# 1. CLI on PATH comes from this checkout.
for cli in annotate_tabular grr_browse; do
    expected="$VG_VENV_BIN/$cli"
    [[ -x "$expected" ]] || vg_die "doctor: FAIL: $expected is missing; run 'uv sync' in $VG_CHECKOUT"
    found="$(command -v "$cli" || true)"
    if [[ -z "$found" ]]; then
        vg_die "doctor: FAIL: $cli is not on PATH; expected $expected.
    Fix: export PATH=\"$VG_VENV_BIN:\$PATH\""
    fi
    if [[ "$(realpath "$found")" != "$(realpath "$expected")" \
          && "$found" != "$expected" ]]; then
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
echo "doctor: PASS"
