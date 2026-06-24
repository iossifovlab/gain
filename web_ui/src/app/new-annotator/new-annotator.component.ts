import { CommonModule } from '@angular/common';
import { AfterViewInit, Component, ElementRef, inject, OnDestroy, OnInit, signal, ViewChild } from '@angular/core';
import { FormBuilder, FormControl, FormGroup, FormsModule, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatAutocompleteModule, MatAutocompleteTrigger } from '@angular/material/autocomplete';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatStepper, MatStepperModule } from '@angular/material/stepper';
import { CdkStepperModule, STEPPER_GLOBAL_OPTIONS } from '@angular/cdk/stepper';
import { PipelineEditorService } from '../pipeline-editor.service';
import {
  map,
  Observable,
  of,
  switchMap,
  take,
  forkJoin,
  debounceTime,
  filter,
  Subscription,
  distinctUntilChanged,
  Subject
} from 'rxjs';
import {
  AnnotatorConfig,
  AttributeData,
  AttributePage,
  AnnotatorConfigResource,
  ResourceAnnotatorConfigs,
  ResourcePage,
  Resource,
  AnnotatorAttribute,
  AggregatorConfig
} from './annotator';
import { MAT_DIALOG_DATA, MatDialogRef } from '@angular/material/dialog';
import { MatSelect } from '@angular/material/select';
import { MatTooltipModule } from '@angular/material/tooltip';

@Component({
  selector: 'app-new-annotator',
  imports: [
    MatButtonModule,
    MatStepperModule,
    FormsModule,
    ReactiveFormsModule,
    MatFormFieldModule,
    MatInputModule,
    CommonModule,
    CdkStepperModule,
    MatAutocompleteModule,
    MatSelect,
    MatTooltipModule
  ],
  providers: [
    {
      provide: STEPPER_GLOBAL_OPTIONS,
      useValue: { showError: true }
    }
  ],
  templateUrl: './new-annotator.component.html',
  styleUrls: ['./new-annotator.component.css', './material-components.css'],
})
export class NewAnnotatorComponent implements OnInit, AfterViewInit, OnDestroy {
  public resourceStep: FormGroup<{ resourceType: FormControl<string>, resourceId: FormControl<string> }>;
  public resourceTypes: string[];
  public resourcePage: ResourcePage;
  public selectedResourceType = '';
  private searchSubject = new Subject<{ value: string; type: string }>();
  private resourceSearchSubscription = new Subscription();
  public resourceAnnotators: ResourceAnnotatorConfigs;
  public annotatorStep: FormGroup<{ annotator: FormControl<string> }>;
  public annotatorTypes: string[] = [];
  public filteredAnnotatorTypes: string[];
  public configurationStep: FormGroup = new FormGroup({});
  public filteredResourceValues: Map<string, string[]>;
  public annotatorConfig: AnnotatorConfig;
  public attributeStep: FormGroup<{ attribute: FormControl<string> }>;
  public attributePage: AttributePage;
  public selectedAttributes: AttributeData[] = [];
  public filteredAttributes: AttributeData[];
  public areAttributesValid: boolean;
  @ViewChild('stepper', { static: true }) public stepper: MatStepper;
  public existingAttributeNames: Set<string> = new Set();
  public attributesSubscription = new Subscription();
  public aggregators: AggregatorConfig[] = [];
  public annotatorAttributes: AnnotatorAttribute[] = [];
  public errorMessage = '';
  public editAttributeNameView = false;
  public searchError = '';
  public displayWarningMessage = false;
  public isAttributeLoading = false;
  private attributePanel: HTMLElement = null;
  private attributePanelScrollHandler: (() => void) = null;
  public tooltipContent =
    '- use AND to perform \'and\',\n'+
    '- use OR to perform \'or\',\n' +
    '- use spaces to separate strings\n' +
    '- surround strings in "" to use spaces inside the string';


