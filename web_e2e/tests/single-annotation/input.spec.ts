import { test, expect } from '@playwright/test';
import { PipelineEditor } from '../../pages/pipeline-editor.page';
import * as utils from '../../utils';
import { customDefaultPipeline } from './helpers';

test.describe('Single annotation input tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});
    await PipelineEditor.waitForLoaded(page);
  });

  test('should disable Go button when no annotatable is typed', async({ page }) => {
    await utils.selectPipeline(page, 'pipeline/hg38_clinical_annotation');
    await expect(page.getByRole('button', { name: 'Go' })).toBeDisabled();
  });

  test('should disable Go button when annotatable format is invalid', async({ page }) => {
    await utils.selectPipeline(page, 'pipeline/hg38_clinical_annotation');
    await page.getByPlaceholder('Type annotatable...').fill('invalid input');
    await expect(page.getByRole('button', { name: 'Go' })).toBeDisabled();
  });

  test('should enable Go button when valid annotatable and pipeline are selected', async({ page }) => {
    await utils.selectPipeline(page, 'pipeline/hg38_clinical_annotation');
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G A');
    await expect(page.getByRole('button', { name: 'Go' })).toBeEnabled();
  });

  test('should show validation message for invalid annotatable format', async({ page }) => {
    await page.getByPlaceholder('Type annotatable...').fill('invalid input');
    await expect(page.locator('#validation-message')).toHaveText('Invalid annotatable format!');
  });

  test('should not show validation message for colon-separated annotatable', async({ page }) => {
    await page.getByPlaceholder('Type annotatable...').fill('chr1:11796321:G:A');
    await expect(page.locator('#validation-message')).not.toBeVisible();
  });

  test('should not show validation message for arrow-separated annotatable', async({ page }) => {
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G>A');
    await expect(page.locator('#validation-message')).not.toBeVisible();
  });

  test('should not show validation message for region format', async({ page }) => {
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 11800000');
    await expect(page.locator('#validation-message')).not.toBeVisible();
  });

  test('should not show validation message for position-only format', async({ page }) => {
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321');
    await expect(page.locator('#validation-message')).not.toBeVisible();
  });

  test('should not show validation message for dash-range format', async({ page }) => {
    await page.getByPlaceholder('Type annotatable...').fill('chr1:11796321-11800000');
    await expect(page.locator('#validation-message')).not.toBeVisible();
  });

  test('should show examples menu when info button is clicked', async({ page }) => {
    await page.locator('#examples-button').click();
    await expect(page.getByRole('menuitem', { name: 'chr1 11796321 G A', exact: true })).toBeVisible();
    await expect(page.getByRole('menuitem', { name: 'chr1:11796321:G:A', exact: true })).toBeVisible();
    await expect(page.getByRole('menuitem', { name: 'chr1 11796321 11800000', exact: true })).toBeVisible();
  });

  test('should populate input when example is selected', async({ page }) => {
    await page.locator('#examples-button').click();
    await page.getByRole('menuitem', { name: 'chr1 11796321 G A', exact: true }).click();
    await expect(page.getByPlaceholder('Type annotatable...')).toHaveValue('chr1 11796321 G A');
  });

  test('should clear report when annotatable input changes', async({ page }) => {
    await customDefaultPipeline(page);
    await page.getByPlaceholder('Type annotatable...').fill('chr1 1265232 G A');
    await page.getByRole('button', { name: 'Go' }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G A');
    await expect(page.locator('#report')).not.toBeVisible();
  });
});
