import { test, expect } from '@playwright/test';
import { PipelineEditor } from '../../pages/pipeline-editor.page';
import * as utils from '../../utils';
import { effectAnnotatorPipeline, customDefaultPipeline } from './helpers';

test.describe('Single annotation annotatable formats and report features', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', { waitUntil: 'load' });
    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);
    await utils.loginUser(page, email, password);
    await PipelineEditor.waitForLoaded(page);
  });

  test('should display position-start and position-end for region annotatable', async({ page }) => {
    await effectAnnotatorPipeline(page);
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 11800000');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await expect(page.locator('#position-start')).toBeVisible();
    await expect(page.locator('#position-end')).toBeVisible();
    await expect(page.locator('#position-start')).toHaveText('11796321');
    await expect(page.locator('#position-end')).toHaveText('11800000');
    await expect(page.locator('#annotatable-reference')).not.toBeVisible();
    await expect(page.locator('#annotatable-alternate')).not.toBeVisible();
  });

  test('should display position for position-only annotatable', async({ page }) => {
    await effectAnnotatorPipeline(page);
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await expect(page.locator('#annotatable-position')).toBeVisible();
    await expect(page.locator('#annotatable-position')).toHaveText('11796321');
    await expect(page.locator('#position-start')).not.toBeVisible();
    await expect(page.locator('#position-end')).not.toBeVisible();
    await expect(page.locator('#annotatable-reference')).not.toBeVisible();
    await expect(page.locator('#annotatable-alternate')).not.toBeVisible();
  });

  test('should render effect table in full report mode', async({ page }) => {
    await effectAnnotatorPipeline(page);
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await expect(page.locator('.compact-value-result').nth(0)).toHaveText('MTHFR:missense');
    await expect(page.locator('.compact-value-result').nth(1)).toHaveText(
      'ENST00000376590.9:MTHFR:missense:222/656(Ala->Val)'
    );
    await page.locator('.switch').click();
    await expect(page.locator('.attribute-container').nth(0).locator('app-effect-table')).toBeVisible();
    await expect(page.locator('.attribute-container').nth(1).locator('app-effect-table')).toBeVisible();
  });

  test('should render histogram in report body in full report mode', async({ page }) => {
    await customDefaultPipeline(page);
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await expect(page.locator('.attribute-container').nth(1).locator('app-histogram-wrapper')).not.toBeVisible();
    await page.locator('.switch').click();
    await expect(page.locator('.attribute-container').nth(1).locator('app-histogram-wrapper')).toBeVisible();
  });
});
