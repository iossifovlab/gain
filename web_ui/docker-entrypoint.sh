#!/bin/sh
# Runtime injection of the Google Analytics snippet into
# the SPA's index.html.
#
# When GA_MEASUREMENT_ID is set, the placeholder
# `<!-- __GA_SNIPPET__ -->` in index.html.tmpl is replaced
# with a gtag.js snippet. When unset or empty, the
# placeholder is replaced with the empty string so no
# script tag ships.
#
# Idempotent: always starts from index.html.tmpl (baked
# into the image), so repeated restarts or `compose up -d`
# after a .env edit always produce a fresh index.html.

set -eu

TMPL=/usr/local/apache2/htdocs/index.html.tmpl
OUT=/usr/local/apache2/htdocs/index.html

if [ -n "${GA_MEASUREMENT_ID:-}" ]; then
    snippet='<script async src="https://www.googletagmanager.com/gtag/js?id='"$GA_MEASUREMENT_ID"'"></script><script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}gtag("js",new Date());gtag("config","'"$GA_MEASUREMENT_ID"'");</script>'
else
    snippet=''
fi

# awk match/substr (not sed or awk gsub) so the snippet can
# contain `&`, `/`, `\`, and other regex/replacement
# metachars without escaping.
awk -v s="$snippet" '
  {
    if (match($0, /<!-- __GA_SNIPPET__ -->/)) {
      print substr($0, 1, RSTART-1) s substr($0, RSTART+RLENGTH)
    } else {
      print
    }
  }
' "$TMPL" > "$OUT"

exec "$@"
