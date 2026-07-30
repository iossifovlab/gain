import { test, expect } from '@playwright/test';
import { PipelineEditor } from '../../pages/pipeline-editor.page';
import { SingleAnnotation } from '../../pages/single-annotation.page';
import * as utils from '../../utils';

/**
 * Regression cover for iossifovlab/gain#492.
 *
 * When daphne stalls a request past the reverse proxy's timeout, Apache hands
 * the browser a 502 and the single-annotation component renders its error
 * message; the report is never produced. Before #492 the page object waited
 * only on the report element, so this failed as a 120s timeout (twice, with
 * the CI retry) whose message named neither the request nor the error text
 * that was on screen the whole time.
 *
 * The 502 is injected by route interception rather than by provoking a real
 * backend stall, so the error path is deterministic and cheap.
 */
test.describe('Single annotation error handling', () => {
  const ANNOTATE_ROUTE = '**/api/single_allele/annotate';

  test.beforeEach(async({ page }) => {
    await page.goto('/', { waitUntil: 'load' });
    await PipelineEditor.waitForLoaded(page);
    await utils.waitForSession(page);
  });

  test('should surface a failed annotate instead of waiting for a report that never comes',
    async({ page }) => {
      await page.route(ANNOTATE_ROUTE, route => route.fulfill({
        status: 502,
        contentType: 'text/html',
        body: '<html><head><title>502 Proxy Error</title></head><body>Proxy Error</body></html>',
      }));

      const singleAnnotation = new SingleAnnotation(page);
      await singleAnnotation.annotatableInput.fill('chr1 11796321 G A');
      await singleAnnotation.goButton.click();

      // The component renders the failure, and no report appears. The status
      // must be in the text: a bare 'Error occurred!' would leave the failure
      // report with nothing identifying the cause, which is the point of #492.
      const errorMessage = page.locator(utils.SINGLE_ANNOTATION_ERROR);
      await expect(errorMessage).toBeVisible();
      await expect(errorMessage).toContainText('HTTP 502');
      await expect(singleAnnotation.report).not.toBeVisible();
    });

  test('should fail the report wait fast, quoting the error, rather than timing out',
    async({ page }) => {
      await page.route(ANNOTATE_ROUTE, route => route.fulfill({
        status: 502,
        contentType: 'text/html',
        body: '<html><head><title>502 Proxy Error</title></head><body>Proxy Error</body></html>',
      }));

      const singleAnnotation = new SingleAnnotation(page);
      const started = Date.now();

      await expect(singleAnnotation.annotate('chr1 11796321 G A')).rejects.toThrow(/rendered an error instead/);

      // The point of the fix: the wait must not burn its 120s budget. A
      // generous bound keeps this stable on a loaded CI agent while still
      // failing if the error-race regresses to a blind wait.
      expect(Date.now() - started).toBeLessThan(60000);
    });

  test('should still render the report once a failed annotate is retried successfully',
    async({ page }) => {
      // A stale error must not survive into the next attempt -- otherwise the
      // error-race would trip on the previous failure's message and report a
      // spurious error for an annotation that actually succeeded.
      await page.route(ANNOTATE_ROUTE, route => route.fulfill({
        status: 502,
        contentType: 'text/html',
        body: '<html><head><title>502 Proxy Error</title></head><body>Proxy Error</body></html>',
      }));

      const singleAnnotation = new SingleAnnotation(page);
      await singleAnnotation.annotatableInput.fill('chr1 11796321 G A');
      await singleAnnotation.goButton.click();
      await expect(page.locator(utils.SINGLE_ANNOTATION_ERROR)).toBeVisible();

      await page.unroute(ANNOTATE_ROUTE);

      await singleAnnotation.annotatableInput.fill('chr1 11796321 G T');
      await singleAnnotation.goButton.click();
      await singleAnnotation.waitForReport();
      await expect(singleAnnotation.report).toBeVisible();
      await expect(page.locator(utils.SINGLE_ANNOTATION_ERROR)).not.toBeVisible();
    });
});