  @ViewChild('loadPageIndicator') public loadPageIndicator!: ElementRef<Element>;
  public resources = signal<Resource[]>([]);
  public nextPage: number;
  public totalPages: number;
  public isLoading = false;
  public hasMore = true;
  public observer!: IntersectionObserver;
  public isResourceTableInitialized = false;
  public createWithDefaults = true;

  @ViewChild('attributeInput') private set attributeInput(trigger: MatAutocompleteTrigger) {
    if (!trigger) {
      return;
    }
    trigger.autocomplete.opened.subscribe(() => {
      setTimeout(() => {
        const panel = trigger.autocomplete.panel?.nativeElement as HTMLElement | undefined;
        if (!panel) {
          return;
        }
        this.removeAttributePanelScrollHandler();
        this.attributePanelScrollHandler = (): void => this.onAttributePanelScroll(panel);
        panel.addEventListener('scroll', this.attributePanelScrollHandler);
        this.attributePanel = panel;
      });
    });
  }

  public readonly data = inject(MAT_DIALOG_DATA) as {pipelineId: string, isResourceWorkflow: boolean};
  private readonly dialogRef = inject(MatDialogRef) as MatDialogRef<NewAnnotatorComponent>;
  private readonly editorService = inject(PipelineEditorService);
  private readonly formBuilder = inject(FormBuilder);

  public ngOnInit(): void {
    this.annotatorStep = this.formBuilder.group({
      annotator: ['', Validators.required],
    });
    this.setupAnnotatorValueFiltering();

    if (this.data.isResourceWorkflow) {
      this.resourceStep = this.formBuilder.group({
        resourceType: ['', Validators.required],
        resourceId: ['', Validators.required],
      });

      this.editorService.getResourceTypes().pipe(take(1)).subscribe(res => {
        this.resourceTypes = ['All', ...res.sort()];
        this.setupResourceSearching();
        this.selectedResourceType = this.resourceTypes[0];
      });

      // Clear search when returning to resource selection step from later steps
      this.stepper.selectionChange.subscribe(event => {
        if (event.selectedIndex === 0 && event.previouslySelectedIndex > 0) {
          this.resourceStep.get('resourceId').setValue('', { emitEvent: true });
        }
      });
    } else {
      this.requestAnnotators();
    }

    this.editorService.getAggregators().pipe(take(1)).subscribe(res => {
      this.aggregators = res;
    });
  }

  public ngAfterViewInit(): void {
    if (!this.data.isResourceWorkflow) {
      return;
    }
    const container: Element = this.loadPageIndicator.nativeElement.closest('.resource-list-wrapper');
    if (!container) {
      console.error('Resource list container not found for infinite scroll');
      return;
    }

    this.observer = new IntersectionObserver(entries => {
      if (entries[0].isIntersecting && this.hasMore && !this.isLoading && this.isResourceTableInitialized) {
        this.loadMore();
      }
    },
    {
      root: container,
      threshold: 0
    });

    this.observer.observe(this.loadPageIndicator.nativeElement);
  }

  public loadMore(): void {
    this.isLoading = true;

    this.editorService.getResourcesBySearch(
      this.resourceStep.get('resourceId').value,
      this.resourceStep.get('resourceType').value,
      this.nextPage
    ).subscribe({
      next: (data) => {
        this.resources.update(current => [...current, ...data.resources]);
        this.resourcePage.resources.push(...data.resources);
        this.nextPage++;
        this.hasMore = this.nextPage < this.totalPages;
        this.isLoading = false;
      },
      error: () => {
        this.isLoading = false;
      }
    });
  }

  public ngOnDestroy(): void {
    this.observer?.disconnect();
    this.attributesSubscription?.unsubscribe();
    this.resourceSearchSubscription?.unsubscribe();
    this.searchSubject?.complete();

    this.removeAttributePanelScrollHandler();
  }

