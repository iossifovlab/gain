# HTTP GRR tests are served by Apache, not an in-process server

`build_http_test_protocol` (`core/gain/genomic_resources/testing/__init__.py`)
copies a filesystem GRR into a directory the `docker-compose.yaml` `httpd`
service mounts, and yields an fsspec http protocol pointing at that server
(`HTTP_HOST`, default `localhost:28080`). The http-scheme tests run only
under `--enable-http-testing`, and skip without it. GAIn does not replace
this with an in-process server (stdlib `http.server`, `pytest-httpserver`,
werkzeug) started on an ephemeral port per test.

## Why this is out of scope

The proposal (gain#42, filed from the work on gain#40) was to make HTTP GRR
coverage hermetic: no container and no shared host directory, an ephemeral
port per server, a context manager that owns its server's whole lifecycle,
and http coverage that runs by default.

The maintainer chose to keep the current setup. The http tests exist to
check that a GRR reads correctly over a real web server. GRRs are published
by real web servers, so Apache is the thing being tested, not scaffolding
around the test. A stdlib or werkzeug server behaves differently on range
requests, directory listings, headers and connection handling. Swapping it
in would test GAIn against a server no GRR is published on.

The parts of the setup that hurt are either deliberate or have since been
fixed in place:

- **The gate is deliberate.** `--enable-http-testing` runs on every CI path
  that brings the server up: the `Jenkinsfile` core stage,
  `Jenkinsfile.python-matrix` and `core/Jenkinsfile.conda-integration`. So
  the http scheme is covered on every build. A local run without the
  fixtures skips those tests and says so.
- **The `__file__` coupling has an override.** `HTTP_GRR_DIR` (gain#1571)
  names the serving directory, so an installed `gain` or a test bed outside
  the source tree can point the fixture at whatever directory `httpd`
  mounts.
- **Intra-checkout collisions are handled.** Every call serves from a
  `<root_path.name>-<uuid4>` subdirectory, so parallel cells sharing one
  mount do not delete each other's trees.

What is left is a known cost: one Apache per host port, which serves one
checkout's `.test_grr`. A worktree's http tests need that worktree's
`httpd`. That is a local-workflow wrinkle, not a correctness gap, and it
does not justify replacing the server under test.

## Prior requests

- iossifovlab/gain#42 -- "Rework HTTP GRR test fixture: hermetic in-process
  server instead of host-mounted Apache"
