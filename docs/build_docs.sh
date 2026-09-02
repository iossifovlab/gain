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
# In CI, the Build docs Jenkinsfile stage is unconditional: it runs
# on every build, so an edit anywhere — including a docstring under
# core/gain, which sphinx-apidoc renders into the development
# section — refreshes the rendered page. The Deploy docs stage
# publishes on master builds only.
#
# The Deploy docs stage authenticates to iossifovlab.com via the
# `gpf-docs-deploy` Jenkins-managed SSH credential (shared with
# gpf's docs deploy — same SSH login + target host).

set -euo pipefail

# Repo root regardless of where the script is invoked from.
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${REPO_ROOT}"

# `sphinx_last_updated_by_git` shells out to git for each page's date. In CI
# the checkout is bind-mounted into a container whose uid does not own it, so
# git refuses the repository outright:
#
#     fatal: detected dubious ownership in repository at '/workspace'
#
# The extension degrades quietly -- the build still succeeds, but every page
# loses its stamp and two warnings are emitted. Declare the repo safe.
#
# Passed as command-scope config through GIT_CONFIG_* rather than
# `git config --global`, which would write to the invoking user's gitconfig
# when this script is run locally. `safe.directory` is honoured only from
# protected configuration; the command scope qualifies, the environment's
# ordinary config does not.
export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0=safe.directory
export GIT_CONFIG_VALUE_0="${REPO_ROOT}"

# Clean previous auto-generated tree so stale modules don't
# linger if files were deleted upstream.
rm -rf docs/source/development/gain

# sphinx-apidoc → .rst skeletons with automodule directives.
#
# `no-index` keeps the generated tree out of the object inventory: its
# `automodule`s still render, but they register no index entries and no
# cross-reference anchors.  Curated `autoclass` pages layered on top would
# otherwise compete with it for exactly the anchors gain#1033 spent a fix
# reclaiming -- 194 duplicates and three `more than one target found`
# classes.  This lets the curated pages own them from the start.
#
# CAUTION: `SPHINX_APIDOC_OPTIONS` REPLACES sphinx-apidoc's default option
# set, it does not extend it (sphinx/ext/apidoc/_generate.py).  Setting it to
# `no-index` alone drops `members`/`undoc-members`/`show-inheritance` and the
# generated tree documents nothing at all -- and it fails quietly, because a
# gutted build has the same anchor count and the same (empty) index as a
# correctly no-indexed one.  The tell is the rendered signature blocks
# (`class="sig sig-object py"`), which a gutted build has none of.  Keep the
# three defaults listed here whenever this line is edited.
#
# Spelling note: `no-index` is the modern name (Sphinx renamed `:noindex:` in
# 7.2); verified against the Sphinx 9.1.0 pinned in uv.lock.
SPHINX_APIDOC_OPTIONS="members,undoc-members,show-inheritance,no-index" \
    sphinx-apidoc -o docs/source/development/gain/modules/ core/gain

# Build HTML.
rm -rf docs/build
sphinx-build -M html docs/source docs/build

# Tarball for ansible deploy.
tar -czf docs/gaindocs-html.tar.gz -C docs/build/ html/
