import { expect, Page } from '@playwright/test';

export const backendUrl = process.env['CI'] === '1' ? 'http://backend:9001' : 'http://localhost:8000';
export const mailpitUrl = process.env['CI'] === '1' ? 'http://mail:8025' : 'http://localhost:8025';


export const inProcessBackgroundColor = 'rgb(211, 237, 255)';
export const failedBackgroundColor = 'rgb(255, 237, 239)';
export const waitingBackgroundColor = 'rgb(255, 245, 214)';
export const successBackgroundColor = 'rgb(255, 255, 255)';

export function getRandomString(): string {
  return Math.random().toString(36).substring(2, 9);
}

/**
 * Default budget for the long waits below. The backend serialises synchronous
 * views on daphne's single thread_sensitive thread (iossifovlab/gain#150), so
 * a slow-but-progressing request legitimately takes tens of seconds under
 * 4-worker CI contention. Must stay ABOVE the Apache ProxyTimeout in
 * web_ui/httpd.conf so a true stall surfaces as a reportable 502 rather than
 * as an opaque client timeout -- see the comment there before changing either.
 */
export const LONG_WAIT_TIMEOUT = 120000;

/**
 * Grace window granted to the success element after the error element wins the
 * race, to absorb a stale error frame. Long enough to cover an Angular render
 * under CI contention, far short of the full wait budget.
 */
export const ERROR_GRACE_TIMEOUT = 5000;

/** Error element rendered by the single-annotation component. */
export const SINGLE_ANNOTATION_ERROR = '#single-annotation-container .error-message';

/** Error element rendered by the annotation-pipeline component. */
export const PIPELINE_ERROR = '#pipelines-container .error-message';

/**
 * Wait for `successSelector`, failing fast if the UI renders `errorSelector`
 * instead.
 *
 * A plain waitForSelector on a success-only element cannot tell "still
 * working" from "already failed": when a request errors, the component
 * renders its error text and the success element never appears, so the wait
 * burns its entire budget and then reports only which selector it wanted.
 * That is how iossifovlab/gain#492 cost 120s twice per occurrence and still
 * produced a failure message that named neither the failing request nor the
 * error the page had been displaying the whole time.
 *
 * Racing the two means the error path fails in seconds with the visible text
 * quoted, so the JUnit report alone identifies the cause without a trace --
 * which matters because traces are captured only on retry and are subject to
 * Jenkins build rotation.
 */
export async function waitForSuccessOrError(
  page: Page,
  successSelector: string,
  errorSelector: string,
  options: { timeout?: number; description?: string } = {},
): Promise<void> {
  const timeout = options.timeout ?? LONG_WAIT_TIMEOUT;
  const what = options.description ?? successSelector;

  const success = page.locator(successSelector).first();
  const error = page.locator(errorSelector).first();

  // Settle both branches to a sentinel instead of letting the loser reject:
  // Promise.race leaves the loser pending, and an unhandled rejection when it
  // later times out would surface as a spurious failure in an unrelated test.
  const outcome = await Promise.race([
    success.waitFor({ state: 'visible', timeout }).then(() => 'success' as const, () => 'timeout' as const),
    error.waitFor({ state: 'visible', timeout }).then(() => 'error' as const, () => 'timeout' as const),
  ]);

  if (outcome === 'success') {
    return;
  }

  if (outcome === 'error') {
    // The error element can win the race transiently: a previous step's error
    // may still be in the DOM for a tick while the success element is being
    // rendered (e.g. a pipeline config error clearing as a newly-typed valid
    // config finishes validating). Give the success element a short grace
    // window before declaring failure, so a stale frame cannot turn a passing
    // flow into a spurious error. Still fails in seconds, not the full budget.
    const recovered = await success
      .waitFor({ state: 'visible', timeout: ERROR_GRACE_TIMEOUT })
      .then(() => true, () => false);
    if (recovered) {
      return;
    }

    const text = (await error.textContent())?.trim() ?? '(empty)';
    throw new Error(
      `Waiting for ${what} failed: the UI rendered an error instead -- "${text}" ` +
      `(matched ${errorSelector}).`
    );
  }

  throw new Error(
    `Waiting for ${what} timed out after ${timeout}ms: neither ${successSelector} ` +
    `nor the error element ${errorSelector} became visible. The request is most ` +
    'likely still stalled on the backend (iossifovlab/gain#150).'
  );
}

/**
 * Wait for the pipeline editor to reach its loaded state, failing fast on a
 * config/load error. `.loaded-editor` comes from the selected pipeline's
 * status class; a config error replaces it with `invalid-config`, so on the
 * error path the success class can never appear.
 */
export async function waitForLoadedEditor(page: Page): Promise<void> {
  await waitForSuccessOrError(page, '.loaded-editor', PIPELINE_ERROR, {
    description: 'the pipeline editor to finish loading',
  });
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
  const query = encodeURIComponent(`subject:"${subject}" to:${email}`);

  let messageId = '';
  await expect.poll(async() => {
    const response = await page.request.get(`${mailpitUrl}/api/v1/search?query=${query}`);
    const data = await response.json() as { messages: Array<{ ID: string }> };
    messageId = data.messages?.[0]?.ID ?? '';
    return messageId !== '';
  }, { timeout: 10000, intervals: [1000, 2000, 3000, 4000] }).toBe(true);

  const response = await page.request.get(`${mailpitUrl}/api/v1/message/${messageId}`);
  const message = await response.json() as { Text: string };

  const match = message.Text.match(/https?:\/\/\S+/);
  if (!match) {
    throw new Error('Confirmation link not found in email.');
  }
  return match[0];
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
  const cookies = await page.context().cookies();
  const sessionId = cookies.find(c => c.name === 'sessionid')?.value;
  const params = new URLSearchParams({ quota_type: quotaType, amount: String(amount) });
  if (sessionId) {
    params.append('session_id', sessionId);
  }
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

// Reset the accumulated anonymous jobs for the caller's IP. Completed anonymous
// jobs are no longer reaped on WebSocket disconnect (iossifovlab/gain#216), so
// they pile up across tests on the shared CI IP and trip can_create()'s hard
// per-IP daily-jobs cap. Call this in beforeEach (after waitForSession) so each
// job-creating anonymous test starts from zero rows.
export async function deleteAnonymousJobs(page: Page): Promise<void> {
  const response = await page.request.get(`${backendUrl}/admin-panel/delete-anonymous-jobs`);
  expect(response.status()).toBe(204);
}

export async function waitForSession(page: Page): Promise<void> {
  await expect.poll(async() => {
    const cookies = await page.context().cookies();
    return cookies.some(c => c.name === 'sessionid');
  }, { timeout: 30000 }).toBe(true);
}

export async function selectPipeline(page: Page, pipeline: string): Promise<void> {
  await waitForLoadedEditor(page);
  await page.locator('.dropdown-icon').click();
  await page.getByRole('option', { name: 'circle ' + pipeline, exact: true }).click();
  await waitForLoadedEditor(page);
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

  await waitForLoadedEditor(page);
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

