import { Locator, Page } from '@playwright/test';

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
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
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
