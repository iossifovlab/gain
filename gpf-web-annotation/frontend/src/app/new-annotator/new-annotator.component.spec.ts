import { ComponentFixture, TestBed, fakeAsync, tick } from '@angular/core/testing';

import { NewAnnotatorComponent } from './new-annotator.component';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { MAT_DIALOG_DATA, MatDialogRef } from '@angular/material/dialog';
import { Observable, of } from 'rxjs';
import { PipelineEditorService } from '../pipeline-editor.service';
import {
  AnnotatorConfig,
  AttributeData,
  AttributePage,
  AnnotatorConfigResource,
  ResourceAnnotator,
  ResourceAnnotatorConfigs,
  Resource,
  ResourcePage
} from './annotator';
import { FormBuilder, FormControl } from '@angular/forms';


const annotatorConfigMock = new AnnotatorConfig(
  'effect_annotator',
  'annotatorUrl',
  [
    new AnnotatorConfigResource(
      'gene_models',
      'resource',
      'gene_models',
      'hg19/gene_models/ccds_v201309',
      [
        'hg19/gene_models/refGene_v20190211',
        'hg38/gene_models/GENCODE/34/basic/ALL',
        'hg38/gene_models/GENCODE/34/basic/CHR'
      ],
      false,
      ''
    ),
    new AnnotatorConfigResource(
      'genome',
      'resource',
      'genome',
      '',
      ['hg38/genomes/GRCh38.p14', 't2t/genomes/t2t-chm13v2.0'],
      true,
      ''
    ),
    new AnnotatorConfigResource(
      'input_annotatable',
      'attribute',
      '',
      '',
      ['normalized_allele', 'hg19_annotatable'],
      true,
      'annotatable'
    )
  ]
);

const attributesMock = [
  new AttributeData('mpc', 'string', 'mpc', false, true, 'Missense badness, PolyPhen-2, and Constraint.'),
  new AttributeData(
    'effect_details',
    'bool',
    'effect_details',
    false,
    false,
    'Effect details for each affected transcript'
  ),
  new AttributeData('dbSNP_RS', 'string', 'dbSNP_RS', true, true, 'dbSNP ID (i.e. rs number)')
];

const attributePageMock = new AttributePage(attributesMock, 0, 1, 3);

const ymlResponse = `- gene_set_annotator:\n
  resource_id: gene_properties/gene_sets/autism\n
  input_gene_list: gene_list\n
  attributes:\n
  - "autism candidates from Iossifov PNAS 2015"\n
  - "autism candidates from Sanders Neuron 2015"\n 
  - "Yuen Scherer Nature 2017"\n
`;
class PipelineEditorServiceMock {
  public getAnnotators(): Observable<string[]> {
    return of(['effect_annotator', 'liftover_annotator', 'gene_set_annotator']);
  }

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public getAnnotatorConfig(annotator: string): Observable<AnnotatorConfig> {
    return of(annotatorConfigMock);
  }

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public getAttributes(pipelineId: string, annotator: string, resources: object): Observable<AttributePage> {
    return of(attributePageMock);
  }

  // eslint-disable-next-line @typescript-eslint/no-unused-vars, @stylistic/max-len
  public getAnnotatorYml(pipelineId: string, annotator: string, resources: object, attributes: AttributeData[]): Observable<string> {
    return of(ymlResponse);
  }

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public getPipelineAttributes(pipelineId: string, attributeType: string,): Observable<string[]> {
    return of(['normalized_allele', 'hg19_annotatable']);
  }

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public getResourceTypes(): Observable<string[]> {
    return of(['gene_set_collection', 'position_score', 'genome']);
  }

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public getResourcesBySearch(value: string, type: string, page?: number): Observable<ResourcePage> {
    return of(
      new ResourcePage(
        0,
        3,
        [
          new Resource(
            'hg19/scores/phyloP46_primates',
            'hg19/scores/phyloP46_primates',
            'position_score',
            0,
            'url/index.html',
            'phyloP46_primates summary'
          ),
          new Resource(
            'hg19/scores/phyloP46_vertebrates',
            'hg19/scores/phyloP46_vertebrates',
            'position_score',
            0,
            'url/index.html',
            'phyloP46_vertebrates summary'
          ),
          new Resource(
            'hg38/scores/phastCons100way',
            'hg38/scores/phastCons100way',
            'position_score',
            0,
            'url/index.html',
            'phastCons100way summary'
          ),
          new Resource(
            'hg38/scores/phastCons20way',
            'hg38/scores/phastCons20way',
            'position_score',
            0,
            'url/index.html',
            'phastCons20way summary'
          )
        ].filter(r => r.fullId.includes(value)),
        100
      )
    );
  }

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public getResourceAnnotators(resourceId: string): Observable<ResourceAnnotatorConfigs> {
    return of(
      new ResourceAnnotatorConfigs(
        null,
        [
          new ResourceAnnotator('effect_annotator', 'gene_models: hg19/gene_models/ccds_v201309'),
          new ResourceAnnotator('simple_effect_annotator', 'gene_models: hg19/gene_models/ccds_v201309')
        ]
      )
    );
  }

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public getPipelineAttributesNames(pipelineId: string): Observable<string[]> {
    return of([
      'normalized_allele',
      'dbSNP_RS',
      'CLNSIG',
      'CLNDN',
      'mpc',
      'worst_effect',
      '3\'UTR_gene_list'
    ]);
  }
}
class MatDialogRefMock {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public close(value: boolean): void { }
}

