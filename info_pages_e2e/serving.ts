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
const JQUERY_VERSION = '3.7.1';
const JQUERY_URL =
  `https://ajax.googleapis.com/ajax/libs/jquery/${JQUERY_VERSION}/jquery.min.js`;
const JQUERY_DIR = 'jquery';
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
const SQLITE_VERSION = '3.51.2-build6';
const SQLITE_URL_PREFIX =
  `https://cdn.jsdelivr.net/npm/@sqlite.org/sqlite-wasm@${SQLITE_VERSION}/`;
const SQLITE_DIR = '@sqlite.org/sqlite-wasm';

/** Where `npm ci` unpacks the two vendored packages. */
const NODE_MODULES = path.join(__dirname, 'node_modules');

/**
 * What must be installed, and at which version, for the URLs above to be
 * answered honestly.
 *
 * These bytes are served *at the CDN URLs*, so the installed version has
 * to be the version the URL names. Exact pins in `package.json` stop a
 * range from drifting, but they do not stop a deliberate bump: a
 * dependabot PR moving jquery to 3.8.0 leaves `JQUERY_VERSION` at 3.7.1,
 * and the suite would then serve 3.8.0's bytes at a URL claiming 3.7.1
 * -- every test green, while testing a library the published page never
 * loads. Nothing about that is visible from a diff of either file alone,
 * which is why it is checked rather than merely commented.
 *
 * Changing a version means four edits: `package.json`, the lockfile, the
 * matching constant above, and the URL in
 * `core/gain/templates/template_files/grr_scripts.jinja`. This catches
 * the first three; the browser catches the fourth, loudly, because the
 * template would then request a URL nothing serves.
 */
const VENDORED = [
  { dir: JQUERY_DIR, version: JQUERY_VERSION },
  { dir: SQLITE_DIR, version: SQLITE_VERSION },
];

/**
 * Why the vendored packages cannot be served, if they cannot.
 *
 * Checked in `global-setup.ts` rather than per test: without them the
 * symptom is four specs reporting an empty tree and an empty status
 * line, which looks exactly like a broken template.
 */
export function vendoringProblems(): string[] {
  return VENDORED.flatMap(({ dir, version }) => {
    const manifest = path.join(NODE_MODULES, dir, 'package.json');
    if (!fs.existsSync(manifest)) {
      return [`${dir} is not installed (${manifest} is missing) -- run 'npm ci'`];
    }
    const installed = JSON.parse(fs.readFileSync(manifest, 'utf8')).version;
    if (installed !== version) {
      return [
        `${dir} is installed at ${installed}, but this suite serves it at a `
        + `URL naming ${version}. Update the constant in serving.ts and the `
        + 'URL in core/gain/templates/template_files/grr_scripts.jinja, or '
        + 'pin the package back.',
      ];
    }
    return [];
  });
}

/**
 * Content types for what is served.
 *
 * Two of these are load-bearing, and both fail the whole suite if
 * removed: `text/html`, without which the browser offers the page as a
 * download instead of rendering it, and `text/javascript`, without which
 * Chromium refuses the `<script type="module">` blocks outright.
 *
 * `application/wasm` is NOT, despite being the obvious candidate.
 * sqlite-wasm does try `WebAssembly.instantiateStreaming` and that does
 * reject a wrong type -- but it then logs `falling back to ArrayBuffer
 * instantiation` and loads anyway, so dropping the entry changes
 * nothing. It is kept because it is what a real server sends, not
 * because a test would notice.
 *
 * Anything unlisted falls back to a byte stream.
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
