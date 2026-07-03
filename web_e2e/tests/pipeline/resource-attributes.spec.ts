import { test, expect } from '@playwright/test';
import { PipelineEditor } from '../../pages/pipeline-editor.page';
import * as utils from '../../utils';
import { ResourceDialog } from '../../pages/annotator.dialog';

test.describe('New resource modal attribute tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});

    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);

    await utils.loginUser(page, email, password);
    // wait for default pipeline to load
    await PipelineEditor.waitForLoaded(page);
  });

  test('should show duplicate attribute error, allow rename to fix it', async({ page }) => {
    const resourceModal = new ResourceDialog(page);
    await resourceModal.open();

    await resourceModal.searchResource('"hg19/scores/AlphaMissense"');

    await resourceModal.getResourceContinueButton('hg19/scores/AlphaMissense').click();

    // Configure and navigate to attributes step
    await expect(resourceModal.resourcesContent).toBeVisible({ timeout: 15000 });
    await expect(resourceModal.nextButton).toBeEnabled();
    await resourceModal.next();

    // Wait for attributes to load
    await expect(resourceModal.attributeDropdown).toBeVisible({ timeout: 15000 });

    await resourceModal.addAttribute('am_pathogenicity');

    // Verify attribute was selected
    await expect(page.locator('.attribute-name .editable-name').first()).toHaveValue('am_pathogenicity');

    // Check that duplicate error message is visible
    await expect(page.locator('.error-message').filter({ hasText: 'Attribute with this name already exists' }))
      .toBeVisible();

    await expect(resourceModal.finishButton).toBeDisabled();

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

    await expect(resourceModal.finishButton).toBeEnabled();

    // Verify both attributes are in the table with correct names
    const firstAttributeInput = page.locator('.attribute-name .editable-name').nth(0);
    const secondAttributeInput = page.locator('.attribute-name .editable-name').nth(2);
    await expect(firstAttributeInput).toHaveValue('am_pathogenicity');
    await expect(secondAttributeInput).toHaveValue('custom_name_1');
  });

  test('should allow deleting duplicate attribute without renaming', async({ page }) => {
    const resourceModal = new ResourceDialog(page);
    await resourceModal.open();

    await resourceModal.searchResource('"hg19/scores/AlphaMissense"');

    await resourceModal.getResourceContinueButton('hg19/scores/AlphaMissense').click();

    // Configure and navigate to attributes step
    await expect(resourceModal.resourcesContent).toBeVisible({ timeout: 15000 });
    await expect(resourceModal.nextButton).toBeEnabled();
    await resourceModal.next();

    // Wait for attributes to load
    await expect(resourceModal.attributeDropdown).toBeVisible({ timeout: 15000 });

    await resourceModal.addAttribute('am_pathogenicity');

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
    const resourceModal = new ResourceDialog(page);
    await resourceModal.open();

    await resourceModal.searchResource('"hg19/scores/AlphaMissense"');

    await resourceModal.getResourceContinueButton('hg19/scores/AlphaMissense').click();

    // Configure and navigate to attributes step
    await expect(resourceModal.resourcesContent).toBeVisible({ timeout: 15000 });
    await expect(resourceModal.nextButton).toBeEnabled();
    await resourceModal.next();

    // Wait for attributes to load
    await expect(resourceModal.attributeDropdown).toBeVisible({ timeout: 15000 });


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
    await resourceModal.addAttribute('am_pathogenicity');

    // Verify the attribute has the original name (not the renamed one)
    const newAttributeInput = page.locator('.editable-name').nth(1);
    await expect(newAttributeInput).toHaveValue('am_pathogenicity');

    // Finish button should be enabled
    await expect(resourceModal.finishButton).toBeEnabled();
  });
});