  private setupResourceSearching(): void {
    // Set up the search subject to handle API calls
    this.resourceSearchSubscription = this.searchSubject.pipe(
      distinctUntilChanged((prev, curr) => prev.value === curr.value && prev.type === curr.type),
      switchMap(({ value, type }) => this.editorService.getResourcesBySearch(value, type)),
    ).subscribe({
      next: pageData => {
        this.resourcePage = pageData;
        this.resources.set(pageData.resources);
        this.nextPage = pageData.page + 1;
        this.totalPages = pageData.totalPages;
        this.hasMore = this.nextPage < this.totalPages;
        this.searchError = '';
        this.isResourceTableInitialized = true;
      },
      error: (err: Error) => {
        this.searchError = err.message;
      }
    });

    // Trigger search on resourceId value changes
    this.resourceStep.get('resourceId').valueChanges.pipe(
      debounceTime(400),
      map(value => ({ value: this.normalizeString(value), type: this.selectedResourceType })),
    ).subscribe(obj => {
      this.searchSubject.next(obj);
    });

    // Trigger search when resourceType changes
    this.resourceStep.get('resourceType').valueChanges.subscribe(type => {
      this.selectedResourceType = type;
      this.searchSubject.next({ value: this.resourceStep.get('resourceId').value, type: type });
    });
  }

  public selectResource(id: string, navigate = false): void {
    this.createWithDefaults = navigate;
    this.clearErrorMessage();
    this.resourceStep.get('resourceId').setValue(id, { emitEvent: false });
    this.requestResourceAnnotators();
  }

  private requestAnnotators(): void {
    this.clearErrorMessage();
    this.editorService.getAnnotators().subscribe(res => {
      this.annotatorTypes = res.sort();
      const currentValue = this.annotatorStep.get('annotator').value;
      this.filteredAnnotatorTypes = this.filterDropdownContent(currentValue, this.annotatorTypes);
    });
  }

  public requestResourceAnnotators(): void {
    this.editorService.getResourceAnnotators(this.resourceStep.value.resourceId.trim()).pipe(
      take(1),
    ).subscribe(res => {
      this.resourceAnnotators = res;
      this.annotatorTypes = res.annotators.map(r => r.annotatorType);
      const currentValue = this.annotatorStep.get('annotator').value;
      this.filteredAnnotatorTypes = this.filterDropdownContent(currentValue, this.annotatorTypes);
      if (this.resourceAnnotators.annotators.length === 1) {
        this.autoSelectAnnotator(this.resourceAnnotators.annotators[0].annotatorType);
      } else if (this.resourceAnnotators.defaultAnnotator) {
        this.autoSelectAnnotator(this.resourceAnnotators.defaultAnnotator);
      }

      if (this.createWithDefaults) {
        if (this.annotatorStep.invalid) {
          this.errorMessage = 'Error while setting annotator in step 2';
        }
      } else {
        this.stepper.next();
      }
    });
  }

  private autoSelectAnnotator(annotatorType: string): void {
    this.annotatorStep.get('annotator').setValue(annotatorType);
    this.requestResources();
  }

  private setupAnnotatorValueFiltering(): void {
    this.annotatorStep.get('annotator').valueChanges.pipe(
      map((value: string) => this.filterDropdownContent(value, this.annotatorTypes))
    ).subscribe(filtered => {
      this.filteredAnnotatorTypes = filtered;
      if (!filtered.includes(this.normalizeString(this.annotatorStep.get('annotator').value))) {
        this.annotatorStep.get('annotator').setErrors({ invalidOption: true });
      }
    });
  }

  public normalizeString(value: string): string {
    return value === null ? '' : value.trim();
  }

  private getPipelineAttributes(config: AnnotatorConfig): Observable<AnnotatorConfig> {
    const attributeResources = config.resources.filter(r => r.fieldType === 'attribute');

    if (attributeResources.length === 0) {
      return of(config);
    }

    const observables = attributeResources.map(resource =>
      this.editorService.getPipelineAttributes(this.data.pipelineId, resource.attributeType).pipe(
        take(1),
        map(res => {
          const resourceIndex = config.resources.findIndex(r => r.key === resource.key);
          if (resourceIndex !== -1) {
            config.resources[resourceIndex] = new AnnotatorConfigResource(
              resource.key,
              resource.fieldType,
              resource.resourceType,
              resource.defaultValue,
              res,
              resource.optional,
              resource.attributeType
            );
          }
        })
      )
    );

    return forkJoin(observables).pipe(map(() => config));
  }

