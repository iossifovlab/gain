import * as fs from 'fs';
import * as path from 'path';

import type { Page } from '@playwright/test';

/**
 * The origin a generated GRR is served from during a test run.
 *
 * The pages are still driven straight off disk -- nothing is listening on
 * a socket, and `serveGrr` answers every request by reading a file. The
 * origin is a fiction maintained entirely inside Playwright's router.
 *
 * It exists because the index page cannot work from `file:` at all.  Its
 * search deserializes `.CONTENTS.sqlite3.gz` through `fetch()`, and
 * Chromium refuses `fetch()` of a `file:` URL in the renderer --
 * "URL scheme \"file\" is not supported" -- before any request is made.
 * That is not a permission decision, so `--allow-file-access-from-files`
 * does not lift it; and because no request is issued, `page.route` never
 * sees one to answer either.  Both were measured, not assumed.
 *
 * Serving the pages from an https origin removes the restriction rather
 * than working around it, and costs no fidelity: a published GRR *is*
 * served over https, so this is the transport the pages are written for.
 */
export const GRR_ORIGIN = 'https://grr.test/';

/**
 * The jQuery the templates load, and where `npm ci` puts our copy.
 *
 * `grr_scripts.jinja` loads jQuery from Google's CDN, and all three of
 * its module blocks use `$` -- including the row scrape that builds
 * `window.rowData`, which the tree view is built from. Aborting it does
 * not merely lose the fonts: it empties the tree.
 */
const JQUERY_URL =
  'https://ajax.googleapis.com/ajax/libs/jquery/3.7.1/jquery.min.js';
const JQUERY_FILE = 'jquery/dist/jquery.min.js';

/**
 * The sqlite-wasm build the templates import, as a *prefix*.
 *
 * A prefix rather than the one URL the template names, because
 * `dist/index.mjs` locates its `sqlite3.wasm` through `import.meta.url`.
 * Fulfilling the module at its CDN address leaves that address as the
 * module's own URL, so the wasm is a second request to the same origin.
 * Allowing only the imported URL loads the module and then starves it.
 */
const SQLITE_URL_PREFIX =
  'https://cdn.jsdelivr.net/npm/@sqlite.org/sqlite-wasm@3.51.2-build6/';
const SQLITE_DIR = '@sqlite.org/sqlite-wasm';

/** Where `npm ci` unpacks the two vendored packages. */
const NODE_MODULES = path.join(__dirname, 'node_modules');

/**
 * Content types the pages actually depend on being right.
 *
 * `application/wasm` is load-bearing: sqlite-wasm instantiates through
 * `WebAssembly.instantiateStreaming`, which rejects any other type. The
 * rest are here so nothing is served as a type that would make a browser
 * refuse it; anything unlisted falls back to a byte stream.
 */
const CONTENT_TYPES: Record<string, string> = {
  '.css': 'text/css',
  '.gz': 'application/gzip',
  '.html': 'text/html',
  '.js': 'text/javascript',
  '.json': 'application/json',
  '.mjs': 'text/javascript',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.wasm': 'application/wasm',
};

/** The file a request is answered from, or null if nothing may answer it. */
function resolveRequest(url: string, grrDir: string): string | null {
  // Query strings address nothing on disk. The FTS database is fetched
  // with a `?v=<md5>` cache-buster, which is part of the page's contract
  // with a real web server and must not become part of a filename here.
  const address = url.split('?')[0];

  if (address.startsWith(GRR_ORIGIN)) {
    const relative = decodeURIComponent(address.slice(GRR_ORIGIN.length));
    const file = path.resolve(grrDir, relative);
    // Kept inside the fixture: `path.resolve` would happily follow `..`
    // out of it, and a helper that serves the checkout to a page under
    // test is not a harness anyone should have to think about again.
    return file.startsWith(path.resolve(grrDir) + path.sep) ? file : null;
  }
  if (address === JQUERY_URL) {
    return path.join(NODE_MODULES, JQUERY_FILE);
  }
  if (address.startsWith(SQLITE_URL_PREFIX)) {
    const relative = address.slice(SQLITE_URL_PREFIX.length);
    const file = path.resolve(NODE_MODULES, SQLITE_DIR, relative);
    const root = path.resolve(NODE_MODULES, SQLITE_DIR);
    return file.startsWith(root + path.sep) ? file : null;
  }
  return null;
}

/**
 * Answer every request this page makes out of `grrDir` and `node_modules`.
 *
 * The single definition of what the harness allows, shared by every spec:
 * the generated GRR, the two vendored CDN packages, and nothing else.
 * Anything unrecognised is aborted -- the Google Fonts stylesheet the
 * pages link, and any dependency on the network a page grows later.
 *
 * That abort is the point rather than a precaution. The Jenkins stage
 * runs the suite under `docker run --network none`, so a page that
 * quietly started needing the network would pass on a developer's
 * machine and hang in CI; failing here makes the two agree.
 */
export async function serveGrr(page: Page, grrDir: string): Promise<void> {
  await page.route(
    () => true,
    async (route) => {
      const file = resolveRequest(route.request().url(), grrDir);
      if (file === null || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
        await route.abort();
        return;
      }
      await route.fulfill({
        body: fs.readFileSync(file),
        contentType:
          CONTENT_TYPES[path.extname(file)] ?? 'application/octet-stream',
      });
    },
  );
}

/** The served URL of the repository index page. */
export function indexPageUrl(): string {
  return `${GRR_ORIGIN}index.html`;
}

/** The served URL of one resource's generated info page. */
export function infoPageUrl(resourceId: string): string {
  return `${GRR_ORIGIN}${resourceId}/index.html`;
}
