# `gain-infra` Ansible deployment — design

Captures the design decisions reached during the grill-me
interview on 2026-04-29. The implementation lives in a new
repo called `gain-infra`, separate from the gain monorepo.
This document is the source of truth for *why* the playbook
looks the way it does; the playbook's own `README.md` is the
operator runbook.

For the one-time cutover from the current `gpfwa` deployment
to the new `gainwa` layout, see
[2026-04-29-gpfwa-to-gainwa-migration.md](./2026-04-29-gpfwa-to-gainwa-migration.md).

## What the playbook does

Deploys the gain web application stack
(`registry.seqpipe.org/gain-web-api` +
`gain-web-ui` + Postgres + optional mailhog) to one or more
hosts via `docker compose`, owned by a systemd unit, with
pre-deploy database snapshots and automatic rollback on
healthcheck failure.

What it does NOT do:

- Bootstrap the host (Docker install, user creation, firewall,
  CephFS mounts) — host is a precondition.
- Manage the upstream reverse proxy / TLS — assumed external
  (host nginx/Apache, configured out of band).
- Standing scheduled backups — only pre-deploy snapshots. A
  separate cron / playbook / external tool covers daily
  backup if/when added.
- Trigger itself — operator-driven, no Jenkins integration in
  this iteration.

## Hosts in scope

| Alias | FQDN | Group | Mail backend | GRR mounts |
|---|---|---|---|---|
| `iossifovweb` | `iossifovweb.iossifovlab.com` | `gainwa_prod` | external (Mailjet) | `/mnt/cephfs/seqpipe/grr` + `/mnt/cephfs/seqpipe/grr_encode` |
| `piglet` | `piglet.seqpipe.org` | `gainwa_internal` | mailhog (in-cluster) | `/mnt/wigclust3/data/unsafe/chorbadj/grr.sync` |

## Repository layout

```
gain-infra/
├── ansible.cfg
├── deploy.yml                          # single playbook
├── inventory/hosts.yml
├── group_vars/all/{vars.yml,vault.yml}
├── host_vars/iossifovweb/{vars.yml,vault.yml}
├── host_vars/piglet/{vars.yml,vault.yml}
├── files/compose.yaml                  # canonical, copied as-is
├── templates/
│   ├── env.j2                          # /opt/gainwa/.env
│   ├── gainwa.service.j2               # systemd unit
│   └── gainwa-logrotate.j2             # /etc/logrotate.d/gainwa
├── requirements.yml                    # community.docker, community.postgresql
└── README.md                           # operator runbook
```

Flat playbook layout (one `deploy.yml`) chosen over a
roles-based layout: a single application stack doesn't
justify the role indirection, and tagged `block:`s inside the
playbook give operators selective entry-points
(`--tags healthcheck`, `--tags backup`, etc.) without
restructuring.

`inventory/hosts.yml`:

```yaml
all:
  children:
    gainwa:
      children:
        gainwa_prod:
          hosts:
            iossifovweb:
              ansible_host: iossifovweb.iossifovlab.com
        gainwa_internal:
          hosts:
            piglet:
              ansible_host: piglet.seqpipe.org
```

## Compose file

A single canonical `files/compose.yaml` covers both hosts.
Per-host differences are driven entirely by `${VAR}`
substitution from a templated `/opt/gainwa/.env`. The
compose file is copied as-is (no Jinja); the `.env` is the
only Jinja-rendered file.

Structural differences between hosts (the `mail` service
exists on `piglet` but not `iossifovweb`) are handled with
**compose profiles**: `mail` is declared with
`profiles: ["internal-mail"]`, and each host's `.env` sets
`COMPOSE_PROFILES` accordingly (empty on iossifovweb,
`internal-mail` on piglet).

The retired files `web_infra/compose-iossifovweb.yaml` and
`web_infra/compose-wigclust.yaml` in the gain monorepo are
deleted in the same PR that ships gain-infra's first
release. The CI/dev variants (`compose-jenkins.yaml`,
`compose.yaml`) stay.

The compose project is renamed `gpfwa` → `gainwa`. Container
names, the Docker network, and bind-mount path conventions
all change accordingly. The `GPFWA_*` environment variable
names (read by Django code) are **not** renamed — that's an
application change, out of scope.

## Image versioning

`gain_image_tag` is an explicit Ansible var (per-host or in
`group_vars/all/`), templated into both `BACKEND_IMAGE` and
`FRONTEND_IMAGE` lines of `.env`. Both images move in
lockstep — same tag for both. Default value is `stable`.

