# Plan: Phase 8 — wheel-based backend, supervisord-free Apache frontend, retire gpf-image / ubuntu-image

## Context

Today's production stack is a four-image conda chain wrapped in
supervisord:

- `web_infra/Dockerfile.ubuntu` builds an Ubuntu base with
  Apache + supervisord pre-installed.
- `web_infra/Dockerfile.gpf` extends it (via miniforge3) with a
  conda env that mamba-installs the gain-* conda packages from
  a local channel.
- `web_api/Dockerfile.production` extends `gpf-image`, runs
  `mamba env update -f environment.yml`, `pip install .`, then
  `conda-pack`s the env into `/gpf` on the `ubuntu-image`
  runtime. supervisord runs a bootstrap that does `migrate +
  collectstatic`, then daphne.
- `web_ui/Dockerfile.production` builds the Angular SPA and
  serves it via Apache on `ubuntu-image` runtime; supervisord
  runs Apache, which reverse-proxies `/api` and `/ws` to
  daphne and aliases `/static/` to a shared volume.

This is heavy (multi-GB images, conda-pack overhead,
build-on-prod), couples backend image production to upstream
conda packaging, and uses supervisord to multiplex what is
really a single foreground process per container.

Phase 8 replaces the whole stack with state-of-the-art shapes:

- **Backend** = `python:3.12-slim` + apt `libmagic1` + the
  pre-built `gain-core` and `django-gpf-web-annotation` wheels
  the root `Jenkinsfile`'s parallel block already produces.
  Single foreground process: `daphne`. ~150–250 MB image,
  rebuilds in seconds because the only inputs are wheels.
