# Plan: Phase 9 (slice 1) — push gain-web-api / gain-web-ui to registry.seqpipe.org

## Context

The Phase-8 production images
(`gain-web-api-prod` = wheel-based `python:3.12-slim` backend,
`gain-web-ui-prod` = `httpd:2.4-alpine` frontend with Django
collectstatic baked in) are currently built only inside the
downstream `gain-web-e2e` Jenkins job, with a per-build tag
that gets `docker rmi`'d when the job finishes. Nothing
publishes them anywhere.

Phase 9 is the deployment-modernization tail (per
`docs/2026-04-25-merge-roadmap.md`). The first slice is
**image registry + push**: build the prod images in the root
`Jenkinsfile` and push them to `registry.seqpipe.org` (the
internal registry already used by `core/gain/dask/named_cluster.yaml`
for `iossifovlab-gpf:latest`). Once images are in the
registry, prod hosts can switch to `docker compose pull && up
-d` instead of build-on-host (a follow-up slice), and the
e2e job can pull instead of rebuild (also a follow-up).

## Scope

**In scope:**

- Add a new `Build & push prod images` stage to the root
  `Jenkinsfile`, placed after `Conda packages` and before
  `Trigger web_e2e`. The stage:
  - Builds `web_api/Dockerfile.production` tagged
    `registry.seqpipe.org/gain-web-api:${BUILD_NUMBER}` and
    additionally `:${GIT_SHA_SHORT}` (8-char prefix of
    `env.GIT_COMMIT`).
  - Builds `web_ui/Dockerfile.production` with
    `--build-arg BACKEND_IMAGE=registry.seqpipe.org/gain-web-api:${BUILD_NUMBER}`,
    tagged `registry.seqpipe.org/gain-web-ui:${BUILD_NUMBER}`
    and `:${GIT_SHA_SHORT}`.
  - On `master` only: also tags both as `:latest` and pushes
    all three tags (build number, short SHA, latest) for both
    repositories.
  - On non-master branches: builds the images (validates the
    Dockerfiles + that the wheels install) but skips the push
    entirely, with an `echo` log message.
- Extend `post.cleanup` in the same `Jenkinsfile` to
  `docker rmi` the new registry-prefixed tags
  (`registry.seqpipe.org/gain-web-api:${BUILD_NUMBER}` etc.)
  so the agent's image store doesn't accumulate across runs.
  The existing CI-image cleanup loop is preserved
  unchanged.

**Out of scope:**

- **Rewiring `gain-web-e2e` to pull from the registry**
  instead of rebuilding its own backend/frontend prod
  images. Would save ~5 min per e2e run but introduces a
  registry-pull dependency in the e2e job; tackle in a
  separate slice.
- **Pull-deploy on prod hosts**
  (`compose-iossifovweb.yaml` / `compose-wigclust.yaml`
  switching from `build:` to `image:` referencing the
  registry). Next Phase-9 slice.
- **TLS modernization (Caddy / Traefik)** and
  **observability (Loki / Grafana)** — later Phase-9 slices.
- **Branch-build pushes** — only master pushes for now.
- **Image vulnerability scanning** before push — out of
  scope; can be added as a `docker scout` or `trivy` step
  later if desired.

## Approach

### New `Build & push prod images` stage

Placed between `Conda packages` and `Trigger web_e2e`:

