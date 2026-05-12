#!/usr/bin/env bash
# Build the GAIn Sphinx documentation tree.
#
# Run from the gain repository root:
#     uv sync --group docs
#     uv run bash docs/build_docs.sh
#
# Produces:
#     docs/build/html/           rendered site
#     docs/gaindocs-html.tar.gz  tarball consumed by docs/deploy/
#
# In CI, the Build docs Jenkinsfile stage only runs when the
# `docs/**` tree changes (see `when { changeset 'docs/**' }` in
# Jenkinsfile). Edits outside docs/ do not refresh the rendered
# autodoc page until a subsequent docs-tree commit lands.
#
# The Deploy docs stage authenticates to iossifovlab.com via the
# `gpf-docs-deploy` Jenkins-managed SSH credential (shared with
# gpf's docs deploy — same SSH login + target host).

set -euo pipefail

# Repo root regardless of where the script is invoked from.
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${REPO_ROOT}"

# Clean previous auto-generated tree so stale modules don't
# linger if files were deleted upstream.
rm -rf docs/source/development/gain

# sphinx-apidoc → .rst skeletons with automodule directives.
sphinx-apidoc -o docs/source/development/gain/modules/ core/gain

# Build HTML.
rm -rf docs/build
sphinx-build -M html docs/source docs/build

# Tarball for ansible deploy.
tar -czf docs/gaindocs-html.tar.gz -C docs/build/ html/
