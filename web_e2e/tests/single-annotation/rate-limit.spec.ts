import { test, expect } from '@playwright/test';
import { SingleAnnotation } from '../../pages/single-annotation.page';
import * as utils from '../../utils';

test.describe('Single annotation rate limit tests - logged in user', () => {
  test('should return 429 when rate limit is exceeded', async({ page }) => {
    // all tests for single annotation should be done with logged in user
    // as anonymous users have a very low rate limit which makes it hard not to hit the limit
    await page.goto('/', {waitUntil: 'load'});
    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);
    await utils.loginUser(page, email, password);

    // Keep quota well above the 10 requests needed to reach the throttle limit
    // so quota exhaustion never masks the throttle response.
    await utils.setCurrentQuota(page, email, 'daily_variants', 10_000);
    await utils.setCurrentQuota(page, email, 'daily_attributes', 100_000);

    const singleAnnotation = new SingleAnnotation(page);
    await singleAnnotation.annotatableInput.fill('chr1 11796321 G A');

    /* eslint-disable no-await-in-loop */
    for (let i = 0; i < 10; i++) {
      await Promise.all([
        singleAnnotation.goButton.click(),
        page.waitForResponse(
          resp => resp.url().includes('api/single_allele/annotate') && resp.status() === 200, {timeout: 30000}
        )
      ]);
    }
    /* eslint-enable */

    // 11th click should fail
    let annotateResponse;
    await Promise.all([
      singleAnnotation.goButton.click(),
      annotateResponse = page.waitForResponse(
        resp => resp.url().includes('api/single_allele/annotate')
      )
    ]);
    expect((await annotateResponse).status()).toBe(429);
  });
});
