import { defineConfig, devices } from '@playwright/test';

/**
 * See https://playwright.dev/docs/test-configuration.
 */
export default defineConfig({
  // 2 workers on CI parallelizes across the 5 spec files
  // (annotation-jobs / annotation-pipeline / anonymous-user /
  // front-page / single-annotation); `fullyParallel: false`
  // below keeps tests within a file serial. We cap at 2
  // (rather than 4 = file-count / 2) because workers=4
  // surfaces a timing race in
  // `annotation-jobs.spec.ts:105:7` — that test asserts
  // `#download-annotated` is NOT visible (job not yet
  // complete), and with 4 concurrent job-creating workers
  // backend job-queue depth shifts so sometimes the job
  // already finished. workers=2 keeps the backend pressure
  // low enough that the existing timing assumptions hold.
  // Locally Playwright defaults to ~half-CPU.
  workers: process.env.CI ? 2 : undefined,
  timeout: 300000,
  expect: {
    timeout: 5000,
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
    trace: process.env.CI ? 'on' : 'on-first-retry',
    video: process.env.CI ? {
      mode: 'retain-on-failure',
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
      }
    }
  ],
});
