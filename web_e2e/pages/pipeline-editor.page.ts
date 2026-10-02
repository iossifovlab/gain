import { Locator, Page, Request, Response, WebSocket } from '@playwright/test';
import { waitForLoadedEditor } from '../utils';

function isValidateRequest(request: Request): boolean {
  return request.method() === 'POST' && request.url().includes('api/pipelines/validate');
}

function isPipelineListRequest(request: Request): boolean {
  return request.method() === 'GET'
    && /\/api\/pipelines\/?$/.test(new URL(request.url()).pathname);
}

function validatedConfig(request: Request): string | undefined {
  try {
    return (request.postDataJSON() as {config?: string} | null)?.config;
  } catch {
    return undefined;
  }
}

/**
 * Records the editor traffic that decides whether New pipeline's validation
 * arrives: every validate POST (with the config it sent and how it was
 * answered), every pipeline-list GET, every websocket (re)connect, and
 * snapshots of the monaco editor taken on request. Its report is what a
 * timed-out wait throws, so a failure names the mechanism that suppressed or
 * replaced the expected request.
 */
class EditorTrafficLog {
  private readonly started = Date.now();
  private readonly events: string[] = [];

  private readonly onRequest = (request: Request): void => {
    if (isValidateRequest(request)) {
      const config = validatedConfig(request);
      const shown = config === undefined
        ? 'unreadable body'
        : `config length ${config.length}, prefix ${JSON.stringify(config.slice(0, 40))}`;
      this.log(`validate POST sent (${shown})`);
    } else if (isPipelineListRequest(request)) {
      this.log(`pipeline-list GET sent (${request.url()})`);
    }
  };

  private readonly onResponse = (response: Response): void => {
    const request = response.request();
    if (isValidateRequest(request)) {
      const config = validatedConfig(request);
      this.log(`validate POST answered ${response.status()} (config length ${config?.length ?? '?'})`);
    } else if (isPipelineListRequest(request)) {
      this.log(`pipeline-list GET answered ${response.status()}`);
    }
  };

  private readonly onRequestFailed = (request: Request): void => {
    if (isValidateRequest(request) || isPipelineListRequest(request)) {
      this.log(`${request.method()} ${request.url()} failed: ${request.failure()?.errorText ?? 'unknown'}`);
    }
  };

  private readonly onWebSocket = (ws: WebSocket): void => {
    this.log(`websocket connect (${ws.url()})`);
    ws.on('close', () => this.log(`websocket closed (${ws.url()})`));
  };

  public constructor(private readonly page: Page) {
    page.on('request', this.onRequest);
    page.on('response', this.onResponse);
    page.on('requestfailed', this.onRequestFailed);
    page.on('websocket', this.onWebSocket);
  }

  public stop(): void {
    this.page.off('request', this.onRequest);
    this.page.off('response', this.onResponse);
    this.page.off('requestfailed', this.onRequestFailed);
    this.page.off('websocket', this.onWebSocket);
  }

  public report(): string {
    return `Observed during the wait:\n${this.events.map(e => `  ${e}`).join('\n')}`;
  }

  /**
   * Log whether the monaco editor exists yet and how long its text is. Until
   * monaco has loaded, the editor only stores the text it is given, and the
   * editor reports no change for it once it does load.
   */
  public async noteEditorState(moment: string): Promise<void> {
    /* eslint-disable
    @typescript-eslint/no-unsafe-assignment,
    @typescript-eslint/no-unsafe-member-access,
    @typescript-eslint/no-unsafe-call,
    @typescript-eslint/no-explicit-any */
    const state = await this.page.evaluate(() => {
      const editors = (window as any).monaco?.editor?.getEditors?.() ?? [];
      const lengths: number[] = editors.map((e: any) => (e.getValue() as string).length);
      return lengths;
    }).catch((error: unknown) => `unreadable (${String(error)})`);
    /* eslint-enable */
    const shown = typeof state === 'string'
      ? state
      : state.length === 0
        ? 'no monaco editor exists yet'
        : `monaco editor text length ${state.join(', ')}`;
    this.log(`${moment}: ${shown}`);
  }

  private log(event: string): void {
    this.events.push(`+${Date.now() - this.started}ms ${event}`);
  }
}

/**
 * Page object for the pipeline editor surface: the pipeline dropdown, the YAML
 * editor and status bar, the actions bar (New pipeline / Save / Save as /
 * Delete), the Save-as name modal, and the change/create/delete confirmation
 * popovers.
 *
 * Thin by design: locators are exposed for assertions, methods wrap the repeated
 * multi-step flows. Typing YAML into the editor stays in utils.typeInPipelineEditor
 * (a low-level monaco helper shared beyond this surface).
 */
export class PipelineEditor {
  // pipeline dropdown + editor
  public readonly pipelineInput: Locator;
  public readonly dropdownIcon: Locator;
  public readonly monacoEditor: Locator;
  public readonly statusItems: Locator;
  public readonly errorMessage: Locator;

  // actions bar
  public readonly newPipelineButton: Locator;
  public readonly saveButton: Locator;
  public readonly saveAsButton: Locator;
  public readonly deleteButton: Locator;

  // Save-as name modal
  public readonly nameModal: Locator;
  public readonly nameInput: Locator;
  public readonly saveNameButton: Locator;
  public readonly cancelNameButton: Locator;
  public readonly nameError: Locator;

