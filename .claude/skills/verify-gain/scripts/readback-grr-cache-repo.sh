#!/usr/bin/env bash
# readback-grr-cache-repo.sh <cache dir of mini_http> <served GRR copy>
#
# Compare what grr_cache_repo cached for mini_pipeline with the served
# copy, byte for byte (cmp):
#   mini_pipeline/mini_pipeline.yaml
#   mini_positionscore_bw/genomic_resource.yaml
#   mini_positionscore_bw/mini_positionscore_bw_0.bw
# Exits 0 when every file is cached and equal to its source, 1 otherwise.

set -euo pipefail

cache="${1:-}"
grr="${2:-}"
[[ -d "$cache" && -d "$grr" ]] \
    || { echo "readback: usage: readback-grr-cache-repo.sh <cache dir> <GRR copy>" >&2; exit 1; }

fail=0
for f in mini_pipeline/mini_pipeline.yaml \
         mini_positionscore_bw/genomic_resource.yaml \
         mini_positionscore_bw/mini_positionscore_bw_0.bw; do
    if [[ ! -f "$cache/$f" ]]; then
        echo "readback: FAIL: $f is not cached in $cache"
        fail=1
    elif ! cmp -- "$cache/$f" "$grr/$f"; then
        echo "readback: FAIL: cached $f differs from $grr/$f"
        fail=1
    else
        echo "readback: ok: cmp $f: cached = source ($(wc -c < "$cache/$f") bytes)"
    fi
done
[[ "$fail" -eq 0 ]] || exit 1
echo "readback: PASS: grr_cache_repo cached mini_pipeline and mini_positionscore_bw_0.bw byte for byte"
