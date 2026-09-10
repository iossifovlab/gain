import * as fs from 'fs';
import * as path from 'path';

/**
 * Where `generate_fixtures.py` writes the built GRRs. Relative to this
 * file rather than to `process.cwd()`, so a test run from anywhere finds
 * them.
 */
const FIXTURE_ROOT = path.join(__dirname, 'fixtures');

/**
 * The Coverage GRR: one score whose statistics table this suite sorts.
 *
 * Its shape is what makes the sort assertions sharp, and it is generated
 * rather than committed: see `generate_fixtures.py`.
 */
export const FIXTURE_GRR = path.join(FIXTURE_ROOT, 'grr');

/**
 * The browse GRR: a repository shaped to be navigated and searched.
 *
 * Separate from the Coverage GRR rather than folded into it, because the
 * two fixtures are tuned against each other: folders added to the
 * Coverage GRR would move the rows its sort assertions read.
 */
export const FIXTURE_BROWSE_GRR = path.join(FIXTURE_ROOT, 'browse');

/** The resource whose Coverage table this suite drives. */
export const COVERAGE_RESOURCE = 'scores/coverage';

/*
 * What the browse GRR carries.
 *
 * Mirrors `gain.genomic_resources.testing.info_page_fixtures`, which is
 * where the fixture is built and where these values are explained. The
 * duplication is unavoidable -- this project has no Python -- but it is
 * not load-bearing: `test_info_page_browse_fixture.py` pins the same
 * terms against the real index, so a term that stopped discriminating
 * fails there rather than quietly weakening the assertions here.
 */

/** How many resources the browse GRR holds. */
export const BROWSE_RESOURCE_COUNT = 7;

/**
 * Its top-level folders, in the order the tree sorts them.
 *
 * `Zoo` is capitalised and belongs at the *end*: comparing names as
 * UTF-16 code units puts every capitalised name ahead of every lowercase
 * one, while `localeCompare` -- which the table has always used -- puts
 * it last. Listing it last is therefore an assertion about the
 * comparator, not a detail of spelling (iossifovlab/gain#564).
 */
export const BROWSE_TOP_LEVEL_FOLDERS = ['genomes', 'hg19', 'hg38', 'Zoo'];

/** The capitalised top-level folder, whose *position* the tree sorts. */
export const BROWSE_CAPITALISED_FOLDER = 'Zoo';

/**
 * The names of the two resources sharing that folder, unordered.
 *
 * Deliberately not in sorted order here: the tests derive both candidate
 * orders from this pair, so the constant cannot quietly become the
 * expected answer it is supposed to be tested against.
 */
export const BROWSE_ORDERING_RESOURCE_NAMES = ['alpha', 'Track'];

/** A term carried by exactly one resource's summary, and by no id. */
export const BROWSE_SUMMARY_ONLY_TERM = 'marmoset';
export const BROWSE_SUMMARY_ONLY_RESOURCE_ID = 'hg19/legacy/allele_frequencies';

/** Its mirror: a term carried by exactly one id, and by no summary. */
export const BROWSE_ID_ONLY_TERM = 'phylop';
export const BROWSE_ID_ONLY_RESOURCE_ID = 'hg38/scores/conservation/phylop';

/** Where a generated resource's info page lands on disk. */
function infoPagePath(grrDir: string, resourceId: string): string {
  return path.join(grrDir, ...resourceId.split('/'), 'index.html');
}

/**
 * Whether both fixture GRRs have been generated.
 *
 * Read through generated pages rather than through the directories: an
 * interrupted generation leaves the resource directories behind, and a
 * suite that ran against a half-built GRR would report a template
 * failure. The search index is checked too -- it is what the index page
 * fetches, and a GRR without one renders a page that looks complete and
 * searches nothing.
 *
 * The two GRRs are checked through different pages because they are
 * built by different commands: the Coverage GRR gets the full statistics
 * pass and so has a page inside each resource, while the browse GRR is
 * published by `repo-index`, which writes the repository's own pages and
 * nothing inside any resource directory.
 */
export function fixturesArePresent(): boolean {
  return (
    fs.existsSync(infoPagePath(FIXTURE_GRR, COVERAGE_RESOURCE))
    && fs.existsSync(path.join(FIXTURE_GRR, '.CONTENTS.sqlite3.gz'))
    && fs.existsSync(path.join(FIXTURE_BROWSE_GRR, 'index.html'))
    && fs.existsSync(path.join(FIXTURE_BROWSE_GRR, '.CONTENTS.sqlite3.gz'))
  );
}
