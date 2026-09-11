# sqlite-wasm, vendored

The SQLite build the GRR index page's search runs on. `grr_manage
repo-info` / `repo-index` publish these two files into every repository
they index, at `.static/sqlite-wasm-<VERSION>/` beside `index.html`, and
the page imports the module from there by relative URL. The repository
therefore carries everything its search needs: no CDN at view time, a
working search on an intranet or behind an air gap, and the bytes that
run are the bytes this gain version was tested with (gain#1335).

## Contents

| File           | What it is                                              |
| -------------- | ------------------------------------------------------- |
| `version.txt`  | The npm package version, one line. The single source of truth: the published directory name and the page's import are both derived from it |
| `index.mjs`    | `dist/index.mjs` of the npm package, unmodified — the ES module |
| `sqlite3.wasm` | `dist/sqlite3.wasm` of the npm package, unmodified — the engine |

The module locates the wasm through `import.meta.url`, so the two must
sit in the same directory, published and vendored alike. The package's
other `dist/` files (`node.mjs`, `sqlite3-worker1.mjs`, the OPFS proxy,
the `.d.mts`) are not needed: the page runs the module on the main
thread with an in-memory database and never installs an OPFS VFS.

## Provenance

- Package: [`@sqlite.org/sqlite-wasm`](https://www.npmjs.com/package/@sqlite.org/sqlite-wasm),
  the npm distribution of [SQLite Wasm](https://sqlite.org/wasm).
- Licence: Apache-2.0 (the package's `package.json` and README), on top
  of SQLite's public-domain source.
- Version: see `version.txt`.
- SHA-256 at vendoring:
  - `index.mjs` `044a5844df842a96908eb9aa5542896ae6df174c23866f2c8573ded2ea5df9a2`
  - `sqlite3.wasm` `2d8049756d9d3765f3afac6eb4766457152fb5f417d2febe2836ee3c2223b3e2`

## Refreshing

No build step and no Node in the Python toolchain; the files are copied
out of the published npm tarball by hand:

```bash
cd "$(mktemp -d)"
npm pack @sqlite.org/sqlite-wasm@<version>
tar -xzf sqlite.org-sqlite-wasm-<version>.tgz
cp package/dist/index.mjs package/dist/sqlite3.wasm <gain>/core/gain/templates/static/sqlite-wasm/
echo <version> > <gain>/core/gain/templates/static/sqlite-wasm/version.txt
sha256sum package/dist/index.mjs package/dist/sqlite3.wasm   # update the digests above
```

Bumping `version.txt` changes the published directory name, which is what
busts every cache: a browser that has the old module cached is sent to
a directory it has never seen, so an upgraded gain never runs a stale
module against a new wasm. The next `repo-info` publishes the new
directory; the old one is left in place and can be deleted by hand.
