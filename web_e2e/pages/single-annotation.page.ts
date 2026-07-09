import { Locator, Page } from '@playwright/test';
import { PipelineEditor } from './pipeline-editor.page';

/**
 * Page object for the single-annotation surface: the annotatable input + Go
 * button, the annotation report (annotatable fields, attribute containers,
 * value grids, the compact/full toggle, download), the attribute/annotator
 * info modal, the query history, and per-annotatable notes.
 *
 * Thin by design: locators are exposed for assertions, methods wrap the
 * repeated flows (annotate, toggle full report, pick an example).
 */
export class SingleAnnotation {
  // input
  public readonly annotatableInput: Locator;
  public readonly goButton: Locator;
  public readonly validationMessage: Locator;
  public readonly examplesButton: Locator;

  // report container + annotatable fields
  public readonly report: Locator;
  public readonly chromosome: Locator;
  public readonly position: Locator;
  public readonly reference: Locator;
  public readonly alternate: Locator;
  public readonly annotatableType: Locator;
  public readonly positionStart: Locator;
  public readonly positionEnd: Locator;

  // report body
  public readonly annotators: Locator;
  public readonly attributeContainers: Locator;
  public readonly attributeHeaders: Locator;
  public readonly attributeResults: Locator;
  public readonly attributeDescriptions: Locator;
  public readonly compactReport: Locator;
  public readonly compactValueResults: Locator;
  public readonly valueResults: Locator;
  public readonly fullReportSwitch: Locator;
  public readonly downloadReportButton: Locator;

  // attribute / annotator info modal
  public readonly infoIcons: Locator;
  public readonly modalContent: Locator;
  public readonly annotatorHeader: Locator;

  // history
  public readonly historyTable: Locator;
  public readonly annotatableLinks: Locator;
  public readonly annotatableCells: Locator;
  public readonly deleteButtons: Locator;

  // notes
  public readonly editNoteButtons: Locator;
  public readonly noteInput: Locator;
  public readonly confirmNoteButton: Locator;
  public readonly cancelNoteButton: Locator;
  public readonly noteLabels: Locator;

  public constructor(private readonly page: Page) {
    this.annotatableInput = page.getByPlaceholder('Type annotatable...');
    this.goButton = page.getByRole('button', { name: 'Go', exact: true });
    this.validationMessage = page.locator('#validation-message');
    this.examplesButton = page.locator('#examples-button');

    this.report = page.locator('#report');
    this.chromosome = page.locator('#annotatable-chromosome');
    this.position = page.locator('#annotatable-position');
    this.reference = page.locator('#annotatable-reference');
    this.alternate = page.locator('#annotatable-alternate');
    this.annotatableType = page.locator('#annotatable-type');
    this.positionStart = page.locator('#position-start');
    this.positionEnd = page.locator('#position-end');

    this.annotators = page.locator('.annotator');
    this.attributeContainers = page.locator('.attribute-container');
    this.attributeHeaders = page.locator('.attribute-header');
    this.attributeResults = page.locator('.attribute-result');
    this.attributeDescriptions = page.locator('.attribute-container .attribute-description');
    this.compactReport = page.locator('#compact-report');
    this.compactValueResults = page.locator('.compact-value-result');
    this.valueResults = page.locator('.value-result');
    this.fullReportSwitch = page.locator('.switch');
    this.downloadReportButton = page.locator('#download-report-button');

    this.infoIcons = page.locator('.info-icon');
    this.modalContent = page.locator('#modal-content');
    this.annotatorHeader = page.locator('.annotator-header');

    this.historyTable = page.locator('#history-table');
    this.annotatableLinks = page.locator('.annotatable-link');
    this.annotatableCells = page.locator('.annotatable-cell');
    this.deleteButtons = page.locator('.delete-btn');

    this.editNoteButtons = page.locator('.edit-btn');
    this.noteInput = page.locator('.note-input');
    this.confirmNoteButton = page.locator('.confirm-btn');
    this.cancelNoteButton = page.locator('.cancel-btn');
    this.noteLabels = page.locator('.note-label');
  }

  /** Navigate to the Single Annotation page and wait for the pipeline editor to load. */
  public static async open(page: Page): Promise<void> {
    await page.getByRole('link', { name: 'Single Annotation' }).click();
    await PipelineEditor.waitForLoaded(page);
  }

  /** Wait until the annotation report is rendered. */
  public async waitForReport(): Promise<void> {
    await this.page.waitForSelector('#report', { timeout: 120000 });
  }

  /** Type an annotatable, run it, and wait for the report. */
  public async annotate(annotatable: string): Promise<void> {
    await this.annotatableInput.fill(annotatable);
    await this.goButton.click();
    await this.waitForReport();
  }

  /** Toggle between compact and full report mode. */
  public async toggleFullReport(): Promise<void> {
    await this.fullReportSwitch.click();
  }

  /** Open the examples menu and pick an example annotatable by its label. */
  public async selectExample(name: string): Promise<void> {
    await this.examplesButton.click();
    await this.exampleMenuItem(name).click();
  }

  /** Menu item locator inside the examples menu (for visibility assertions). */
  public exampleMenuItem(name: string): Locator {
    return this.page.getByRole('menuitem', { name, exact: true });
  }

  /** The attribute container whose header matches the given text. */
  public attributeContainer(headerText: string): Locator {
    return this.attributeContainers.filter({
      has: this.page.locator('.attribute-header', { hasText: headerText }),
    });
  }
}
