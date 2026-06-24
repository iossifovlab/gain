#!/usr/bin/env bash
# Drive the #164 cheap-endpoint SLO harness across a K-sweep against a FRESH
# (cold-pipeline) daphne server per K. Emits one JSON record per K to stdout
# and a combined JSON array to the path in $OUT (default: matrix.json).
#
# Run the SAME script on BOTH checkouts (async master and the a04a82926 sync
# baseline, with the #164 delay instrumentation cherry-picked onto it) for an
# apples-to-apples comparison.
#
# Usage:
#   DELAY=0.4 KS="8 16 32" LABEL=async \
#     bash web_annotation/loadtest/run_matrix.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_API_DIR="$(cd "${HERE}/../.." && pwd)"
REPO_ROOT="$(cd "${WEB_API_DIR}/.." && pwd)"
cd "${WEB_API_DIR}"

if [[ -z "${VIRTUAL_ENV:-}" && -f "${REPO_ROOT}/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/.venv/bin/activate"
fi

DELAY="${DELAY:-0.4}"
KS="${KS:-8 16 32}"
LABEL="${LABEL:-run}"
TIMEOUT="${TIMEOUT:-30}"
PORT="${PORT:-21011}"
EMAIL="${EMAIL:-loadtest@example.com}"
OUT="${OUT:-${HERE}/matrix-${LABEL}.json}"

records=()
for K in ${KS}; do
  echo "[run_matrix] === ${LABEL} K=${K} delay=${DELAY}s (fresh cold server) ===" >&2
  pkill -f "daphne.*:${PORT}\b" 2>/dev/null || true
  pkill -f "daphne -b 127.0.0.1 -p ${PORT}" 2>/dev/null || true
  sleep 1
  GPFWA_BUILD_DELAY_SECONDS="${DELAY}" PORT="${PORT}" \
    bash "${HERE}/run_daphne_server.sh" > "/tmp/daphne-${LABEL}-K${K}.log" 2>&1 &
  # Wait for readiness.
  for _ in $(seq 1 60); do
    if curl -s -m2 "http://127.0.0.1:${PORT}/api/version" >/dev/null 2>&1; then
      break
    fi
    sleep 0.5
  done
  rec="$(python -m web_annotation.loadtest.cheap_endpoint_slo \
      --base-url "http://127.0.0.1:${PORT}" \
      --concurrency "${K}" --timeout "${TIMEOUT}" \
      --delay "${DELAY}" --label "${LABEL}-K${K}" \
      --email "${EMAIL}" --compact)"
  echo "${rec}"
  records+=("${rec}")
  pkill -f "daphne -b 127.0.0.1 -p ${PORT}" 2>/dev/null || true
  sleep 1
done

# Combine into a JSON array.
printf '%s\n' "${records[@]}" | python -c "import sys,json; print(json.dumps([json.loads(l) for l in sys.stdin if l.strip()], indent=2))" > "${OUT}"
echo "[run_matrix] wrote ${OUT}" >&2
