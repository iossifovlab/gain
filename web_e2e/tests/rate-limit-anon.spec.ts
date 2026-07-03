import { test, expect } from '@playwright/test';
import { PipelineEditor } from '../pages/pipeline-editor.page';
import * as utils from '../utils';

// Runs after all main tests via the 'rate-limit-anon' project dependency in
// playwright.config.ts. The anonymous throttle key is IP-based (UserRateThrottle
// falls back to IP for unauthenticated requests), so this test shares the
// 10/minute budget with every other anonymous annotation request from the same
// host. Running it in a dependent project guarantees the main suite has finished
// and the 1-minute window has reset before this test starts.
test.describe('Single annotation rate limit tests - anonymous user', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', { waitUntil: 'load' });
    await PipelineEditor.waitForLoaded(page);
    await utils.waitForSession(page);
    // Keep quota well above the throttle limit so quota exhaustion never
    // masks the throttle response.
    await utils.setAnonymousUserIpQuota(page, 'daily_variants', 100_000);
    await utils.setAnonymousUserSessionQuota(page, 'daily_variants', 100_000);
    await utils.setAnonymousUserIpQuota(page, 'daily_attributes', 1_000_000);
    await utils.setAnonymousUserSessionQuota(page, 'daily_attributes', 1_000_000);
  });

  test('should return 429 when rate limit is exceeded', async({ page }) => {
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G A');

    // UserRateThrottle is configured at 10/minute for anonymous users; exhaust
    // the budget then verify the 11th request is rejected.
    /* eslint-disable no-await-in-loop */
    for (let i = 0; i < 10; i++) {
      await Promise.all([
        page.getByRole('button', { name: 'Go', exact: true }).click(),
        page.waitForResponse(
          resp => resp.url().includes('api/single_allele/annotate') && resp.status() === 200, {timeout: 30000}
        )
      ]);
    }
    /* eslint-enable */

    let annotateResponse;
    await Promise.all([
      page.getByRole('button', { name: 'Go', exact: true }).click(),
      annotateResponse = page.waitForResponse(
        resp => resp.url().includes('api/single_allele/annotate')
      )
    ]);
    expect((await annotateResponse).status()).toBe(429);
  });
});
