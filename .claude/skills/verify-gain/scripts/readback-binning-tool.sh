#!/usr/bin/env bash
# readback-binning-tool.sh <bins.h5>
#
# Open a binning_tool output with h5py from the checkout's .venv and check:
#   - the file attribute coordinates is 1-based-inclusive;
#   - bins holds chr1 1-5, chr1 6-10, chr2 1-5, chr2 6-10;
#   - tracks holds one track, mini_positionscore_bw, aggregator mean;
#   - values has the shape (4, 1) and holds 0, 0.1, 0.2, 0.3.
# The bigWig stores float32, so values holds 0.30000001: the values are
# compared within 1e-6, not exactly. Exits 0 on a match, 1 otherwise.

set -euo pipefail

h5="${1:-}"
[[ -n "$h5" ]] || { echo "readback: missing <bins.h5> argument" >&2; exit 1; }
[[ -f "$h5" ]] || { echo "readback: FAIL: $h5 does not exist" >&2; exit 1; }

checkout="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
python="$checkout/.venv/bin/python"
[[ -x "$python" ]] || { echo "readback: FAIL: $python is missing; run 'uv sync'" >&2; exit 1; }

exec "$python" -I - "$h5" <<'PY'
import sys

import h5py

path = sys.argv[1]
fails = []
with h5py.File(path, "r") as h5:
    coordinates = h5.attrs.get("coordinates")
    print(f"readback: coordinates = {coordinates!r}")
    if coordinates != "1-based-inclusive":
        fails.append(f"coordinates is {coordinates!r}, not '1-based-inclusive'")

    bins = [
        (row["chrom"].decode(), int(row["start"]), int(row["end"]))
        for row in h5["bins"][()]
    ]
    print(f"readback: bins = {bins}")
    expected_bins = [
        ("chr1", 1, 5), ("chr1", 6, 10), ("chr2", 1, 5), ("chr2", 6, 10)]
    if bins != expected_bins:
        fails.append(f"bins are {bins}, not {expected_bins}")

    tracks = [
        (row["name"].decode(), row["aggregator"].decode())
        for row in h5["tracks"][()]
    ]
    print(f"readback: tracks = {tracks}")
    if tracks != [("mini_positionscore_bw", "mean")]:
        fails.append(f"tracks are {tracks}, not [('mini_positionscore_bw', 'mean')]")

    values = h5["values"][()]
    print(f"readback: values shape = {values.shape}, column 0 = {values[:, 0].tolist()}")
    expected_values = [0.0, 0.1, 0.2, 0.3]
    if values.shape != (4, 1):
        fails.append(f"values has the shape {values.shape}, not (4, 1)")
    else:
        for i, (got, want) in enumerate(zip(values[:, 0].tolist(), expected_values)):
            if not abs(got - want) < 1e-6:
                fails.append(f"values[{i}, 0] is {got}, not {want} within 1e-6")

if fails:
    for fail in fails:
        print(f"readback: FAIL: {fail}")
    sys.exit(1)
print(f"readback: PASS: 4 bins with values 0, 0.1, 0.2, 0.3 (within 1e-6) in {path}")
PY