- **Frontend** = `httpd:2.4-alpine` (Apache, official image)
  serving the Angular SPA *and* Django's collected static
  files (baked in at build time via a multi-stage that uses
  the backend image as a collectstatic source). Single
  foreground process: `httpd-foreground` (the
  `httpd:2.4-alpine` image's default CMD). ~60 MB image.
  **No shared `/static` volume** — the frontend is fully
  self-contained. Staying on Apache keeps continuity with
  the existing config style; only the supervisord wrap goes
  away.
- **Migrations** move out of supervisord's bootstrap into a
  one-shot `backend-migrate` compose service that runs
  `python manage.py migrate` and exits 0; the long-running
  `backend` service `depends_on:
  service_completed_successfully` of that one-shot.
- The whole `gpf-image` / `ubuntu-image` / supervisord /
  conda-pack apparatus retires. So does
  `web_api/environment.yml` (no longer consumed) and the
  per-image bootstrap scripts.

The user-chosen design forks (this round):

1. **Static files (option C)** — bake Django's collected
   static into the *frontend* image at build time, eliminating
   the shared `static-data` volume.
2. **Conda artefacts (keep)** — `dist/conda/gain-*.conda` keep
   being produced by the existing `Conda packages` Jenkins
   stage. Still useful as release artefacts for downstream
   conda consumers; no longer feed any in-tree image.

This subsumes the previously-documented Phase 8 ("optional
residual cleanup") — much of it (`web_api/environment.yml`
retirement, `gpf-image` retirement) is collateral here.

## Scope

**In scope:**

- **Rewrite `web_api/Dockerfile.production`** as a 2-stage
  `python:3.12-slim` image:
  - **builder stage** installs the wheels (`gain-core`,
    `django-gpf-web-annotation`) into a `/opt/gpf` venv.
  - **runtime stage** (`python:3.12-slim`) `apt-get`s
    `libmagic1` (for `python-magic`), copies the venv, sets
    `PATH`, exposes 9001, ENTRYPOINT
    `daphne --bind 0.0.0.0 --port 9001 --proxy-headers
    --http-timeout 1200 web_annotation.asgi:application`.
  - Wheels copied from build context: `dist/core/*.whl`
    (gain-core) + `dist/web_api/*.whl`
    (django-gpf-web-annotation). Annotators are NOT installed
    — backend doesn't need them at runtime.
  - Adds `web_api/scripts/grr-definition.yaml` and
    `grr-definition-dir.yaml` to `/` (current contract).
  - No supervisord, no bootstrap script, no
    migrate/collectstatic at container boot.

- **Rewrite `web_ui/Dockerfile.production`** as a 3-stage
  build:
  - **angular stage** (`node:22.14.0-alpine`) runs
    `npm ci && npm run build`, output `/app/dist/*/browser`.
  - **django-static stage** uses `${BACKEND_IMAGE}` (passed
    via `--build-arg`, defaulting to
    `gain-web-api-prod:latest`) as a base; sets
    `STATIC_ROOT=/static` env, runs `python -m django
    collectstatic --noinput`, dropping Django admin / DRF
    static files into `/static`.
  - **runtime stage** (`httpd:2.4-alpine`) copies the
    Angular SPA into `/usr/local/apache2/htdocs/`, copies
    `/static` (Django collected) into
    `/usr/local/apache2/htdocs/static/`, and overrides
    `/usr/local/apache2/conf/httpd.conf` with a new
    `web_ui/httpd.conf`. The image's default
    `httpd-foreground` CMD is the single foreground
    process.

- **New `web_ui/httpd.conf`** mirroring today's Apache vhost
  (`web_ui/scripts/localhost.conf`) semantics, minus the
  supervisord-era boilerplate:
  - `LoadModule` for `mod_proxy`, `mod_proxy_http`,
    `mod_proxy_wstunnel` (for /ws), `mod_rewrite` (SPA
    fallback), `mod_headers`, `mod_deflate`, `mod_expires`,
    `mod_remoteip`, `mod_dir`, `mod_mime`, `mod_authz_core`,
    `mod_log_config`, `mod_unixd`, `mod_setenvif`. Apache's
    own httpd.conf doesn't enable these by default in the
    Alpine image, so we explicitly enable what we need.
  - `Listen 80`, `User daemon`, `Group daemon`,
    `ServerName localhost`, `DocumentRoot
    /usr/local/apache2/htdocs`, error/access logs to
    stderr/stdout (so docker logs surfaces them).
  - `ProxyPass /api/ http://backend:9001/api/` +
    `ProxyPassReverse`, plus `ProxyPreserveHost on` and
    `RequestHeader set X-Forwarded-Proto $scheme` style
    headers.
  - `ProxyPass /ws/ ws://backend:9001/ws/` with
    `mod_proxy_wstunnel` for WebSocket upgrade.
  - `Alias /static/ /usr/local/apache2/htdocs/static/` plus
    `<Directory>` permissions matching the current vhost.
  - SPA fallback via `mod_rewrite`:
    `RewriteCond %{REQUEST_FILENAME} !-f`,
    `RewriteCond %{REQUEST_FILENAME} !-d`,
    `RewriteRule ^ /index.html [L]`.
  - `mod_deflate` for text types; `mod_expires` for long
    cache on hashed assets; `Header set Cache-Control
    "no-store"` for `/index.html`.

- **New `backend-migrate` compose service** in
  `web_infra/compose-jenkins.yaml` and the production compose
  files. Runs `python manage.py migrate` (no
  `collectstatic` — that's now baked into the frontend
  image). Same image as `backend`. Exits 0 on success.
  `backend` and `backend-e2e` services
  `depends_on: backend-migrate: { condition: service_completed_successfully }`.

- **Update all four compose files** (`compose.yaml`,
  `compose-jenkins.yaml`, `compose-iossifovweb.yaml`,
  `compose-wigclust.yaml`):
  - Drop `volumes_from: static-data` from `frontend` /
    `frontend-e2e` (no longer needed).
  - Drop `volumes_from: static-data` from `backend` /
    `backend-e2e` (no longer writes to it).
  - Drop `static-data` service entirely (no consumers).
  - Drop `ubuntu-image` and `gpf-image` services entirely
    (no consumers — backend is now `python:3.12-slim`-based,
    frontend is `httpd:2.4-alpine`-based).
  - Add `backend-migrate` service.
  - Drop `COMPOSE_PROJECT_NAME` build-args on services that
    no longer need them.
  - Backend `EXPOSE` may shift from 80/443 to just 9001
    (today the conda-pack image exposes 80/443 left over from
    the ubuntu base; daphne only ever binds 9001).

- **Simplify the `web_e2e` stage in the root `Jenkinsfile`**:
  - Drop the `rattler-build publish dist/conda/*.conda` step
    (the local conda-channel was only for `gpf-image`, which
    is going away).
  - Drop the `web_infra/conda-channel/noarch/repodata.json`
    seeding.
  - Build sequence:
    1. `docker build -f web_api/Dockerfile.production -t
       gain-web-api-prod:${BUILD_NUMBER} .`
    2. `docker build -f web_ui/Dockerfile.production
       --build-arg BACKEND_IMAGE=gain-web-api-prod:${BUILD_NUMBER}
       -t gain-web-ui-prod:${BUILD_NUMBER} .`
    3. `docker compose -p ... -f web_infra/compose-jenkins.yaml
       build e2e-tests`
    4. The compose file consumes images by tag (or compose
       still does its own `build` for backend-e2e/frontend-e2e
       — TBD: the cleanest is to set
       `image: gain-web-api-prod:${COMPOSE_BACKEND_TAG}` and
       remove the `build:` block, but that changes more
       compose files than necessary; alternative is to keep
       compose's `build: web_api Dockerfile.production` and
       let it do its own backend build, then frontend with
       `--build-arg`). Pick during implementation.
  - Add `gain-web-api-prod` and `gain-web-ui-prod` to the
    Jenkinsfile post.cleanup loop.

- **Retire**:
  - `web_infra/Dockerfile.gpf`
  - `web_infra/Dockerfile.ubuntu`
  - `web_api/scripts/supervisord.conf`
  - `web_api/scripts/supervisord-bootstrap.sh`
  - `web_api/scripts/wait-for-it.sh`
  - `web_api/environment.yml`
  - `web_ui/scripts/localhost.conf`
  - `web_ui/scripts/supervisord.conf`
  - `web_ui/scripts/supervisord-bootstrap.sh`
  - `web_ui/scripts/wait-for-it.sh`
  - `web_infra/conda-channel/` (gitignored anyway; remove the
    .gitignore line referencing it)

- **Verify locally end-to-end** that:
  - The new backend image starts daphne directly on
    `python manage.py runserver`-equivalent settings.
  - `backend-migrate` completes cleanly against a fresh db.
  - The new frontend image serves the SPA on `/`, Django
    static on `/static/`, and proxies `/api/`+`/ws/` to
    backend:9001.
  - The Phase-6 e2e Playwright suite still passes (139 tests)
    against the new images.

**Out of scope (deliberately):**

- **Image registry + pull-deploy.** The existing
  `gpfwa-iossifovweb` / `gpfwa-wigclust` deploy flow stays
  build-on-host. Pull-deploy is a separate phase.
- **Caddy/Traefik for TLS termination.** TLS is upstream of
  this Apache today; out of scope.
- **Loki/Grafana / observability stack.** Out of scope.
- **Retirement of the `Conda packages` Jenkins stage.** Per
  user choice, the gain-*.conda artefacts keep being built.
- **Retirement of the conda dev workflow** in CLAUDE.md /
  root `environment.yml`. Untouched.
- **Repo-root `Dockerfile` / `Dockerfile.seqpipe`** legacy
  audit. Not part of this round.

## Approach

### `web_api/Dockerfile.production`

```dockerfile
# syntax=docker/dockerfile:1.7

ARG PYTHON_VERSION=3.12-slim

FROM python:${PYTHON_VERSION} AS builder

# Wheels produced by the root Jenkinsfile parallel block.
WORKDIR /wheels
COPY dist/core/gain_core-*.whl ./
COPY dist/web_api/django_gpf_web_annotation-*.whl ./

RUN pip install --no-cache-dir --upgrade pip uv \
 && uv venv /opt/gpf \
 && /opt/gpf/bin/pip install --no-cache-dir /wheels/*.whl


FROM python:${PYTHON_VERSION} AS runtime

# python-magic needs libmagic at runtime; psycopg3 wheels bundle
# their own libpq, so no other apt deps required for now.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libmagic1 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/gpf /opt/gpf
ENV PATH=/opt/gpf/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=web_annotation.settings_daphne

# GRR config files (env var GRR_DEFINITION_FILE selects which one).
COPY web_api/scripts/grr-definition.yaml \
     web_api/scripts/grr-definition-dir.yaml /

EXPOSE 9001

ENTRYPOINT ["daphne", \
            "--bind", "0.0.0.0", \
            "--port", "9001", \
            "--proxy-headers", \
            "--http-timeout", "1200", \
            "web_annotation.asgi:application"]
```

### `web_ui/Dockerfile.production`

```dockerfile
# syntax=docker/dockerfile:1.7

ARG NODE_VERSION=22.14.0-alpine
# Pulled from the same root build that produced the backend
# wheels. Jenkinsfile passes the per-build tag.
ARG BACKEND_IMAGE=gain-web-api-prod:latest

# 1) Angular SPA.
FROM node:${NODE_VERSION} AS angular
WORKDIR /app
COPY web_ui/package.json web_ui/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY web_ui/ ./
RUN npm run build

# 2) Django collectstatic — uses the backend image as a base
#    so the same wheels and INSTALLED_APPS produce the static
#    output. STATIC_ROOT overridden to /static for this stage;
#    settings_default works (no DB required for collectstatic).
FROM ${BACKEND_IMAGE} AS django-static
ENV DJANGO_SETTINGS_MODULE=web_annotation.settings_default \
    STATIC_ROOT=/static
RUN mkdir -p /static \
 && python -m django collectstatic --noinput --clear

# 3) Apache runtime.
FROM httpd:2.4-alpine AS runtime
COPY --from=angular /app/dist/frontend/browser \
     /usr/local/apache2/htdocs/
COPY --from=django-static /static \
     /usr/local/apache2/htdocs/static/
COPY web_ui/httpd.conf /usr/local/apache2/conf/httpd.conf

EXPOSE 80
# httpd:2.4-alpine's default CMD is `httpd-foreground`, which
# is exactly the single foreground process we want — no
# override needed.
```

### `web_ui/httpd.conf` (new)

```apache
ServerRoot "/usr/local/apache2"
ServerName localhost
Listen 80
User daemon
Group daemon

LoadModule mpm_event_module       modules/mod_mpm_event.so
LoadModule unixd_module           modules/mod_unixd.so
LoadModule authz_core_module      modules/mod_authz_core.so
LoadModule log_config_module      modules/mod_log_config.so
LoadModule mime_module            modules/mod_mime.so
LoadModule dir_module             modules/mod_dir.so
LoadModule alias_module           modules/mod_alias.so
LoadModule headers_module         modules/mod_headers.so
LoadModule deflate_module         modules/mod_filter.so
LoadModule deflate_module         modules/mod_deflate.so
LoadModule expires_module         modules/mod_expires.so
LoadModule rewrite_module         modules/mod_rewrite.so
LoadModule remoteip_module        modules/mod_remoteip.so
LoadModule proxy_module           modules/mod_proxy.so
LoadModule proxy_http_module      modules/mod_proxy_http.so
LoadModule proxy_wstunnel_module  modules/mod_proxy_wstunnel.so
LoadModule setenvif_module        modules/mod_setenvif.so

ErrorLog  /proc/self/fd/2
CustomLog /proc/self/fd/1 common

TypesConfig conf/mime.types

DocumentRoot "/usr/local/apache2/htdocs"
<Directory "/usr/local/apache2/htdocs">
    Options FollowSymLinks
    AllowOverride None
    Require all granted
    DirectoryIndex index.html

    # SPA fallback: anything that isn't a file or directory
    # falls back to index.html so client-side routing works.
    RewriteEngine On
    RewriteCond %{REQUEST_FILENAME} !-f
    RewriteCond %{REQUEST_FILENAME} !-d
    RewriteCond %{REQUEST_URI} !^/api/
    RewriteCond %{REQUEST_URI} !^/ws/
    RewriteCond %{REQUEST_URI} !^/static/
    RewriteRule ^ /index.html [L]
</Directory>

# Django admin / DRF static, baked in at build time.
Alias /static/ "/usr/local/apache2/htdocs/static/"
<Directory "/usr/local/apache2/htdocs/static/">
    Options FollowSymLinks
    AllowOverride None
    Require all granted
    ExpiresActive On
    ExpiresDefault "access plus 1 year"
    Header set Cache-Control "public, immutable"
</Directory>

# Long cache on hashed Angular assets, no-cache on index.html
# so a deploy is picked up immediately.
<Files "index.html">
    Header set Cache-Control "no-store, no-cache, must-revalidate"
</Files>
<FilesMatch "\.(js|css|woff2?|svg|png|jpg|jpeg|gif|ico)$">
    ExpiresActive On
    ExpiresDefault "access plus 1 year"
    Header set Cache-Control "public, immutable"
</FilesMatch>

# Reverse-proxy /api/ to daphne.
ProxyPreserveHost On
ProxyRequests Off
RequestHeader set X-Forwarded-Proto "%{REQUEST_SCHEME}e"
ProxyPass        /api/ http://backend:9001/api/
ProxyPassReverse /api/ http://backend:9001/api/

# WebSockets.
ProxyPass        /ws/ ws://backend:9001/ws/
ProxyPassReverse /ws/ ws://backend:9001/ws/

# Compression for text types.
AddOutputFilterByType DEFLATE \
    text/plain text/html text/css text/xml \
    application/javascript application/json image/svg+xml
```

### `backend-migrate` compose service

```yaml
backend-migrate:
  image: ${COMPOSE_PROJECT_NAME}-backend:latest
  build:
    context: ..
    dockerfile: web_api/Dockerfile.production
  depends_on:
    db: { condition: service_healthy }
  environment:
    DJANGO_SETTINGS_MODULE: web_annotation.settings_daphne
    GPFWA_DB_HOST: db
    GPFWA_DB_NAME: gpfwa
    GPFWA_DB_USER: postgres
    GPFWA_DB_PASSWORD: secret
    GPFWA_DB_PORT: 5432
    GPFWA_SECRET_KEY: "django-insecure-..."
  entrypoint: ["python", "-m", "django", "migrate", "--noinput"]
  restart: "no"
```

The long-running `backend` service then:

```yaml
backend:
  ...
  depends_on:
    backend-migrate: { condition: service_completed_successfully }
    db:              { condition: service_healthy }
    mail:            { condition: service_started }
```

The `e2e` variant (`backend-e2e-migrate` + `backend-e2e`)
mirrors this with `settings_e2e`.

### Jenkinsfile `web_e2e` stage

```groovy
sh '''
    mkdir -p web_e2e/reports reports/web_e2e
    BACKEND_IMG="gain-web-api-prod:${BUILD_NUMBER}"
    FRONTEND_IMG="gain-web-ui-prod:${BUILD_NUMBER}"
    docker build \
        -f web_api/Dockerfile.production \
        -t "$BACKEND_IMG" .
    docker build \
        -f web_ui/Dockerfile.production \
        --build-arg BACKEND_IMAGE="$BACKEND_IMG" \
        -t "$FRONTEND_IMG" .
    BACKEND_IMAGE="$BACKEND_IMG" \
    FRONTEND_IMAGE="$FRONTEND_IMG" \
    docker compose -p "$COMPOSE_PROJECT" \
        -f web_infra/compose-jenkins.yaml \
        build e2e-tests
    BACKEND_IMAGE="$BACKEND_IMG" \
    FRONTEND_IMAGE="$FRONTEND_IMG" \
    docker compose -p "$COMPOSE_PROJECT" \
        -f web_infra/compose-jenkins.yaml \
        run --rm e2e-tests || TEST_RC=$?
    cp web_e2e/reports/junit-report.xml \
        reports/web_e2e/junit.xml
    exit ${TEST_RC:-0}
'''
```

`compose-jenkins.yaml`'s `backend-e2e` and `frontend-e2e`
services switch from `build:` to
`image: ${BACKEND_IMAGE:-gain-web-api-prod:latest}` and
`image: ${FRONTEND_IMAGE:-gain-web-ui-prod:latest}` — they
consume what Jenkins already built rather than rebuilding.

The `||` + `exit ${TEST_RC:-0}` pattern fixes the
JUnit-report-not-published issue from build #39.

### Files retired

- `web_infra/Dockerfile.gpf`
- `web_infra/Dockerfile.ubuntu`
- `web_api/scripts/supervisord.conf`
- `web_api/scripts/supervisord-bootstrap.sh`
- `web_api/scripts/wait-for-it.sh`
- `web_api/environment.yml`
- `web_ui/scripts/localhost.conf`
- `web_ui/scripts/supervisord.conf`
- `web_ui/scripts/supervisord-bootstrap.sh`
- `web_ui/scripts/wait-for-it.sh`
- The `static-data`, `gpf-image`, `ubuntu-image` service
  blocks across the four compose files.

## Step-by-step

1. **Backend rewrite**:
   - New `web_api/Dockerfile.production` (full body above).
   - Local smoke-build: `docker build -f web_api/Dockerfile.production
     -t gain-web-api-prod:smoke .`. Inspect: image < 300 MB,
     `daphne` resolves on `$PATH`.
   - Local smoke-run with a one-off postgres + mailhog: confirm
     daphne starts and `/api/user_info` responds.
2. **Frontend rewrite**:
   - New `web_ui/Dockerfile.production` (3-stage above) and
     new `web_ui/httpd.conf`.
   - Local smoke-build:
     `docker build -f web_ui/Dockerfile.production
     --build-arg BACKEND_IMAGE=gain-web-api-prod:smoke
     -t gain-web-ui-prod:smoke .`.
     Inspect: SPA `index.html` and Django `/static/admin/css/`
     both present; image < 80 MB.
3. **Compose updates**:
   - Add `backend-migrate` (and `backend-e2e-migrate`) to all
     four compose files; rewire `backend` / `backend-e2e`
     `depends_on` to it.
   - Drop `static-data`, `ubuntu-image`, `gpf-image`,
     `volumes_from: static-data` everywhere. Drop the legacy
     `EXPOSE 80 443` from production backend (compose port
     map only exposes 9001 anyway).
   - Switch `frontend` / `frontend-e2e` build to the new
     `Dockerfile.production` (it already points there).
   - For `compose-jenkins.yaml` (used by Jenkins e2e): switch
     `backend-e2e` and `frontend-e2e` to consume images via
     `image:` env-vars from the Jenkins stage rather than
     rebuilding.
   - `docker compose -f web_infra/compose-jenkins.yaml config`
     parses cleanly.
4. **Jenkinsfile**:
   - Drop the `rattler-build publish` step from the `web_e2e`
     stage (no longer needed).
   - Wrap the e2e run with `|| TEST_RC=$?` and `exit
     ${TEST_RC:-0}` so JUnit gets copied even on test
     failure (the build #39 lesson).
   - Add `gain-web-api-prod` and `gain-web-ui-prod` to the
     `post.cleanup` `for img` loop.
5. **Retire dead files**:
   - `git rm web_infra/Dockerfile.gpf web_infra/Dockerfile.ubuntu`
   - `git rm web_api/scripts/{supervisord.conf,supervisord-bootstrap.sh,wait-for-it.sh}`
   - `git rm web_ui/scripts/{localhost.conf,supervisord.conf,supervisord-bootstrap.sh,wait-for-it.sh}`
   - `git rm web_api/environment.yml`
6. **Verify locally end-to-end** (see Verification block).
7. **Update `docs/2026-04-25-merge-roadmap.md`** to flip
   Phase 8 to DONE and link this plan.
8. **Commit in five logical chunks**:
   - *(a) Add wheel-based web_api Dockerfile.production* —
     new Dockerfile only; old one renamed away in a later
     commit so smoke builds work mid-stack.
   - *(b) Add Apache-based web_ui Dockerfile.production +
     httpd.conf* — new files only; old Dockerfile.production
     renamed away.
   - *(c) Wire compose to new images + add backend-migrate* —
     all four compose files; drop static-data /
     ubuntu-image / gpf-image services.
   - *(d) Simplify root Jenkinsfile e2e stage and wrap junit
     copy* — remove rattler-build publish, add `|| TEST_RC`,
     add new images to cleanup loop.
   - *(e) Retire conda-era production stack* — git rm
     Dockerfile.gpf, Dockerfile.ubuntu, all supervisord
     scripts, web_api/environment.yml; mark Phase 8 DONE in
     the roadmap.

## Critical files

- `/home/lubo/Work/seq-pipeline/gain/web_api/Dockerfile.production`
  (rewritten)
- `/home/lubo/Work/seq-pipeline/gain/web_ui/Dockerfile.production`
  (rewritten)
- `/home/lubo/Work/seq-pipeline/gain/web_ui/httpd.conf`
  (created)
- `/home/lubo/Work/seq-pipeline/gain/web_infra/compose-jenkins.yaml`
  (services rewired)
- `/home/lubo/Work/seq-pipeline/gain/web_infra/compose.yaml`
  (same)
- `/home/lubo/Work/seq-pipeline/gain/web_infra/compose-iossifovweb.yaml`
  (same)
- `/home/lubo/Work/seq-pipeline/gain/web_infra/compose-wigclust.yaml`
  (same)
- `/home/lubo/Work/seq-pipeline/gain/Jenkinsfile`
  (`web_e2e` stage simplified, cleanup loop extended)
- `/home/lubo/Work/seq-pipeline/gain/docs/2026-04-25-merge-roadmap.md`
  (Phase 8 row → DONE on completion)
- `/home/lubo/Work/seq-pipeline/gain/web_infra/Dockerfile.gpf`
  (deleted)
- `/home/lubo/Work/seq-pipeline/gain/web_infra/Dockerfile.ubuntu`
  (deleted)
- `/home/lubo/Work/seq-pipeline/gain/web_api/environment.yml`
  (deleted)
- `/home/lubo/Work/seq-pipeline/gain/web_api/scripts/{supervisord.conf,supervisord-bootstrap.sh,wait-for-it.sh}`
  (deleted)
- `/home/lubo/Work/seq-pipeline/gain/web_ui/scripts/{localhost.conf,supervisord.conf,supervisord-bootstrap.sh,wait-for-it.sh}`
  (deleted)

## Reference files

- `/home/lubo/Work/seq-pipeline/gain/web_api/web_annotation/asgi.py`
  — confirms ASGI target `web_annotation.asgi:application`.
- `/home/lubo/Work/seq-pipeline/gain/web_api/web_annotation/settings_daphne.py`
  — production settings module; `STATIC_ROOT='/static/gpf/static'`
  is replaced by `/static` (passed via env to django-static
  build stage), `STATIC_URL='/static/'` is preserved.
- `/home/lubo/Work/seq-pipeline/gain/web_api/web_annotation/settings_default.py`
  — base config (used in the `django-static` collectstatic
  stage); no DB required for collectstatic so this works.
- `/home/lubo/Work/seq-pipeline/gain/Jenkinsfile`
  — `runProject('web_api', ...)` already produces
  `dist/web_api/*.whl`; `runProject('core', ...)` produces
  `dist/core/*.whl`. Both are the inputs to the new backend
  Dockerfile.
- `/home/lubo/Work/seq-pipeline/gain/web_e2e/playwright.config.ts`
  — `baseURL: 'http://frontend'`, no changes needed.

## Risks and known unknowns

- **`pysam`/`pybigwig` wheel availability on `python:3.12-slim`.**
  Both ship manylinux wheels for cpython 3.12; should
  install cleanly. If a transitive `gain-core` dep doesn't
  have a manylinux wheel, the builder stage needs build deps
  (gcc, libhts-dev, etc.) — keep those isolated to the
  builder stage so the runtime image stays slim. Verify on
  first smoke build.
- **`psycopg` wheels.** psycopg3 ships binary wheels with
  bundled libpq; no apt dep typically required. If the
  binary wheel isn't picked up (`psycopg[binary]` extra),
  add `libpq5` to the runtime apt list.
- **Build-time coupling between frontend and backend.** The
  frontend Dockerfile uses
  `FROM ${BACKEND_IMAGE} AS django-static`, so the backend
  image must exist (locally or in a registry) before the
  frontend builds. Jenkins's web_e2e stage handles this
  ordering; local developers building only the frontend need
  to either build the backend first or set
  `BACKEND_IMAGE=gain-web-api-prod:latest` to whatever
  reference makes sense in their workflow.
- **`collectstatic` at frontend build time.** Requires
  `INSTALLED_APPS` to be importable cleanly without a DB.
  Django allows this for `collectstatic` (it doesn't touch
  the ORM), but if any custom app has a side-effect at
  import time that touches the DB or the network, it'll
  fail. None of `web_annotation`'s apps appear to do that;
  verify on the smoke build.
- **`backend-migrate` ordering vs deploy.** With three
  compose files (jenkins, iossifovweb, wigclust) all
  needing the same migrate step, mistakes are likely.
  Helper: factor `backend-migrate` into a single compose
  fragment via `extends:` or YAML anchor where possible.
- **Healthcheck preservation.** Today's
  `curl http://localhost:9001/api/user_info` healthcheck
  passes because curl returns 0 on any HTTP response. The
  new `daphne`-only image still binds 9001 and serves
  `/api/user_info`; same passing condition. No change
  required, but verify on smoke run.
- **WebSocket reverse proxy through Apache.**
  `mod_proxy_wstunnel` is compiled into the
  `httpd:2.4-alpine` image but isn't `LoadModule`-d by
  default; the new `httpd.conf` enables it explicitly. The
  `ws://backend:9001/ws/` rewrite preserves the upgrade
  handshake. Verify the e2e suite's WebSocket-using spec
  (`anonymous-user.spec.ts`) passes.
- **`web_e2e/playwright.config.ts` baseURL.** Currently
  `http://frontend`; Apache listens on 80 by default which
  matches. No change.
- **/static path stability.** Django and the Angular SPA
  both expect `/static/` to resolve. After the rewrite,
  Apache serves `/static/*` from
  `/usr/local/apache2/htdocs/static/` (the collectstatic
  output) — same URL contract. No client-side code change.
- **Apache module list.** The `httpd:2.4-alpine` image ships
  with most modules built but not enabled in the default
  `httpd.conf`. The new config explicitly `LoadModule`s
  every module the vhost uses. If a module name moves
  between Apache point releases, the build fails fast at
  `httpd-foreground` startup, surfacing the issue immediately.

## Verification end-to-end

```bash
cd /home/lubo/Work/seq-pipeline/gain

# 1. Wheels exist (root Jenkinsfile parallel-block output).
ls dist/core/gain_core-*.whl
ls dist/web_api/django_gpf_web_annotation-*.whl

# 2. Backend image builds and is small.
docker build -f web_api/Dockerfile.production \
    -t gain-web-api-prod:smoke .
docker image ls gain-web-api-prod:smoke
# expect: SIZE < 300 MB

# 3. Backend image starts daphne cleanly (smoke).
docker run --rm -d --name backend-smoke \
    -e GPFWA_SECRET_KEY=x \
    -e GPFWA_DB_HOST=db -e GPFWA_DB_NAME=gpfwa \
    -e GPFWA_DB_USER=postgres -e GPFWA_DB_PASSWORD=secret \
    -p 9001:9001 \
    gain-web-api-prod:smoke
sleep 3
curl -fs http://localhost:9001/api/user_info -o /dev/null \
    || echo 'OK if curl exits non-zero with 401/403'
docker rm -f backend-smoke

# 4. Frontend image builds (depends on backend image).
docker build -f web_ui/Dockerfile.production \
    --build-arg BACKEND_IMAGE=gain-web-api-prod:smoke \
    -t gain-web-ui-prod:smoke .
docker image ls gain-web-ui-prod:smoke
# expect: SIZE < 80 MB

# 5. Frontend image serves SPA + Django static.
docker run --rm -d --name frontend-smoke \
    -p 8080:80 gain-web-ui-prod:smoke
sleep 1
curl -fs http://localhost:8080/ -o /dev/null
curl -fs http://localhost:8080/static/admin/css/base.css -o /dev/null \
    && echo "Django static present"
docker rm -f frontend-smoke

# 6. End-to-end stack via compose.
docker compose -p gain-phase8-verify \
    -f web_infra/compose-jenkins.yaml up -d --wait \
    db backend-migrate
docker compose -p gain-phase8-verify \
    -f web_infra/compose-jenkins.yaml ps backend-migrate
# expect: state = exited (0)

# 7. Phase-6 e2e suite still passes against new images.
BACKEND_IMAGE=gain-web-api-prod:smoke \
FRONTEND_IMAGE=gain-web-ui-prod:smoke \
docker compose -p gain-phase8-verify \
    -f web_infra/compose-jenkins.yaml \
    run --rm e2e-tests
grep -E '<testsuite[s]? ' web_e2e/reports/junit-report.xml | head
# expect: tests="139" failures="0" errors="0" (or close to it)

# 8. Compose configs across all four files parse.
for f in compose.yaml compose-jenkins.yaml \
         compose-iossifovweb.yaml compose-wigclust.yaml; do
    docker compose -f "web_infra/$f" config > /dev/null
done

# 9. Cleanup.
docker compose -p gain-phase8-verify \
    -f web_infra/compose-jenkins.yaml down -v --remove-orphans
docker rmi gain-web-api-prod:smoke gain-web-ui-prod:smoke
```

If steps 1–9 succeed, push the commits. The next master
build's `web_e2e` stage should run identically to today's
shape but on the new images, in less wall-clock time
(no conda-pack, faster collectstatic, smaller images).

## After Phase 8

- **Phase 9 (deployment modernization, separate)**: image
  registry + pull-deploy on the prod hosts; Caddy/Traefik in
  front for TLS; small Loki/Grafana for logs.
- The conda dev workflow (root `environment.yml` /
  `dev-environment.yml` + CLAUDE.md section) stays
  documented; retiring it remains optional and not gated on
  Phase 8.

The merge roadmap doc gets the Phase 8 row flipped to DONE in
the same change that lands this work.
