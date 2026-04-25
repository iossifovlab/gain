# CI image for the Angular frontend (web_ui).
#
# Build from the repo root:
#     docker build -f web_ui/Dockerfile -t gain-web-ui-ci .

# syntax=docker/dockerfile:1.7
#
# Pinned to the same Node version as Dockerfile.production so CI
# exercises the same toolchain that ships to production. Bumping is
# a one-line change in both files when an upgrade is wanted.
ARG NODE_VERSION=22.14.0-alpine
FROM node:${NODE_VERSION}

WORKDIR /app

# Deps layer first so source-only edits hit the cache.
COPY web_ui/package.json web_ui/package-lock.json ./

RUN --mount=type=cache,target=/root/.npm npm ci

COPY web_ui/ ./

CMD ["npx", "jest", "--ci"]
