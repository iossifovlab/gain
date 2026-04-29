# Migrating production hosts from `gpfwa` to `gainwa`

One-time, host-by-host manual cutover. Run before the first
`gain-infra` Ansible deploy on each host. After cutover, the
host is owned by the playbook and all state lives under
`/opt/gainwa/`.

## Scope

Hosts in scope:

- `iossifovweb` — public production (Mailjet, GRR + GRR_ENCODE
  on `/mnt/cephfs/seqpipe/grr`).
- `piglet` (FQDN `piglet.seqpipe.org`) — internal CSHL
  deployment (mailhog, GRR only on
  `/mnt/wigclust3/data/unsafe/chorbadj/grr.sync`). The
  current per-host compose file is named
  `compose-wigclust.yaml` for historical reasons; the actual
  target host is `piglet`.

What changes:

- Compose project name: `gpfwa` → `gainwa` (container names,
  network, default volume prefix all change).
- Host paths: `<WD>/gpfwa-*` → `/opt/gainwa/gainwa-*`.
- Service supervision: ad-hoc `docker compose up -d` →
  systemd unit `gainwa.service`.
- Compose file source: `web_infra/compose-<host>.yaml` (this
  repo) → `/opt/gainwa/compose.yaml` (deployed by `gain-infra`).

What does NOT change:

- The Docker images (`registry.seqpipe.org/gain-web-api`,
  `gain-web-ui`).
- The `GPFWA_*` environment variable names (those are read by
  Django code; renaming is an app change, out of scope).
- Postgres database name (`gpfwa`) and user (`postgres`).
- The GRR mounts on the host (`/mnt/cephfs/...`,
  `/mnt/wigclust3/...`).

## Pre-flight (per host)

Run as the user that currently owns the deployment.

1. **Identify the current working directory.** The existing
   compose file uses `${WD:-.}` for bind-mount paths. Find
   where it was launched:

   ```bash
   docker inspect gpfwa-db-1 \
     --format '{{ range .Mounts }}{{ .Source }}{{ "\n" }}{{ end }}'
   ```

   Record this as `WD_OLD` (e.g. `/srv/gpfwa` or
   `/home/seqpipe/gpfwa`).

2. **Confirm the running stack is healthy** before touching
   anything:

   ```bash
   docker compose -p gpfwa ps
   curl -fsS http://127.0.0.1:<frontend_port>/api/jobs/genomes \
     >/dev/null && echo OK
   ```

   `<frontend_port>` is `9002` on iossifovweb, `8000` on
   piglet.

3. **Record the currently-deployed image tag** for rollback:

   ```bash
   docker inspect gpfwa-backend-1 \
     --format '{{ .Config.Image }}'
   ```

   Save it somewhere you can find it again.

4. **Verify free space** under the eventual `/opt` mount.
   Need roughly 2× the size of `gpfwa-pg-data` plus enough
   headroom for the next image pull (~1 GB).

   ```bash
   du -sh "$WD_OLD"/gpfwa-pg-data "$WD_OLD"/gpfwa-data
   df -h /opt
   ```

5. **Verify GRR mounts are present** on the host (precondition
   for the new playbook; verify now to avoid a surprise
   later):

   ```bash
   mountpoint /mnt/cephfs/seqpipe/grr        # iossifovweb
   mountpoint /mnt/wigclust3/data/unsafe/chorbadj/grr.sync  # piglet
   ```

## Snapshot the database

Take a pg_dump while the stack is still running. This is the
**only** trustworthy rollback point for the cutover.

```bash
mkdir -p "$HOME/gpfwa-cutover-backup"
docker exec gpfwa-db-1 \
    pg_dump -U postgres -Fc gpfwa \
  | gzip \
  > "$HOME/gpfwa-cutover-backup/gpfwa-pre-rename.dump.gz"

# Keep a sanity-check size:
ls -lh "$HOME/gpfwa-cutover-backup/"
```

Also tar the data directory in case anything in
`gpfwa-data/` (job inputs/results, annotation configs) needs
to be referred to during cutover:

```bash
tar -czf "$HOME/gpfwa-cutover-backup/gpfwa-data.tgz" \
    -C "$WD_OLD" gpfwa-data
```

## Stop the old stack

```bash
cd "$WD_OLD"
docker compose -p gpfwa down
```

Confirm nothing of `gpfwa` is left running:

```bash
docker ps -a --filter "label=com.docker.compose.project=gpfwa"
```

If any containers remain (e.g. dangling `backend-migrate`),
remove them:

```bash
docker compose -p gpfwa rm -f
```

## Move directories to `/opt/gainwa/`

