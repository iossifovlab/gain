import { test, expect, Page } from '@playwright/test';
import * as utils from '../utils';

const EXTRA_UNITS_EMAIL = 'extra_units_user@email.com';
const EXTRA_UNITS_PASSWORD = 'secret';
const CATEGORIES: Array<string> = ['variants', 'attributes', 'jobs'];

async function navigateToQuotas(page: Page): Promise<void> {
  const quotasResponse = page.waitForResponse(
    resp => resp.url().includes('/api/quotas') && resp.status() === 200
  );
  await page.getByRole('link', { name: 'Quotas' }).click();
  await quotasResponse;
  await page.waitForSelector('app-user-quotas', { state: 'visible' });
}

async function getDailyCurrentValue(page: Page, category: string): Promise<number> {
  const text = await page.locator(`#daily-current-${category}`).innerText();
  return parseInt(text.replace(/,/g, ''), 10);
}

async function getMonthlyCurrentValue(page: Page, category: string): Promise<number> {
  const text = await page.locator(`#monthly-current-${category}`).innerText();
  return parseInt(text.replace(/,/g, ''), 10);
}

test.describe('User quotas - user with extra units', () => {
  test.beforeEach(async({ page }) => {
    await utils.loginUser(page, EXTRA_UNITS_EMAIL, EXTRA_UNITS_PASSWORD);
    await navigateToQuotas(page);
  });

  test('should show dashes for daily cells', async({ page }) => {
    const assertions = [];
    for (const category of CATEGORIES) {
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
    await expect(extraCells).toHaveCount(CATEGORIES.length * 2);

    const count = await extraCells.count();
    const texts = await Promise.all(
      Array.from({ length: count }, (_, i) => extraCells.nth(i).innerText())
    );
    for (const text of texts) {
      expect(parseInt(text.replace(/,/g, ''), 10)).toBeGreaterThan(0);
    }
  });
});

test.describe('User quotas - regular user', () => {
  test.beforeEach(async({ page }) => {
    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);
    await utils.loginUser(page, email, password);
    await navigateToQuotas(page);
  });

  test('should show numbers instead of dashes for daily and monthly cells', async({ page }) => {
    const assertions = [];
    for (const category of CATEGORIES) {
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
    await expect(extraCells).toHaveCount(CATEGORIES.length * 2);

    const count = await extraCells.count();
    await Promise.all(
      Array.from({ length: count }, (_, i) => expect(extraCells.nth(i)).toHaveText('0'))
    );
  });
});

test.describe('User quotas - anonymous user', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', { waitUntil: 'load' });
    await navigateToQuotas(page);
  });

  test('should not show extra cells and note', async({ page }) => {
    await expect(page.locator('.cell.extra')).toHaveCount(0);
    const extraHeaders = page.locator('.cell.header').filter({ hasText: '* Extra' });
    await expect(extraHeaders).toHaveCount(0);
    await expect(page.locator('#note')).not.toBeVisible();
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
      await navigateToQuotas(page);
      const initialDailyVariants = await getDailyCurrentValue(page, 'variants');
      const initialDailyAttributes = await getDailyCurrentValue(page, 'attributes');
      const initialMonthlyVariants = await getMonthlyCurrentValue(page, 'variants');
      const initialMonthlyAttributes = await getMonthlyCurrentValue(page, 'attributes');

      await page.getByRole('link', { name: 'Single Annotation' }).click();
      await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
      await customDefaultPipeline(page);
      await page.getByPlaceholder('Type annotatable...').fill('chr1 1265232 G A');
      await page.getByRole('button', { name: 'Go', exact: true }).click();
      await page.waitForSelector('#report', { timeout: 120000 });

      await navigateToQuotas(page);
      expect(await getDailyCurrentValue(page, 'variants')).toBe(initialDailyVariants - 1);
      expect(
        await getDailyCurrentValue(page, 'attributes')
      ).toBe(initialDailyAttributes - 3); // user pipeline has 3 attributes
      expect(await getMonthlyCurrentValue(page, 'variants')).toBe(initialMonthlyVariants - 1);
      expect(await getMonthlyCurrentValue(page, 'attributes')).toBe(initialMonthlyAttributes - 3);
    });

    test('should decrease job, variant and attribute quotas after job annotation', async({ page }) => {
      await navigateToQuotas(page);
      const initialDailyJobs = await getDailyCurrentValue(page, 'jobs');
      const initialDailyVariants = await getDailyCurrentValue(page, 'variants');
      const initialDailyAttributes = await getDailyCurrentValue(page, 'attributes');
      const initialMonthlyJobs = await getMonthlyCurrentValue(page, 'jobs');
      const initialMonthlyVariants = await getMonthlyCurrentValue(page, 'variants');
      const initialMonthlyAttributes = await getMonthlyCurrentValue(page, 'attributes');

      await page.getByRole('link', { name: 'Annotation Jobs' }).click();
      await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
      await customDefaultPipeline(page);
      await page.locator('input[id="file-upload"]').setInputFiles('./fixtures/input-vcf-file-reduced.vcf');
      await page.locator('#create-button').click();
      await page.waitForSelector('.success-status', { timeout: 120000 });

      await navigateToQuotas(page);
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
    });

    test('should decrease variant and attribute quotas after single annotation', async({ page }) => {
      await navigateToQuotas(page);
      const initialDailyVariants = await getDailyCurrentValue(page, 'variants');
      const initialDailyAttributes = await getDailyCurrentValue(page, 'attributes');
      const initialMonthlyVariants = await getMonthlyCurrentValue(page, 'variants');
      const initialMonthlyAttributes = await getMonthlyCurrentValue(page, 'attributes');

      await page.getByRole('link', { name: 'Single Annotation' }).click();
      await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
      await page.getByPlaceholder('Type annotatable...').fill('chr1 1265232 G A');
      await page.getByRole('button', { name: 'Go', exact: true }).click();
      await page.waitForSelector('#report', { timeout: 120000 });

      await navigateToQuotas(page);
      expect(await getDailyCurrentValue(page, 'variants')).toBe(initialDailyVariants - 1);
      expect(await getDailyCurrentValue(page, 'attributes')).toBeLessThan(initialDailyAttributes);
      expect(await getMonthlyCurrentValue(page, 'variants')).toBe(initialMonthlyVariants - 1);
      expect(await getMonthlyCurrentValue(page, 'attributes')).toBeLessThan(initialMonthlyAttributes);
    });

    test('should decrease job, variant and attribute quotas after job annotation', async({ page }) => {
      await navigateToQuotas(page);
      const initialDailyJobs = await getDailyCurrentValue(page, 'jobs');
      const initialDailyVariants = await getDailyCurrentValue(page, 'variants');
      const initialDailyAttributes = await getDailyCurrentValue(page, 'attributes');
      const initialMonthlyJobs = await getMonthlyCurrentValue(page, 'jobs');
      const initialMonthlyVariants = await getMonthlyCurrentValue(page, 'variants');
      const initialMonthlyAttributes = await getMonthlyCurrentValue(page, 'attributes');

      await page.getByRole('link', { name: 'Annotation Jobs' }).click();
      await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
      await customDefaultPipeline(page);
      await page.locator('input[id="file-upload"]').setInputFiles('./fixtures/input-vcf-file-reduced.vcf');
      await page.locator('#create-button').click();
      await page.waitForSelector('.success-status', { timeout: 120000 });

      await navigateToQuotas(page);
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

async function customDefaultPipeline(page: Page): Promise<void> {
  await page.locator('#pipeline-actions').getByRole('button', { name: 'draft New pipeline', exact: true }).click();
  await expect(page.locator('#pipelines-input')).toBeEmpty();
  await expect(page.locator('.monaco-editor').nth(0)).toBeEmpty();

  const saveResponse = page.waitForResponse(
    resp => resp.url().includes('api/pipelines/user'), {timeout: 30000}
  );

  await utils.typeInPipelineEditor(
    page,
    '- effect_annotator:\n' +
    '   gene_models: hg38/gene_models/GENCODE/48/basic/ALL\n' +
    '   genome: hg38/genomes/GRCh38.p13\n' +
    '   attributes:\n' +
    '   - worst_effect\n' +
    '   - gene_effects\n' +
    '   - effect_details\n' +
    '   - name: gene_list \n' +
    '     internal: true\n'
  );

  await saveResponse;

  await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
}
