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

  // 30s timeout (vs default 5s): page just navigated to the email-confirmation
  // href, which redirects to /login; the SPA needs to bootstrap from cold and
  // render <app-login>. Under 4-worker CI contention this can exceed 5s. Match
  // Playwright's default 30s actionability budget. Bead tb-1am.
  await expect(page.locator('app-login')).toBeVisible({timeout: 30000});
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
  await page.waitForSelector('app-single-annotation-wrapper', {timeout: 120000});
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

export const EXTRA_QUOTA_TYPES = ['jobs', 'variants', 'attributes'] as const;
export type ExtraQuotaType = typeof EXTRA_QUOTA_TYPES[number];

export type CurrentQuotaType =
  | 'daily_jobs'
  | 'monthly_jobs'
  | 'daily_variants'
  | 'monthly_variants'
  | 'daily_attributes'
  | 'monthly_attributes';

export const backendUrl = process.env['CI'] === '1' ? 'http://backend:9001' : 'http://localhost:8000';

export async function resetDailyQuota(page: Page): Promise<void> {
  const response = await page.request.get(`${backendUrl}/admin-panel/reset-daily-quota`);
  expect(response.status()).toBe(204);
}

export async function resetMonthlyQuota(page: Page): Promise<void> {
  const response = await page.request.get(`${backendUrl}/admin-panel/reset-monthly-quota`);
  expect(response.status()).toBe(204);
}

export async function setExtraQuota(
  page: Page, email: string, quotaType: ExtraQuotaType, amount: number
): Promise<void> {
  const params = new URLSearchParams({ user_email: email, quota_type: quotaType, amount: String(amount) });
  const response = await page.request.get(`${backendUrl}/admin-panel/set-extra-quota?${params.toString()}`);
  expect(response.status()).toBe(200);
}

export async function setCurrentQuota(
  page: Page, email: string, quotaType: CurrentQuotaType, amount: number
): Promise<void> {
  const params = new URLSearchParams({ user_email: email, quota_type: quotaType, amount: String(amount) });
  const response = await page.request.get(`${backendUrl}/admin-panel/set-current-quota?${params.toString()}`);
  expect(response.status()).toBe(200);
}

export async function setAnonymousUserSessionQuota(
  page: Page, quotaType: CurrentQuotaType, amount: number
): Promise<void> {
  const params = new URLSearchParams({ quota_type: quotaType, amount: String(amount) });
  const response = await page.request.get(`${backendUrl}/admin-panel/set-session-quota?${params.toString()}`);
  expect(response.status()).toBe(200);
}

export async function setAnonymousUserIpQuota(
  page: Page, quotaType: CurrentQuotaType, amount: number
): Promise<void> {
  const params = new URLSearchParams({ quota_type: quotaType, amount: String(amount) });
  const response = await page.request.get(`${backendUrl}/admin-panel/set-ip-quota?${params.toString()}`);
  expect(response.status()).toBe(200);
}

export async function selectPipeline(page: Page, pipeline: string): Promise<void> {
  await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
  await page.locator('.dropdown-icon').click();
  await page.getByRole('option', { name: 'circle ' + pipeline, exact: true }).click();
  await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
}

export async function customDefaultPipeline(page: Page): Promise<void> {
  await page.locator('#pipeline-actions').getByRole('button', { name: 'draft New pipeline', exact: true }).click();
  await expect(page.locator('#pipelines-input')).toBeEmpty();
  await expect(page.locator('.monaco-editor').nth(0)).toBeEmpty();

  const saveResponse = page.waitForResponse(
    resp => resp.url().includes('api/pipelines/user'), {timeout: 30000}
  );

  await typeInPipelineEditor(
    page,
    '- effect_annotator:\n' +
    '   gene_models: hg38/gene_models/GENCODE/48/basic/ALL\n' +
    '   genome: hg38/genomes/GRCh38.p13\n' +
    '   attributes:\n' +
    '   - worst_effect\n' +
    '   - gene_effects\n' +
    '   - effect_details\n' +
    '   - name: gene_list \n' +
    '     internal: true\n'
  );

  await saveResponse;

  await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
}

export async function navigateToQuotas(page: Page): Promise<void> {
  const quotasResponse = page.waitForResponse(
    resp => resp.url().includes('/api/quotas') && resp.status() === 200,
    { timeout: 120000 }
  );
  await page.getByRole('link', { name: 'Quotas' }).click();
  await quotasResponse;
  await page.waitForSelector('app-user-quotas', { state: 'visible' });
}

