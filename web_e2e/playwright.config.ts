import { defineConfig, devices } from '@playwright/test';

/**
 * See https://playwright.dev/docs/test-configuration.
 */
export default defineConfig({
  // 4 workers on CI parallelizes across the 5 spec files
  // (annotation-jobs / annotation-pipeline / anonymous-user /
  // front-page / single-annotation); `fullyParallel: false`
  // below keeps tests within a file serial. The previous
  // workers=2 cap was justified by a flake in
  // `annotation-jobs.spec.ts:105:7` (asserts
  // `#download-annotated` is NOT visible — under 4
  // concurrent job-creating workers the backend job-queue
  // depth could let the job finish before the assertion);
  // probing 4 runs at workers=4 against `:131`/`:132` (tb-3zf
  // diagnosis, 2026-05-07) didn't reproduce that race. Bumped
  // back to 4 to recover the file-count/2 throughput; revert
  // to 2 if the historical race resurfaces.
  // Locally Playwright defaults to ~half-CPU.
  workers: process.env.CI ? 4 : undefined,
  /* One retry on CI so a failing test reruns once. Combined with the
   * on-first-retry video/trace below, every failure captures a video (and
   * trace) of the retry attempt, and a flaky-but-passing test surfaces as
   * `flaky` in the JUnit report instead of failing the build. Local stays at
   * 0 retries for a fast fail signal (on-first-retry records nothing without a
   * retry). Mirrors gpf-web-e2e. */
  retries: process.env.CI ? 1 : 0,
  timeout: 300000,
  expect: {
    // Sync DRF views serialize on daphne's single thread_sensitive thread
    // (gain#150), so UI assertions waiting on a backend response can exceed a
    // 5s window under load. Widen the default to absorb that until the
    // multi-worker web tier lands; per-test timeout (300s) leaves ample budget.
    timeout: 15000,
    toHaveScreenshot: {
      maxDiffPixels: 100
    },
  },
  globalTimeout: 1200000,
  testDir: './tests',
  outputDir: process.env.CI ? '/reports' : './test-results',
  fullyParallel: false,
  /* Reporter to use. See https://playwright.dev/docs/test-reporters */
  reporter: process.env.CI ? 
    [['junit', { outputFile: '/reports/junit-report.xml' }]] : 
    [['html']],
  use: {
    /* Base URL to use in actions like `await page.goto('/')`. */
    baseURL: process.env.CI === '1' ? 'http://frontend' : 'http://localhost:4200',
    /* on-first-retry (was 'on' for CI): with retries=1 above this captures a
     * trace for exactly the tests that failed once — the only ones anyone
     * opens a trace for — instead of a full trace.zip per green test (a
     * multi-GB bundle). Matches gpf-web-e2e. */
    trace: 'on-first-retry',
    /* tb-84q: CI mode 'on-first-retry' instead of 'retain-on-failure'.
     * 'retain-on-failure' starts ffmpeg at the beginning of every test
     * and deletes the file on pass — that means 16 concurrent 1080p
     * ffmpeg processes recording continuously across all green tests,
     * which overloads the Jenkins agent. 'on-first-retry' only records
     * the retry attempt; with retries=1 every failure still has video
     * evidence, but green tests never start ffmpeg. Local stays at
     * retain-on-failure (no contention; retries=0 locally so
     * on-first-retry would record nothing). */
    video: process.env.CI ? {
      mode: 'on-first-retry',
      size: { width: 1920, height: 1080 }
    }: {
      mode: 'retain-on-failure',
      size: { width: 1920, height: 1080 }
    },
    actionTimeout: 10000
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1920, height: 1080 }
      },
      testIgnore: /rate-limit-anon\.spec\.ts/,
    },
    {
      // Runs after 'chromium' so the 1-minute IP throttle window has reset
      // before the anonymous rate limit test fires. See rate-limit-anon.spec.ts.
      // To run standalone: npx playwright test --project=rate-limit-anon --ignore-dependencies
      name: 'rate-limit-anon',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1920, height: 1080 }
      },
      testMatch: /rate-limit-anon\.spec\.ts/,
      dependencies: ['chromium'],
    },
  ],
});
