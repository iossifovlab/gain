import { test, expect } from '@playwright/test';
import { PipelineEditor } from '../../pages/pipeline-editor.page';
import { SingleAnnotation } from '../../pages/single-annotation.page';
import * as utils from '../../utils';
import { customDefaultPipeline } from './helpers';

test.describe('Single annotation note tests', () => {
  let email: string;
  let password: string;

  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});
    email = utils.getRandomString() + '@email.com';
    password = 'aaabbb';
    await utils.registerUser(page, email, password);
    await utils.loginUser(page, email, password);
    await PipelineEditor.waitForLoaded(page);
    await customDefaultPipeline(page);
    const singleAnnotation = new SingleAnnotation(page);
    await singleAnnotation.annotate('chr1 11796321 G A');
  });

  test('should save and display a note for an annotatable', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await singleAnnotation.editNoteButtons.first().click();
    await singleAnnotation.noteInput.fill('BRCA1 review');

    const noteResponse = page.waitForResponse(
      resp => resp.url().includes('/api/single_allele/note') && resp.status() === 200
    );
    await singleAnnotation.confirmNoteButton.first().click();
    await noteResponse;

    await expect(singleAnnotation.noteLabels.first()).toHaveText('BRCA1 review');
    await expect(singleAnnotation.noteLabels.first()).not.toHaveClass(/empty/);
  });

  test('should save a note by pressing Enter', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await singleAnnotation.editNoteButtons.first().click();
    await singleAnnotation.noteInput.fill('entered via keyboard');

    const noteResponse = page.waitForResponse(
      resp => resp.url().includes('/api/single_allele/note') && resp.status() === 200
    );
    await singleAnnotation.noteInput.press('Enter');
    await noteResponse;

    await expect(singleAnnotation.noteLabels.first()).toHaveText('entered via keyboard');
  });

  test('should discard edit on cancel without saving', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await singleAnnotation.editNoteButtons.first().click();
    await singleAnnotation.noteInput.fill('should not be saved');
    await singleAnnotation.cancelNoteButton.first().click();

    await expect(singleAnnotation.noteInput).not.toBeVisible();
    await expect(singleAnnotation.noteLabels.first()).toHaveText('no label');
  });

  test('should clear a note when saved with empty input', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await singleAnnotation.editNoteButtons.first().click();
    await singleAnnotation.noteInput.fill('to be cleared');
    const firstSave = page.waitForResponse(
      resp => resp.url().includes('/api/single_allele/note') && resp.status() === 200
    );
    await singleAnnotation.confirmNoteButton.first().click();
    await firstSave;

    await singleAnnotation.editNoteButtons.first().click();
    await singleAnnotation.noteInput.fill('');
    const clearResponse = page.waitForResponse(
      resp => resp.url().includes('/api/single_allele/note') && resp.status() === 200
    );
    await singleAnnotation.confirmNoteButton.first().click();
    await clearResponse;

    await expect(singleAnnotation.noteLabels.first()).toHaveText('no label');
    await expect(singleAnnotation.noteLabels.first()).toHaveClass(/empty/);
  });

  test('should persist note after logout and login', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await singleAnnotation.editNoteButtons.first().click();
    await singleAnnotation.noteInput.fill('persisted label');
    const noteResponse = page.waitForResponse(
      resp => resp.url().includes('/api/single_allele/note') && resp.status() === 200
    );
    await singleAnnotation.confirmNoteButton.first().click();
    await noteResponse;

    await Promise.all([
      page.waitForNavigation({ waitUntil: 'load' }),
      page.locator('#logout-button').click(),
    ]);
    await PipelineEditor.waitForLoaded(page);

    await utils.loginUser(page, email, password);
    await PipelineEditor.waitForLoaded(page);

    await expect(singleAnnotation.noteLabels.first()).toHaveText('persisted label');
  });

  test('should not retain note when annotatable is deleted and re-queried', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await singleAnnotation.editNoteButtons.first().click();
    await singleAnnotation.noteInput.fill('label that will be lost');
    const noteResponse = page.waitForResponse(
      resp => resp.url().includes('/api/single_allele/note') && resp.status() === 200
    );
    await singleAnnotation.confirmNoteButton.first().click();
    await noteResponse;
    await expect(singleAnnotation.noteLabels.first()).toHaveText('label that will be lost');

    const deleteResponse = page.waitForResponse(
      resp => resp.url().includes('/api/single_allele/history') && resp.status() === 204
    );
    await singleAnnotation.deleteButtons.first().click();
    await deleteResponse;
    await expect(singleAnnotation.annotatableCells).toHaveCount(0);

    await singleAnnotation.goButton.click();
    await singleAnnotation.waitForReport();

    await expect(singleAnnotation.noteLabels.first()).toHaveText('no label');
    await expect(singleAnnotation.noteLabels.first()).toHaveClass(/empty/);
  });
});
