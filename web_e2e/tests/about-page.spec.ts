import { test, expect } from '@playwright/test';
import * as utils from '../utils';

test.describe('About page', () => {
  test('should show content for anonymous user', async({ page }) => {
    const aboutResponse = page.waitForResponse(
      resp => resp.url().includes('/api/about') && resp.status() === 200
    );
    await page.goto('/about', { waitUntil: 'load' });
    await aboutResponse;
    await expect(page.locator('#page-content')).toBeVisible();
    await expect(page.locator('#page-content')).toContainText('GAIn: Genomic Annotation Infrastructure');
  });


  test('should render links with correct hrefs', async({ page }) => {
    const aboutResponse = page.waitForResponse(
      resp => resp.url().includes('/api/about') && resp.status() === 200
    );
    await page.goto('/about', { waitUntil: 'load' });
    await aboutResponse;
    const content = page.locator('#page-content');
    await expect(content.locator('a[href="https://github.com/iossifovlab/gain"]')).toBeVisible();
    await expect(content.locator('a[href="https://iossifovlab.com/gaindocs/index.html"]')).toBeVisible();
    await expect(content.locator('a[href="https://grr.iossifovlab.com/"]')).toBeVisible();
    await expect(content.locator('a[href="https://grr-encode.iossifovlab.com/"]')).toBeVisible();
  });

  test('should be accessible via nav link for logged-in user', async({ page }) => {
    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);
    await utils.loginUser(page, email, password);

    const aboutResponse = page.waitForResponse(
      resp => resp.url().includes('/api/about') && resp.status() === 200
    );
    await page.getByRole('link', { name: 'About' }).first().click();
    await aboutResponse;
    await expect(page.locator('#page-content')).toContainText('GAIn: Genomic Annotation Infrastructure');
    expect(page.url()).toContain('/about');
  });
});
