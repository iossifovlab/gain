# http-grr

`grr_browse` lists the resources of a GRR, and `grr_cache_repo` copies
resources from a remote GRR into a local cache. Both read the GRR from a
definition. Here the GRR is an HTTP GRR: a `type: url` definition over a
scratch copy of `test_fixtures/mini-GRR`, served by the run's own `httpd`
container.

## Sub-features

- `-g GRR_FILENAME`: the GRR definition. The HTTP definition has `id`,
  `type: url`, `url` and `cache_dir`.
- An HTTP GRR serves its resource index as `.CONTENTS.json` or
  `.CONTENTS.json.gz`. `mini-GRR` commits both, so a plain copy is an HTTP
  GRR.
- `cache_dir`: the definition wraps the HTTP GRR in a local cache.
  `grr_browse` shows the cached file count of each resource as
  `<cached>/<files>`.
- `grr_browse` prints one row per resource: type, version, cached/files,
  size, repository id and resource id.
- `grr_cache_repo <resource id>...`: caches the named resources and every
  resource that their configuration uses. `mini_pipeline` uses
  `mini_positionscore_bw`, so the drive caches both.
- `--no-progress`: no progress bars on stderr.

## How to get to it (user POV)

A user with an HTTP GRR at `http://127.0.0.1:<port>/` writes `grr-http.yaml`:

```yaml
id: mini_http
type: url
url: 'http://127.0.0.1:<port>/'
cache_dir: '<cache dir>'
```

and runs:

```bash
grr_browse -g grr-http.yaml
grr_cache_repo -g grr-http.yaml --no-progress mini_pipeline
grr_browse -g grr-http.yaml      # mini_pipeline 1/ 3, mini_positionscore_bw 1/ 9
```

The second command fills `<cache dir>/mini_http/` with the files of
`mini_pipeline` and of `mini_positionscore_bw`.

## Driving it with verify-gain

```bash
S=.claude/skills/verify-gain/scripts
RUN_ID=$($S/launch.sh)
$S/launch-http-grr.sh "$RUN_ID"
PATH="$PWD/.venv/bin:$PATH" $S/doctor.sh "$RUN_ID"
$S/drive-grr-browse.sh "$RUN_ID"
$S/drive-grr-cache-repo.sh "$RUN_ID"
$S/cleanup.sh "$RUN_ID"
```

`launch-http-grr.sh` copies `mini-GRR` to `scratch/http/grr`, starts the
`apache` service in the compose project `verify-gain-<run id>` on an
ephemeral `127.0.0.1` port, and writes `scratch/grr-http.yaml`.

| Drive | Evidence | Read-back |
| --- | --- | --- |
| `drive-grr-browse.sh` | `evidence/grr-browse/` | the listed resource ids equal the ids in the served `.CONTENTS.json`, all from `mini_http`, all `0/<files>` |
| `drive-grr-cache-repo.sh` | `evidence/grr-cache-repo/` | `cmp` of the cached `mini_pipeline.yaml`, `genomic_resource.yaml` and `mini_positionscore_bw_0.bw` with the served copy |

Pass: `exit_code.txt` and `readback_exit_code.txt` are `0` in both
evidence directories. After Cleanup:

```bash
docker ps -a --filter label=com.docker.compose.project=verify-gain-$RUN_ID       # empty
docker network ls --filter label=com.docker.compose.project=verify-gain-$RUN_ID  # empty
git -C test_fixtures/mini-GRR status --porcelain                                 # empty
```

## Gotchas

- **Fixed host ports.** `docker-compose.override.yaml` publishes
  `127.0.0.1:28080:80`, and the default compose project name is shared
  with CI. The helpers never load that file. Each call passes
  `-f docker-compose.yaml -f scratch/http/compose.override.yaml
  -p verify-gain-<run id>` through `verify_compose` in `verify-lib.sh`, so two
  runs get two projects and two ports.
- **Relative paths.** Compose resolves a relative path in any `-f` file
  against the project directory, not against the override file. The
  override writes the bind source as an absolute path.
- **The volume merge.** The override mounts the copy at the same target as
  `./core/tests/.test_grr` in `docker-compose.yaml`. Compose merges volumes
  by target, so the copy replaces that mount. `core/tests/.test_grr` need
  not exist.
- **No pull.** `up` runs with `--pull never`. On a machine without
  `httpd:latest`, Launch and Doctor fail with `Fix: docker pull
  httpd:latest`. `VERIFY_GAIN_HTTPD_IMAGE=<image>` replaces the image, so
  a tag that is not local proves that message.
- **The health check.** Doctor runs `grr_browse -g scratch/grr-http.yaml`,
  not a bare request for `.CONTENTS.json`: a GRR can ship only
  `.CONTENTS.json.gz`.
- **Temporary files.** `grr_cache_repo` builds an annotation pipeline and
  writes `gain-annotation-work-*` under `TMPDIR`. The isolated environment
  sets `TMPDIR` to `scratch/tmp`, so nothing lands in `/tmp`.
- **Cache location.** Without `cache_dir`, the cached files land outside
  the run. The definition puts it in `scratch/http/cache`.
- **Not the proxy.** The `http(s)_proxy` variables do not reach gain's HTTP
  GRR client (fsspec over aiohttp). The definition names `127.0.0.1`, and
  that keeps the drive on the host.
