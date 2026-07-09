import { test, expect, Page } from '@playwright/test';
import { SingleAnnotation } from '../pages/single-annotation.page';
import { AnnotationJobs } from '../pages/annotation-jobs.page';
import * as utils from '../utils';


async function getDailyCurrentValue(page: Page, category: string): Promise<number> {
  const text = await page.locator(`#daily-current-${category}`).innerText();
  return parseInt(text.replace(/,/g, ''), 10);
}

async function getMonthlyCurrentValue(page: Page, category: string): Promise<number> {
  const text = await page.locator(`#monthly-current-${category}`).innerText();
  return parseInt(text.replace(/,/g, ''), 10);
}

async function createUserWithExtraUnits(
  page: Page, units: number
): Promise<{ email: string; password: string }> {
  const email = utils.getRandomString() + '@email.com';
  const password = 'aaabbb';
  await utils.registerUser(page, email, password);
  await utils.loginUser(page, email, password);
  for (const category of utils.EXTRA_QUOTA_TYPES) {
    // eslint-disable-next-line no-await-in-loop
    await utils.setExtraQuota(page, email, category, units);
  }
  return { email, password };
}

async function getExtraValue(page: Page, category: string): Promise<number> {
  const text = await page.locator('.category-table')
    .filter({ has: page.locator(`#monthly-current-${category}`) })
    .locator('.cell.extra')
    .first()
    .innerText();
  return parseInt(text.replace(/,/g, ''), 10);
}

test.describe('Quotas page', () => {
  test.describe('user with extra units', () => {
    test.beforeEach(async({ page }) => {
      await createUserWithExtraUnits(page, 100);
      await utils.navigateToQuotas(page);
    });

    test('should show dashes for daily cells', async({ page }) => {
      const assertions = [];
      for (const category of utils.EXTRA_QUOTA_TYPES) {
        assertions.push(
          expect(page.locator(`#daily-current-${category}`)).toHaveText('-'),
          expect(page.locator(`#daily-max-${category}`)).toHaveText('-'),
          expect(page.locator(`#monthly-current-${category}`)).not.toHaveText('-'),
          expect(page.locator(`#monthly-max-${category}`)).not.toHaveText('-')
        );
      }
      await Promise.all(assertions);
    });

    test('should show non-zero extra values for all categories', async({ page }) => {
      const extraCells = page.locator('.cell.extra');
      await expect(extraCells).toHaveCount(utils.EXTRA_QUOTA_TYPES.length * 2);

      const count = await extraCells.count();
      const texts = await Promise.all(
        Array.from({ length: count }, (_, i) => extraCells.nth(i).innerText())
      );
      for (const text of texts) {
        expect(parseInt(text.replace(/,/g, ''), 10)).toBe(100);
      }
    });
  });

  test.describe('regular user', () => {
    test.beforeEach(async({ page }) => {
      const email = utils.getRandomString() + '@email.com';
      const password = 'aaabbb';
      await utils.registerUser(page, email, password);
      await utils.loginUser(page, email, password);
      await utils.navigateToQuotas(page);
    });

    test('should show numbers instead of dashes for daily and monthly cells', async({ page }) => {
      const assertions = [];
      for (const category of utils.EXTRA_QUOTA_TYPES) {
        assertions.push(
          expect(page.locator(`#daily-current-${category}`)).not.toHaveText('-'),
          expect(page.locator(`#daily-max-${category}`)).not.toHaveText('-'),
          expect(page.locator(`#monthly-current-${category}`)).not.toHaveText('-'),
          expect(page.locator(`#monthly-max-${category}`)).not.toHaveText('-')
        );
      }
      await Promise.all(assertions);
    });

    test('should show 0 for extra cells', async({ page }) => {
      const extraCells = page.locator('.cell.extra');
      await expect(extraCells).toHaveCount(utils.EXTRA_QUOTA_TYPES.length * 2);

      const count = await extraCells.count();
      await Promise.all(
        Array.from({ length: count }, (_, i) => expect(extraCells.nth(i)).toHaveText('0'))
      );
    });
  });

  test.describe('anonymous user', () => {
    test.beforeEach(async({ page }) => {
      await page.goto('/', { waitUntil: 'load' });
      await utils.navigateToQuotas(page);
    });

    test('should not show extra cells and note', async({ page }) => {
      await expect(page.locator('.cell.extra')).toHaveCount(0);
      const extraHeaders = page.locator('.cell.header').filter({ hasText: '* Extra' });
      await expect(extraHeaders).toHaveCount(0);
      await expect(page.locator('#note')).not.toBeVisible();
    });
  });
});

