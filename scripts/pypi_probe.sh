#!/usr/bin/env bash
# Probe PyPI for a gain-core release tag. Run by the `Pre-flight: PyPI`
# stage of Jenkinsfile.release, before `Setup`: it needs only curl.
#
# Inputs (environment): TAG_NAME, WHEELS_HOST, DRY_RUN.
#
# Exit 1 means "gain-core TAG_NAME is already published, abort". Every
# other outcome exits 0.
set -u

code() {
    # curl prints 000 and exits non-zero on a network failure.
    curl -sS -o /dev/null -w '%{http_code}' "$1" || true
}

pypi=$(code "https://pypi.org/pypi/gain-core/${TAG_NAME}/json")
echo "PyPI probe: HTTP $pypi"
if [ "$pypi" != "200" ]; then
    # 404, 5xx or a network failure: not there yet. A flaky index
    # must not block a release.
    exit 0
fi

wheel=$(code "https://${WHEELS_HOST}/gain/gain_core-${TAG_NAME}-py3-none-any.whl")
echo "Wheel index probe: HTTP $wheel"

if [ "$wheel" != "200" ]; then
    # Step 0 of `Publish` uploaded to PyPI and a later step failed.
    # `uv publish --check-url` skips the files PyPI already has.
    echo "gain-core ${TAG_NAME} is on PyPI but not at" \
         "https://${WHEELS_HOST}/gain/: this run resumes a partial publish"
    exit 0
fi

published="gain-core ${TAG_NAME} is already published on PyPI"
published="$published and at https://${WHEELS_HOST}/gain/"
if [ "${DRY_RUN:-false}" = "true" ]; then
    # The `Pre-flight: PyPI` stage copies the `DRY RUN:` line, less
    # the prefix, into the Notify message: keep it a bare finding.
    echo "DRY RUN: $published"
    echo "A real release would be rejected here as tag mutation. Continuing."
    exit 0
fi
echo "$published"
exit 1