describe('NewAnnotatorComponent', () => {
  let component: NewAnnotatorComponent;
  let fixture: ComponentFixture<NewAnnotatorComponent>;
  const mockMatDialogRef = new MatDialogRefMock();
  const pipelineEditorServiceMock = new PipelineEditorServiceMock();

  beforeEach(async() => {
    await TestBed.configureTestingModule({
      imports: [NewAnnotatorComponent],
      providers: [
        { provide: MAT_DIALOG_DATA, useValue: { pipelineId: 'pipelineId', isResourceWorkflow: false }},
        { provide: MatDialogRef, useValue: mockMatDialogRef },
        { provide: PipelineEditorService, useValue: pipelineEditorServiceMock },
        provideHttpClient(),
        provideHttpClientTesting(),
        FormBuilder
      ]
    }).compileComponents();

    fixture = TestBed.createComponent(NewAnnotatorComponent);
    component = fixture.componentInstance;

    const mockContainer = document.createElement('div');

    Object.defineProperty(component, 'loadPageIndicator', {
      get: () => ({ nativeElement: {
        closest: jest.fn().mockReturnValue(mockContainer)
      } }),
      set: () => {},
      configurable: true
    });

    window.IntersectionObserver = jest.fn() as unknown as typeof IntersectionObserver;

    jest.spyOn(window, 'IntersectionObserver').mockImplementation(() => {
      return {
        observe: jest.fn(),
        disconnect: jest.fn(),
        unobserve: jest.fn(),
        root: null,
        rootMargin: '',
        thresholds: [],
        scrollMargin: '',
        takeRecords: jest.fn()
      } as IntersectionObserver;
    });

    fixture.detectChanges(); //triggers ngAfterViewInit
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should normalize strings', () => {
    expect(component.normalizeString('aaaa     ')).toBe('aaaa');
    expect(component.normalizeString('   aaaa     ')).toBe('aaaa');
    expect(component.normalizeString(null)).toBe('');
  });

  it('should get annotator types on component initialization', () => {
    component.ngOnInit();
    expect(component.annotatorTypes).toStrictEqual(
      ['effect_annotator', 'gene_set_annotator', 'liftover_annotator']
    );
    expect(component.filteredAnnotatorTypes).toStrictEqual(
      ['effect_annotator', 'gene_set_annotator', 'liftover_annotator']
    );
    expect(component.annotatorStep).toBeDefined();
  });

  it('should get resources of an annotator', () => {
    component.requestResources();
    expect(component.annotatorConfig).toStrictEqual(annotatorConfigMock);
    expect(component.filteredResourceValues).toStrictEqual(
      new Map<string, string[]>([
        [
          'gene_models',
          [
            'hg19/gene_models/refGene_v20190211',
            'hg38/gene_models/GENCODE/34/basic/ALL',
            'hg38/gene_models/GENCODE/34/basic/CHR'
          ]
        ],
        [
          'genome',
          ['hg38/genomes/GRCh38.p14', 't2t/genomes/t2t-chm13v2.0']
        ],
        [
          'input_annotatable',
          ['normalized_allele', 'hg19_annotatable']
        ]
      ]));
    expect(component.configurationStep).toBeDefined();
  });

  it('should get resources of an annotator and set default values', () => {
    component.resourceAnnotators = new ResourceAnnotatorConfigs(
      null,
      [
        new ResourceAnnotator('effect_annotator', 'gene_models: hg19/gene_models/ccds_v201309'),
        new ResourceAnnotator('simple_effect_annotator', 'gene_models: hg19/gene_models/ccds_v201309')
      ]
    );
    component.annotatorStep.setControl('annotator', new FormControl('effect_annotator'));
    component.requestResources();
    expect(component.annotatorConfig).toStrictEqual(annotatorConfigMock);
    expect(component.filteredResourceValues).toStrictEqual(
      new Map<string, string[]>([
        [
          'gene_models',
          [
            'hg19/gene_models/refGene_v20190211',
            'hg38/gene_models/GENCODE/34/basic/ALL',
            'hg38/gene_models/GENCODE/34/basic/CHR'
          ]
        ],
        [
          'genome',
          ['hg38/genomes/GRCh38.p14', 't2t/genomes/t2t-chm13v2.0']
        ],
        [
          'input_annotatable',
          ['normalized_allele', 'hg19_annotatable']
        ]
      ]));
    expect(component.configurationStep).toBeDefined();
    expect(component.configurationStep.get('gene_models').value).toBe('hg19/gene_models/ccds_v201309');
    expect(component.configurationStep.get('genome').value).toBe('');
    expect(component.configurationStep.get('input_annotatable').value).toBe('');
  });

  it('should set default value of input gene list', () => {
    jest.spyOn(pipelineEditorServiceMock, 'getAnnotatorConfig').mockReturnValueOnce(of(
      new AnnotatorConfig(
        'gene_score_annotator',
        'annotatorUrl',
        [
          new AnnotatorConfigResource(
            'resource_id',
            'resource',
            'gene_score',
            'hg19/gene_models/ccds_v201309',
            [
              'gene_properties/gene_scores/LGD',
              'gene_properties/gene_scores/Satterstrom_Buxbaum_Cell_2020',
              'hg38/enrichment/ur_synonymous_SFARI_SSC_WES_CSHL_liftover',
            ],
            false,
            ''
          ),
          new AnnotatorConfigResource(
            'input_gene_list',
            'attribute',
            'gene_list',
            '',
            ['gene_list'],
            false,
            ''
          ),
          new AnnotatorConfigResource(
            'input_annotatable',
            'attribute',
            '',
            '',
            ['normalized_allele', 'hg19_annotatable'],
            true,
            'annotatable'
          )
        ]
      )
    ));
    jest.spyOn(pipelineEditorServiceMock, 'getPipelineAttributes').mockReturnValueOnce(of(['gene_list']));
    component.requestResources();
    expect(component.configurationStep.get('input_gene_list').value).toBe('gene_list');
  });

  it('should get attributes', () => {
    component.configurationStep.setControl('resource_id', new FormControl('gene_properties/gene_scores/RVIS'));
    component.configurationStep.setControl('input_annotatable', new FormControl(null));
    component.annotatorStep.setControl('annotator', new FormControl('gene_set_annotator'));
    const getAttributesSpy = jest.spyOn(pipelineEditorServiceMock, 'getAttributes');
    const nextStepSpy = jest.spyOn(component.stepper, 'next');

    component.requestAttributes();
    expect(getAttributesSpy).toHaveBeenCalledWith(
      'pipelineId',
      'gene_set_annotator',
      // eslint-disable-next-line camelcase
      { resource_id: 'gene_properties/gene_scores/RVIS' },
      undefined
    );
    expect(component.attributePage).toStrictEqual(attributePageMock);
    expect(component.selectedAttributes).toStrictEqual([attributesMock[0], attributesMock[2]]);
    expect(component.filteredAttributes).toStrictEqual(attributesMock);
    expect(nextStepSpy).toHaveBeenCalledWith();
  });

  it('should get final yaml text', () => {
    component.configurationStep.setControl('resource_id', new FormControl('gene_properties/gene_scores/RVIS'));
    component.configurationStep.setControl('input_annotatable', new FormControl(null));
    component.annotatorStep.setControl('annotator', new FormControl('gene_set_annotator'));
    const getAnnotatorYmlSpy = jest.spyOn(pipelineEditorServiceMock, 'getAnnotatorYml');
    const closeModalSpy = jest.spyOn(mockMatDialogRef, 'close');
    component.selectedAttributes = [attributesMock[0]];

    component.onFinish();
    expect(getAnnotatorYmlSpy).toHaveBeenCalledWith(
      'pipelineId',
      'gene_set_annotator',
      // eslint-disable-next-line camelcase
      { resource_id: 'gene_properties/gene_scores/RVIS' },
      [new AttributeData('mpc', 'string', 'mpc', false, true, 'Missense badness, PolyPhen-2, and Constraint.')]
    );

    expect(closeModalSpy).toHaveBeenCalledWith('\n' + ymlResponse);
  });

  it('should clear annotator value in form', () => {
    component.annotatorStep.setControl('annotator', new FormControl('gene_set_annotator'));
    component.clearAnnotator();
    expect(component.annotatorStep.get('annotator').value).toBeNull();
  });

  it('should clear resource value in form', () => {
    component.configurationStep.setControl('resource_id', new FormControl('gene_properties/gene_scores/RVIS'));
    component.clearResource('resource_id');
    expect(component.configurationStep.get('resource_id').value).toBeNull();
  });

  it('should toggle internal value of attribute', () => {
    const attribute = new AttributeData('attribute', 'string', 'source1', false, true, 'desc');
    component.selectedAttributes = [attribute];
    component.toggleAttributeInternal(attribute);
    expect(component.selectedAttributes[0].internal).toBe(true);
  });

  it('should change annotator input value and filter dropdown content', () => {
    component.annotatorTypes = ['effect_annotator', 'liftover_annotator', 'gene_set_annotator'];
    component.filteredAnnotatorTypes = ['effect_annotator', 'liftover_annotator', 'gene_set_annotator'];
    component.annotatorStep.get('annotator').setValue('LiFt  ');
    expect(component.filteredAnnotatorTypes).toStrictEqual(['liftover_annotator']);
  });

  it('should change annotator input value to invalid one and set error', () => {
    component.annotatorTypes = ['effect_annotator', 'liftover_annotator', 'gene_set_annotator'];
    component.filteredAnnotatorTypes = ['effect_annotator', 'liftover_annotator', 'gene_set_annotator'];
    component.annotatorStep.get('annotator').setValue('ewew');
    expect(component.filteredAnnotatorTypes).toStrictEqual([]);
    expect(component.annotatorStep.get('annotator').errors).toStrictEqual({invalidOption: true});
  });

  it('should change resource input value and filter dropdown content', () => {
    component.requestResources(); // trigger setup of resource form controls and filtering
    component.configurationStep.get('gene_models').setValue('code');

    expect(component.filteredResourceValues.get('gene_models')).toStrictEqual(
      ['hg38/gene_models/GENCODE/34/basic/ALL', 'hg38/gene_models/GENCODE/34/basic/CHR']
    );
    expect(component.configurationStep.get('gene_models').errors).toBeNull();
  });

  it('should change resource input value to invalid one and set error', () => {
    component.requestResources(); // trigger setup of resource form controls and filtering
    component.configurationStep.get('gene_models').setValue('ewew');

    expect(component.filteredResourceValues.get('gene_models')).toStrictEqual([]);
    expect(component.configurationStep.get('gene_models').errors).toStrictEqual({invalidOption: true});
  });

  it('should store attribute names which already exists in config', () => {
    component.requestAttributes();
    expect(component.existingAttributeNames).toStrictEqual(new Set([
      'normalized_allele',
      'dbSNP_RS',
      'CLNSIG',
      'CLNDN',
      'mpc',
      'worst_effect',
      '3\'UTR_gene_list'
    ]));
  });

  it('should disable finish button if there is selected attribute with duplicate name', () => {
    component.requestAttributes();
    component.validateAttributes();
    expect(component.areAttributesValid).toBe(false);
  });

  it('should not disable finish button if there is unselected attribute with exisitng name', () => {
    component.requestAttributes();
    component.selectedAttributes = [
      new AttributeData(
        'effect_details',
        'bool',
        'effect_details',
        false,
        true,
        'Effect details for each affected transcript'
      )
    ];
    component.validateAttributes();
    expect(component.existingAttributeNames).toStrictEqual(new Set([
      'normalized_allele',
      'dbSNP_RS',
      'CLNSIG',
      'CLNDN',
      'mpc',
      'worst_effect',
      '3\'UTR_gene_list'
    ]));
    expect(component.areAttributesValid).toBe(true);
  });

  it('should select attribute and remove it from dropdown content', () => {
    component.requestAttributes();
    component.selectedAttributes = [attributesMock[1], attributesMock[2]];
    component.onSelectAttribute(attributesMock[0]);
    expect(component.selectedAttributes).toStrictEqual([attributesMock[1], attributesMock[2], attributesMock[0]]);
    expect(component.filteredAttributes).toStrictEqual(attributesMock);
  });

  it('should be able to select already selected attribute', () => {
    component.requestAttributes();
    component.selectedAttributes = [attributesMock[1], attributesMock[2]];
    component.areAttributesValid = true;
    component.onSelectAttribute(attributesMock[1]);
    expect(component.selectedAttributes).toStrictEqual([attributesMock[1], attributesMock[2], attributesMock[1]]);
    expect(component.areAttributesValid).toBe(false);
  });

  it('should select attribute, clean input and validate attributes', () => {
    component.requestAttributes();
    component.selectedAttributes = [attributesMock[1], attributesMock[2]];
    component.attributeStep.setControl(
      'attribute',
      new FormControl('mpc - Missense badness, PolyPhen-2, and Constraint.')
    );
    component.onSelectAttribute(attributesMock[0]);
    expect(component.attributeStep.get('attribute').value).toBeNull();
    expect(component.areAttributesValid).toBe(false);
  });

  it('should remove selected attribute, validate attributes and request attributes', () => {
    component.requestAttributes();
    component.selectedAttributes = attributesMock;

    component.removeSelectedAttribute(attributesMock[1]);
    expect(component.selectedAttributes).toStrictEqual([attributesMock[0], attributesMock[2]]);
    expect(component.filteredAttributes).toStrictEqual(attributesMock);
  });

  it('should trigger search request for attributes', fakeAsync(() => {
    component.configurationStep.setControl('gene_models', new FormControl('hg38/gene_models/GENCODE/48'));
    component.annotatorStep.setControl('annotator', new FormControl('effect_annotator'));
    const getAttributesSpy = jest.spyOn(pipelineEditorServiceMock, 'getAttributes');

    component.requestAttributes();

    component.selectedAttributes = [
      new AttributeData(
        '3\'UTR_gene_list', 'object',
        '3\'UTR_gene_list',
        false,
        false,
        'List of all 3\'UTR genes'
      ),
      new AttributeData(
        'worst_effect',
        'string',
        'worst_effect',
        false,
        false,
        'Worst effect accross all transcripts.'
      ),
    ];

    getAttributesSpy.mockReturnValueOnce(of(new AttributePage([
      new AttributeData('3\'UTR_gene_list', 'object', '3\'UTR_gene_list', false, true, 'List of all 3\'UTR genes'),
      new AttributeData('5\'UTR_gene_list', 'object', '5\'UTR_gene_list', false, true, 'List of all 5\'UTR genes'),
    ], 0, 10, 1500)));

    component.attributeStep.get('attribute').setValue('UTR');

    tick(400); // Wait for debounceTime

    expect(getAttributesSpy).toHaveBeenCalledWith(
      'pipelineId',
      'effect_annotator',
      // eslint-disable-next-line camelcase
      { gene_models: 'hg38/gene_models/GENCODE/48' },
      'UTR'
    );

    expect(component.filteredAttributes).toStrictEqual([
      new AttributeData('3\'UTR_gene_list', 'object', '3\'UTR_gene_list', false, true, 'List of all 3\'UTR genes'),
      new AttributeData('5\'UTR_gene_list', 'object', '5\'UTR_gene_list', false, true, 'List of all 5\'UTR genes'),
    ]);
  })
  );

  it('should clear attribute input and reset attributes', () => {
    component.configurationStep.setControl('gene_models', new FormControl('hg38/gene_models/GENCODE/48'));
    component.annotatorStep.setControl('annotator', new FormControl('effect_annotator'));
    const getAttributesSpy = jest.spyOn(pipelineEditorServiceMock, 'getAttributes');
    component.requestAttributes();

    component.attributePage = new AttributePage(
      [
        new AttributeData('attr1', 'string', 'source1', true, true, 'desc1'),
        new AttributeData('attr1', 'string', 'source1', true, true, 'desc1'),
      ],
      0,
      1,
      2
    );

    component.attributeStep.get('attribute').setValue('mpc');

    component.selectedAttributes = [attributesMock[0]];
    component.clearAttributeInput();

    expect(component.attributeStep.get('attribute').value).toBeNull();
    expect(getAttributesSpy).toHaveBeenCalledWith(
      'pipelineId',
      'effect_annotator',
      // eslint-disable-next-line camelcase
      { gene_models: 'hg38/gene_models/GENCODE/48' },
      undefined
    );
    expect(component.attributePage).toStrictEqual(attributePageMock);
    expect(component.filteredAttributes).toStrictEqual(attributesMock);
  });

  it('should change name of a selected attribute', () => {
    const attribute = new AttributeData('name', 'str', 'source', true, false, 'desc');
    component.selectedAttributes = [attribute];
    component.onAttributeNameChange(attribute, ' newName      ');
    expect(attribute.name).toBe('newName');
    expect(component.isAttributeValid(attribute)).toBe(true);
    expect(component.areAttributesValid).toBe(true);
  });

  it('should change name of a selected attribute to exisitng in config name', () => {
    const attribute = new AttributeData('name', 'str', 'source', true, false, 'desc');
    component.selectedAttributes = [attribute];
    component.existingAttributeNames = new Set(['hg19_annotatable']);
    component.onAttributeNameChange(attribute, ' hg19_annotatable      ');
    expect(attribute.name).toBe('hg19_annotatable');
    expect(component.isAttributeValid(attribute)).toBe(false);
    expect(component.areAttributesValid).toBe(false);
  });

  it('should change name of a selected attribute to exisitng in selected attributes name', () => {
    const attribute = new AttributeData('name', 'str', 'source', true, false, 'desc');
    component.selectedAttributes = [
      attribute,
      new AttributeData('hg19_annotatable', 'str', 'liftover_annotatable', true, false, 'desc')
    ];
    component.onAttributeNameChange(attribute, ' hg19_annotatable      ');
    expect(attribute.name).toBe('hg19_annotatable');
    expect(component.isAttributeValid(attribute)).toBe(false);
    expect(component.areAttributesValid).toBe(false);
  });
});

