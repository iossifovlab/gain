import * as fs from 'fs';
import * as path from 'path';
import { pathToFileURL } from 'url';

/**
 * Where `generate_fixtures.py` writes the built GRR.  Relative to this
 * file rather than to `process.cwd()`, so a test run from anywhere finds
 * it.
 */
export const FIXTURE_GRR = path.join(__dirname, 'fixtures', 'grr');

/**
 * The resource whose Coverage table this suite drives.
 *
 * Its shape is what makes the assertions sharp, and it is generated
 * rather than committed: see `generate_fixtures.py`.
 */
export const COVERAGE_RESOURCE = 'scores/coverage';

/** The `file://` URL of a generated resource's info page. */
export function infoPageUrl(resourceId: string): string {
  return pathToFileURL(
    path.join(FIXTURE_GRR, ...resourceId.split('/'), 'index.html'),
  ).href;
}

/**
 * Whether the fixture GRR has been generated.
 *
 * Read through `index.html` rather than the directory: an interrupted
 * generation leaves the resource directories behind, and a suite that
 * ran against a half-built GRR would report a template failure.
 */
export function fixturesArePresent(): boolean {
  return fs.existsSync(
    path.join(FIXTURE_GRR, ...COVERAGE_RESOURCE.split('/'), 'index.html'),
  );
}