Promotion = PR to gain-infra bumping `gain_image_tag` →
operator merges → operator runs the playbook. The git history
of gain-infra is the deployment audit trail.

## Secrets

Ansible Vault inside `gain-infra`. Encrypted vars files:

- `group_vars/all/vault.yml` — registry credentials shared
  across hosts.
- `host_vars/<host>/vault.yml` — per-host secrets:
  - `vault_secret_key` (Django `GPFWA_SECRET_KEY`)
  - `vault_postgres_password`
  - `vault_email_host_user` / `vault_email_host_password`
    (iossifovweb only — Mailjet credentials)
  - `vault_ansible_become_password` — sudo password,
    referenced from `vars.yml` as
    `ansible_become_password: "{{ vault_ansible_become_password }}"`

Vault password lives in `~/.ansible/vault-pass` (mode 0600)
on the operator's machine. `ansible.cfg` references it via
`vault_password_file`. The same vault unlock provides both
app secrets and the sudo escalation password — no
interactive prompts during deploy.

## Operator workflow

Operator SSHes from their laptop as their own user (e.g.
`lubo`). Ansible escalates with `become: yes` using the
vault-stored sudo password. SSH user is set per-host in
`host_vars/<host>/vars.yml` (different login on each host).

```bash
ansible-playbook -i inventory/hosts.yml deploy.yml \
    --limit iossifovweb
```

(Vault password and sudo password come from the file +
vault, no flags needed.)

## Service supervision

A systemd unit, `gainwa.service`, owns the lifecycle of the
compose project. Templated by Ansible from
`templates/gainwa.service.j2`:

```ini
[Unit]
Description=GAIn web application stack
Requires=docker.service
After=docker.service network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/gainwa
EnvironmentFile=/opt/gainwa/.env
ExecStart=/usr/bin/docker compose up
ExecStop=/usr/bin/docker compose down
Restart=always
RestartSec=10s

[Install]
WantedBy=multi-user.target
```

Long-running services in the compose file additionally carry
`restart: unless-stopped` as defense in depth. The
`*-migrate` services keep `restart: "no"`.

## Deploy flow

The playbook's main `block:` for the deploy step:

1. **Authenticate to registry.** `community.docker.docker_login`
   with vault creds; persists to `/root/.docker/config.json`.
2. **Read current `BACKEND_IMAGE`** from `/opt/gainwa/.env`
   and register as `previous_backend_image` (for rollback).
3. **Pull new images.** `community.docker.docker_image`
   pulls backend + frontend by tag; the module's `changed`
   flag tells us whether the image digest actually moved
   (so floating tags like `:stable` still trigger a redeploy
   when the registry pushes a new digest under the same name).
4. **Pre-deploy snapshot.** If the db container is running
   (it isn't on first-ever deploy), shell out to
   `docker exec gainwa-db-1 pg_dump -Fc gpfwa | gzip` →
   `/opt/gainwa/backups/pre-deploy-<ts>.sql.gz`.
5. **Render config.** Copy `compose.yaml`, render `.env`
   (mode 0600), render systemd unit (notifies the `Reload
   systemd` handler), render logrotate config.
6. **`systemctl stop gainwa`.**
7. **Run migrations explicitly.**
   `docker compose run --rm --remove-orphans backend-migrate`
   — owned by the playbook, not by compose. The compose
   file's `backend` service deliberately does NOT depend on
   `backend-migrate` so that the migration step is a
   discrete, fail-closed Ansible task. The db container that
   `compose run` brings up as a dependency is left running
   and reused by the subsequent start.
8. **`systemctl start gainwa`.** Compose brings up backend,
   then frontend. (Db is already up from step 7.)
9. **Healthcheck.** `uri:` module polls
   `http://127.0.0.1:{{ frontend_port }}/api/jobs/genomes`
   until it returns 200 or
   `gainwa_healthcheck_timeout_seconds` (default 120s)
   elapses. Local-only check; the upstream proxy is not
   exercised.
10. **On success:** `docker image prune -af --filter
    "until=168h"` (7 days). End of play.

A migration failure (step 7), startup failure (step 8), or
healthcheck timeout (step 9) all fall into the same rescue
block:

1. Capture `docker compose logs --tail=200`; if migration
   failed, also dump migrate stdout/stderr.
2. `docker compose down` to a known state.
3. Bring up **only** db (`compose up -d --wait db`).
4. Wait for `pg_isready`.
5. Restore the pre-deploy `pg_dump` (drop + create + restore).
6. `docker compose down` again — schema is now old, but
   we don't want to start anything yet.
7. Revert `BACKEND_IMAGE` and `FRONTEND_IMAGE` lines in
   `.env` to `previous_backend_image`.
8. `systemctl start gainwa` — full stack on old images
   against restored data.
9. Fail the play with a clear error.

The deliberate ordering (restore the dump while *only* db
is up, before any app code starts) avoids the failure mode
where old-image backend briefly runs against a
partially-forward-migrated schema.

## Persistent state and external mounts

Ansible owns:

- `/opt/gainwa/` (root)
- `/opt/gainwa/gainwa-pg-data/` — Postgres data
- `/opt/gainwa/gainwa-data/` — Django app data
- `/opt/gainwa/gainwa-logs/` — application logs
- `/opt/gainwa/backups/` — pre-deploy `pg_dump`s

Created on first run (idempotent), `root:root 0755`.
Containers run as root inside; files written to volumes are
root-owned on the host.

Ansible does **not** own the GRR mounts. Each host's
`vars.yml` has `grr_mount_path` (and optionally
`grr_encode_mount_path`); the playbook calls
`ansible.builtin.stat` on the path and fails fast if it's
not a mountpoint. The compose file expects both `/grr` and
`/grr_encode` mounts because that's what
`grr-definition-dir.yaml` (baked into the production image)
references; on piglet, only `/grr` is mounted today — that
inconsistency is preserved (the optional mount is gated by
whether the host var is set).

## Reverse proxy

Each host already has an upstream proxy handling TLS and
forwarding to the compose stack's published port (9002 on
iossifovweb, 8000 on piglet). The playbook publishes only on
those ports and treats the proxy as a precondition. The
post-deploy healthcheck talks directly to `127.0.0.1:<port>`,
not through the proxy, so DNS / TLS / proxy-config issues
don't mask deploy success.

