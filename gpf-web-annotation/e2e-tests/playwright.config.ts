import { defineConfig, devices } from '@playwright/test';

/**
 * See https://playwright.dev/docs/test-configuration.
 */
export default defineConfig({
  workers: process.env.CI ? 1 : undefined,
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
