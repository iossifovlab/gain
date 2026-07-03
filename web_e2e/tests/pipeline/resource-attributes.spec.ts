import { test, expect } from '@playwright/test';
import * as utils from '../../utils';

test.describe('New resource modal attribute tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});

    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);

    await utils.loginUser(page, email, password);
    // wait for default pipeline to load
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
  });

  test('should show duplicate attribute error, allow rename to fix it', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-resource-button').click();

    // Search for and select AlphaMissense resource
    await Promise.all([
      page.locator('#resource-search-input').fill('"hg19/scores/AlphaMissense"'),
      page.locator('#resource-search-input').dispatchEvent('keyup'),
      page.waitForResponse(
        resp => resp.url().includes('api/resources/search?search=%22hg19/scores/AlphaMissense%22'), {timeout: 30000}
      )
    ]);

    await page.waitForSelector(
      '[id="hg19/scores/AlphaMissense-continue-button"]',
      { state: 'visible', timeout: 15000 }
    );
    await page.locator('[id="hg19/scores/AlphaMissense-continue-button"]').click();

    // Configure and navigate to attributes step
    await expect(page.locator('#resources-form')).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole('button', { name: 'Next' })).toBeEnabled();
    await page.getByRole('button', { name: 'Next' }).click();

    // Wait for attributes to load
    await expect(page.locator('#attributes-dropdown')).toBeVisible({ timeout: 15000 });

    // Type to search for am_pathogenicity attribute
    await page.locator('#attributes-dropdown input').fill('am_pathogenicity');
    await page.waitForTimeout(300);

    // Select the am_pathogenicity attribute
    await page.locator('.attribute-option', { hasText: 'am_pathogenicity' }).first().click();

    // Verify attribute was selected
    await expect(page.locator('.attribute-name .editable-name').first()).toHaveValue('am_pathogenicity');

    // Check that duplicate error message is visible
    await expect(page.locator('.error-message').filter({ hasText: 'Attribute with this name already exists' }))
      .toBeVisible();

    // Verify Finish button is disabled
    await expect(page.getByRole('button', { name: 'Finish' })).toBeDisabled();

    // Rename the second attribute to make names unique
    const secondAttribute = page.locator('.attribute-name .editable-name').nth(2);
    await secondAttribute.fill('custom_name_1');
    // Trigger input event to make ngModelChange fire
    await secondAttribute.dispatchEvent('input');
    await secondAttribute.blur();
    // Wait for validation to process
    await page.waitForTimeout(500);

    // Error message should disappear
    await expect(page.locator('.error-message').filter({ hasText: 'Attribute with this name already exists' }))
      .not.toBeVisible();

    // Finish button should be enabled
    await expect(page.getByRole('button', { name: 'Finish' })).toBeEnabled();

    // Verify both attributes are in the table with correct names
    const firstAttributeInput = page.locator('.attribute-name .editable-name').nth(0);
    const secondAttributeInput = page.locator('.attribute-name .editable-name').nth(2);
    await expect(firstAttributeInput).toHaveValue('am_pathogenicity');
    await expect(secondAttributeInput).toHaveValue('custom_name_1');
  });

  test('should allow deleting duplicate attribute without renaming', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-resource-button').click();

    // Search for and select AlphaMissense resource
    await Promise.all([
      page.locator('#resource-search-input').fill('"hg19/scores/AlphaMissense"'),
      page.locator('#resource-search-input').dispatchEvent('keyup'),
      page.waitForResponse(
        resp => resp.url().includes('api/resources/search?search=%22hg19/scores/AlphaMissense%22'), {timeout: 30000}
      )
    ]);

    await page.waitForSelector(
      '[id="hg19/scores/AlphaMissense-continue-button"]',
      { state: 'visible', timeout: 15000 }
    );
    await page.locator('[id="hg19/scores/AlphaMissense-continue-button"]').click();

    // Configure and navigate to attributes step
    await expect(page.locator('#resources-form')).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole('button', { name: 'Next' })).toBeEnabled();
    await page.getByRole('button', { name: 'Next' }).click();

    // Wait for attributes to load
    await expect(page.locator('#attributes-dropdown')).toBeVisible({ timeout: 15000 });

    // Type to search for am_pathogenicity attribute
    await page.locator('#attributes-dropdown input').fill('am_pathogenicity');
    await page.waitForTimeout(300);

    // Select the am_pathogenicity attribute again
    await page.locator('.attribute-option', { hasText: 'am_pathogenicity' }).first().click();

    // Verify error message is visible
    await expect(page.locator('.error-message').filter({ hasText: 'Attribute with this name already exists' }))
      .toBeVisible();

    // Delete the second attribute (without renaming)
    const deleteButton = page.locator('.remove-attribute').nth(2);
    await deleteButton.click();

    // Error message should disappear
    await expect(page.locator('.error-message').filter({ hasText: 'Attribute with this name already exists' }))
      .not.toBeVisible();

    await expect(page.locator('.editable-name').nth(0)).toHaveValue('am_pathogenicity');

    // Finish button should be enabled
    await expect(page.getByRole('button', { name: 'Finish' })).toBeEnabled();
  });

  test('should preserve original attribute name after renaming and deletion', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-resource-button').click();

    // Search for and select AlphaMissense resource
    await Promise.all([
      page.locator('#resource-search-input').fill('"hg19/scores/AlphaMissense"'),
      page.locator('#resource-search-input').dispatchEvent('keyup'),
      page.waitForResponse(
        resp => resp.url().includes('api/resources/search?search=%22hg19/scores/AlphaMissense%22'), {timeout: 30000}
      )
    ]);

    await page.waitForSelector(
      '[id="hg19/scores/AlphaMissense-continue-button"]',
      { state: 'visible', timeout: 15000 }
    );
    await page.locator('[id="hg19/scores/AlphaMissense-continue-button"]').click();

    // Configure and navigate to attributes step
    await expect(page.locator('#resources-form')).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole('button', { name: 'Next' })).toBeEnabled();
    await page.getByRole('button', { name: 'Next' }).click();

    // Wait for attributes to load
    await expect(page.locator('#attributes-dropdown')).toBeVisible({ timeout: 15000 });


    // Rename the attribute
    const attributeInput = page.locator('.editable-name').nth(0);
    await attributeInput.fill('renamed_attribute');
    await attributeInput.blur();

    // Verify the renamed value is shown
    await expect(attributeInput).toHaveValue('renamed_attribute');

    // Delete the attribute
    const deleteButton = page.locator('.remove-attribute').nth(0);
    await deleteButton.click();

    // Verify the table is empty
    await expect(page.locator('.editable-name')).toHaveCount(1);

    // Open dropdown and select the same attribute again
    await page.locator('#attributes-dropdown input').fill('am_pathogenicity');
    await page.waitForTimeout(300);
    await page.locator('.attribute-option', { hasText: 'am_pathogenicity' }).first().click();

    // Verify the attribute has the original name (not the renamed one)
    const newAttributeInput = page.locator('.editable-name').nth(1);
    await expect(newAttributeInput).toHaveValue('am_pathogenicity');

    // Finish button should be enabled
    await expect(page.getByRole('button', { name: 'Finish' })).toBeEnabled();
  });
});