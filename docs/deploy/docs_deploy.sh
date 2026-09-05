#!/usr/bin/env bash
# Ship docs/gaindocs-html.tar.gz to the iossifovlab.com docs host.
# Run from the gain repo root after `docs/build_docs.sh` has produced
# the tarball:
#     bash docs/deploy/docs_deploy.sh
#
# The play publishes into a per-deploy release directory named after
# `docs_stamp` and flips a symlink onto it. Set DOCS_STAMP to name that
# directory -- CI passes "<build number>-<short sha>" so the published
# release is traceable to the build that made it. Left unset, the play
# falls back to the docs host's clock, which is what a hand-run deploy
# wants.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "${REPO_ROOT}/docs/deploy"

extra_vars=()
if [ -n "${DOCS_STAMP:-}" ]; then
    extra_vars+=(-e "docs_stamp=${DOCS_STAMP}")
fi

ansible-playbook -i docs_inventory docs_deploy.yaml "${extra_vars[@]}"