describe('Annotator created by resource', () => {
  let component: NewAnnotatorComponent;
  let fixture: ComponentFixture<NewAnnotatorComponent>;
  const mockMatDialogRef = new MatDialogRefMock();
  const pipelineEditorServiceMock = new PipelineEditorServiceMock();
  let mockObserverCallback: IntersectionObserverCallback;

  beforeEach(async() => {
    await TestBed.configureTestingModule({
      imports: [NewAnnotatorComponent],
      providers: [
        { provide: MAT_DIALOG_DATA, useValue: { pipelineId: 'pipelineId', isResourceWorkflow: true }},
        { provide: MatDialogRef, useValue: mockMatDialogRef },
        { provide: PipelineEditorService, useValue: pipelineEditorServiceMock },
        provideHttpClient(),
        provideHttpClientTesting(),
        FormBuilder
      ]
    }).compileComponents();

    fixture = TestBed.createComponent(NewAnnotatorComponent);
    component = fixture.componentInstance;

    const mockContainer = document.createElement('div');
    Object.defineProperty(component, 'loadPageIndicator', {
      get: () => ({ nativeElement: {
        closest: jest.fn().mockReturnValue(mockContainer)
      } }),
      set: () => {},
      configurable: true
    });

    window.IntersectionObserver = jest.fn() as unknown as typeof IntersectionObserver;

    jest.spyOn(window, 'IntersectionObserver').mockImplementation((cb: IntersectionObserverCallback) => {
      mockObserverCallback = cb;
      return {
        observe: jest.fn(),
        disconnect: jest.fn(),
        unobserve: jest.fn(),
        root: null,
        rootMargin: '',
        thresholds: [],
        scrollMargin: '',
        takeRecords: jest.fn()
      } as IntersectionObserver;
    });

    component.resourcePage = new ResourcePage(
      0,
      3,
      [
        new Resource(
          'hg19/scores/phyloP46_primates',
          'hg19/scores/phyloP46_primates',
          'position_score',
          0,
          'url',
          'phyloP46_primates summary'
        ),
        new Resource(
          'hg19/scores/phyloP46_vertebrates',
          'hg19/scores/phyloP46_vertebrates',
          'position_score',
          0,
          'url',
          'phyloP46_vertebrates summary'
        ),
        new Resource(
          'hg38/scores/phastCons100way',
          'hg38/scores/phastCons100way',
          'position_score',
          0,
          'url',
          'phastCons100way summary'
        ),
        new Resource(
          'hg38/scores/phastCons20way',
          'hg38/scores/phastCons20way',
          'position_score',
          0,
          'url',
          'phastCons20way summary'
        )
      ],
      100
    );
    fixture.detectChanges(); //triggers ngAfterViewInit
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should get resource types on component initialization and set default', () => {
    expect(component.resourceTypes).toStrictEqual(['All', 'gene_set_collection', 'genome', 'position_score']);
    expect(component.selectedResourceType).toBe('All');
  });

  it('should trigger search on resource type change', () => {
    const searchSpy = jest.spyOn(pipelineEditorServiceMock, 'getResourcesBySearch');
    component.resourceStep.get('resourceId').setValue('hg', { emitEvent: true });
    component.resourceStep.get('resourceType').setValue('genome', { emitEvent: true });

    expect(searchSpy).toHaveBeenCalledWith('hg', 'genome');
    expect(component.resourcePage.resources).toStrictEqual([
      new Resource(
        'hg19/scores/phyloP46_primates',
        'hg19/scores/phyloP46_primates',
        'position_score',
        0,
        'url/index.html',
        'phyloP46_primates summary'
      ),
      new Resource(
        'hg19/scores/phyloP46_vertebrates',
        'hg19/scores/phyloP46_vertebrates',
        'position_score',
        0,
        'url/index.html',
        'phyloP46_vertebrates summary'
      ),
      new Resource(
        'hg38/scores/phastCons100way',
        'hg38/scores/phastCons100way',
        'position_score',
        0,
        'url/index.html',
        'phastCons100way summary'
      ),
      new Resource(
        'hg38/scores/phastCons20way',
        'hg38/scores/phastCons20way',
        'position_score',
        0,
        'url/index.html',
        'phastCons20way summary'
      )
    ]);
  });

  it('should trigger search on resource input change', fakeAsync(() => {
    const searchSpy = jest.spyOn(pipelineEditorServiceMock, 'getResourcesBySearch');
    component.resourceStep.get('resourceId').setValue('hg38  ', { emitEvent: true });
    component.selectedResourceType = 'gene_set_collection';
    tick(400);
    expect(searchSpy).toHaveBeenCalledWith('hg38', 'gene_set_collection');
    expect(component.resourcePage.resources).toStrictEqual([
      new Resource('hg38/scores/phastCons100way',
        'hg38/scores/phastCons100way',
        'position_score',
        0,
        'url/index.html',
        'phastCons100way summary'
      ),
      new Resource('hg38/scores/phastCons20way',
        'hg38/scores/phastCons20way',
        'position_score',
        0,
        'url/index.html',
        'phastCons20way summary'
      ),
    ]);
  }));

  it('should selet resource and get annotators', fakeAsync(() => {
    jest.clearAllMocks(); // clear search method calls from previous tests
    const getAnnotatorsSpy = jest.spyOn(pipelineEditorServiceMock, 'getResourceAnnotators');
    component.selectResource('hg38/scores/phastCons20way');
    expect(component.resourceStep.get('resourceId').value).toBe('hg38/scores/phastCons20way');
    expect(getAnnotatorsSpy).toHaveBeenCalledWith('hg38/scores/phastCons20way');
  }));

  it('should not trigger resource search when the search value has not changed', fakeAsync(() => {
    jest.clearAllMocks(); // clear search method calls from previous tests
    const searchSpy = jest.spyOn(pipelineEditorServiceMock, 'getResourcesBySearch');
    component.resourceStep.get('resourceId').setValue('hg38  ', { emitEvent: true });
    tick(400);
    expect(searchSpy).toHaveBeenCalledTimes(1);
    component.resourceStep.get('resourceId').setValue('hg38', { emitEvent: true });
    tick(400);
    expect(searchSpy).toHaveBeenCalledTimes(1);
  }));

  it('should set default annotator', () => {
    jest.clearAllMocks(); // clear search method calls from previous tests
    component.resourceStep.get('resourceId').setValue('gene_models');
    jest.spyOn(pipelineEditorServiceMock, 'getResourceAnnotators').mockReturnValueOnce(
      of(
        new ResourceAnnotatorConfigs(
          'simple_effect_annotator',
          [
            new ResourceAnnotator('effect_annotator', 'gene_models: hg19/gene_models/ccds_v201309'),
            new ResourceAnnotator('simple_effect_annotator', 'gene_models: hg19/gene_models/ccds_v201309')
          ]
        )
      )
    );
    component.requestResourceAnnotators();
    expect(component.annotatorStep.get('annotator').value).toBe('simple_effect_annotator');
  });

  it('should set the only annotator as default', () => {
    jest.clearAllMocks(); // clear search method calls from previous tests
    component.resourceStep.get('resourceId').setValue('gene_models');
    jest.spyOn(pipelineEditorServiceMock, 'getResourceAnnotators').mockReturnValueOnce(
      of(
        new ResourceAnnotatorConfigs(
          null,
          [
            new ResourceAnnotator('effect_annotator', 'gene_models: hg19/gene_models/ccds_v201309'),
          ]
        )
      )
    );
    component.requestResourceAnnotators();
    expect(component.annotatorStep.get('annotator').value).toBe('effect_annotator');
  });

  it('should not load resources when not intersecting', () => {
    jest.spyOn(component, 'loadMore');
    component.ngAfterViewInit();
    component.isResourceTableInitialized = true;

    mockObserverCallback([{ isIntersecting: false }] as IntersectionObserverEntry[], null);
    expect(component.loadMore).not.toHaveBeenCalled();
  });

  it('should not load resources when already loading', () => {
    jest.spyOn(component, 'loadMore');
    component.ngAfterViewInit();
    component.isResourceTableInitialized = true;
    component.isLoading = true;

    mockObserverCallback([{ isIntersecting: true }] as IntersectionObserverEntry[], null);
    expect(component.loadMore).not.toHaveBeenCalled();
  });

  it('should not load resources when no more pages', () => {
    jest.spyOn(component, 'loadMore');
    component.ngAfterViewInit();
    component.isResourceTableInitialized = true;
    component.hasMore = false;

    mockObserverCallback([{ isIntersecting: true }] as IntersectionObserverEntry[], null);
    expect(component.loadMore).not.toHaveBeenCalled();
  });

  it('should load more resources when loading indicator is visible', () => {
    component.nextPage = 1;
    component.totalPages = 300;
    component.isResourceTableInitialized = true;
    jest.spyOn(component, 'loadMore');
    component.resources.set([
      new Resource(
        'mockFullId',
        'mockId',
        'str',
        0,
        '',
        ''
      )
    ]);
    const getResourcesBySearchSpy = jest.spyOn(pipelineEditorServiceMock, 'getResourcesBySearch');

    // manually trigger the observer callback
    mockObserverCallback([{ isIntersecting: true }] as IntersectionObserverEntry[], null);

    expect(component.loadMore).toHaveBeenCalledWith();
    expect(getResourcesBySearchSpy).toHaveBeenCalledWith('', '', 1);
    expect(component.nextPage).toBe(2);
    expect(component.hasMore).toBe(true);
    expect(component.resources()).toStrictEqual([
      new Resource(
        'mockFullId',
        'mockId',
        'str',
        0,
        '',
        ''
      ),
      new Resource(
        'hg19/scores/phyloP46_primates',
        'hg19/scores/phyloP46_primates',
        'position_score',
        0,
        'url/index.html',
        'phyloP46_primates summary'
      ),
      new Resource(
        'hg19/scores/phyloP46_vertebrates',
        'hg19/scores/phyloP46_vertebrates',
        'position_score',
        0,
        'url/index.html',
        'phyloP46_vertebrates summary'
      ),
      new Resource(
        'hg38/scores/phastCons100way',
        'hg38/scores/phastCons100way',
        'position_score',
        0,
        'url/index.html',
        'phastCons100way summary'
      ),
      new Resource(
        'hg38/scores/phastCons20way',
        'hg38/scores/phastCons20way',
        'position_score',
        0,
        'url/index.html',
        'phastCons20way summary'
      )
    ]);
  });
});