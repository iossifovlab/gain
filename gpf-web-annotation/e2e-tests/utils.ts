import { expect, Page } from '@playwright/test';

export const mailhogUrl = 'http://mail:8025';
// export const mailhogUrl = 'http://localhost:8025';

export const inProcessBackgroundColor = 'rgb(211, 237, 255)';
export const failedBackgroundColor = 'rgb(255, 237, 239)';
export const waitingBackgroundColor = 'rgb(255, 245, 214)';
export const successBackgroundColor = 'rgb(255, 255, 255)';

export function getRandomString(): string {
  return Math.random().toString(36).substring(2, 9);
}

export async function registerUser(page: Page, email: string, password: string): Promise<void> {
  await page.goto('/register', {waitUntil: 'load'});
  await page.locator('#email').pressSequentially(email);
  await page.locator('#password').pressSequentially(password);

  const registerResponse = page.waitForResponse(
    resp => resp.url().includes('/api/register') && resp.status() === 200
  );
  await page.getByRole('button', { name: 'Create' }).click();

  await registerResponse;

  const href = await getLinkInEmail(page, email, 'GPFWA: Registration validation');
  await page.goto(href, {waitUntil: 'load'});

  await expect(page.locator('app-login')).toBeVisible();
}

export async function getLinkInEmail(page: Page, email: string, subject: string): Promise<string> {
  await page.goto(mailhogUrl, {waitUntil: 'load'});

  await expect(async() => {
    await page.locator('#search').pressSequentially(subject);
    await page.keyboard.press('Enter');
    await page.getByText(email).click();
  }).toPass({intervals: [1000, 2000, 3000, 4000]});

  const href = await page.locator('#preview-plain > a').getAttribute('href');
  if (!href) {
    throw new Error('Confirmation link not found in email.');
  }
  return href;
}

export async function loginUser(page: Page, email: string, password: string): Promise<void> {
  await page.goto('/login', {waitUntil: 'load'});
  await page.locator('#email').pressSequentially(email);
  await page.locator('#password').pressSequentially(password);
  await page.locator('#login-container').getByRole('button', { name: 'Login' }).click();
  await page.waitForSelector('app-single-allele-annotation-wrapper', {timeout: 120000});
}

export async function typeInPipelineEditor(page: Page, input: string): Promise<void> {
  /* eslint-disable
  @typescript-eslint/no-unsafe-assignment,
  @typescript-eslint/no-unsafe-member-access,
  @typescript-eslint/no-unsafe-call,
  @typescript-eslint/no-explicit-any */
  await page.waitForFunction(() => {
    return (window as any).monaco?.editor?.getModels()?.length > 0;
  });
  await page.evaluate((value) => {
    const editors = (window as any).monaco.editor.getEditors();
    // Pick the editor whose container is visible in the DOM
    const editor = editors.find((e: any) => {
      const container = e.getContainerDomNode();
      return container.offsetParent !== null; // visible in DOM
    });
    const model = editor.getModel();
    model.setValue(value);
  }, input);
  /* eslint-enable */
}

export async function selectPipeline(page: Page, pipeline: string): Promise<void> {
  await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
  await page.locator('.dropdown-icon').click();
  await page.getByRole('option', { name: 'circle ' + pipeline, exact: true }).click();
  await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
}

