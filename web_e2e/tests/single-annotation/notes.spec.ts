import { test, expect } from '@playwright/test';
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
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
    await customDefaultPipeline(page);
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });
  });

  test('should save and display a note for an annotatable', async({ page }) => {
    await page.locator('.edit-btn').first().click();
    await page.locator('.note-input').fill('BRCA1 review');

    const noteResponse = page.waitForResponse(
      resp => resp.url().includes('/api/single_allele/note') && resp.status() === 200
    );
    await page.locator('.confirm-btn').first().click();
    await noteResponse;

    await expect(page.locator('.note-label').first()).toHaveText('BRCA1 review');
    await expect(page.locator('.note-label').first()).not.toHaveClass(/empty/);
  });

  test('should save a note by pressing Enter', async({ page }) => {
    await page.locator('.edit-btn').first().click();
    await page.locator('.note-input').fill('entered via keyboard');

    const noteResponse = page.waitForResponse(
      resp => resp.url().includes('/api/single_allele/note') && resp.status() === 200
    );
    await page.locator('.note-input').press('Enter');
    await noteResponse;

    await expect(page.locator('.note-label').first()).toHaveText('entered via keyboard');
  });

  test('should discard edit on cancel without saving', async({ page }) => {
    await page.locator('.edit-btn').first().click();
    await page.locator('.note-input').fill('should not be saved');
    await page.locator('.cancel-btn').first().click();

    await expect(page.locator('.note-input')).not.toBeVisible();
    await expect(page.locator('.note-label').first()).toHaveText('no label');
  });

  test('should clear a note when saved with empty input', async({ page }) => {
    await page.locator('.edit-btn').first().click();
    await page.locator('.note-input').fill('to be cleared');
    const firstSave = page.waitForResponse(
      resp => resp.url().includes('/api/single_allele/note') && resp.status() === 200
    );
    await page.locator('.confirm-btn').first().click();
    await firstSave;

    await page.locator('.edit-btn').first().click();
    await page.locator('.note-input').fill('');
    const clearResponse = page.waitForResponse(
      resp => resp.url().includes('/api/single_allele/note') && resp.status() === 200
    );
    await page.locator('.confirm-btn').first().click();
    await clearResponse;

    await expect(page.locator('.note-label').first()).toHaveText('no label');
    await expect(page.locator('.note-label').first()).toHaveClass(/empty/);
  });

  test('should persist note after logout and login', async({ page }) => {
    await page.locator('.edit-btn').first().click();
    await page.locator('.note-input').fill('persisted label');
    const noteResponse = page.waitForResponse(
      resp => resp.url().includes('/api/single_allele/note') && resp.status() === 200
    );
    await page.locator('.confirm-btn').first().click();
    await noteResponse;

    await Promise.all([
      page.waitForNavigation({ waitUntil: 'load' }),
      page.locator('#logout-button').click(),
    ]);
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });

    await utils.loginUser(page, email, password);
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });

    await expect(page.locator('.note-label').first()).toHaveText('persisted label');
  });

  test('should not retain note when annotatable is deleted and re-queried', async({ page }) => {
    await page.locator('.edit-btn').first().click();
    await page.locator('.note-input').fill('label that will be lost');
    const noteResponse = page.waitForResponse(
      resp => resp.url().includes('/api/single_allele/note') && resp.status() === 200
    );
    await page.locator('.confirm-btn').first().click();
    await noteResponse;
    await expect(page.locator('.note-label').first()).toHaveText('label that will be lost');

    const deleteResponse = page.waitForResponse(
      resp => resp.url().includes('/api/single_allele/history') && resp.status() === 204
    );
    await page.locator('.delete-btn').first().click();
    await deleteResponse;
    await expect(page.locator('.annotatable-cell')).toHaveCount(0);

    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await expect(page.locator('.note-label').first()).toHaveText('no label');
    await expect(page.locator('.note-label').first()).toHaveClass(/empty/);
  });
});