test.describe('Quota changes', () => {
  test.describe.configure({ mode: 'default' });

  test.describe('logged in user', () => {
    test.beforeEach(async({ page }) => {
      const email = utils.getRandomString() + '@email.com';
      const password = 'aaabbb';
      await utils.registerUser(page, email, password);
      await utils.loginUser(page, email, password);
    });

    test('should decrease variant and attribute quotas after single annotation', async({ page }) => {
      await utils.navigateToQuotas(page);
      const initialDailyVariants = await getDailyCurrentValue(page, 'variants');
      const initialDailyAttributes = await getDailyCurrentValue(page, 'attributes');
      const initialMonthlyVariants = await getMonthlyCurrentValue(page, 'variants');
      const initialMonthlyAttributes = await getMonthlyCurrentValue(page, 'attributes');

      await SingleAnnotation.open(page);
      await utils.customDefaultPipeline(page);
      await new SingleAnnotation(page).annotate('chr1 1265232 G A');

      await utils.navigateToQuotas(page);
      expect(await getDailyCurrentValue(page, 'variants')).toBe(initialDailyVariants - 1);
      expect(
        await getDailyCurrentValue(page, 'attributes')
      ).toBe(initialDailyAttributes - 3); // user pipeline has 3 attributes
      expect(await getMonthlyCurrentValue(page, 'variants')).toBe(initialMonthlyVariants - 1);
      expect(await getMonthlyCurrentValue(page, 'attributes')).toBe(initialMonthlyAttributes - 3);
    });

    test('should decrease job, variant and attribute quotas after job annotation', async({ page }) => {
      await utils.navigateToQuotas(page);
      const initialDailyJobs = await getDailyCurrentValue(page, 'jobs');
      const initialDailyVariants = await getDailyCurrentValue(page, 'variants');
      const initialDailyAttributes = await getDailyCurrentValue(page, 'attributes');
      const initialMonthlyJobs = await getMonthlyCurrentValue(page, 'jobs');
      const initialMonthlyVariants = await getMonthlyCurrentValue(page, 'variants');
      const initialMonthlyAttributes = await getMonthlyCurrentValue(page, 'attributes');

      await AnnotationJobs.open(page);
      await utils.customDefaultPipeline(page);
      const jobs = new AnnotationJobs(page);
      await jobs.uploadFile('./fixtures/input-vcf-file-reduced.vcf');
      await jobs.create();
      await page.waitForSelector('.success-status', { timeout: 120000 });

      await utils.navigateToQuotas(page);
      expect(await getDailyCurrentValue(page, 'jobs')).toBe(initialDailyJobs - 1);
      expect(await getDailyCurrentValue(page, 'variants')).toBe(initialDailyVariants - 2); // vcf file has 2 variants
      expect(
        await getDailyCurrentValue(page, 'attributes')
      ).toBe(initialDailyAttributes - 3); // user pipeline has 3 attributes
      expect(await getMonthlyCurrentValue(page, 'jobs')).toBe(initialMonthlyJobs - 1);
      expect(await getMonthlyCurrentValue(page, 'variants')).toBe(initialMonthlyVariants - 2);
      expect(await getMonthlyCurrentValue(page, 'attributes')).toBe(initialMonthlyAttributes - 3);
    });
  });

  test.describe('anonymous user', () => {
    test.beforeEach(async({ page }) => {
      await page.goto('/', { waitUntil: 'load' });
      await utils.waitForSession(page);
      await utils.deleteAnonymousJobs(page);
      // IP quota is shared across parallel workers — keep it far above the
      // session value so min(session, ip) == session always, making exact
      // toBe assertions independent of what other workers consume.
      await utils.setAnonymousUserIpQuota(page, 'daily_variants', 100_000);
      await utils.setAnonymousUserSessionQuota(page, 'daily_variants', 1_000);
      await utils.setAnonymousUserIpQuota(page, 'monthly_variants', 100_000);
      await utils.setAnonymousUserSessionQuota(page, 'monthly_variants', 1_000);
      await utils.setAnonymousUserIpQuota(page, 'daily_attributes', 100_000);
      await utils.setAnonymousUserSessionQuota(page, 'daily_attributes', 1_000);
      await utils.setAnonymousUserIpQuota(page, 'monthly_attributes', 100_000);
      await utils.setAnonymousUserSessionQuota(page, 'monthly_attributes', 1_000);
      await utils.setAnonymousUserIpQuota(page, 'daily_jobs', 100_000);
      await utils.setAnonymousUserSessionQuota(page, 'daily_jobs', 1_000);
      await utils.setAnonymousUserIpQuota(page, 'monthly_jobs', 100_000);
      await utils.setAnonymousUserSessionQuota(page, 'monthly_jobs', 1_000);
      await utils.navigateToQuotas(page);
    });

    test('should decrease variant and attribute quotas after single annotation', async({ page }) => {
      const initialDailyVariants = await getDailyCurrentValue(page, 'variants');
      const initialDailyAttributes = await getDailyCurrentValue(page, 'attributes');
      const initialMonthlyVariants = await getMonthlyCurrentValue(page, 'variants');
      const initialMonthlyAttributes = await getMonthlyCurrentValue(page, 'attributes');

      await SingleAnnotation.open(page);
      await utils.customDefaultPipeline(page);
      await new SingleAnnotation(page).annotate('chr1 1265232 G A');

      await utils.navigateToQuotas(page);
      expect(await getDailyCurrentValue(page, 'variants')).toBe(initialDailyVariants - 1);
      expect(await getDailyCurrentValue(page, 'attributes'))
        .toBe(initialDailyAttributes - 3); // pipeline has 3 attributes
      expect(await getMonthlyCurrentValue(page, 'variants')).toBe(initialMonthlyVariants - 1);
      expect(await getMonthlyCurrentValue(page, 'attributes')).toBe(initialMonthlyAttributes - 3);
    });

    test('should decrease job, variant and attribute quotas after job annotation', async({ page }) => {
      const initialDailyJobs = await getDailyCurrentValue(page, 'jobs');
      const initialDailyVariants = await getDailyCurrentValue(page, 'variants');
      const initialDailyAttributes = await getDailyCurrentValue(page, 'attributes');
      const initialMonthlyJobs = await getMonthlyCurrentValue(page, 'jobs');
      const initialMonthlyVariants = await getMonthlyCurrentValue(page, 'variants');
      const initialMonthlyAttributes = await getMonthlyCurrentValue(page, 'attributes');

      await AnnotationJobs.open(page);
      await utils.customDefaultPipeline(page);
      const jobs = new AnnotationJobs(page);
      await jobs.uploadFile('./fixtures/input-vcf-file-reduced.vcf');
      await jobs.create();
      await page.waitForSelector('.success-status', { timeout: 120000 });

      await utils.navigateToQuotas(page);
      expect(await getDailyCurrentValue(page, 'jobs')).toBe(initialDailyJobs - 1);
      expect(await getDailyCurrentValue(page, 'variants')).toBe(initialDailyVariants - 2); // vcf file has 2 variants
      expect(
        await getDailyCurrentValue(page, 'attributes')
      ).toBe(initialDailyAttributes - 3); // user pipeline has 3 attributes
      expect(await getMonthlyCurrentValue(page, 'jobs')).toBe(initialMonthlyJobs - 1);
      expect(await getMonthlyCurrentValue(page, 'variants')).toBe(initialMonthlyVariants - 2);
      expect(await getMonthlyCurrentValue(page, 'attributes')).toBe(initialMonthlyAttributes - 3);
    });
  });
});

