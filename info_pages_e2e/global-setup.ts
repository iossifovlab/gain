import { FIXTURE_GRR, fixturesArePresent } from './fixtures';

/**
 * Refuse to run without the generated pages.
 *
 * A hard failure, never a skip: the pages are the entire subject of this
 * suite, so a run that quietly finds none is the "green but meaningless"
 * state that browser coverage was stood up to remove (gain#987).
 */
async function globalSetup(): Promise<void> {
  if (!fixturesArePresent()) {
    throw new Error(
      `the fixture GRR has not been generated at ${FIXTURE_GRR}.\n` +
      'run, from the repository root:\n' +
      '    uv run python info_pages_e2e/generate_fixtures.py ' +
      'info_pages_e2e/fixtures/grr\n' +
      'In the CI image the pages are baked in by the builder stage of ' +
      'info_pages_e2e/Dockerfile, so seeing this there means that stage ' +
      'produced nothing.',
    );
  }
}

export default globalSetup;
