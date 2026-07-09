import { expect, Locator, Page } from '@playwright/test';
import { PipelineEditor } from './pipeline-editor.page';

/**
 * Page object for the annotation-jobs surface: file upload (picker + drag/drop),
 * the CSV/TSV column-mapping table and separator controls, the result / new-job
 * panel, the jobs table, and the job-details modal.
 *
 * Thin by design: locators are exposed for assertions, methods wrap the repeated
 * flows (navigate to the page, upload, create, poll a job's status). Pipeline
 * editing on this page stays with PipelineEditor.
 */
export class AnnotationJobs {
  // file upload
  public readonly fileUpload: Locator;
  public readonly fileUploadField: Locator;
  public readonly uploadedFileContainer: Locator;
  public readonly fileInfo: Locator;
  public readonly deleteUploadedFile: Locator;
  public readonly createButton: Locator;

  // column mapping (CSV/TSV)
  public readonly columnSpecifying: Locator;
  public readonly table: Locator;
  public readonly instructions: Locator;
  public readonly cells: Locator;
  public readonly separatorList: Locator;
  public readonly tabSeparatorRadio: Locator;
  public readonly commaSeparatorRadio: Locator;
  public readonly selectGenome: Locator;

  // result / new job
  public readonly result: Locator;
  public readonly newJobSection: Locator;
  public readonly newJobButton: Locator;
  public readonly jobCreation: Locator;
  public readonly downloadResult: Locator;

  // jobs table
  public readonly jobsTable: Locator;
  public readonly jobNames: Locator;
  public readonly actions: Locator;
  public readonly deleteIcons: Locator;
  public readonly downloadIcons: Locator;
  public readonly gridCells: Locator;

  // job details modal
  public readonly jobDetails: Locator;
  public readonly downloadInput: Locator;
  public readonly downloadConfig: Locator;
  public readonly downloadAnnotated: Locator;
  public readonly jobStatusLabel: Locator;

  public constructor(private readonly page: Page) {
    this.fileUpload = page.locator('input[id="file-upload"]');
    this.fileUploadField = page.locator('#file-upload-field');
    this.uploadedFileContainer = page.locator('#uploaded-file-container');
    this.fileInfo = page.locator('#file-info');
    this.deleteUploadedFile = page.locator('#delete-uploaded-file');
    this.createButton = page.locator('#create-button');

    this.columnSpecifying = page.locator('app-column-specifying');
    this.table = page.locator('#table');
    this.instructions = page.locator('#instructions');
    this.cells = page.locator('.cell');
    this.separatorList = page.locator('.separator-list');
    this.tabSeparatorRadio = page.locator('#tab-separtor-radio');
    this.commaSeparatorRadio = page.locator('#comma-separtor-radio');
    this.selectGenome = page.locator('#select-genome');

    this.result = page.locator('#result');
    this.newJobSection = page.locator('#new-job-section');
    this.newJobButton = page.locator('#new-job-button');
    this.jobCreation = page.locator('app-job-creation');
    this.downloadResult = page.locator('#download-result');

    this.jobsTable = page.locator('app-jobs-table');
    this.jobNames = page.locator('.job-name');
    this.actions = page.locator('.actions');
    this.deleteIcons = page.locator('.delete-icon');
    this.downloadIcons = page.locator('app-jobs-table .download-icon');
    this.gridCells = page.locator('.grid-cell');

    this.jobDetails = page.locator('app-job-details');
    this.downloadInput = this.jobDetails.locator('#download-input');
    this.downloadConfig = this.jobDetails.locator('#download-config');
    this.downloadAnnotated = this.jobDetails.locator('#download-annotated');
    this.jobStatusLabel = this.jobDetails.locator('.status-label');
  }

  /** Navigate to the Annotation Jobs page and wait for the pipeline editor to load. */
  public static async open(page: Page): Promise<void> {
    await page.getByRole('link', { name: 'Annotation Jobs' }).click();
    await PipelineEditor.waitForLoaded(page);
  }

  /** Upload an input file through the file picker. */
  public async uploadFile(filePath: string): Promise<void> {
    await this.fileUpload.setInputFiles(filePath);
  }

  /** Create the job from the uploaded file + current pipeline. */
  public async create(): Promise<void> {
    await this.createButton.click();
  }

  /** The mat-select for a mapping column header (e.g. 'CHROM', 'POS', 'CHROM+POS+REF+ALT'). */
  public columnHeaderSelect(header: string): Locator {
    return this.page.locator(`[id="${header}-header"]`).locator('mat-select');
  }

  /** Map a column header to a column type option. */
  public async selectColumnType(header: string, option: string): Promise<void> {
    await this.columnHeaderSelect(header).click();
    await this.page.getByRole('option', { name: option, exact: true }).click();
  }

  /** Open the job-details modal for the nth job (info button in the table). */
  public async openJobDetails(index = 0): Promise<void> {
    await this.jobNames.getByText('info').nth(index).click();
  }

  /** Drop a file onto the upload field (drag-and-drop upload). Pass the file bytes
   * (e.g. fs.readFileSync(path)) from the test, where the fs types are in scope. */
  public async dropFile(fileBytes: Uint8Array, fileName: string, mimeType?: string): Promise<void> {
    const dataTransfer = await this.page.evaluateHandle(({ data, name, type }) => {
      const dt = new DataTransfer();
      const file = type
        ? new File([new Uint8Array(data)], name, { type })
        : new File([new Uint8Array(data)], name);
      dt.items.add(file);
      return dt;
    }, { data: [...fileBytes], name: fileName, type: mimeType });
    await this.fileUploadField.dispatchEvent('drop', { dataTransfer });
  }

  /** Poll (with reloads) until the first job's status cell shows the given color. */
  public async waitForJobStatus(color: string): Promise<void> {
    await expect(async() => {
      await expect(this.gridCells.nth(0)).toHaveCSS('background-color', color);
      await this.page.reload();
    }).toPass({ intervals: [2000, 3000, 5000], timeout: 120000 });
  }
}