test.describe('Quota limit', () => {
  test.describe('user daily quotas', () => {
    let email: string;
    test.beforeEach(async({ page }) => {
      email = utils.getRandomString() + '@email.com';
      await utils.registerUser(page, email, 'aaabbb');
      await utils.loginUser(page, email, 'aaabbb');
    });

    test('single annotation is blocked when daily variant quota is exhausted', async({ page }) => {
      await utils.setCurrentQuota(page, email, 'daily_variants', 0);

      await SingleAnnotation.open(page);
      await utils.customDefaultPipeline(page);
      const singleAnnotation = new SingleAnnotation(page);
      await singleAnnotation.annotatableInput.fill('chr1 1265232 G A');

      const quotaResponse = page.waitForResponse(
        resp => resp.url().includes('/api/single_allele/annotate') && resp.status() === 429
      );
      await singleAnnotation.goButton.click();
      await quotaResponse;

      await expect(page.locator('.error-message')).toHaveText('Single allele query quota exceeded!');
      await expect(singleAnnotation.report).not.toBeVisible();
    });

    test('job annotation shows error message when daily job quota is exhausted', async({ page }) => {
      await utils.setCurrentQuota(page, email, 'daily_jobs', 0);

      await AnnotationJobs.open(page);
      await utils.customDefaultPipeline(page);
      const jobs = new AnnotationJobs(page);
      await jobs.uploadFile('./fixtures/input-vcf-file-reduced.vcf');
      await jobs.create();

      await expect(page.locator('#creation-error')).toHaveText('Job quota exceeded!');
    });
  });

  test.describe('user monthly quotas', () => {
    let email: string;
    test.beforeEach(async({ page }) => {
      email = utils.getRandomString() + '@email.com';
      await utils.registerUser(page, email, 'aaabbb');
      await utils.loginUser(page, email, 'aaabbb');
    });

    test('single annotation is blocked when monthly variant quota is exhausted', async({ page }) => {
      await utils.setCurrentQuota(page, email, 'monthly_variants', 0);

      await SingleAnnotation.open(page);
      await utils.customDefaultPipeline(page);
      const singleAnnotation = new SingleAnnotation(page);
      await singleAnnotation.annotatableInput.fill('chr1 1265232 G A');

      const quotaResponse = page.waitForResponse(
        resp => resp.url().includes('/api/single_allele/annotate') && resp.status() === 429
      );
      await singleAnnotation.goButton.click();
      await quotaResponse;

      await expect(page.locator('.error-message')).toHaveText('Single allele query quota exceeded!');
      await expect(singleAnnotation.report).not.toBeVisible();
    });

    test('job annotation shows error message when monthly job quota is exhausted', async({ page }) => {
      await utils.setCurrentQuota(page, email, 'monthly_jobs', 0);

      await AnnotationJobs.open(page);
      await utils.customDefaultPipeline(page);
      const jobs = new AnnotationJobs(page);
      await jobs.uploadFile('./fixtures/input-vcf-file-reduced.vcf');
      await jobs.create();

      await expect(page.locator('#creation-error')).toHaveText('Job quota exceeded!');
    });
  });

  test.describe('anonymous user daily quotas', () => {
    test.beforeEach(async({ page }) => {
      await page.goto('/single-annotation', { waitUntil: 'load' });
      await utils.waitForSession(page);
      // Reset accumulated anonymous jobs so this IP is below can_create()'s
      // hard per-IP daily-jobs cap (2); otherwise the job-quota-exhausted test
      // below trips "Daily job limit reached!" instead of the intended
      // "Job quota exceeded!" (iossifovlab/gain#216).
      await utils.deleteAnonymousJobs(page);
      // IP stays high so the initial "> 0" check always passes;
      // tests set only the session quota to 0 to avoid blocking parallel workers.
      await utils.setAnonymousUserIpQuota(page, 'daily_variants', 100_000);
      await utils.setAnonymousUserSessionQuota(page, 'daily_variants', 100);
      await utils.setAnonymousUserIpQuota(page, 'daily_jobs', 100_000);
      await utils.setAnonymousUserSessionQuota(page, 'daily_jobs', 100);
    });

    test('single annotation is blocked when variants quota is exhausted', async({ page }) => {
      await utils.navigateToQuotas(page);
      expect(await getDailyCurrentValue(page, 'variants')).toBeGreaterThan(0);

      await utils.setAnonymousUserSessionQuota(page, 'daily_variants', 0);

      await page.reload({ waitUntil: 'load' });
      expect(await getDailyCurrentValue(page, 'variants')).toBe(0);

      const singleAnnotation = new SingleAnnotation(page);
      await SingleAnnotation.open(page);
      await singleAnnotation.annotatableInput.fill('chr1 1265232 G A');

      const quotaResponse = page.waitForResponse(
        resp => resp.url().includes('/api/single_allele/annotate') && resp.status() === 429
      );
      await singleAnnotation.goButton.click();
      await quotaResponse;

      await expect(page.locator('.error-message')).toHaveText('Single allele query quota exceeded!');
      await expect(singleAnnotation.report).not.toBeVisible();
    });

    test('job annotation shows error message when job quota is exhausted', async({ page }) => {
      await utils.navigateToQuotas(page);
      expect(await getDailyCurrentValue(page, 'jobs')).toBeGreaterThan(0);

      await utils.setAnonymousUserSessionQuota(page, 'daily_jobs', 0);

      await page.reload({ waitUntil: 'load' });
      expect(await getDailyCurrentValue(page, 'jobs')).toBe(0);

      await AnnotationJobs.open(page);
      await utils.customDefaultPipeline(page);
      const jobs = new AnnotationJobs(page);
      await jobs.uploadFile('./fixtures/input-vcf-file-reduced.vcf');
      await jobs.create();

      await expect(page.locator('#creation-error')).toHaveText('Job quota exceeded!');
    });
  });
});

