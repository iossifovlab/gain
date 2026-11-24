import { expect, test, type Page } from '@playwright/test';

import { FIXTURE_NESTED_GRR } from '../fixtures';
import { indexPageUrl, serveGrr } from '../serving';

/*
 * The counts of the extended search in the tree view (gain#2503), on
 * the nested GRR. Its labels:
 *
 *   brain/cortex/layer1  tissue "cortex",      modality [RNA]
 *   brain/cortex/layer2  tissue "cortex",      modality [RNA, ATAC]
 *   brain/hippocampus    tissue "hippocampus", modality [ATAC]
 *   brainstem/medulla    tissue "medulla",     modality [ATAC]
 *   liver/hepatocytes    tissue "liver",       modality [RNA]
 *
 * In the tree view a count covers only the resources under the current
 * folder, sub-folders included. The table view counts the whole
 * repository.
 */

/** The counts of the whole repository. */
const ROOT_TISSUES = [
  ['cortex', '2'], ['hippocampus', '1'], ['liver', '1'], ['medulla', '1'],
];
const ROOT_MODALITIES = [['ATAC', '3'], ['RNA', '3']];

/** The counts under `brain`. */
const BRAIN_TISSUES = [['cortex', '2'], ['hippocampus', '1']];
const BRAIN_MODALITIES = [['ATAC', '2'], ['RNA', '2']];

/** The counts under `brain/cortex`. */
const CORTEX_TISSUES = [['cortex', '2']];
const CORTEX_MODALITIES = [['RNA', '2'], ['ATAC', '1']];

async function openNestedIndex(page: Page, hash: string): Promise<void> {
  await serveGrr(page, FIXTURE_NESTED_GRR);
  await page.goto(indexPageUrl() + hash);
  await expect(page.locator('#status')).toHaveText('5 resources');
}

function dialog(page: Page) {
  return page.locator('#extended-search-dialog');
}

async function openDialog(page: Page): Promise<void> {
  await page.locator('#extended-search-btn').click();
  await expect(dialog(page)).toBeVisible();
}

/** The values that a facet shows, with their counts, in their order. */
function facetValues(page: Page, label: string) {
  return dialog(page)
    .locator(`.facet[data-label="${label}"] .facet-value`)
    .evaluateAll((rows) => rows.map((row) => [
      row.querySelector('.facet-value-name')!.textContent!,
      row.querySelector('.facet-value-count')!.textContent!,
    ]));
}

/** Wait until a facet shows `expected`. */
async function expectFacet(
  page: Page, label: string, expected: string[][],
): Promise<void> {
  await expect.poll(() => facetValues(page, label)).toEqual(expected);
}

function applyButton(page: Page) {
  return dialog(page).locator('.es-apply');
}

async function openFolder(page: Page, name: string): Promise<void> {
  await page.locator('#hierarchical-list .hv-folder .hv-name')
    .getByText(name, { exact: true }).click();
}

test('at a folder, the counts cover the resources under it, sub-folders included', async ({
  page,
}) => {
  await openNestedIndex(page, '#/brain');

  await openDialog(page);

  await expectFacet(page, 'tissue', BRAIN_TISSUES);
  await expectFacet(page, 'modality', BRAIN_MODALITIES);
});

test('after a click into a sub-folder, the counts cover that sub-folder', async ({
  page,
}) => {
  await openNestedIndex(page, '#/brain');
  await openDialog(page);
  await expectFacet(page, 'tissue', BRAIN_TISSUES);
  await dialog(page).locator('.es-cancel').click();

  await openFolder(page, 'cortex');
  await openDialog(page);

  await expectFacet(page, 'tissue', CORTEX_TISSUES);
  await expectFacet(page, 'modality', CORTEX_MODALITIES);
});

test('in the tree view, Show N resources counts the matches under the folder', async ({
  page,
}) => {
  await openNestedIndex(page, '#/brain');
  await openDialog(page);

  await dialog(page).getByRole('checkbox', { name: 'RNA' }).click();

  await expect(applyButton(page)).toHaveText('Show 2 resources');
});

test('at the root of the tree, the counts are those of the whole repository', async ({
  page,
}) => {
  await openNestedIndex(page, '#/');

  await openDialog(page);

  await expectFacet(page, 'tissue', ROOT_TISSUES);
  await expectFacet(page, 'modality', ROOT_MODALITIES);
  await expect(applyButton(page)).toHaveText('Show 5 resources');
});

test('in the table view, the counts are those of the whole repository', async ({
  page,
}) => {
  await openNestedIndex(page, '#/brain');
  await page.locator('#table-view-btn').click();

  await openDialog(page);

  await expectFacet(page, 'tissue', ROOT_TISSUES);
  await expectFacet(page, 'modality', ROOT_MODALITIES);
  await expect(applyButton(page)).toHaveText('Show 5 resources');
});

test('with the dialog open, Back to the parent folder changes the counts and N', async ({
  page,
}) => {
  await openNestedIndex(page, '#/brain');
  await openFolder(page, 'cortex');
  await openDialog(page);
  await dialog(page).getByRole('checkbox', { name: 'ATAC' }).click();
  await expectFacet(page, 'tissue', [['cortex', '1']]);
  await expectFacet(page, 'modality', CORTEX_MODALITIES);
  await expect(applyButton(page)).toHaveText('Show 1 resources');

  await page.goBack();

  await expect(dialog(page)).toBeVisible();
  await expectFacet(page, 'tissue', [['cortex', '1'], ['hippocampus', '1']]);
  await expectFacet(page, 'modality', BRAIN_MODALITIES);
  await expect(applyButton(page)).toHaveText('Show 2 resources');
});

test('with the dialog open, Back to an entry with another term counts that term', async ({
  page,
}) => {
  await openNestedIndex(page, '#/brain');
  await openFolder(page, 'cortex');
  await page.locator('#search-field').fill('layer1');
  await page.locator('#search-field').press('Enter');
  await expect(page).toHaveURL(/layer1/);
  await openDialog(page);
  await expectFacet(page, 'tissue', [['cortex', '1']]);
  await expect(applyButton(page)).toHaveText('Show 1 resources');

  await page.goBack();

  await expect(dialog(page)).toBeVisible();
  await expectFacet(page, 'tissue', BRAIN_TISSUES);
  await expectFacet(page, 'modality', BRAIN_MODALITIES);
  await expect(applyButton(page)).toHaveText('Show 3 resources');
});

test('in the tree view, a ticked value with folder count 0 stays shown and ticked', async ({
  page,
}) => {
  await openNestedIndex(page, '#/brain');
  await openFolder(page, 'cortex');
  await page.goBack();
  await openDialog(page);
  await dialog(page).getByRole('checkbox', { name: 'hippocampus' }).click();

  await page.goForward();

  await expect(dialog(page)).toBeVisible();
  await expectFacet(page, 'tissue', [['cortex', '2'], ['hippocampus', '0']]);
  await expect(dialog(page).getByRole('checkbox', { name: 'hippocampus' }))
    .toBeChecked();
});
