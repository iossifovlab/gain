#!/usr/bin/env bash
# Drive the #170 WS-notification SLO harness across a K-sweep against a FRESH
# (cold-pipeline) daphne server per K. Emits one compact JSON record per K to
# stdout and a combined JSON array to $OUT (default: ws-matrix-${LABEL}.json).
#
# Reuses run_daphne_server.sh (which uses settings_e2e -> the test-only ping
# route is live). Run the SAME script on the post-#163 master to record the
# headline numbers; a pre/post version comparison is intentionally NOT the
# deliverable (see docs/170-ws-notification-responsiveness-slo.md -- both
# versions keep the loop clean, so the comparison is low-information; the
# committed positive-control test is the sensitivity proof instead).
#
# Usage:
#   DELAY=0.4 KS="8 16 32" LABEL=async DURATION=10 \
#     bash web_annotation/loadtest/run_ws_matrix.sh
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
DURATION="${DURATION:-10}"
TIMEOUT="${TIMEOUT:-30}"
PORT="${PORT:-21011}"
EMAIL="${EMAIL:-loadtest@example.com}"
OUT="${OUT:-${HERE}/ws-matrix-${LABEL}.json}"

records=()
for K in ${KS}; do
  echo "[run_ws_matrix] === ${LABEL} K=${K} delay=${DELAY}s (fresh cold server) ===" >&2
  pkill -f "daphne -b 127.0.0.1 -p ${PORT}" 2>/dev/null || true
  sleep 1
  GPFWA_BUILD_DELAY_SECONDS="${DELAY}" PORT="${PORT}" \
    bash "${HERE}/run_daphne_server.sh" > "/tmp/daphne-ws-${LABEL}-K${K}.log" 2>&1 &
  for _ in $(seq 1 60); do
    if curl -s -m2 "http://127.0.0.1:${PORT}/api/version" >/dev/null 2>&1; then
      break
    fi
    sleep 0.5
  done
  rec="$(python -m web_annotation.loadtest.ws_notification_slo \
      --base-url "http://127.0.0.1:${PORT}" \
      --ws-url "ws://127.0.0.1:${PORT}/ws/notifications/" \
      --concurrency "${K}" --duration "${DURATION}" --timeout "${TIMEOUT}" \
      --delay "${DELAY}" --label "${LABEL}-K${K}" \
      --email "${EMAIL}" --compact)"
  echo "${rec}"
  records+=("${rec}")
  pkill -f "daphne -b 127.0.0.1 -p ${PORT}" 2>/dev/null || true
  sleep 1
done

printf '%s\n' "${records[@]}" | python -c "import sys,json; print(json.dumps([json.loads(l) for l in sys.stdin if l.strip()], indent=2))" > "${OUT}"
echo "[run_ws_matrix] wrote ${OUT}" >&2
