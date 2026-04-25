import { CommonModule } from '@angular/common';
import { AfterViewInit, Component, ElementRef, Inject, OnDestroy, OnInit, signal, ViewChild } from '@angular/core';
import { FormBuilder, FormControl, FormGroup, FormsModule, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatAutocompleteModule } from '@angular/material/autocomplete';
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
  Resource
} from './annotator';
import { MAT_DIALOG_DATA, MatDialogRef } from '@angular/material/dialog';
import { MatSelect } from '@angular/material/select';
import { cloneDeep } from 'lodash';
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
  styleUrl: './new-annotator.component.css',
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
  public errorMessage = '';
  public editAttributeNameView = false;
  public searchError = '';
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

  public constructor(
    private editorService: PipelineEditorService,
    private formBuilder: FormBuilder,
    @Inject(MAT_DIALOG_DATA) public data: {pipelineId: string, isResourceWorkflow: boolean},
    private dialogRef: MatDialogRef<NewAnnotatorComponent>
  ) {
  }

  public ngOnInit(): void {
    this.annotatorStep = this.formBuilder.group({
      annotator: ['', Validators.required],
    });

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
    } else {
      this.requestAnnotators();
    }
  }

  public ngAfterViewInit(): void {
    if (!this.data.isResourceWorkflow) {
      return;
    }
    const container: Element = this.loadPageIndicator.nativeElement.closest('#resource-list');
    this.observer = new IntersectionObserver(entries => {
      if (entries[0].isIntersecting && this.hasMore && !this.isLoading && this.isResourceTableInitialized) {
        this.loadMore();
      }
    },
    {
      root: container,
      threshold: 0.1
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
        this.resourceSearchSubscription.unsubscribe();
        this.setupResourceSearching();
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
      this.annotatorTypes = res;
      this.filteredAnnotatorTypes = res.sort();
      this.setupAnnotatorValueFiltering();
    });
  }

  public requestResourceAnnotators(): void {
    this.editorService.getResourceAnnotators(this.resourceStep.value.resourceId.trim()).pipe(
      take(1),
    ).subscribe(res => {
      this.resourceAnnotators = res;
      this.annotatorTypes = res.annotators.map(r => r.annotatorType);
      this.filteredAnnotatorTypes = res.annotators.map(r => r.annotatorType);
      this.setupAnnotatorValueFiltering();
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
        this.selectedAttributes = cloneDeep(res.attributes.filter(a => a.selectedByDefault));
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
    });
  }

  private getAttributesObservable(value?: string): Observable<AttributePage> {
    return this.editorService.getAttributes(
      this.data.pipelineId,
      this.annotatorStep.value.annotator,
      this.getPopulatedResourceValues(),
      value || undefined
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
    return !this.existingAttributeNames.has(attribute.name) &&
      !this.selectedAttributes.filter(a => a !== attribute).some(a => a.name === attribute.name);
  }

  public onFinish(): void {
    const filtered = this.getPopulatedResourceValues();

    this.editorService.getAnnotatorYml(
      this.data.pipelineId,
      this.annotatorStep.value.annotator,
      filtered,
      this.selectedAttributes
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
    if (!this.attributeStep.get('attribute').value) {
      return;
    }
    this.attributeStep.get('attribute').setValue(null);

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
    this.selectedAttributes.push(this.attributePage.attributes.find(a => a === attribute));
    this.clearAttributeInput();
    this.validateAttributes();
  }

  public removeSelectedAttribute(attribute: AttributeData): void {
    this.selectedAttributes = this.selectedAttributes.filter(a => a !== attribute);
    this.validateAttributes();

    this.attributesSubscription = this.getAttributesObservable().subscribe(res => {
      this.attributePage = res;
      this.filteredAttributes = res.attributes;
    });
  }

  public clearErrorMessage(): void {
    this.errorMessage = '';
  }

  public getResourceById(): Resource {
    const id = this.resourceStep.get('resourceId').value;
    return this.resourcePage.resources.find(r => r.fullId === id);
  }
}