  public requestResources(): void {
    let annotatorConfigObservable = this.editorService.getAnnotatorConfig(
      this.normalizeString(this.annotatorStep.value.annotator));

    if (this.data.isResourceWorkflow) {
      annotatorConfigObservable = this.editorService.getAnnotatorConfig(
        this.annotatorStep.get('annotator').value,
        this.resourceAnnotators.annotators
          .find(a => a.annotatorType === this.annotatorStep.get('annotator').value).resourceJson
      );
    }

    annotatorConfigObservable.pipe(
      take(1),
      switchMap(config => this.getPipelineAttributes(config))
    ).subscribe(res => {
      this.annotatorConfig = res;
      this.initializeFilteredResourceValues();
      this.setupResourceControls();
      this.autoselectInputGeneList();


      if (this.data.isResourceWorkflow && this.createWithDefaults) {
        if (this.configurationStep.invalid) {
          this.errorMessage = 'Error while configuring annotator in step 3';
        } else {
          this.requestAttributes();
        }
      } else {
        this.stepper.next();
      }
    });
  }

  private autoselectInputGeneList(): void {
    const inputGeneList = this.annotatorConfig.resources.find(r => r.key === 'input_gene_list');
    if (inputGeneList && inputGeneList.possibleValues.length) {
      this.configurationStep.get('input_gene_list').setValue(inputGeneList.possibleValues[0]);
    }
  }

  private initializeFilteredResourceValues(): void {
    this.filteredResourceValues = new Map<string, string[]>();
    for (const resource of this.annotatorConfig.resources) {
      if (resource.fieldType === 'resource' || resource.fieldType === 'attribute') {
        this.filteredResourceValues.set(resource.key, resource.possibleValues);
      }
    }
  }

  private setupResourceControls(): void {
    const resourceGroup: Record<string, FormControl> = {};

    for (const resource of this.annotatorConfig.resources) {
      resourceGroup[resource.key] = new FormControl(
        resource.defaultValue ?? '',
        resource.optional ? Validators.nullValidator : Validators.required
      );
    }

    this.configurationStep = new FormGroup(resourceGroup);
    this.setupResourceValueFiltering();
  }

  private setupResourceValueFiltering(): void {
    // eslint-disable-next-line @stylistic/max-len
    this.annotatorConfig.resources.filter(r => r.fieldType === 'resource' || r.fieldType === 'attribute').forEach(resource => {
      this.configurationStep.get(resource.key).valueChanges.pipe(
        map((value: string) => this.filterDropdownContent(value, resource.possibleValues))
      ).subscribe(filtered => {
        this.filteredResourceValues.set(resource.key, filtered);
        if (!filtered.length) {
          this.configurationStep.get(resource.key).setErrors({ invalidOption: true });
        }
      });
    });
  }

  private filterDropdownContent(value: string, options: string[]): string[] {
    if (!value) {
      return options;
    }
    const filterValue = value.toLowerCase().replace(/\s/g, '');
    return options.filter(p => p.toLowerCase().replace(/\s/g, '').includes(filterValue));
  }

  public requestAttributes(): void {
    this.attributesSubscription.unsubscribe();
    this.attributesSubscription = this.getAttributesObservable().subscribe({
      next: res => {
        this.attributePage = res;
        this.selectedAttributes = [...res.attributes.filter(a => a.selectedByDefault)];
        this.filteredAttributes = res.attributes;
        this.setupAttributeValueFiltering();
        this.getPipelineAttributesNames();
        this.clearErrorMessage();

        if (!this.data.isResourceWorkflow || !this.createWithDefaults) {
          this.stepper.next();
        }
      },
      error: (e: Error) => {
        this.errorMessage = e.message;
      }
    });
  }

