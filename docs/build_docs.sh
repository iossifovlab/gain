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
# Fails on any Sphinx warning or docutils error (`sphinx-build -W`,
# gain#1220), so a docstring that breaks reStructuredText turns the
# branch red before merge.
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

# The architecture overview includes CONTEXT.md's vocabulary by two sentinel
# comments (`.. include:: :start-after:/:end-before:`). A missing sentinel
# makes docutils log a CRITICAL error, which `-W` below now fails the build
# on -- but check the sentinels here anyway, before anything is deleted or
# built: this names the missing sentinel and the page that needs it, which
# -W alone would not, and it refuses before the apidoc tree is deleted
# (gain#1142; the check predates -W, gain#1220).
for sentinel in "published-on-docs-site: start" "published-on-docs-site: end"; do
    if ! grep -q -F -- "<!-- ${sentinel} -->" CONTEXT.md; then
        echo "build_docs.sh: CONTEXT.md is missing the sentinel" \
             "'<!-- ${sentinel} -->' that docs/source/development/" \
             "architecture_overview.rst slices the vocabulary by;" \
             "refusing to build a site without its vocabulary." >&2
        exit 1
    fi
done

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
#
# `--no-toc` suppresses the one-entry `modules.rst` apidoc would otherwise
# write: `development/module_index.rst` toctrees `gain/modules/gain` directly,
# so nothing includes it and it was the build's `document isn't included in
# any toctree` warning. Not generating it beats generating it and then
# listing it in `exclude_patterns` (gain#1183).
SPHINX_APIDOC_OPTIONS="members,undoc-members,show-inheritance,no-index" \
    sphinx-apidoc --no-toc -o docs/source/development/gain/modules/ core/gain

# Build HTML. `-W` fails the build on any warning or docutils error:
# gain#1183 took the build from 173 problems to zero, and this is what keeps
# it there (gain#1220).
#
# `-W` alone is enough: since Sphinx 8.1 it no longer stops at the first
# warning but finishes the build and then exits non-zero, so one run reports
# every problem. `--keep-going` is a hidden no-op in the pinned Sphinx 9.1.0
# and is deliberately not passed. The deliberate suppression in conf.py
# (`suppress_warnings = ["myst.header"]`, gain#1142) is honoured by -W --
# suppressed warnings are never raised.
#
# The previous run's tarball goes too, so a failed build leaves nothing
# behind for a later step to mistake for this run's output.
rm -rf docs/build docs/gaindocs-html.tar.gz
rc=0
sphinx-build -M html docs/source docs/build -W || rc=$?
if [ "${rc}" -ne 0 ]; then
    echo "build_docs.sh: the docs build treats every Sphinx warning and" \
         "docutils error as fatal (sphinx-build -W, gain#1220). If Sphinx" \
         "reported 'build finished with problems' above, it ran to" \
         "completion and the WARNING/ERROR lines are the complete list;" \
         "a traceback instead means it stopped early." >&2
    exit "${rc}"
fi

# Tarball for ansible deploy.
tar -czf docs/gaindocs-html.tar.gz -C docs/build/ html/
