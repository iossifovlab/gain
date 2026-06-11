import { TestBed } from '@angular/core/testing';

import { PipelineEditorService } from './pipeline-editor.service';
import { HttpClient, HttpErrorResponse, HttpParams, provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { of, lastValueFrom, take, throwError } from 'rxjs';
import {
  AnnotatorConfig,
  AnnotatorAttribute,
  AttributeData,
  AttributePage,
  AnnotatorConfigResource,
  ResourceAnnotator,
  ResourceAnnotatorConfigs,
  Resource,
  ResourcePage,
  AttributeAggregatorsResponse
} from './new-annotator/annotator';

const mockResources = [
  /* eslint-disable camelcase */
  {
    full_id: 'hg19/scores/MPC',
    resource_id: 'hg19/scores/MPC',
    type: 'allele_score',
    version: 0,
    url: 'url',
    summary: 'mpc summary'
  },
  {
    full_id: 'hg19/scores/CADD',
    resource_id: 'hg19/scores/CADD',
    type: 'allele_score',
    version: 0,
    url: 'url',
    summary: 'CADD summary'
  },
  {
    full_id: 'hg38/scores/CADD_v1.7',
    resource_id: 'hg38/scores/CADD_v1.7',
    type: 'allele_score',
    version: 0,
    url: 'url',
    summary: 'CADD_v1.7 summary'
  },
  {
    full_id: 'hg38/genomes/GRCh38.p13',
    resource_id: 'hg38/genomes/GRCh38.p13',
    type: 'genome',
    version: 0,
    url: 'url',
    summary: 'GRCh38.p13 summary'
  },
  {
    full_id: 'hg38/genomes/GRCh38.p14',
    resource_id: 'hg38/genomes/GRCh38.p14',
    type: 'genome',
    version: 0,
    url: 'url',
    summary: 'GRCh38.p14 summary'
  },
  {
    full_id: 't2t/genomes/t2t-chm13v2.0',
    resource_id: 't2t/genomes/t2t-chm13v2.0',
    type: 'genome',
    version: 0,
    url: 'url',
    summary: 't2t-chm13v2.0 summary'
  }
  /* eslint-enable */
];

describe('PipelineEditorService', () => {
  let service: PipelineEditorService;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
      ]
    });
    service = TestBed.inject(PipelineEditorService);
    jest.clearAllMocks();
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  it('should get list of annotators', async() => {
    const httpGetSpy = jest.spyOn(HttpClient.prototype, 'get');
    httpGetSpy.mockReturnValue(of([
      'gene_set_annotator',
      'liftover_annotator',
      'position_score',
    ]));

    const getResponse = service.getAnnotators();

    expect(httpGetSpy).toHaveBeenCalledWith(
      '//localhost:8000/api/editor/annotator_types'
    );
    const res = await lastValueFrom(getResponse.pipe(take(1)));
    expect(res).toStrictEqual([
      'gene_set_annotator',
      'liftover_annotator',
      'position_score',
    ]);
  });

  it('should get annotator config', async() => {
    const httpPostSpy = jest.spyOn(HttpClient.prototype, 'post');
    httpPostSpy.mockReturnValue(of(
      /* eslint-disable camelcase */
      {
        annotator_type: 'position_score',
        documentation_url: 'annotatorUrl',
        resource_id: {
          field_type: 'resource',
          resource_type: 'position_score',
          optional: false
        },
        input_annotatable: {
          field_type: 'attribute',
          optional: true,
          attribute_type: 'annotatable'
        }
      }
      /* eslint-enable */
    ));

    jest.spyOn(service, 'getResources').mockReturnValue(of([
      'hg19/scores/FitCons-i6-merged',
      'hg19/scores/FitCons2_E035',
      'hg19/scores/FitCons2_E067',
    ]));

    const options = { headers: {'X-CSRFToken': ''}, withCredentials: true };
    const getResponse = service.getAnnotatorConfig('position_score');

    expect(httpPostSpy).toHaveBeenCalledWith(
      '//localhost:8000/api/editor/annotator_config',
      // eslint-disable-next-line camelcase
      {annotator_type: 'position_score'},
      options
    );
    const res = await lastValueFrom(getResponse.pipe(take(1)));
    expect(res).toStrictEqual(new AnnotatorConfig(
      'position_score',
      'annotatorUrl',
      [
        new AnnotatorConfigResource(
          'resource_id', 'resource', 'position_score', '', [
            'hg19/scores/FitCons-i6-merged',
            'hg19/scores/FitCons2_E035',
            'hg19/scores/FitCons2_E067',
          ],
          false,
          ''
        ),
        new AnnotatorConfigResource('input_annotatable', 'attribute', '', '', null, true, 'annotatable'),
      ],
    ));
  });

  it('should get annotator config without resource of type resource', async() => {
    const httpPostSpy = jest.spyOn(HttpClient.prototype, 'post');
    httpPostSpy.mockReturnValue(of(
      /* eslint-disable camelcase */
      {
        annotator_type: 'gene_set_annotator',
        documentation_url: 'annotatorUrl',
        input_gene_list: {
          field_type: 'string',
          optional: false
        },
        input_annotatable: {
          field_type: 'attribute',
          optional: true,
          attribute_type: 'annotatable'
        }
      }
      /* eslint-enable */
    ));

    const options = { headers: {'X-CSRFToken': ''}, withCredentials: true };
    const getResponse = service.getAnnotatorConfig('gene_set_annotator');

    expect(httpPostSpy).toHaveBeenCalledWith(
      '//localhost:8000/api/editor/annotator_config',
      // eslint-disable-next-line camelcase
      {annotator_type: 'gene_set_annotator'},
      options
    );
    const res = await lastValueFrom(getResponse.pipe(take(1)));
    expect(res).toStrictEqual(new AnnotatorConfig(
      'gene_set_annotator',
      'annotatorUrl',
      [
        new AnnotatorConfigResource('input_gene_list', 'string', '', '', null, false, ''),
        new AnnotatorConfigResource('input_annotatable', 'attribute', '', '', null, true, 'annotatable'),
      ]
    ));
  });

  it('should get resources of annotator type', async() => {
    const httpGetSpy = jest.spyOn(HttpClient.prototype, 'get');
    httpGetSpy.mockReturnValue(of([
      'hg19/scores/FitCons-i6-merged',
      'hg19/scores/FitCons2_E035',
      'hg19/scores/FitCons2_E067',
    ]));
    const options = { headers: {'X-CSRFToken': ''}, withCredentials: true };
    const getResponse = service.getResources('position_score');

    expect(httpGetSpy).toHaveBeenCalledWith(
      '//localhost:8000/api/resources?type=position_score',
      options
    );
    const res = await lastValueFrom(getResponse.pipe(take(1)));
    expect(res).toStrictEqual([
      'hg19/scores/FitCons-i6-merged',
      'hg19/scores/FitCons2_E035',
      'hg19/scores/FitCons2_E067',
    ]);
  });

  it('should get attributes of annotator type', async() => {
    const httpPostSpy = jest.spyOn(HttpClient.prototype, 'post');
    httpPostSpy.mockReturnValue(of({
      attributes: [{
        name: 'fitcons_i6_merged',
        source: 'fc_i6_score',
        type: 'float',
        internal: false,
        default: true,
        description: 'probability that a point mutation at each position in a genome will influence fitness'
      }],
      page: 0,
      // eslint-disable-next-line camelcase
      total_pages: 1,
      // eslint-disable-next-line camelcase
      total_attributes: 1,
    }));

    const options = { headers: {'X-CSRFToken': ''}, withCredentials: true };
    const getResponse = service.getAttributes(
      'pipelineId',
      'position_score',
      // eslint-disable-next-line camelcase
      { resource_id: 'hg19/scores/FitCons-i6-merged' }
    );

    expect(httpPostSpy).toHaveBeenCalledWith(
      '//localhost:8000/api/editor/annotator_attributes',
      /* eslint-disable camelcase */
      {
        pipeline_id: 'pipelineId',
        annotator_type: 'position_score',
        resource_id: 'hg19/scores/FitCons-i6-merged',
        search: undefined,
        page: 0
      },
      /* eslint-enable */
      options
    );
    const res = await lastValueFrom(getResponse.pipe(take(1)));
    expect(res).toStrictEqual(
      new AttributePage(
        [
          new AttributeData(
            'fitcons_i6_merged',
            'float',
            'fc_i6_score',
            false,
            true,
            'probability that a point mutation at each position in a genome will influence fitness'
          ),
        ],
        0,
        1,
        1
      )
    );
  });

  it('should get attributes of pipeline', async() => {
    const httpGetSpy = jest.spyOn(HttpClient.prototype, 'get');
    httpGetSpy.mockReturnValue(of(['normalized_allele', 'hg19_annotatable']));

    const options = { headers: {'X-CSRFToken': ''}, withCredentials: true };
    const getResponse = service.getPipelineAttributes(
      'pipelineId',
      'input_annotatable',
    );

    expect(httpGetSpy).toHaveBeenCalledWith(
      '//localhost:8000/api/editor/pipeline_attributes?pipeline_id=pipelineId&attribute_type=input_annotatable',
      options
    );
    const res = await lastValueFrom(getResponse.pipe(take(1)));
    expect(res).toStrictEqual(['normalized_allele', 'hg19_annotatable']);
  });

  const liftoverAttribute: AnnotatorAttribute = {
    source: 'liftover_annotatable',
    name: 'liftover_annotatable',
    type: 'annotatable',
    internal: true,
    aggregators: [],
    defaultAggregator: null,
    selectedAggregator: null,
    parameterValue: null,
  };

  const liftoverResources = {
    chain: 'liftover/T2T_to_hg38',
    // eslint-disable-next-line camelcase
    source_genome: 'hg38/genomes/GRCh38-hg38',
    // eslint-disable-next-line camelcase
    target_genome: 'hg19/genomes/GATK_ResourceBundle_5777_b37_phiX174'
  };

  it('should get yml config text', async() => {
    const yml = '- liftover_annotator:\n    '+
    'attributes:\n    '+
    '- internal: true\n      '+
    'name: liftover_annotatable\n      '+
    'source: liftover_annotatable\n      '+
    'type: annotatable\n    '+
    'chain: liftover/T2T_to_hg38\n    '+
    'source_genome: hg38/genomes/GRCh38-hg38\n    ' +
    'target_genome: hg19/genomes/GATK_ResourceBundle_5777_b37_phiX174\n';

    const httpPostSpy = jest.spyOn(HttpClient.prototype, 'post');
    httpPostSpy.mockReturnValue(of(yml));

    const options = { headers: {'X-CSRFToken': ''}, withCredentials: true };
    const getResponse = service.getAnnotatorYml(
      'pipelineId', 'liftover_annotator', liftoverResources, [liftoverAttribute]
    );

    expect(httpPostSpy).toHaveBeenCalledWith(
      '//localhost:8000/api/editor/annotator_yaml',
      {
        // eslint-disable-next-line camelcase
        pipeline_id: 'pipelineId',
        attributes: [{ name: 'liftover_annotatable', source: 'liftover_annotatable', internal: true }],
        // eslint-disable-next-line camelcase
        annotator_type: 'liftover_annotator',
        ...liftoverResources,
      },
      options
    );
    const res = await lastValueFrom(getResponse.pipe(take(1)));
    expect(res).toStrictEqual(yml);
  });

  it('should include aggregator in yml request when attribute has selectedAggregator', () => {
    const httpPostSpy = jest.spyOn(HttpClient.prototype, 'post');
    httpPostSpy.mockReturnValue(of('yml'));

    const options = { headers: {'X-CSRFToken': ''}, withCredentials: true };
    service.getAnnotatorYml('pipelineId', 'gene_score_annotator', {}, [{
      ...liftoverAttribute,
      source: 'pLI',
      name: 'pLI',
      aggregators: ['min', 'max'],
      defaultAggregator: 'min',
      selectedAggregator: 'max',
      parameterValue: null,
    }]);

    /* eslint-disable camelcase */
    expect(httpPostSpy).toHaveBeenCalledWith(
      '//localhost:8000/api/editor/annotator_yaml',
      {
        pipeline_id: 'pipelineId',
        attributes: [
          {
            name: 'pLI',
            source: 'pLI',
            internal: true,
            aggregator: { aggregator_type: 'max' }
          }],
        annotator_type: 'gene_score_annotator',
      },
      options
    );
    /* eslint-enable */
  });

  it('should include aggregator and parameter in yml request for parametrized aggregator', () => {
    const httpPostSpy = jest.spyOn(HttpClient.prototype, 'post');
    httpPostSpy.mockReturnValue(of('yml'));

    const options = { headers: {'X-CSRFToken': ''}, withCredentials: true };
    service.getAnnotatorYml('pipelineId', 'gene_score_annotator', {}, [{
      ...liftoverAttribute,
      source: 'pLI',
      name: 'pLI',
      aggregators: ['min', 'max', 'join'],
      defaultAggregator: 'min',
      selectedAggregator: 'join',
      parameterValue: ';',
    }]);

    /* eslint-disable camelcase */
    expect(httpPostSpy).toHaveBeenCalledWith(
      '//localhost:8000/api/editor/annotator_yaml',
      {
        pipeline_id: 'pipelineId',
        attributes: [
          {
            name: 'pLI',
            source: 'pLI',
            internal: true,
            aggregator: { aggregator_type: 'join', parameters: [';'] }
          }
        ],
        annotator_type: 'gene_score_annotator',
      },
      options
      /* eslint-enable */
    );
  });

  it('should omit aggregator in yml request for non-aggregatable attribute', () => {
    const httpPostSpy = jest.spyOn(HttpClient.prototype, 'post');
    httpPostSpy.mockReturnValue(of('yml'));

    const options = { headers: {'X-CSRFToken': ''}, withCredentials: true };
    service.getAnnotatorYml('pipelineId', 'gene_score_annotator', {}, [{
      ...liftoverAttribute,
      source: 'pLI_rank_null',
      name: 'pLI_rank_null',
      aggregators: [],
      defaultAggregator: null,
      selectedAggregator: null,
      parameterValue: null,
    }]);

    expect(httpPostSpy).toHaveBeenCalledWith(
      '//localhost:8000/api/editor/annotator_yaml',
      {
        // eslint-disable-next-line camelcase
        pipeline_id: 'pipelineId',
        attributes: [{ name: 'pLI_rank_null', source: 'pLI_rank_null', internal: true }],
        // eslint-disable-next-line camelcase
        annotator_type: 'gene_score_annotator',
      },
      options
    );
  });

  it('should catch error 400 when requesting yml', async() => {
    const httpError = new HttpErrorResponse({status: 400, error: {error: 'Invalid annotator configuration!'}});
    jest.spyOn(HttpClient.prototype, 'post').mockReturnValue(throwError(() => httpError));

    const postResult = service.getAnnotatorYml(
      'pipelineId', 'liftover_annotator', liftoverResources, [liftoverAttribute]
    );

    await expect(() => lastValueFrom(postResult.pipe(take(1))))
      .rejects.toThrow('Invalid annotator configuration!');
  });

  it('should return default error message when requesting yml fails', async() => {
    const httpError = new HttpErrorResponse({status: 415});
    jest.spyOn(HttpClient.prototype, 'post').mockReturnValue(throwError(() => httpError));

    const postResult = service.getAnnotatorYml(
      'pipelineId', 'liftover_annotator', liftoverResources, [liftoverAttribute]
    );

    await expect(() => lastValueFrom(postResult.pipe(take(1))))
      .rejects.toThrow('Error occurred!');
  });

  it('should get aggregators list', async() => {
    /* eslint-disable camelcase */
    const aggregators = [
      { aggregator_type: 'min', parametrized: false },
      { aggregator_type: 'max', parametrized: false },
      { aggregator_type: 'join', parametrized: true, default_parameter_value: ',' },
    ];
    /* eslint-enable*/
    jest.spyOn(HttpClient.prototype, 'get').mockReturnValue(of(aggregators));
    const res = await lastValueFrom(service.getAggregators().pipe(take(1)));
    expect(res).toStrictEqual(
      [
        { aggregatorType: 'min', parametrized: false },
        { aggregatorType: 'max', parametrized: false },
        { aggregatorType: 'join', parametrized: true, defaultParameterValue: ',' }
      ]
    );
  });

  it('should return aggregator states from getAttributesAggregators', async() => {
    /* eslint-disable camelcase */
    const mock: AttributeAggregatorsResponse = {
      pLI: { aggregators: ['min', 'max', 'join'], default_aggregator: 'min' },
      pLI_rank: { aggregators: ['min', 'max', 'join'], default_aggregator: 'max' },
      pLI_rank_null: null
    };
    /* eslint-enable */
    jest.spyOn(HttpClient.prototype, 'post').mockReturnValue(of(mock));
    const res = await lastValueFrom(
      service.getAttributesAggregators('gene_score_annotator', 'pipelineId', {}, ['pLI', 'pLI_rank', 'pLI_rank_null'])
        .pipe(take(1))
    );
    expect(res).toHaveLength(3);

    const aggregatable1 = res.find(r => r.source === 'pLI');
    expect(aggregatable1.aggregators).toStrictEqual(['min', 'max', 'join']);
    expect(aggregatable1.defaultAggregator).toBe('min');
    expect(aggregatable1.selectedAggregator).toBe('min');
    const aggregatable2 = res.find(r => r.source === 'pLI_rank');
    expect(aggregatable2.aggregators).toStrictEqual(['min', 'max', 'join']);
    expect(aggregatable2.defaultAggregator).toBe('max');
    expect(aggregatable2.selectedAggregator).toBe('max');
    const nonAggregatable = res.find(r => r.source === 'pLI_rank_null');
    expect(nonAggregatable.aggregators).toStrictEqual([]);
    expect(nonAggregatable.defaultAggregator).toBeNull();
    expect(nonAggregatable.selectedAggregator).toBeNull();
  });


  it('should check parameters of attributes aggregators query', () => {
    /* eslint-disable camelcase */
    const mock: AttributeAggregatorsResponse = {
      pLI: { aggregators: ['min', 'max', 'join'], default_aggregator: 'min' },
      pLI_rank: { aggregators: ['min', 'max', 'join'], default_aggregator: 'max' },
      pLI_rank_null: null
    };

    const postSpy = jest.spyOn(HttpClient.prototype, 'post').mockReturnValue(of(mock));

    const options = { headers: {'X-CSRFToken': ''}, withCredentials: true };

    service.getAttributesAggregators(
      'gene_score_annotator',
      'pipelineId',
      { resource_id: 'gene_properties/gene_scores/RVIS', input_gene_list: 'gene_list' },
      ['pLI', 'pLI_rank', 'pLI_rank_null']
    );

    expect(postSpy).toHaveBeenCalledWith(
      '//localhost:8000/api/editor/annotator_aggregators',
      {
        annotator_type: 'gene_score_annotator',
        pipeline_id: 'pipelineId',
        attribute_sources: ['pLI', 'pLI_rank', 'pLI_rank_null'],
        resource_id: 'gene_properties/gene_scores/RVIS',
        input_gene_list: 'gene_list'
      },
      options
    );
    /* eslint-enable */
  });

  it('should get resource types', async() => {
    const httpGetSpy = jest.spyOn(HttpClient.prototype, 'get');
    httpGetSpy.mockReturnValue(of([
      'gene_models',
      'position_score',
      'allele_score',
    ]));

    const getResponse = service.getResourceTypes();

    expect(httpGetSpy).toHaveBeenCalledWith(
      '//localhost:8000/api/resources/types'
    );
    const res = await lastValueFrom(getResponse.pipe(take(1)));
    expect(res).toStrictEqual([
      'gene_models',
      'position_score',
      'allele_score',
    ]);
  });

  it('should get resources by search value', async() => {
    const httpGetSpy = jest.spyOn(HttpClient.prototype, 'get');
    httpGetSpy.mockReturnValue(of({
      page: 1,
      pages: 10,
      resources: mockResources.filter(r => r.full_id.includes('CADD')),
      // eslint-disable-next-line camelcase
      total_resources: 200
    }));

    const getResponse = service.getResourcesBySearch('cadd', 'allele_score');

    const params = new HttpParams().set('type', 'allele_score').set('search', 'cadd');
    expect(httpGetSpy).toHaveBeenCalledWith(
      '//localhost:8000/api/resources/search',
      {params: params}
    );
    const res = await lastValueFrom(getResponse.pipe(take(1)));
    expect(res).toStrictEqual(
      new ResourcePage(
        1,
        10,
        [
          new Resource('hg19/scores/CADD', 'hg19/scores/CADD', 'allele_score', 0, 'url/index.html', 'CADD summary'),
          // eslint-disable-next-line @stylistic/max-len
          new Resource('hg38/scores/CADD_v1.7', 'hg38/scores/CADD_v1.7', 'allele_score', 0, 'url/index.html', 'CADD_v1.7 summary'),
        ],
        200
      )
    );
  });

  it('should get resources by type', async() => {
    const httpGetSpy = jest.spyOn(HttpClient.prototype, 'get');
    httpGetSpy.mockReturnValue(of({
      page: 1,
      pages: 10,
      resources: mockResources.filter(r => r.type.includes('allele_score')),
      // eslint-disable-next-line camelcase
      total_resources: 200
    }));

    const getResponse = service.getResourcesBySearch('', 'allele_score');

    const params = new HttpParams().set('type', 'allele_score');
    expect(httpGetSpy).toHaveBeenCalledWith(
      '//localhost:8000/api/resources/search',
      {params: params}
    );
    const res = await lastValueFrom(getResponse.pipe(take(1)));
    expect(res).toStrictEqual(
      new ResourcePage(
        1,
        10,
        [
          new Resource('hg19/scores/MPC', 'hg19/scores/MPC', 'allele_score', 0, 'url/index.html', 'mpc summary'),
          new Resource('hg19/scores/CADD', 'hg19/scores/CADD', 'allele_score', 0, 'url/index.html', 'CADD summary'),
          // eslint-disable-next-line @stylistic/max-len
          new Resource('hg38/scores/CADD_v1.7', 'hg38/scores/CADD_v1.7', 'allele_score', 0, 'url/index.html', 'CADD_v1.7 summary'),
        ],
        200
      )
    );
  });

  it('should get all resources', async() => {
    const httpGetSpy = jest.spyOn(HttpClient.prototype, 'get');
    httpGetSpy.mockReturnValue(of(
      {
        page: 1,
        pages: 10,
        resources: mockResources,
        // eslint-disable-next-line camelcase
        total_resources: 300
      }
    ));

    const getResponse = service.getResourcesBySearch('', 'All');

    const params = new HttpParams();
    expect(httpGetSpy).toHaveBeenCalledWith(
      '//localhost:8000/api/resources/search',
      {params: params}
    );
    const res = await lastValueFrom(getResponse.pipe(take(1)));
    expect(res).toStrictEqual(
      new ResourcePage(
        1,
        10,
        [
          /* eslint-disable @stylistic/max-len */
          new Resource('hg19/scores/MPC', 'hg19/scores/MPC', 'allele_score', 0, 'url/index.html', 'mpc summary'),
          new Resource('hg19/scores/CADD', 'hg19/scores/CADD', 'allele_score', 0, 'url/index.html', 'CADD summary'),
          new Resource('hg38/scores/CADD_v1.7', 'hg38/scores/CADD_v1.7', 'allele_score', 0, 'url/index.html', 'CADD_v1.7 summary'),
          new Resource('hg38/genomes/GRCh38.p13', 'hg38/genomes/GRCh38.p13', 'genome', 0, 'url/index.html', 'GRCh38.p13 summary'),
          new Resource('hg38/genomes/GRCh38.p14', 'hg38/genomes/GRCh38.p14', 'genome', 0, 'url/index.html', 'GRCh38.p14 summary'),
          new Resource('t2t/genomes/t2t-chm13v2.0', 't2t/genomes/t2t-chm13v2.0', 'genome', 0, 'url/index.html', 't2t-chm13v2.0 summary'),
          /* eslint-enable */
        ],
        300
      ));
  });

  it('should get annotators of a resource', async() => {
    const httpGetSpy = jest.spyOn(HttpClient.prototype, 'get');
    httpGetSpy.mockReturnValue(of({
      default: 'allele_score',
      configs: {
        // eslint-disable-next-line camelcase
        allele_score: {annotator_type: 'allele_score', resource_id: 'hg19/scores/CADD'}
      }
    }));

    const getResponse = service.getResourceAnnotators('hg19/scores/CADD');

    expect(httpGetSpy).toHaveBeenCalledWith(
      '//localhost:8000/api/editor/resource_annotators?resource_id=hg19/scores/CADD'
    );
    const res = await lastValueFrom(getResponse.pipe(take(1)));
    expect(res).toStrictEqual(
      new ResourceAnnotatorConfigs(
        'allele_score',
        [new ResourceAnnotator('allele_score', '{"resource_id":"hg19/scores/CADD"}')]
      )
    );
  });

  it('should get all attribute names of a pipeline', async() => {
    const httpGetSpy = jest.spyOn(HttpClient.prototype, 'get');
    // eslint-disable-next-line camelcase
    httpGetSpy.mockReturnValue(of(
      [
        'normalized_allele',
        'CLNSIG',
        'CLNDN',
        'hg19_annotatable',
        'mpc',
      ]
    ));

    const getResponse = service.getPipelineAttributesNames('pipelineId');

    expect(httpGetSpy).toHaveBeenCalledWith(
      '//localhost:8000/api/editor/pipeline_attributes?pipeline_id=pipelineId',
      { headers: { 'X-CSRFToken': '' }, withCredentials: true }
    );
    const res = await lastValueFrom(getResponse.pipe(take(1)));
    expect(res).toStrictEqual([
      'normalized_allele',
      'CLNSIG',
      'CLNDN',
      'hg19_annotatable',
      'mpc',
    ]);
  });

  it('should get attribute names of a pipeline with specific attribute type', async() => {
    const httpGetSpy = jest.spyOn(HttpClient.prototype, 'get');
    // eslint-disable-next-line camelcase
    httpGetSpy.mockReturnValue(of(['cadd_phred', 'cadd_raw']));

    const getResponse = service.getPipelineAttributesNames('pipelineId', 'str');

    expect(httpGetSpy).toHaveBeenCalledWith(
      '//localhost:8000/api/editor/pipeline_attributes?pipeline_id=pipelineId&attribute_type=str',
      { headers: { 'X-CSRFToken': '' }, withCredentials: true }
    );
    const res = await lastValueFrom(getResponse.pipe(take(1)));
    expect(res).toStrictEqual(['cadd_phred', 'cadd_raw']);
  });

  it('should include parsed resource fields in request body when resourcesJsonString is provided', () => {
    document.cookie = 'csrftoken=editorToken';
    const httpPostSpy = jest.spyOn(HttpClient.prototype, 'post');
    httpPostSpy.mockReturnValue(of(
      /* eslint-disable camelcase */
      {
        annotator_type: 'gene_set_annotator',
        documentation_url: 'url',
        input_gene_list: { field_type: 'string', optional: false }
      }
      /* eslint-enable */
    ));

    // eslint-disable-next-line camelcase
    const resourcesJson = JSON.stringify({gene_list: 'hg19/geneSets/SFARI'});
    service.getAnnotatorConfig('gene_set_annotator', resourcesJson);

    expect(httpPostSpy).toHaveBeenCalledWith(
      '//localhost:8000/api/editor/annotator_config',
      // eslint-disable-next-line camelcase
      {annotator_type: 'gene_set_annotator', gene_list: 'hg19/geneSets/SFARI'},
      {headers: {'X-CSRFToken': 'editorToken'}, withCredentials: true}
    );
  });

  it('should catch error 400 when requesting annotator attributes', async() => {
    const httpError = new HttpErrorResponse({status: 400, error: {error: 'Invalid annotator configuration!'}});
    jest.spyOn(HttpClient.prototype, 'post').mockReturnValue(throwError(() => httpError));

    // eslint-disable-next-line camelcase
    const result = service.getAttributes('pipelineId', 'position_score', {resource_id: 'res1'});

    await expect(() => lastValueFrom(result.pipe(take(1))))
      .rejects.toThrow('Invalid annotator configuration!');
  });

  it('should throw default error for other errors when requesting attributes', async() => {
    const httpError = new HttpErrorResponse({status: 415});
    jest.spyOn(HttpClient.prototype, 'post').mockReturnValue(throwError(() => httpError));

    // eslint-disable-next-line camelcase
    const result = service.getAttributes('pipelineId', 'position_score', {resource_id: 'res1'});

    await expect(() => lastValueFrom(result.pipe(take(1))))
      .rejects.toThrow('Error occurred!');
  });

  it('should include page parameter in search request when page is provided', () => {
    const httpGetSpy = jest.spyOn(HttpClient.prototype, 'get');
    httpGetSpy.mockReturnValue(of({
      // eslint-disable-next-line camelcase
      page: 2, pages: 5, resources: [], total_resources: 50
    }));

    service.getResourcesBySearch('cadd', 'allele_score', 2);

    const params = new HttpParams().set('type', 'allele_score').set('search', 'cadd').set('page', 2);
    expect(httpGetSpy).toHaveBeenCalledWith(
      '//localhost:8000/api/resources/search',
      {params: params}
    );
  });

  it('should throw invalid search value error for 500 status when searching resources', async() => {
    const httpError = new HttpErrorResponse({status: 500});
    jest.spyOn(HttpClient.prototype, 'get').mockReturnValue(throwError(() => httpError));

    const result = service.getResourcesBySearch('cadd', 'allele_score');

    await expect(() => lastValueFrom(result.pipe(take(1))))
      .rejects.toThrow('Invalid search value');
  });

  it('should throw default error for other errors when searching resources', async() => {
    const httpError = new HttpErrorResponse({status: 404});
    jest.spyOn(HttpClient.prototype, 'get').mockReturnValue(throwError(() => httpError));

    const result = service.getResourcesBySearch('cadd', 'allele_score');

    await expect(() => lastValueFrom(result.pipe(take(1))))
      .rejects.toThrow('Error occurred!');
  });
});
