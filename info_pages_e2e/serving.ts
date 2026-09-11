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

/** Where `npm ci` unpacks the vendored package. */
const NODE_MODULES = path.join(__dirname, 'node_modules');

/**
 * What must be installed, and at which version, for the URL above to be
 * answered honestly.
 *
 * These bytes are served *at the CDN URL*, so the installed version has
 * to be the version the URL names. Exact pins stop a range drifting but
 * not a deliberate bump: move jquery to 3.8.0 and the suite serves
 * 3.8.0's bytes at a URL claiming 3.7.1 -- every test green, testing a
 * library the published page never loads. Invisible in a diff of either
 * file alone, which is why it is checked rather than commented.
 */
const VENDORED = [
  { dir: JQUERY_DIR, version: JQUERY_VERSION },
];

/**
 * Why the vendored package cannot be served, if it cannot.
 *
 * Checked in `global-setup.ts` rather than per test: without it the
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
 * ``relative`` resolved under ``root``, or null if it escapes.
 *
 * `path.resolve` follows `..` out of the directory it was given, and a
 * harness that can be talked into serving the checkout to the page under
 * test is not one anyone should have to think about again. One
 * definition, because a containment rule with two copies is a
 * containment rule that gets hardened in one of them.
 */
function resolveUnder(root: string, relative: string): string | null {
  const base = path.resolve(root);
  const file = path.resolve(base, relative);
  return file.startsWith(base + path.sep) ? file : null;
}

/**
 * The address a request names, with any query string dropped.
 *
 * Query strings address nothing on disk. The FTS database is fetched
 * with a `?v=<md5>` cache-buster, which is part of the page's contract
 * with a real web server and must not become part of a filename here.
 */
function addressOf(url: string): string {
  return url.split('?')[0];
}

/** The file a vendored-CDN request is answered from, or null. */
function resolveVendored(address: string): string | null {
  if (address === JQUERY_URL) {
    return resolveUnder(
      path.join(NODE_MODULES, JQUERY_DIR), 'dist/jquery.min.js');
  }
  return null;
}

/** The file a request is answered from, or null if nothing may answer it. */
function resolveRequest(url: string, grrDir: string): string | null {
  const address = addressOf(url);

  if (address.startsWith(GRR_ORIGIN)) {
    return resolveUnder(
      grrDir, decodeURIComponent(address.slice(GRR_ORIGIN.length)));
  }
  return resolveVendored(address);
}

/**
 * As `resolveRequest`, but with a GRR under each named sub-path.
 *
 * The first path segment selects the repository and is then stripped, so
 * `/one/index.html` is `one`'s own `index.html` -- the same file it
 * would serve at the root. That is the arrangement the index page was
 * fixed for in iossifovlab/gain#129: several GRRs published under one
 * host, each in its own directory.
 */
function resolveSubPathRequest(
  url: string, grrDirs: Map<string, string>,
): string | null {
  const address = addressOf(url);
  if (!address.startsWith(GRR_ORIGIN)) return resolveVendored(address);

  const relative = address.slice(GRR_ORIGIN.length);
  const slash = relative.indexOf('/');
  if (slash < 0) return null;

  // A `Map`, not an object keyed by sub-path. A plain object answers
  // `constructor`, `toString`, `valueOf` and `__proto__` out of its
  // prototype, so those lookups yield a *function* instead of
  // undefined; the miss goes unnoticed and a path join is handed a
  // function and throws. An exception here escapes the route handler,
  // and a request that raises is neither fulfilled nor aborted -- it
  // hangs, which under `--network none` is indistinguishable from a
  // page reaching for the network. A Map has no inherited keys, so
  // that whole class of miss cannot arise rather than being guarded
  // against at the one site that happens to trip over it.
  const grrDir = grrDirs.get(relative.slice(0, slash));
  if (grrDir === undefined) return null;

  // The remainder is the root case with a different directory
  // answering, so hand it back rather than restate it: decoding and
  // containment get one definition instead of two that can drift.
  return resolveRequest(GRR_ORIGIN + relative.slice(slash + 1), grrDir);
}

/**
 * Answer this page's requests through `resolve`, aborting the rest.
 *
 * The one definition of *how* an answer is delivered and what happens to
 * everything else -- what may be answered at all is each resolver's
 * business, and containment is `resolveUnder`'s alone. See `serveGrr`
 * for why aborting is the point rather than a precaution.
 *
 * `fulfill({ path })` derives the content type from the extension
 * itself, which matters for two of them: without `text/html` the browser
 * offers the page as a download instead of rendering it, and without a
 * JavaScript type it refuses the `<script type="module">` blocks
 * outright. (`application/wasm` looks like a third and is not --
 * sqlite-wasm logs `falling back to ArrayBuffer instantiation` and loads
 * anyway.)
 */
async function routeThrough(
  page: Page, resolve: (url: string) => string | null,
): Promise<void> {
  await page.route(
    () => true,
    async (route) => {
      const file = resolve(route.request().url());
      if (file === null || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
        await route.abort();
        return;
      }
      await route.fulfill({ path: file });
    },
  );
}

/**
 * Answer every request this page makes out of `grrDir` and `node_modules`.
 *
 * The single definition of what the harness allows, shared by every spec:
 * the generated GRR, the one vendored CDN package (jQuery), and nothing else.
 * Anything unrecognised is aborted -- the Google Fonts stylesheet the
 * pages link, and any dependency on the network a page grows later.
 *
 * That abort is the point rather than a precaution. The Jenkins stage
 * runs the suite under `docker run --network none`, so a page that
 * quietly started needing the network would pass on a developer's
 * machine and hang in CI; failing here makes the two agree.
 */
export async function serveGrr(page: Page, grrDir: string): Promise<void> {
  await routeThrough(page, (url) => resolveRequest(url, grrDir));
}

/**
 * Serve a GRR under each named sub-path of the one origin.
 *
 * `serveGrrsUnderSubPaths(page, new Map([['one', dirA], ['two', dirB]]))`
 * puts `dirA` at `/one/` and `dirB` at `/two/`, both on
 * `https://grr.test`. Two repositories sharing a host is the arrangement
 * that broke the index page in iossifovlab/gain#129, and it is the only
 * way to tell state that belongs to a *document* from state that belongs
 * to an origin -- `sessionStorage` and `localStorage` are shared by both
 * of these pages, a fragment is not.
 */
export async function serveGrrsUnderSubPaths(
  page: Page, grrDirs: Map<string, string>,
): Promise<void> {
  await routeThrough(page, (url) => resolveSubPathRequest(url, grrDirs));
}

/** The served URL of the repository index page. */
export function indexPageUrl(): string {
  return `${GRR_ORIGIN}index.html`;
}

/** The served URL of the index page of the GRR under `subPath`. */
export function indexPageUrlUnder(subPath: string): string {
  return `${GRR_ORIGIN}${subPath}/index.html`;
}

/** The served URL of one resource's generated info page. */
export function infoPageUrl(resourceId: string): string {
  return `${GRR_ORIGIN}${resourceId}/index.html`;
}