  private setupAttributeValueFiltering(): void {
    this.attributeStep = this.formBuilder.group({
      attribute: [null],
    });

    this.attributeStep.get('attribute').valueChanges.pipe(
      filter(value => typeof value === 'string'), // trigger search only on typing
      debounceTime(400),
      switchMap(value => {
        this.attributesSubscription.unsubscribe();
        return this.getAttributesObservable(value);
      })
    ).subscribe(res => {
      this.attributePage = res;
      this.filteredAttributes = res.attributes;
      this.displayWarningMessage = false;
    });
  }

  private getAttributesObservable(value?: string, page?: number): Observable<AttributePage> {
    return this.editorService.getAttributes(
      this.data.pipelineId,
      this.annotatorStep.value.annotator,
      this.getPopulatedResourceValues(),
      value || undefined,
      page ?? 0
    ).pipe(take(1));
  }

  private getPipelineAttributesNames(): void {
    this.editorService.getPipelineAttributesNames(this.data.pipelineId).pipe(take(1)).subscribe(names => {
      this.existingAttributeNames = new Set([...names]);
      this.validateAttributes();


      if (this.data.isResourceWorkflow && this.createWithDefaults) {
        if (!this.areAttributesValid) {
          this.errorMessage = 'Error while configuring attributes in step 4';
          return;
        }
        this.onFinish();
      }
    });
  }

  public validateAttributes(): void {
    this.areAttributesValid = !this.selectedAttributes.some(
      a => !this.isAttributeValid(a)
    );
  }

  public isAttributeValid(attribute: AttributeData): boolean {
    const currentIndex = this.selectedAttributes.indexOf(attribute);
    return !this.existingAttributeNames.has(attribute.name) &&
      !this.selectedAttributes.some((a, index) => index !== currentIndex && a.name === attribute.name);
  }

  public onFinish(): void {
    const filtered = this.getPopulatedResourceValues();

    const attributes = this.annotatorAttributes.length > 0
      ? this.annotatorAttributes
      : this.selectedAttributes.map(attr => ({
        ...attr,
        aggregators: [],
        defaultAggregator: null,
        selectedAggregator: null,
        parameterValue: null,
      } as AnnotatorAttribute));


    this.editorService.getAnnotatorYml(
      this.data.pipelineId,
      this.annotatorStep.value.annotator,
      filtered,
      attributes,
    ).pipe(take(1)).subscribe({
      next: res => {
        this.dialogRef.close('\n' + res);
      },
      error: (e: Error) => {
        this.errorMessage = e.message;
      }
    });
  }

  private getPopulatedResourceValues(): object {
    return Object.fromEntries(
      Object.entries(this.configurationStep.value as object).filter(([, v]) => v !== null && v !== '')
    );
  }

  public clearAnnotator(): void {
    this.annotatorStep.get('annotator').setValue(null);
  }

  public clearAttributeInput(): void {
    const value = this.attributeStep.get('attribute').value;
    if (!value) {
      return;
    }
    this.attributeStep.get('attribute').setValue(null);

    if (typeof value !== 'string') {
      return;
    }

    this.attributesSubscription.unsubscribe();
    this.attributesSubscription = this.getAttributesObservable().subscribe(res => {
      this.attributePage = res;
      this.filteredAttributes = res.attributes;
    });
  }

  public clearResource(inputField?: string): void {
    if (inputField) {
      this.configurationStep.get(inputField).setValue(null);
      return;
    }
    this.resourceStep.get('resourceId').setValue('');
  }

  public onAttributeNameChange(attribute: AttributeData, newName: string): void {
    attribute.name = newName.trim();
    this.validateAttributes();
  }

  public toggleAttributeInternal(attribute: AttributeData): void {
    attribute.internal = !attribute.internal;
  }

  public onSelectAttribute(attribute: AttributeData): void {
    this.selectedAttributes.push(attribute);
    this.clearAttributeInput();
    this.validateAttributes();
  }

