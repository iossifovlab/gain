import { Locator, Page } from '@playwright/test';

/**
 * Page object for the "New annotator" wizard dialog. The stepper has four steps:
 * select annotator -> configure annotator -> configure attributes -> configure
 * aggregation.
 *
 * Thin by design: locators are exposed for assertions, and methods wrap only the
 * repeated multi-step flows (single-step interactions stay in the tests via the
 * exposed locators).
 */
export class AnnotatorDialog {
  public readonly addAnnotatorButton: Locator;
  public readonly container: Locator;
  public readonly header: Locator;
  public readonly annotatorDropdown: Locator;
  public readonly nextButton: Locator;
  public readonly backButton: Locator;
  public readonly finishButton: Locator;
  public readonly attributeDropdown: Locator;
  public readonly attributeInput: Locator;
  public readonly attributeOptions: Locator;
  public readonly attributeSources: Locator;
  public readonly aggregatorsList: Locator;
  public readonly aggregatorNames: Locator;
  public readonly aggregatorTypes: Locator;
  public readonly aggregatorSelects: Locator;
  public readonly aggregatorOptions: Locator;
  public readonly separatorField: Locator;

  public constructor(protected page: Page) {
    this.addAnnotatorButton = page.locator('#pipeline-actions #add-annotator-button');
    this.container = page.locator('mat-dialog-container');
    this.header = page.locator('#modal-header');
    this.annotatorDropdown = page.getByRole('combobox', { name: 'Select annotator' });
    this.nextButton = page.getByRole('button', { name: 'Next' });
    this.backButton = page.getByRole('button', { name: 'Back' });
    this.finishButton = page.getByRole('button', { name: 'Finish' });
    this.attributeDropdown = page.locator('#attributes-dropdown');
    this.attributeInput = page.locator('#attributes-dropdown input');
    this.attributeOptions = page.locator('mat-option.attribute-option');
    this.attributeSources = page.locator('.attribute-source');
    this.aggregatorsList = page.locator('#attributes-aggregators-list');
    this.aggregatorNames = page.locator('#attributes-aggregators-list .attribute-name-main');
    this.aggregatorTypes = page.locator('#attributes-aggregators-list .data-type-badge');
    this.aggregatorSelects = page.locator('#attributes-aggregators-list .aggregator mat-select');
    this.aggregatorOptions = page.locator('mat-option.aggregator-option');
    this.separatorField = page.locator('#attributes-aggregators-list .separator-field');
  }

  /** Open the dialog from the pipeline actions bar. */
  public async open(): Promise<void> {
    await this.addAnnotatorButton.click();
  }

  /**
   * Step 1: pick an annotator type by name. Pass { exact: true } when the name is
   * a prefix of another annotator (e.g. 'effect_annotator').
   */
  public async selectAnnotator(name: string, options: { exact?: boolean } = {}): Promise<void> {
    await this.annotatorDropdown.click();
    await this.page.locator('mat-option').getByText(name, { exact: options.exact }).first().click();
  }

  /**
   * Step 2: choose an option in a Configure-annotator parameter dropdown, keyed by
   * the parameter name (e.g. 'resource_id', 'input_gene_list', 'chain'). Pass
   * { search } to type into the dropdown's search box first (for long lists where
   * the search text differs from the option label).
   */
  public async selectParameter(
    parameter: string,
    optionText: string,
    options: { search?: string } = {}
  ): Promise<void> {
    const dropdown = this.page.locator(`[id="${parameter}-dropdown"]`);
    await dropdown.click();
    if (options.search !== undefined) {
      await dropdown.locator('input').fill(options.search);
    }
    await this.page.locator('mat-option').getByText(optionText).first().click();
  }

  /** Advance to the next step. */
  public async next(): Promise<void> {
    await this.nextButton.click();
  }

  /** Go back to the previous step. */
  public async back(): Promise<void> {
    await this.backButton.click();
  }

  /** Step 3: open the attribute autocomplete panel without typing. */
  public async openAttributeDropdown(): Promise<void> {
    await this.page.locator('#attributes-dropdown .dropdown-icon').click();
  }

  /** Step 3: add an attribute by typing and picking the matching option. */
  public async addAttribute(text: string): Promise<void> {
    // Typing runs a debounced server-side search that repopulates the option
    // list; wait for it so we do not click a stale option mid-refresh.
    await Promise.all([
      this.attributeInput.fill(text),
      this.page.waitForResponse(resp => resp.url().includes('editor/annotator_attributes')),
    ]);
    await this.page.locator('.attribute-option', { hasText: text }).first().click();
  }

  /**
   * Step 4: set the aggregator for the attribute row at `index`. `aggregator` is
   * matched exactly (e.g. 'mean', 'join', 'min').
   */
  public async selectAggregator(index: number, aggregator: string): Promise<void> {
    await this.aggregatorSelects.nth(index).click();
    await this.aggregatorOptions.getByText(aggregator, { exact: true }).click();
  }

  /** Finish the wizard (writes the annotator into the pipeline config). */
  public async finish(): Promise<void> {
    await this.finishButton.click();
  }
}

/**
 * Page object for the "New resource" wizard dialog. It is the annotator dialog
 * with one extra leading step -- selecting a resource from the resource table --
 * so it extends AnnotatorDialog and adds the resource-selection surface.
 */
export class ResourceDialog extends AnnotatorDialog {
  public readonly addResourceButton: Locator;
  public readonly resourcesContent: Locator;
  public readonly resourceTable: Locator;
  public readonly resourceSearch: Locator;
  public readonly resourceCount: Locator;
  public readonly resourceSearchError: Locator;

  public constructor(page: Page) {
    super(page);
    this.addResourceButton = page.locator('#pipeline-actions #add-resource-button');
    this.resourcesContent = page.locator('#resources-form');
    this.resourceTable = page.locator('#resource-list');
    this.resourceSearch = page.locator('#resource-search-input');
    this.resourceCount = page.locator('#resource-count');
    this.resourceSearchError = page.locator('#resource-input-form .error-message').nth(0);
  }

  /** Open the dialog from the pipeline actions bar. */
  public async open(): Promise<void> {
    await this.addResourceButton.click();
  }

  public async searchResource(searchText: string): Promise<void> {
    const search = searchText.replace(/"/g, '%22');
    await Promise.all([
      this.resourceSearch.fill(searchText),
      this.resourceSearch.dispatchEvent('keyup'),
      this.page.waitForResponse(
        resp => resp.url().includes(`api/resources/search?search=${search}`),
        { timeout: 30000 }
      )
    ]);
  }

  public async selectResourceType(type: string): Promise<void> {
    await this.page.locator('#resource-type mat-select').click();
    await this.page.locator('mat-option').filter({ hasText: type }).click();
  }

  /** Step 1 "Actions" column: the "Continue" (configure) button for a resource row. */
  public getResourceContinueButton(resourceId: string): Locator {
    return this.page.locator(`[id="${resourceId}-continue-button"]`);
  }

  /** Step 1 "Actions" column: the "Finish with defaults" button for a resource row. */
  public getResourceFinishButton(resourceId: string): Locator {
    return this.page.locator(`[id="${resourceId}-finish-button"]`);
  }

  /** Step 1 "Actions" column: the "Resource details" button (opens a new tab via its inner link). */
  public getResourceDetailsButton(resourceId: string): Locator {
    return this.page.locator(`[id="${resourceId}-resource-details-button"]`);
  }
}
