import { test, expect } from '@playwright/test';
import { PipelineEditor } from '../../pages/pipeline-editor.page';
import { SingleAnnotation } from '../../pages/single-annotation.page';
import * as utils from '../../utils';
import { customDefaultPipeline } from './helpers';

test.describe('Single annotation history tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});
    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);
    await utils.loginUser(page, email, password);
    await PipelineEditor.waitForLoaded(page);
    await customDefaultPipeline(page);
  });

  test('should show annotatable in history after annotation', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await singleAnnotation.annotate('chr1 1265232 G A');

    await expect(singleAnnotation.annotatableLinks.getByText('chr1:1265232 G>A')).toBeVisible();
  });

  test('should annotate when clicking annotatable from history', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await singleAnnotation.annotate('chr1 1265232 G A');

    await singleAnnotation.annotatableInput.fill('');
    await expect(singleAnnotation.report).not.toBeVisible();

    await singleAnnotation.annotatableLinks.first().click();
    await singleAnnotation.waitForReport();
    await expect(singleAnnotation.report).toBeVisible();
  });

  test('should delete annotatable from history', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await singleAnnotation.annotate('chr1 1265232 G A');

    await expect(singleAnnotation.annotatableCells).toHaveCount(1);
    await singleAnnotation.deleteButtons.first().click();
    await expect(singleAnnotation.annotatableCells).toHaveCount(0);
  });

  test('should accumulate multiple annotatable in history', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await singleAnnotation.annotate('chr1 1265232 G A');
    await singleAnnotation.annotate('chr1 11796321 G A');

    await expect(singleAnnotation.annotatableCells).toHaveCount(2);
  });

  test('should not duplicate annotatable in history when annotating '+
    'the same annotatable multiple times', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await singleAnnotation.annotate('chr1 1265232 G A');

    await singleAnnotation.goButton.click();
    await singleAnnotation.waitForReport();

    await expect(singleAnnotation.annotatableCells).toHaveCount(1);
  });
});
