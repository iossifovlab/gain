import { test, expect } from '@playwright/test';
import { PipelineEditor } from '../../pages/pipeline-editor.page';
import * as utils from '../../utils';
import {
  strTypePipeline,
  floatTypePipeline,
  annotatableTypePipeline,
  effectAnnotatorPipeline,
  objectTypePipeline,
  clinvarListPipeline,
} from './helpers';

test.describe('Single annotation value type rendering', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});
    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);
    await utils.loginUser(page, email, password);
    await PipelineEditor.waitForLoaded(page);
  });

  test('should render str value as inline scalar in compact and full report', async({ page }) => {
    await strTypePipeline(page);
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await expect(page.locator('.compact-value-result').first()).toHaveText('missense');

    await page.locator('.switch').click();
    await expect(page.locator('.value-result').first()).toHaveText('missense');
    await expect(page.locator('.value-grid-container')).not.toBeVisible();
  });

  test('should render float value as formatted scalar in compact and full report', async({ page }) => {
    await floatTypePipeline(page);
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await expect(page.locator('.compact-value-result').first()).toHaveText('0.323');

    await page.locator('.switch').click();
    await expect(page.locator('.value-result').first()).toHaveText('0.323');
    await expect(page.locator('.value-grid-container')).not.toBeVisible();
  });

  test('should render annotatable value as its string form in compact and full report', async({ page }) => {
    await annotatableTypePipeline(page);
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await expect(page.locator('.compact-value-result').first()).toHaveText('chr1:11796321 G>A');

    await page.locator('.switch').click();
    await expect(page.locator('.value-result').first()).toHaveText('chr1:11796321 G>A');
    await expect(page.locator('.value-grid-container')).not.toBeVisible();
  });

  test('should render gene list object array value as single-column grid in full report', async({ page }) => {
    await effectAnnotatorPipeline(page);
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await expect(page.locator('.compact-value-result').nth(2)).toHaveText('MTHFR');

    await page.locator('.switch').click();
    const valueGrid = page.locator('.attribute-result').nth(2).locator('.value-grid-container');
    await expect(valueGrid).toBeVisible();
    await expect(valueGrid.locator('.value-grid-header')).toHaveCount(1);
    await expect(valueGrid.locator('.value-grid-header').first()).toContainText('Value');
    await expect(valueGrid.locator('.value-grid-cell').first()).toHaveText('MTHFR');
  });

  test('should render gene effect object map value as two-column grid in full report', async({ page }) => {
    await effectAnnotatorPipeline(page);
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await expect(page.locator('.compact-value-result').nth(0)).toHaveText('MTHFR:missense');

    await page.locator('.switch').click();
    const valueGrid = page.locator('.attribute-result').nth(0).locator('.grid-container');
    await expect(valueGrid).toBeVisible();
    await expect(valueGrid.locator('.grid-header')).toHaveCount(2);
    await expect(valueGrid.locator('.grid-header').first()).toContainText('Gene');
    await expect(valueGrid.locator('.grid-header').nth(1)).toContainText('Effect');
    await expect(valueGrid.locator('.grid-cell').first()).toHaveText('MTHFR');
    await expect(valueGrid.locator('.grid-cell').nth(1)).toHaveText('missense');
  });

  test('should render effect details object map value as four-column grid in full report', async({ page }) => {
    await effectAnnotatorPipeline(page);

    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await expect(page.locator('.compact-value-result').nth(1))
      .toHaveText('ENST00000376590.9:MTHFR:missense:222/656(Ala->Val)');

    await page.locator('.switch').click();
    const valueGrid = page.locator('.attribute-result').nth(1).locator('.grid-container');
    await expect(valueGrid).toBeVisible();
    await expect(valueGrid.locator('.grid-header')).toHaveCount(4);
    await expect(valueGrid.locator('.grid-header').first()).toContainText('Gene');
    await expect(valueGrid.locator('.grid-header').nth(1)).toContainText('Transcript');
    await expect(valueGrid.locator('.grid-header').nth(2)).toContainText('Effect');
    await expect(valueGrid.locator('.grid-header').nth(3)).toContainText('Details');
    await expect(valueGrid.locator('.grid-cell').first()).toHaveText('MTHFR');
    await expect(valueGrid.locator('.grid-cell').nth(1)).toHaveText('ENST00000376590.9');
    await expect(valueGrid.locator('.grid-cell').nth(2)).toHaveText('missense');
    await expect(valueGrid.locator('.grid-cell').nth(3)).toHaveText('222/656(Ala->Val)');
  });

  test('should render object map value as two-column grid in full report', async({ page }) => {
    await objectTypePipeline(page);

    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await expect(page.locator('.compact-value-result').nth(1))
      .toHaveText('MTHFR:2');

    await page.locator('.switch').click();
    const valueGrid = page.locator('.attribute-result').nth(1).locator('.value-grid-container');
    await expect(valueGrid).toBeVisible();
    await expect(valueGrid.locator('.value-grid-header')).toHaveCount(2);
    await expect(valueGrid.locator('.value-grid-header').first()).toContainText('Key');
    await expect(valueGrid.locator('.value-grid-header').nth(1)).toContainText('Value');
    await expect(valueGrid.locator('.value-grid-cell').first()).toHaveText('MTHFR');
    await expect(valueGrid.locator('.value-grid-cell').nth(1)).toHaveText('2');
  });

  test('should sort the value grid ascending then descending when clicking the Value header', async({ page }) => {
    // A region-mode list aggregator yields a multi-item array value grid.
    await clinvarListPipeline(page);
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796000 11800000');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });
    await page.locator('.switch').click();

    const container = page.locator('.attribute-container').filter({
      has: page.locator('.attribute-header', { hasText: 'clnsig_list' })
    });
    const cells = container.locator('.value-grid-cell');
    await expect(cells.first()).toBeVisible();
    expect(await cells.count()).toBeGreaterThan(1);

    const valueHeader = container.locator('.value-grid-header', { hasText: 'Value' });
    const cmp = (a: string, b: string): number => a.localeCompare(b, undefined, { sensitivity: 'base' });

    await valueHeader.click();
    const ascending = await cells.allTextContents();
    expect(ascending).toEqual([...ascending].sort((a, b) => cmp(a, b)));

    await valueHeader.click();
    const descending = await cells.allTextContents();
    expect(descending).toEqual([...descending].sort((a, b) => cmp(b, a)));
  });
});
