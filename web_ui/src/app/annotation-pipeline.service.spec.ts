import { TestBed } from '@angular/core/testing';
import { AnnotationPipelineService } from './annotation-pipeline.service';
import { HttpClient, provideHttpClient } from '@angular/common/http';
import { lastValueFrom, of, take } from 'rxjs';
import { PipelineInfo } from './annotation-pipeline';

describe('AnnotationPipelineService', () => {
  let service: AnnotationPipelineService;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        AnnotationPipelineService,
        provideHttpClient(),
      ]
    });

    service = TestBed.inject(AnnotationPipelineService);

    jest.spyOn(document, 'cookie', 'get').mockReturnValue('csrftoken=token1; Path=/');
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  it('should check query params when saving pipeline', () => {
    const httpPostSpy = jest.spyOn(HttpClient.prototype, 'post');
    httpPostSpy.mockReturnValue(of({id: '1'}));

    const config = `
      preamble:
        input_reference_genome: hg38/genomes/GRCh38-hg38
        summary: Clinical Annotation Pipeline
        description: This is a pipeline to annotate with Clinical resources

      annotators:

        - normalize_allele_annotator: genome: hg38/genomes/GRCh38-hg38'
    `;

    service.savePipeline('1', 'pipeline-name', config);

    expect(httpPostSpy).toHaveBeenCalled();
    const [url, formData, options] = httpPostSpy.mock.calls[0];

    expect(url).toBe('//localhost:8000/api/pipelines/user');
    expect(formData).toBeInstanceOf(FormData);
    expect(formData.get('id')).toBe('1');
    expect(formData.get('name')).toBe('pipeline-name');
    expect(formData.get('config')).toBeInstanceOf(File);

    expect(options).toEqual({
      headers: {
        'X-CSRFToken': 'token1'
      },
      withCredentials: true
    });
  });

  it('should save pipeline and get pipeline name as response', async() => {
    const httpPostSpy = jest.spyOn(HttpClient.prototype, 'post');
    httpPostSpy.mockReturnValue(of({id: '1'}));

    const config = `
      preamble:
        input_reference_genome: hg38/genomes/GRCh38-hg38
        summary: Clinical Annotation Pipeline 
        description: This is a pipeline to annotate with Clinical resources  

      annotators:
    
        - normalize_allele_annotator: genome: hg38/genomes/GRCh38-hg38'
    `;

    const postResponse = service.savePipeline('1', 'pipeline-name', config);

    const res = await lastValueFrom(postResponse.pipe(take(1)));
    expect(res).toBe('1');
  });


  it('should delete pipeline by id', () => {
    const httpDelteSpy = jest.spyOn(HttpClient.prototype, 'delete');
    const options = {
      headers: {
        'X-CSRFToken': 'token1'
      },
      withCredentials: true
    };

    service.deletePipeline('1');

    expect(httpDelteSpy).toHaveBeenCalledWith(
      '//localhost:8000/api/pipelines/user?id=1',
      options
    );
  });

  it('should load specific pipeline', () => {
    const httpPostSpy = jest.spyOn(HttpClient.prototype, 'post');
    const options = {
      headers: {
        'X-CSRFToken': 'token1'
      },
      withCredentials: true
    };

    service.loadPipeline('1');

    expect(httpPostSpy).toHaveBeenCalledWith(
      '//localhost:8000/api/pipelines/load',
      {id: '1'},
      options
    );
  });

  it('should get pipeline status', async() => {
    const httpGetSpy = jest.spyOn(HttpClient.prototype, 'get');
    httpGetSpy.mockReturnValue(of({
      /* eslint-disable camelcase */
      annotators_count: 20,
      annotatables: ['hg19_annotatable'],
      attributes_count: 15,
      gene_lists: ['gene_list']
      /* eslint-enable */
    }));
    const options = { headers: {'X-CSRFToken': 'token1'}, withCredentials: true };
    const getResponse = service.getPipelineInfo('pipelineId');

    expect(httpGetSpy).toHaveBeenCalledWith(
      '//localhost:8000/api/editor/pipeline_status?pipeline_id=pipelineId',
      options
    );
    const res = await lastValueFrom(getResponse.pipe(take(1)));
    expect(res).toStrictEqual(new PipelineInfo(15, 20, ['hg19_annotatable'], ['gene_list']));
  });
});