test.describe('User quotas - extra units consumption', () => {
  test('should decrease extra units for variants and attributes after single annotation', async({ page }) => {
    const { email } = await createUserWithExtraUnits(page, 100);
    await utils.setCurrentQuota(page, email, 'monthly_variants', 0);
    await utils.setCurrentQuota(page, email, 'monthly_attributes', 0);

    // delete when daily quota is ignored when extra units are available
    await utils.setCurrentQuota(page, email, 'daily_variants', 0);
    await utils.setCurrentQuota(page, email, 'daily_attributes', 0);

    await utils.navigateToQuotas(page);

    await SingleAnnotation.open(page);
    await utils.customDefaultPipeline(page);
    await new SingleAnnotation(page).annotate('chr1 1265232 G A');

    await utils.navigateToQuotas(page);
    expect(await getExtraValue(page, 'variants')).toBe(99);
    expect(await getExtraValue(page, 'attributes')).toBe(97); // pipeline has 3 attributes
  });

  test('should decrease extra units for jobs, variants and attributes after job annotation', async({ page }) => {
    const { email } = await createUserWithExtraUnits(page, 100);
    await utils.setCurrentQuota(page, email, 'monthly_jobs', 0);
    await utils.setCurrentQuota(page, email, 'monthly_variants', 0);
    await utils.setCurrentQuota(page, email, 'monthly_attributes', 0);


    // delete when daily quota is ignored when extra units are available
    await utils.setCurrentQuota(page, email, 'daily_variants', 0);
    await utils.setCurrentQuota(page, email, 'daily_attributes', 0);
    await utils.setCurrentQuota(page, email, 'daily_jobs', 0);


    await utils.navigateToQuotas(page);

    await AnnotationJobs.open(page);
    await utils.customDefaultPipeline(page);
    const jobs = new AnnotationJobs(page);
    await jobs.uploadFile('./fixtures/input-vcf-file-reduced.vcf');
    await jobs.create();
    await page.waitForSelector('.success-status', { timeout: 120000 });

    await utils.navigateToQuotas(page);
    expect(await getExtraValue(page, 'jobs')).toBe(99);
    expect(await getExtraValue(page, 'variants')).toBe(98); // vcf file has 2 variants
    expect(await getExtraValue(page, 'attributes')).toBe(97); // pipeline has 3 attributes
  });

  test('should not consume extra units when regular quota is still available', async({ page }) => {
    await createUserWithExtraUnits(page, 100);

    await utils.navigateToQuotas(page);
    const initialExtraVariants = await getExtraValue(page, 'variants');
    const initialExtraAttributes = await getExtraValue(page, 'attributes');
    const initialMonthlyVariants = await getMonthlyCurrentValue(page, 'variants');
    const initialMonthlyAttributes = await getMonthlyCurrentValue(page, 'attributes');

    await SingleAnnotation.open(page);
    await utils.customDefaultPipeline(page);
    await new SingleAnnotation(page).annotate('chr1 1265232 G A');

    await utils.navigateToQuotas(page);
    expect(await getExtraValue(page, 'variants')).toBe(initialExtraVariants);
    expect(await getExtraValue(page, 'attributes')).toBe(initialExtraAttributes);
    expect(await getMonthlyCurrentValue(page, 'variants')).toBe(initialMonthlyVariants - 1);
    expect(await getMonthlyCurrentValue(page, 'attributes')).toBe(initialMonthlyAttributes - 3);
  });
});
