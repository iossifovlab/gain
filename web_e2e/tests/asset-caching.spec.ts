import { test, expect } from '@playwright/test';

// Cache headers of the production Apache config (web_ui/httpd.conf),
// iossifovlab/gain#2426. The files under /assets/ keep their names across
// builds, so they must revalidate. Only the hashed bundles at the document
// root may be `immutable`.

// The URL of the Monaco tree. It moved away from /assets/monaco/min, so that a
// browser that still holds the old files as `immutable` never asks for them.
const MONACO_VS = '/assets/monaco-editor/min/vs';

// Monaco files that keep the same name across Monaco versions.
const STABLE_MONACO_FILES = [
  'loader.js',
  'editor/editor.main.js',
  'basic-languages/monaco.contribution.js',
  'nls.messages-loader.js',
];

test.describe('Cache headers of the frontend', () => {
  // Only the production frontend image (CI=1, baseURL http://frontend)
  // sends these headers. The ng dev server applies neither the Apache
  // config nor the output hashing.
  test.skip(
    process.env.CI !== '1',
    'needs the production frontend image (web_ui/httpd.conf)'
  );

  for (const file of STABLE_MONACO_FILES) {
    test(`Monaco ${file} revalidates`, async({ request }) => {
      const response = await request.head(`${MONACO_VS}/${file}`);

      expect(response.status()).toBe(200);
      // The SPA fallback answers an unknown path with index.html, which
      // also revalidates. Check that Apache served the script itself.
      expect(response.headers()['content-type']).toContain('javascript');
      const cacheControl = response.headers()['cache-control'];
      expect(cacheControl).toContain('no-cache');
      expect(cacheControl).not.toContain('immutable');
      expect(response.headers()['etag']).toBeTruthy();
    });
  }

  test('the logo under /assets/ revalidates', async({ request }) => {
    const response = await request.head('/assets/gain-logo.png');

    expect(response.status()).toBe(200);
    expect(response.headers()['content-type']).toBe('image/png');
    const cacheControl = response.headers()['cache-control'];
    expect(cacheControl).toContain('no-cache');
    expect(cacheControl).not.toContain('immutable');
  });

  test('a missing Monaco chunk returns 404', async({ request }) => {
    const response = await request.head(
      `${MONACO_VS}/editor.api-CalNCsUg.js`
    );

    expect(response.status()).toBe(404);
  });

  test('the hashed main bundle stays immutable', async({ request }) => {
    const indexHtml = await (await request.get('/index.html')).text();
    const mainBundle = indexHtml.match(/src="(main-[A-Za-z0-9]+\.js)"/);
    expect(mainBundle).not.toBeNull();

    const response = await request.head(`/${mainBundle![1]}`);

    expect(response.status()).toBe(200);
    expect(response.headers()['cache-control']).toBe('public, immutable');
  });

  test('a client route still loads the app', async({ request }) => {
    const response = await request.get('/single-annotation');

    expect(response.status()).toBe(200);
    expect(response.headers()['content-type']).toContain('text/html');
    expect(await response.text()).toContain('<app-root');
  });

  test('index.html is never cached', async({ request }) => {
    const response = await request.get('/index.html');

    expect(response.headers()['cache-control']).toBe(
      'no-store, no-cache, must-revalidate'
    );
  });
});
