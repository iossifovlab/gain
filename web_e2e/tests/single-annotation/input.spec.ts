import { test, expect } from '@playwright/test';
import { PipelineEditor } from '../../pages/pipeline-editor.page';
import { SingleAnnotation } from '../../pages/single-annotation.page';
import * as utils from '../../utils';
import { customDefaultPipeline } from './helpers';

test.describe('Single annotation input tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});
    await PipelineEditor.waitForLoaded(page);
  });

  test('should disable Go button when no annotatable is typed', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await utils.selectPipeline(page, 'pipeline/hg38_clinical_annotation');
    await expect(singleAnnotation.goButton).toBeDisabled();
  });

  test('should disable Go button when annotatable format is invalid', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await utils.selectPipeline(page, 'pipeline/hg38_clinical_annotation');
    await singleAnnotation.annotatableInput.fill('invalid input');
    await expect(singleAnnotation.goButton).toBeDisabled();
  });

  test('should enable Go button when valid annotatable and pipeline are selected', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await utils.selectPipeline(page, 'pipeline/hg38_clinical_annotation');
    await singleAnnotation.annotatableInput.fill('chr1 11796321 G A');
    await expect(singleAnnotation.goButton).toBeEnabled();
  });

  test('should show validation message for invalid annotatable format', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await singleAnnotation.annotatableInput.fill('invalid input');
    await expect(singleAnnotation.validationMessage).toHaveText('Invalid annotatable format!');
  });

  test('should not show validation message for colon-separated annotatable', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await singleAnnotation.annotatableInput.fill('chr1:11796321:G:A');
    await expect(singleAnnotation.validationMessage).not.toBeVisible();
  });

  test('should not show validation message for arrow-separated annotatable', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await singleAnnotation.annotatableInput.fill('chr1 11796321 G>A');
    await expect(singleAnnotation.validationMessage).not.toBeVisible();
  });

  test('should not show validation message for region format', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await singleAnnotation.annotatableInput.fill('chr1 11796321 11800000');
    await expect(singleAnnotation.validationMessage).not.toBeVisible();
  });

  test('should not show validation message for position-only format', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await singleAnnotation.annotatableInput.fill('chr1 11796321');
    await expect(singleAnnotation.validationMessage).not.toBeVisible();
  });

  test('should not show validation message for dash-range format', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await singleAnnotation.annotatableInput.fill('chr1:11796321-11800000');
    await expect(singleAnnotation.validationMessage).not.toBeVisible();
  });

  test('should show examples menu when info button is clicked', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await singleAnnotation.examplesButton.click();
    await expect(singleAnnotation.exampleMenuItem('chr1 11796321 G A')).toBeVisible();
    await expect(singleAnnotation.exampleMenuItem('chr1:11796321:G:A')).toBeVisible();
    await expect(singleAnnotation.exampleMenuItem('chr1 11796321 11800000')).toBeVisible();
  });

  test('should populate input when example is selected', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await singleAnnotation.selectExample('chr1 11796321 G A');
    await expect(singleAnnotation.annotatableInput).toHaveValue('chr1 11796321 G A');
  });

  test('should clear report when annotatable input changes', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await customDefaultPipeline(page);
    await singleAnnotation.annotate('chr1 1265232 G A');

    await singleAnnotation.annotatableInput.fill('chr1 11796321 G A');
    await expect(singleAnnotation.report).not.toBeVisible();
  });
});
