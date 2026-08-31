import { defineConfig, devices } from '@playwright/test';

/**
 * The GRR info pages are static artifacts: `grr_manage repo-info` writes
 * them to disk and a browser opens them straight off the filesystem. So
 * there is no `webServer` and no `baseURL` here -- every test navigates to
 * a `file://` URL under `fixtures/`, which `global-setup.ts` verifies has
 * been generated.
 *
 * That is the whole reason this suite is cheap enough to be worth having:
 * no service to start, no network, no fixtures to seed through an API.
 *
 * See https://playwright.dev/docs/test-configuration.
 */
/**
 * Where JUnit XML and per-test artifacts go, when anywhere.
 *
 * Set by the Jenkins stage to the bind-mounted `/reports`, rather than
 * baked in behind `process.env.CI`: `CI` is set by many shells, editors
 * and other people's CI, and a config that wrote to an absolute
 * `/reports` on that signal alone fails with EACCES before running a
 * single test. Same split as `web_ui`, which takes its report directory
 * from `JEST_JUNIT_OUTPUT_DIR` for the same reason.
 */
const reportDir = process.env.PLAYWRIGHT_REPORT_DIR;

export default defineConfig({
  testDir: './tests',
  /* A stray `test.only` would silently reduce the suite to one test and
   * still report success. */
  forbidOnly: !!process.env.CI,
  globalSetup: require.resolve('./global-setup'),
  fullyParallel: true,
  /* A page load off the local filesystem is milliseconds; a test that has
   * not settled in 30s is stuck, not slow. */
  timeout: 30000,
  /* No retries anywhere. There is nothing here to be flaky about -- no
   * server, no network, no clock -- so a failure is a real failure and
   * retrying one would only hide it. This is the deliberate difference
   * from `web_e2e`, which retries once on CI because it drives a live
   * backend. */
  retries: 0,
  outputDir: reportDir ? `${reportDir}/test-results` : './test-results',
  reporter: reportDir
    ? [['junit', { outputFile: `${reportDir}/junit-report.xml` }], ['list']]
    : [['list']],
  use: {
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