  public removeSelectedAttribute(attribute: AttributeData): void {
    this.selectedAttributes = this.selectedAttributes.filter(a => a !== attribute);
    this.validateAttributes();
  }

  private onAttributePanelScroll(panel: HTMLElement): void {
    const nearBottom = panel.scrollTop + panel.clientHeight >= panel.scrollHeight - 50;
    if (nearBottom && !this.isAttributeLoading && this.attributePage.page + 1 < this.attributePage.totalPages) {
      this.loadMoreAttributes();
    }
  }

  private loadMoreAttributes(): void {
    this.isAttributeLoading = true;
    const searchValue = this.attributeStep.get('attribute').value ?? null;
    this.getAttributesObservable(searchValue ?? undefined, this.attributePage.page + 1).subscribe({
      next: res => {
        this.filteredAttributes = [...this.filteredAttributes, ...res.attributes];
        this.attributePage = res;
        this.isAttributeLoading = false;
      },
      error: () => {
        this.isAttributeLoading = false;
      }
    });
  }

  private removeAttributePanelScrollHandler(): void {
    if (!this.attributePanelScrollHandler) {
      return;
    }
    this.attributePanel?.removeEventListener('scroll', this.attributePanelScrollHandler);
    this.attributePanelScrollHandler = null;
    this.attributePanel = null;
  }

  public clearErrorMessage(): void {
    this.errorMessage = '';
    this.displayWarningMessage = false;
  }

  public getResourceById(): Resource {
    const id = this.resourceStep.get('resourceId').value;
    return this.resources().find(r => r.fullId === id);
  }

  public removeAllSelectedAttributes(): void {
    this.selectedAttributes = [];
    this.displayWarningMessage = false;
  }

  public selectAllAttributes(): void {
    if (this.attributePage.totalAttributes > 1000) {
      this.displayWarningMessage = true;
      return;
    }
    this.displayWarningMessage = false;

    const nextPage = this.attributePage.page + 1;
    const totalPages = this.attributePage.totalPages;

    if (nextPage >= totalPages) {
      this.selectedAttributes = [...this.filteredAttributes];
      return;
    }

    const searchValue = this.attributeStep.get('attribute').value ?? null;
    const remainingPages = Array.from({ length: totalPages - nextPage }, (_, i) => nextPage + i);
    const snapshot = this.filteredAttributes;

    forkJoin(remainingPages.map(page => this.getAttributesObservable(searchValue ?? undefined, page)))
      .subscribe(pages => {
        const allAttributes = [...snapshot, ...pages.flatMap(p => p.attributes)];
        this.filteredAttributes = allAttributes;
        this.attributePage.attributes = allAttributes;
        this.attributePage.page = totalPages - 1;
        this.selectedAttributes = [...allAttributes];
      });
  }

  public requestAttributeAggregators(): void {
    this.editorService.getAttributesAggregators(
      this.annotatorStep.value.annotator,
      this.data.pipelineId,
      this.getPopulatedResourceValues(),
      this.selectedAttributes.map(a => a.source)
    ).pipe(take(1)).subscribe(res => {
      this.annotatorAttributes = this.selectedAttributes.map(selected => ({
        ...selected,
        aggregators: res.find(r => r.source === selected.source).aggregators ?? [],
        defaultAggregator: res.find(r => r.source === selected.source).defaultAggregator ?? null,
        selectedAggregator: res.find(r => r.source === selected.source).selectedAggregator ?? null,
        parameterValue: res.find(r => r.source === selected.source).parameterValue ?? null
      } as AnnotatorAttribute));
      this.stepper.next();
    });
  }

  public onSelectAggregator(name: string, aggregatorType: string): void {
    const row = this.annotatorAttributes.find(r => r.name === name);
    if (!row) {
      return;
    }
    row.selectedAggregator = aggregatorType;
    const config = this.aggregators.find(a => a.aggregatorType === aggregatorType);
    if (config?.parametrized) {
      row.parameterValue = config.defaultParameterValue ?? null;
    } else {
      row.parameterValue = null;
    }
  }
}
