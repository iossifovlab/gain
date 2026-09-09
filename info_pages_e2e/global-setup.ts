import { fixturesArePresent } from './fixtures';
import { vendoringProblems } from './serving';

/**
 * Refuse to run without the generated pages, or without the packages the
 * pages load.
 *
 * A hard failure, never a skip: the pages are the entire subject of this
 * suite, so a run that quietly finds none is the "green but meaningless"
 * state that browser coverage was stood up to remove (gain#987).
 *
 * The two checks are here for the same reason. Both failures otherwise
 * surface as specs reporting an empty tree and an empty status line --
 * which is also what a genuinely broken template looks like, and is the
 * one symptom this suite exists to attribute correctly.
 */
async function globalSetup(): Promise<void> {
  if (!fixturesArePresent()) {
    throw new Error(
      'the fixture GRRs have not been generated.\n' +
      'run, from the repository root:\n' +
      '    uv run python info_pages_e2e/generate_fixtures.py ' +
      'info_pages_e2e/fixtures\n' +
      'In the CI image the pages are baked in by the builder stage of ' +
      'info_pages_e2e/Dockerfile, so seeing this there means that stage ' +
      'produced nothing.',
    );
  }

  const problems = vendoringProblems();
  if (problems.length > 0) {
    throw new Error(
      'the vendored packages this suite serves to the pages are not '
      + 'usable:\n' + problems.map((p) => `  - ${p}`).join('\n'),
    );
  }
}

export default globalSetup;
