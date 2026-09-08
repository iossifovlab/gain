#!/usr/bin/env bash
# Ship docs/gaindocs-html.tar.gz to the iossifovlab.com docs host.
# Run from the gain repo root after `docs/build_docs.sh` has produced
# the tarball:
#     bash docs/deploy/docs_deploy.sh
#
# Set DOCS_STAMP to name the release directory the play publishes; see
# `docs_stamp` in docs_deploy.yaml, which reads it from the environment.
# CI sets it to "<build number>-<short sha>", and the play declines to
# publish a build number older than the one already live (gain#1190).
# Without DOCS_STAMP the release is named after the clock and always
# publishes.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "${REPO_ROOT}/docs/deploy"

ansible-playbook -i docs_inventory docs_deploy.yaml
