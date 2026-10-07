#!/usr/bin/env bash
# drive-grr-manage.sh <run id>
#
# Drive features/grr-manage.md on a scratch copy of test_fixtures/mini-GRR
# (scratch/grr-copy, without the .git file). The submodule is never
# written. The drive adds its own known file, verify_gain_added.txt, to the
# mini_vcf_plot resource of the copy, so the first dry runs find a stale
# manifest whatever state the committed manifests are in. Then it runs,
# each step with its own evidence subdirectory:
#   01-resource-manifest-dry   grr_manage resource-manifest -n -R <copy> -r mini_vcf_plot
#   02-repo-manifest-dry       grr_manage repo-manifest -n -R <copy>
#   03-repo-manifest           grr_manage repo-manifest -R <copy>
#   04-repo-manifest-dry-again grr_manage repo-manifest -n -R <copy>
#   05-list                    grr_manage list -R <copy>
#   06-grr-browse              grr_browse -g scratch/grr-copy.yaml
# and reads the steps back with readback-grr-manage.sh. A step that exits
# non-zero does not stop the drive: the first two steps must exit non-zero,
# and the read-back judges every exit code.
#
# Evidence lands in .verify/<run id>/evidence/grr-manage/:
#   <step>/{command.txt,stdout.txt,stderr.txt,exit_code.txt}
#   added_file.txt  grr-copy.yaml  resources.txt  manifest.txt
#   readback.txt  readback_exit_code.txt
# Exits 0 only when the read-back passes.

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

run_dir="$(vg_run_dir "${1:-}")"
scratch="$run_dir/scratch"
evidence="$run_dir/evidence/grr-manage"
copy="$scratch/grr-copy"
[[ -f "$scratch/grr.yaml" ]] || vg_die "drive: $scratch/grr.yaml is missing (run launch.sh)"
[[ ! -e "$evidence" ]] || vg_die "drive: $evidence already exists; launch a new run"
[[ ! -e "$copy" ]] || vg_die "drive: $copy already exists; launch a new run"

mini_grr="$VG_CHECKOUT/test_fixtures/mini-GRR"
[[ -f "$mini_grr/mini_pipeline/genomic_resource.yaml" ]] \
    || vg_die "drive: FAIL: $mini_grr is not initialised.
    Fix: cd $VG_CHECKOUT && git submodule update --init test_fixtures/mini-GRR"
mkdir -p "$evidence"

# The scratch copy. grr_manage -R takes an absolute directory only.
rsync -a --exclude=/.git "$mini_grr/" "$copy/"
copy="$(cd "$copy" && pwd -P)"
resource=mini_vcf_plot
added=verify_gain_added.txt
[[ -f "$copy/$resource/genomic_resource.yaml" ]] \
    || vg_die "drive: FAIL: the copy has no $resource resource"
echo "verify-gain: a file that no committed manifest lists" > "$copy/$resource/$added"
echo "$resource $added" > "$evidence/added_file.txt"

# The resources of the copy: every directory with a genomic_resource.yaml.
(cd "$copy" && find . -name genomic_resource.yaml -printf '%h\n' \
    | sed 's|^\./||' | sort) > "$evidence/resources.txt"

# A directory GRR definition over the copy, for grr_browse.
q="'"
cat > "$scratch/grr-copy.yaml" <<YAML
id: mini_copy
type: directory
directory: '${copy//$q/$q$q}'
YAML
cp "$scratch/grr-copy.yaml" "$evidence/grr-copy.yaml"

step() {
    local name="$1"
    shift
    mkdir -p "$evidence/$name"
    vg_drive_cli "$scratch" "$evidence/$name" "$@"
    echo "drive: $name exited $VG_RC"
}
step 01-resource-manifest-dry \
    "$VG_VENV_BIN/grr_manage" resource-manifest -n -R "$copy" -r "$resource"
step 02-repo-manifest-dry \
    "$VG_VENV_BIN/grr_manage" repo-manifest -n -R "$copy"
step 03-repo-manifest \
    "$VG_VENV_BIN/grr_manage" repo-manifest -R "$copy"
step 04-repo-manifest-dry-again \
    "$VG_VENV_BIN/grr_manage" repo-manifest -n -R "$copy"
step 05-list \
    "$VG_VENV_BIN/grr_manage" list -R "$copy"
step 06-grr-browse \
    "$VG_VENV_BIN/grr_browse" -g "$scratch/grr-copy.yaml"
cp "$copy/$resource/.MANIFEST" "$evidence/manifest.txt"

# Second, independent read: the exit codes, the messages, the manifest and
# the two listings against the resources of the copy.
vg_readback "$evidence" "$VG_SCRIPTS/readback-grr-manage.sh" "$evidence"
echo "drive: PASS: evidence in $evidence"
