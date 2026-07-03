import { test, expect } from '@playwright/test';
import * as utils from '../../utils';
import { customDefaultPipeline } from './helpers';

test.describe('Single annotation history tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});
    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);
    await utils.loginUser(page, email, password);
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
    await customDefaultPipeline(page);
  });

  test('should show annotatable in history after annotation', async({ page }) => {
    await page.getByPlaceholder('Type annotatable...').fill('chr1 1265232 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await expect(page.locator('.annotatable-link').getByText('chr1:1265232 G>A')).toBeVisible();
  });

  test('should annotate when clicking annotatable from history', async({ page }) => {
    await page.getByPlaceholder('Type annotatable...').fill('chr1 1265232 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await page.getByPlaceholder('Type annotatable...').fill('');
    await expect(page.locator('#report')).not.toBeVisible();

    await page.locator('.annotatable-link').first().click();
    await page.waitForSelector('#report', { timeout: 120000 });
    await expect(page.locator('#report')).toBeVisible();
  });

  test('should delete annotatable from history', async({ page }) => {
    await page.getByPlaceholder('Type annotatable...').fill('chr1 1265232 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await expect(page.locator('.annotatable-cell')).toHaveCount(1);
    await page.locator('.delete-btn').first().click();
    await expect(page.locator('.annotatable-cell')).toHaveCount(0);
  });

  test('should accumulate multiple annotatable in history', async({ page }) => {
    await page.getByPlaceholder('Type annotatable...').fill('chr1 1265232 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await expect(page.locator('.annotatable-cell')).toHaveCount(2);
  });

  test('should not duplicate annotatable in history when annotating '+
    'the same annotatable multiple times', async({ page }) => {
    await page.getByPlaceholder('Type annotatable...').fill('chr1 1265232 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await expect(page.locator('.annotatable-cell')).toHaveCount(1);
  });
});