  // confirmation popovers
  public readonly changeConfirmPopover: Locator;
  public readonly createConfirmPopover: Locator;
  public readonly deleteConfirmPopover: Locator;
  public readonly confirmChangeButton: Locator;
  public readonly cancelChangeButton: Locator;
  public readonly confirmDeleteButton: Locator;
  public readonly cancelDeleteButton: Locator;

  public constructor(private readonly page: Page) {
    this.pipelineInput = page.locator('#pipelines-input');
    this.dropdownIcon = page.locator('#pipelines-container .dropdown-icon');
    this.monacoEditor = page.locator('.monaco-editor');
    this.statusItems = page.locator('#status-bar .status-item');
    this.errorMessage = page.locator('#pipelines-container .error-message');
    this.newPipelineButton = page.locator('#new-pipeline-button');
    this.saveButton = page.locator('#save-button');
    this.saveAsButton = page.locator('#save-as-button');
    this.deleteButton = page.locator('#delete-button');
    this.nameModal = page.locator('#name-modal');
    this.nameInput = page.locator('#name-modal input');
    this.saveNameButton = page.locator('#save-name-button');
    this.cancelNameButton = page.locator('#cancel-button');
    this.nameError = page.locator('#name-modal .error-message');
    this.changeConfirmPopover = page.locator('#change-confirmation-popover');
    this.createConfirmPopover = page.locator('#create-confirmation-popover');
    this.deleteConfirmPopover = page.locator('#delete-confirmation-popover');
    this.confirmChangeButton = page.locator('#confirm-change');
    this.cancelChangeButton = page.locator('#cancel-change');
    this.confirmDeleteButton = page.locator('#confirm-delete');
    this.cancelDeleteButton = page.locator('#cancel-delete');
  }

  /** Wait until the pipeline editor has finished loading a pipeline. */
  public static async waitForLoaded(page: Page): Promise<void> {
    await waitForLoadedEditor(page);
  }

  /** Select a pipeline from the dropdown by name. */
  public async selectPipeline(name: string): Promise<void> {
    await PipelineEditor.waitForLoaded(this.page);
    await this.dropdownIcon.click();
    await this.page.getByRole('option', { name: 'circle ' + name, exact: true }).click();
    await PipelineEditor.waitForLoaded(this.page);
  }

  /** Start a new (empty) pipeline. */
  public async newPipeline(): Promise<void> {
    await this.newPipelineButton.click();
  }

  /**
   * Start a new (empty) pipeline and wait for the validation round trip the
   * click triggers, so callers can assert on validation-gated UI (e.g. the
   * Create button) without racing the request. The wait matches only the
   * cleared editor's validation (its request body carries an empty config),
   * so a still-in-flight validation of the previous editor text cannot
   * satisfy it. Use this only where the click actually validates: with
   * unsaved changes it opens a confirmation popover instead, and on an
   * already-empty editor with no pipeline selected it is a no-op -- keep
   * using newPipeline() in both cases.
   *
   * Throws when validation answers non-2xx (the server sheds validations
   * with a 503 when its bounded pool is full, iossifovlab/gain#659), so a
   * shed request fails distinctly instead of as a disabled-button timeout
   * in the caller's next assertion. Throws on timeout with a report of the
   * validate POSTs (config length and prefix), pipeline-list GETs and
   * websocket connects seen during the wait, and of whether the monaco editor
   * existed at the click, so the failure says whether the validation went out
   * with other text or never went out, and why.
   */
  public async newPipelineValidated(): Promise<void> {
    const traffic = new EditorTrafficLog(this.page);
    await traffic.noteEditorState('before the click');
    try {
      const [validateResponse] = await Promise.all([
        this.page.waitForResponse(resp =>
          isValidateRequest(resp.request()) && validatedConfig(resp.request()) === '',
        { timeout: 30000 }),
        this.newPipeline(),
      ]);
      if (!validateResponse.ok()) {
        throw new Error(
          `pipeline validation after New pipeline answered ${validateResponse.status()}`);
      }
    } catch (error) {
      if (error instanceof Error && error.name === 'TimeoutError') {
        await traffic.noteEditorState('at the timeout');
        throw new Error(
          'no validation of the cleared editor (config === \'\') was answered after '
          + `New pipeline (${error.message}).\n${traffic.report()}`);
      }
      throw error;
    } finally {
      traffic.stop();
    }
  }

  /** Save the current user pipeline. */
  public async save(): Promise<void> {
    await this.saveButton.click();
  }

  /** Open the Save-as name modal. */
  public async saveAs(): Promise<void> {
    await this.saveAsButton.click();
  }

  /**
   * Fill the (already open) Save-as name modal, confirm, and wait for the saved
   * pipeline to load. Use this only for a successful save; a rejected name (e.g.
   * a duplicate) never triggers the load request.
   */
  public async saveAsName(name: string): Promise<void> {
    await this.nameInput.fill(name);
    await Promise.all([
      this.saveNameButton.click(),
      this.page.waitForResponse(resp => resp.url().includes('api/pipelines/load'), { timeout: 30000 }),
    ]);
  }

  /** Open the delete confirmation popover. */
  public async delete(): Promise<void> {
    await this.deleteButton.click();
  }

  /** Nth status-bar item (0: annotators, 1: attributes, 2: annotatables, 3: gene lists). */
  public statusItem(index: number): Locator {
    return this.statusItems.nth(index);
  }
}