```groovy
stage('Build & push prod images') {
    environment {
        REGISTRY      = 'registry.seqpipe.org'
        BACKEND_REPO  = "${env.REGISTRY}/gain-web-api"
        FRONTEND_REPO = "${env.REGISTRY}/gain-web-ui"
        GIT_SHORT     = "${env.GIT_COMMIT.take(8)}"
    }
    steps {
        sh '''
            # Build backend; tag with build number first so
            # the frontend's --build-arg can reference it.
            docker build \\
                -f web_api/Dockerfile.production \\
                -t "$BACKEND_REPO:$BUILD_NUMBER" .
            docker tag "$BACKEND_REPO:$BUILD_NUMBER" \\
                       "$BACKEND_REPO:$GIT_SHORT"

            # Build frontend; multi-stages collectstatic from
            # the backend image we just built.
            docker build \\
                -f web_ui/Dockerfile.production \\
                --build-arg \\
                    BACKEND_IMAGE="$BACKEND_REPO:$BUILD_NUMBER" \\
                -t "$FRONTEND_REPO:$BUILD_NUMBER" .
            docker tag "$FRONTEND_REPO:$BUILD_NUMBER" \\
                       "$FRONTEND_REPO:$GIT_SHORT"
        '''
        script {
            if (env.BRANCH_NAME == 'master') {
                sh '''
                    docker tag "$BACKEND_REPO:$BUILD_NUMBER" \\
                               "$BACKEND_REPO:latest"
                    docker tag "$FRONTEND_REPO:$BUILD_NUMBER" \\
                               "$FRONTEND_REPO:latest"
                    for repo in "$BACKEND_REPO" "$FRONTEND_REPO"; do
                        docker push "$repo:$BUILD_NUMBER"
                        docker push "$repo:$GIT_SHORT"
                        docker push "$repo:latest"
                    done
                '''
            } else {
                echo "Skipping registry push: " +
                     "branch is ${env.BRANCH_NAME}, not master"
            }
        }
    }
}
```

### Extended `post.cleanup`

Add a second loop after the existing CI-image cleanup that
removes the new registry-prefixed images. Mirror the
existing `|| true` pattern so a missing tag doesn't fail
the cleanup:

```groovy
sh '''
    # Existing CI-image cleanup loop stays:
    for img in gain-core-ci ... gain-conda-builder-ci; do
        docker rmi "$img:${BUILD_NUMBER}" 2>/dev/null || true
    done
    # New: registry-prefixed prod images. `:latest` only
    # exists on master but the rmi is harmless on branches.
    GIT_SHORT="${GIT_COMMIT:0:8}"
    for repo in registry.seqpipe.org/gain-web-api \
                registry.seqpipe.org/gain-web-ui; do
        for tag in "$BUILD_NUMBER" "$GIT_SHORT" latest; do
            docker rmi "$repo:$tag" 2>/dev/null || true
        done
    done
'''
```

(Two separate loops keeps the existing single-tag form
clean for the CI images that genuinely only have one tag,
while the prod-image cleanup handles three tags per repo.)

## Auth

The push uses two pre-provisioned secret-text credentials in
Jenkins:

- `jenkins-registry.seqpipe.org.user` — registry username
- `jenkins-registry.seqpipe.org.passwd` — registry password

Both are bound declaratively in the stage's `environment`
block:

```groovy
environment {
    REGISTRY_USER = credentials('jenkins-registry.seqpipe.org.user')
    REGISTRY_PASS = credentials('jenkins-registry.seqpipe.org.passwd')
}
```

The push step does login + push + logout in a single shell
so the registry auth state is short-lived (agents are
shared across jobs):

```sh
echo "$REGISTRY_PASS" | docker login \
    -u "$REGISTRY_USER" --password-stdin "$REGISTRY"
trap 'docker logout "$REGISTRY" || true' EXIT
# tag :latest, then push three tags × two repos
```

`--password-stdin` keeps the secret out of the process list
and shell trace; the `trap` ensures `docker logout` runs even
if a `docker push` fails.

The `environment` block evaluates on every build (master and
branches), so the credentials must be readable from the
multibranch context. Branch builds bind the env vars but
never use them — the `if (env.BRANCH_NAME == 'master')` gate
is the only caller.

## Verification

The new stage can't be exercised locally without registry
credentials, so verification has two phases:

**Local (build-only):**

