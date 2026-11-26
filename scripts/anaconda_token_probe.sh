#!/usr/bin/env bash
# Check that Anaconda.org accepts ANACONDA_TOKEN. Run by the
# `Pre-flight: credentials` stage of Jenkinsfile.release, on the agent:
# it needs only curl.
#
# Input (environment): ANACONDA_TOKEN.
#
# Exit 0 on HTTP 200. Exit 1 on any other answer, a network failure
# included. Exit 2 when ANACONDA_TOKEN is unset or empty. `GET /user`
# proves only that the token authenticates, not that it can upload to
# the iossifovlab org.
set -u

if [ -z "${ANACONDA_TOKEN:-}" ]; then
    echo "anaconda_token_probe.sh: ANACONDA_TOKEN is not set" >&2
    exit 2
fi

code=$(curl -sS -o /dev/null -w '%{http_code}' \
    -H "Authorization: token ${ANACONDA_TOKEN}" \
    https://api.anaconda.org/user) || true  # curl prints 000 on failure
echo "Anaconda token probe: HTTP $code"
case "$code" in
    200) exit 0 ;;
    401|403)
        echo "Anaconda.org rejects the token of the Jenkins credential" \
             "anaconda-token-iossifovlab (HTTP $code)." >&2
        exit 1 ;;
esac
# A 5xx or a network failure stops the release too: a failure after
# `Publish` starts leaves a partial publish behind.
echo "The Anaconda token probe got HTTP $code, not 200." >&2
exit 1