## Idempotency and update behavior

- **No-op runs:** the playbook detects no-change cases by
  comparing the *image digest* before and after
  `docker compose pull` (via
  `community.docker.docker_image_info`). If digests are
  unchanged and `.env` rendered identically, the
  stop/start cycle is skipped. This catches floating tags
  like `:stable` correctly: a new image content with the
  same tag string still triggers a redeploy.
- **First-time runs:** same playbook. Idempotent
  prereq tasks (`mkdir`, systemd unit install, registry
  login) handle the difference automatically. No separate
  bootstrap playbook.
- **Image cleanup:** `docker image prune -af --filter "until=168h"`
  runs only on a successful deploy. The 7-day window keeps
  enough rollback targets on disk.

## Standing concerns

- **Logrotate.** Ansible installs
  `/etc/logrotate.d/gainwa`: rotates the three log files in
  `gainwa-logs/` daily, 14 days retention, compressed,
  `copytruncate` (the backend keeps file handles open).
  Defense in depth: the compose file also sets reasonable
  `logging:` driver limits for stdout/stderr.
- **Scheduled backups: out of scope.** The pre-deploy
  `pg_dump` is the only backup the playbook produces. Daily
  / offsite backups are deferred to a separate tool. The
  README flags this gap explicitly so it's not silently
  overlooked.

## What's deferred

- **Standing daily backups** with offsite copy.
- **Jenkins-driven deploys.** The current model is operator
  pulls trigger; a future Jenkins job that opens
  `gain_image_tag`-bump PRs to gain-infra is a strict
  superset of today's design and requires no playbook
  changes.
- **Bootstrap automation.** A separate `bootstrap.yml` for
  fresh hosts (Docker install, user creation, mounts) can be
  added later without restructuring `deploy.yml`.
- **Per-service systemd or rootless Docker.** The current
  design runs the daemon and stack as root. A non-root
  rework is a meaningful security improvement but a
  meaningful refactor; deferred.

## Operator quick reference

```bash
# Deploy a new image tag to iossifovweb:
#   1. Edit host_vars/iossifovweb/vars.yml, bump
#      gain_image_tag.
#   2. Commit + merge to main.
#   3. Run:
ansible-playbook -i inventory/hosts.yml deploy.yml \
    --limit iossifovweb

# Just verify health (no deploy):
ansible-playbook -i inventory/hosts.yml deploy.yml \
    --limit iossifovweb --tags healthcheck

# Manual rollback (after successful deploy that turned out
# bad): revert host_vars/iossifovweb/vars.yml to the previous
# tag, commit, run the playbook. The pre-deploy pg_dump from
# the bad deploy is still on the host under
# /opt/gainwa/backups/ if a data rollback is also needed.
```