```bash
cd /home/lubo/Work/seq-pipeline/gain
ls dist/core/gain_core-*.whl \
   dist/web_api/django_gpf_web_annotation-*.whl
docker build -f web_api/Dockerfile.production \
    -t registry.seqpipe.org/gain-web-api:smoke .
docker build -f web_ui/Dockerfile.production \
    --build-arg BACKEND_IMAGE=registry.seqpipe.org/gain-web-api:smoke \
    -t registry.seqpipe.org/gain-web-ui:smoke .
docker image ls registry.seqpipe.org/gain-web-{api,ui}:smoke
docker rmi registry.seqpipe.org/gain-web-{api,ui}:smoke
```

(Confirms the Dockerfile changes, if any, still build with
the registry-prefixed tag form.)

**Jenkins-side:**

1. First non-master build after this lands: stage runs the
   build path, logs `Skipping registry push: branch is …,
   not master`. Builds succeed; nothing pushed.
2. First master build: stage runs the build path then the
   push path; six pushes succeed (3 tags × 2 repos). Watch
   the Jenkins console for the `docker push` outputs.
3. From a workstation after the master build:
   ```bash
   docker pull registry.seqpipe.org/gain-web-api:latest
   docker pull registry.seqpipe.org/gain-web-ui:latest
   ```
   Both pulls succeed, with the digest matching what
   Jenkins's console reported.
4. The `gain-web-e2e` downstream job (unchanged in this
   slice) keeps passing — it still builds its own per-build
   prod images and runs against them.

## Risks and known unknowns

- **Auth not pre-configured.** Mitigation: the
  `withCredentials` fallback shape is in this doc; if the
  first master build 401s, plumbing a credential is a small
  follow-up.
- **Stage adds wall-clock time** to every build. Backend
  image build is dominated by the wheel install (~30s);
  frontend is dominated by `npm ci` + `ng build` + the
  django collectstatic stage (~3 min). Total ~3–4 min per
  build added — acceptable, and overlaps gain nothing with
  the next stage (`Trigger web_e2e`) which is fire-and-
  forget anyway.
- **Registry storage growth.** Three tags per repo per
  master build, with a build every push, accumulates
  steadily. The registry presumably has a retention policy
  (or can grow one); not our problem to solve here, but
  worth flagging.
- **Branch builds still consume agent disk** for the local
  build (no push, no cleanup of the registry, but the local
  images do get rmi'd in `post.cleanup`).

## Step-by-step

1. Edit `Jenkinsfile`: add the `Build & push prod images`
   stage between `Conda packages` and `Trigger web_e2e`;
   extend the `post.cleanup` shell block with the prod-image
   loop.
2. Smoke-build locally (commands above) — confirm the
   Dockerfiles still produce images under the
   registry-prefixed tag.
3. Create
   `docs/2026-04-25-phase-9-image-push.md` (this file).
4. Update `docs/2026-04-25-merge-roadmap.md`: under "Phases
   remaining" → "Phase 9", flip the "Image registry +
   pull-deploy" bullet to a sub-list noting "push (DONE
   with first slice)" + remaining "pull-deploy" work.
5. Commit in two chunks:
   - *(a) Build and push gain-web-api / gain-web-ui to
     registry.seqpipe.org* — Jenkinsfile change + cleanup
     loop extension.
   - *(b) Add Phase 9 first-slice design doc + update
     merge roadmap*.

## After this slice

Next Phase-9 candidates, in rough priority order:

- **Pull-deploy on prod**: switch
  `web_infra/compose-iossifovweb.yaml` and
  `compose-wigclust.yaml` to `image:
  registry.seqpipe.org/gain-web-{api,ui}:latest` (or a
  pinned tag), drop their `image:
  ${BACKEND_IMAGE:-...}` env-var indirection. Prod hosts
  run `docker compose pull && up -d` instead of
  build-on-host.
- **e2e job pulls**: rewire `gain-web-e2e` to
  `docker pull registry.seqpipe.org/gain-web-{api,ui}:${UPSTREAM_BUILD_NUMBER}`
  instead of rebuilding from wheels. Saves ~5 min per e2e
  run.
- **Caddy/Traefik**, **observability lite**, **conda dev
  workflow retirement**, **legacy seqpipe Dockerfile audit**
  — later slices, separately scoped.