The playbook expects `/opt/gainwa/` as the canonical layout.
Move (don't copy) the persistent state in place:

```bash
sudo mkdir -p /opt/gainwa
sudo mv "$WD_OLD"/gpfwa-pg-data    /opt/gainwa/gainwa-pg-data
sudo mv "$WD_OLD"/gpfwa-data       /opt/gainwa/gainwa-data
sudo mv "$WD_OLD"/gpfwa-logs       /opt/gainwa/gainwa-logs
# iossifovweb only:
sudo mv "$WD_OLD"/gpfwa-apache     /opt/gainwa/gainwa-apache  || true
```

Ownership and permissions must survive the move. Verify:

```bash
sudo ls -la /opt/gainwa/gainwa-pg-data | head -5
```

The `pg-data` directory should still be owned by uid `999`
(the postgres image's internal uid). If `mv` straddled
filesystems and changed ownership, fix it:

```bash
sudo chown -R 999:999 /opt/gainwa/gainwa-pg-data
```

## Smoke-test the rename **before** wiring up the playbook

Bring the stack up by hand under the new project name and
paths to confirm Postgres reads the moved data directory and
the backend can talk to it. This validates the move
independently of any Ansible logic.

1. Copy a temporary compose file. The simplest path is to
   copy the existing per-host compose file from the gain
   repo and adjust paths:

   ```bash
   sudo cp /path/to/gain/web_infra/compose-iossifovweb.yaml \
       /opt/gainwa/compose.yaml
   ```

2. Create a temporary `.env` at `/opt/gainwa/.env` with the
   recorded image tag and host-specific values:

   ```bash
   # /opt/gainwa/.env
   WD=/opt/gainwa
   BACKEND_IMAGE=registry.seqpipe.org/gain-web-api:<recorded-tag>
   FRONTEND_IMAGE=registry.seqpipe.org/gain-web-ui:<recorded-tag>
   GRR=/mnt/cephfs/seqpipe/grr               # iossifovweb
   GRR_ENCODE=/mnt/cephfs/seqpipe/grr_encode # iossifovweb
   PUBLIC_ENDPOINT=https://gainwa.iossifovlab.com
   PUBLIC_NAME=gainwa.iossifovlab.com
   SECRET_KEY=<copy from old environment>
   EMAIL_HOST_USER=<copy from old environment>
   EMAIL_HOST_PASSWORD=<copy from old environment>
   ```

   Note the rename of host directory references:
   `${WD}/gpfwa-*` in the compose file refer to
   `${WD}/gainwa-*` after the move; either edit the compose
   file's volume paths or temporarily symlink. The playbook's
   canonical `compose.yaml` will use `gainwa-*` directly.

3. Bring up the stack under the new project name:

   ```bash
   cd /opt/gainwa
   docker compose -p gainwa --env-file .env up -d
   ```

4. Wait ~60s and verify:

   ```bash
   docker compose -p gainwa ps
   docker compose -p gainwa logs db --tail=50
   docker compose -p gainwa logs backend --tail=50
   curl -fsS http://127.0.0.1:<frontend_port>/api/jobs/genomes \
     | head
   ```

   Postgres should report "database system is ready to accept
   connections." Backend should not show migration errors.

5. Once verified, take it down again — the Ansible playbook
   will own the lifecycle from here:

   ```bash
   docker compose -p gainwa down
   ```

   Do **not** delete `/opt/gainwa/compose.yaml` or
   `/opt/gainwa/.env`; the playbook may overwrite them but
   leaving them in place avoids a transient empty state.

## First Ansible deploy

From the `gain-infra` controller:

```bash
ansible-playbook -i inventory/hosts.yml deploy.yml \
    --limit iossifovweb \
    --vault-password-file ~/.ansible/vault-pass
```

The playbook will:

- create `/opt/gainwa/backups/`
- write the canonical `/opt/gainwa/compose.yaml` and
  `/opt/gainwa/.env`
- install `/etc/systemd/system/gainwa.service` and
  `systemctl enable --now gainwa`
- run the standard deploy flow (pg_dump, stop, pull, start,
  healthcheck)

If the healthcheck fails, the playbook itself will restore
the pre-deploy `pg_dump` and revert `.env`, but **the
cutover-time `pg_dump` from `~/gpfwa-cutover-backup/` is your
last-resort restore point** — keep it for at least a week.

## Verification

After the first deploy succeeds:

- `systemctl status gainwa` — active (running).
- `docker compose -p gainwa ps` — all services healthy.
- `curl -fsS http://127.0.0.1:<frontend_port>/api/jobs/genomes`
  — 200 OK.
- Frontend reachable through the upstream proxy at the
  public URL.
- Spot-check a logged-in workflow if you have credentials.

Then reboot the host to confirm `gainwa.service` comes up
cleanly on its own:

```bash
sudo reboot
# wait ~60s, then:
systemctl status gainwa
```

## Rollback (if cutover fails before first successful deploy)

If the smoke test or the first Ansible deploy reveals a
problem you can't fix forward:

1. Stop the new stack: `docker compose -p gainwa down` (and
   `systemctl disable --now gainwa` if the unit got
   installed).
2. Move the data directories back:

   ```bash
   sudo mv /opt/gainwa/gainwa-pg-data "$WD_OLD"/gpfwa-pg-data
   sudo mv /opt/gainwa/gainwa-data    "$WD_OLD"/gpfwa-data
   sudo mv /opt/gainwa/gainwa-logs    "$WD_OLD"/gpfwa-logs
   ```
3. Bring the old stack back up exactly as before:
   `cd "$WD_OLD" && docker compose -p gpfwa up -d`.
4. If the database itself is suspect (e.g. partial migration
   ran), restore from the pre-rename dump:

   ```bash
   gunzip -c ~/gpfwa-cutover-backup/gpfwa-pre-rename.dump.gz \
     | docker exec -i gpfwa-db-1 \
         pg_restore -U postgres -d gpfwa --clean --if-exists
   ```

## Cleanup (after cutover is stable)

Wait at least a week (one full backup-rotation cycle) before
removing the cutover safety net.

```bash
# On each migrated host:
rm -rf "$HOME/gpfwa-cutover-backup/"

# Remove the dangling old project metadata (no containers
# should remain at this point):
docker network rm gpfwa_default 2>/dev/null || true
```

The old `web_infra/compose-iossifovweb.yaml` and
`compose-wigclust.yaml` files in this repo are also retired
once both hosts are on the playbook — delete them in the same
PR that ships `gain-infra`'s first release.
