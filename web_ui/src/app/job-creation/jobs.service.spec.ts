import { TestBed } from '@angular/core/testing';
import { HttpClient, HttpErrorResponse, HttpResponse, provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { lastValueFrom, of, take, throwError } from 'rxjs';
import { JobsService } from './jobs.service';
import { FileContent, getStatusClassName, Job } from './jobs';
import { Pipeline } from './pipelines';

/* eslint-disable camelcase */
const jobsMockJson = [
  {
    id: 1, name: 1, created: '1.10.2025', owner: 'test@email.com',
    status: 'in_progress', duration: 4.7, result_filename: 'job-file.txt', size: '10 KB', error: ''
  },
  {
    id: 2, name: 2, created: '1.10.2025', owner: 'test@email.com',
    status: 'failed', duration: 2.5, result_filename: 'job-file.txt', size: '10 KB', error: ''
  },
  {
    id: 3, name: 3, created: '1.10.2025', owner: 'test@email.com',
    status: 'success', duration: 2.3, result_filename: 'job-file.txt', size: '10 KB', error: ''
  },
  {
    id: 4, name: 4, created: '1.10.2025', owner: 'test@email.com',
    status: 'waiting', duration: 1.9, result_filename: 'job-file.txt', size: '10 KB', error: ''
  },
];
/* eslint-enable */

describe('JobsService', () => {
  let service: JobsService;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        JobsService,
        provideHttpClient(),
        provideHttpClientTesting()
      ]
    });
    service = TestBed.inject(JobsService);
    jest.clearAllMocks();
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  it('should create job with config chosen from pipeline list by user', async() => {
    const httpPostSpy = jest.spyOn(HttpClient.prototype, 'post');
    // eslint-disable-next-line camelcase
    httpPostSpy.mockReturnValue(of({job_id: 12}));

    const mockInputFile = new File(['mockData'], 'mockInput.vcf');

    const postResult = service.createVcfJob(mockInputFile, 'autism', null);

    const res = await lastValueFrom(postResult.pipe(take(1)));
    expect(res).toBe(12);
  });

  it('should create job with non vcf file uploaded', () => {
    const httpPostSpy = jest.spyOn(HttpClient.prototype, 'post');
    httpPostSpy.mockReturnValue(of({}));

    const mockInputFile = new File(['mockData'], 'mockInput.tsv');

    const formData = new FormData();
    formData.append('data', mockInputFile);
    formData.append('genome', 'hg38');
    formData.append('separator', '\t');
    formData.append('pipeline_id', 'autism');

    const mockColumns = new Map<string, string>([
      ['pos', 'POS'],
      ['alt', 'ALT'],
      ['vcf_like', 'VCF'],
    ]);

    formData.append('col_chrom', '-');
    formData.append('col_pos', mockColumns.get('pos'));
    formData.append('col_ref', '-');
    formData.append('col_alt', mockColumns.get('alt'));
    formData.append('col_pos_beg', '-');
    formData.append('col_pos_end', '-');
    formData.append('col_cnv_type', '-');
    formData.append('col_vcf_like', mockColumns.get('vcf_like'));
    formData.append('col_variant', '-');
    formData.append('col_location', '-');

    const options = {
      headers: {
        'X-CSRFToken': ''
      },
      withCredentials: true
    };

    service.createNonVcfJob(mockInputFile, 'autism', 'hg38', '\t', mockColumns);
    const calls = httpPostSpy.mock.calls;
    expect(calls[0][0]).toBe('//localhost:8000/api/jobs/annotate_columns');
    expect(calls[0][1]).toStrictEqual(formData);
    expect(calls[0][2]).toStrictEqual(options);
  });

  it('should catch error 403 for daily quota limit when creating job', async() => {
    const httpError = new HttpErrorResponse({status: 403, error: {reason: 'Daily quota limit reached!'}});
    const httpPostSpy = jest.spyOn(HttpClient.prototype, 'post');
    httpPostSpy.mockReturnValue(throwError(() => httpError));

    const mockInputFile = new File(['mockData'], 'mockInput.vcf');

    const postResult = service.createVcfJob(mockInputFile, 'autism', null);

    await expect(() => lastValueFrom(postResult.pipe(take(1))))
      .rejects.toThrow('Daily quota limit reached!');
  });

  it('should catch error 413 for upload limit when creating job', async() => {
    const httpError = new HttpErrorResponse({status: 413});
    const httpPostSpy = jest.spyOn(HttpClient.prototype, 'post');
    httpPostSpy.mockReturnValue(throwError(() => httpError));

    const mockInputFile = new File(['mockData'], 'mockInput.vcf');

    const postResult = service.createVcfJob(mockInputFile, 'autism', null);

    await expect(() => lastValueFrom(postResult.pipe(take(1))))
      .rejects.toThrow('Upload limit reached!');
  });

  it('should catch error 400 for invalid pipeline configuration file when creating job', async() => {
    const httpError = new HttpErrorResponse({status: 400, error: {reason: 'Invalid pipeline configuration file!'}});
    const httpPostSpy = jest.spyOn(HttpClient.prototype, 'post');
    httpPostSpy.mockReturnValue(throwError(() => httpError));

    const mockInputFile = new File(['mockData'], 'mockInput.vcf');

    const postResult = service.createVcfJob(mockInputFile, 'autism', null);

    await expect(() => lastValueFrom(postResult.pipe(take(1))))
      .rejects.toThrow('Invalid pipeline configuration file!');
  });

  it('should throw default message for other error cases when creating job', async() => {
    const httpError = new HttpErrorResponse({status: 422});
    const httpPostSpy = jest.spyOn(HttpClient.prototype, 'post');
    httpPostSpy.mockReturnValue(throwError(() => httpError));

    const mockInputFile = new File(['mockData'], 'mockInput.vcf');

    const postResult = service.createVcfJob(mockInputFile, 'autism', null);

    await expect(() => lastValueFrom(postResult.pipe(take(1))))
      .rejects.toThrow('Error occurred!');
  });

  it('should check if create query has correct parameters when config is chosen by user', () => {
    const httpPostSpy = jest.spyOn(HttpClient.prototype, 'post');
    httpPostSpy.mockReturnValue(of(new HttpResponse({status: 204})));

    const mockInputFile = new File(['mockData'], 'mockInput.vcf');

    const formData = new FormData();
    formData.append('data', mockInputFile, 'mockInput.vcf');
    formData.append('genome', 'hg38');
    formData.append('pipeline_id', 'autism');

    const options = {
      headers: {
        'X-CSRFToken': ''
      },
      withCredentials: true
    };

    service.createVcfJob(mockInputFile, 'autism', 'hg38');

    expect(httpPostSpy).toHaveBeenCalledWith(
      '//localhost:8000/api/jobs/annotate_vcf',
      formData,
      options
    );
  });

  it('should check if csrf token is get from cookies and sent in create query', () => {
    const httpPostSpy = jest.spyOn(HttpClient.prototype, 'post');
    httpPostSpy.mockReturnValue(of(new HttpResponse({status: 204})));

    const mockInputFile = new File(['mockData'], 'mockInput.vcf');

    const formData = new FormData();
    formData.append('data', mockInputFile, 'mockInput.vcf');
    formData.append('genome', 'hg38');
    formData.append('pipeline_id', 'autism');

    const mockCookie = 'csrftoken=EYZbFmv1i1Ie7cmT3OFHgxdv3kOR7rIt';
    document.cookie = mockCookie;

    const options = {
      headers: {
        'X-CSRFToken': 'EYZbFmv1i1Ie7cmT3OFHgxdv3kOR7rIt'
      },
      withCredentials: true
    };

    service.createVcfJob(mockInputFile, 'autism', 'hg38');
    expect(httpPostSpy).toHaveBeenCalledWith(
      '//localhost:8000/api/jobs/annotate_vcf',
      formData,
      options
    );
  });

  it('should check parameters of get jobs query', () => {
    const httpGetSpy = jest.spyOn(HttpClient.prototype, 'get');

    const mockCookie = 'csrftoken=EYZbFmv1i1Ie7cmT3OFHgxdv3kOR7rIt';
    document.cookie = mockCookie;

    const options = {
      headers: {
        'X-CSRFToken': 'EYZbFmv1i1Ie7cmT3OFHgxdv3kOR7rIt'
      },
      withCredentials: true
    };

    service.getJobs();
    expect(httpGetSpy).toHaveBeenCalledWith(
      '//localhost:8000/api/jobs',
      options
    );
  });

  it('should get users jobs and create list with job objects from response', async() => {
    const httpGetSpy = jest.spyOn(HttpClient.prototype, 'get');
    httpGetSpy.mockReturnValue(of(jobsMockJson));

    const jobsMockResult = [
      new Job(1, 1, new Date('1.10.2025'), 'test@email.com', 'in progress', 4.7, 'job-file.txt', '10 KB', ''),
      new Job(2, 2, new Date('1.10.2025'), 'test@email.com', 'failed', 2.5, 'job-file.txt', '10 KB', ''),
      new Job(3, 3, new Date('1.10.2025'), 'test@email.com', 'success', 2.3, 'job-file.txt', '10 KB', ''),
      new Job(4, 4, new Date('1.10.2025'), 'test@email.com', 'waiting', 1.9, 'job-file.txt', '10 KB', ''),
    ];

    const getResponse = service.getJobs();

    const res = await lastValueFrom(getResponse.pipe(take(1)));
    expect(res).toStrictEqual(jobsMockResult);
  });

  it('should return undefined when converting invalid response into array of jobs', async() => {
    const httpGetSpy = jest.spyOn(HttpClient.prototype, 'get');
    httpGetSpy.mockReturnValue(of(null));

    const getResponse = service.getJobs();

    const res = await lastValueFrom(getResponse.pipe(take(1)));
    expect(res).toBeUndefined();
  });

  it('should return undefined when converting array with invalid data into array of jobs', async() => {
    const httpGetSpy = jest.spyOn(HttpClient.prototype, 'get');
    httpGetSpy.mockReturnValue(of([null]));

    const getResponse = service.getJobs();

    const res = await lastValueFrom(getResponse.pipe(take(1)));
    expect(res).toStrictEqual([undefined]);
  });

  it('should get details of a job', async() => {
    const httpGetSpy = jest.spyOn(HttpClient.prototype, 'get');
    httpGetSpy.mockReturnValue(of(
      {
        id: 16,
        name: 16,
        created: '2025-08-26',
        status: 'waiting',
        owner: 'register@email.com',
        duration: 3.3,
        // eslint-disable-next-line camelcase
        result_filename: 'job-file.txt',
        size: '12K',
        error: ''
      }
    ));

    // eslint-disable-next-line @stylistic/max-len
    const job = new Job(16, 16, new Date('2025-08-26'), 'register@email.com', 'waiting', 3.3, 'job-file.txt', '12K', '');

    const getResponse = service.getJobDetails(16);

    const res = await lastValueFrom(getResponse.pipe(take(1)));
    expect(res).toStrictEqual(job);
  });


  it('should create annotated file download link', () => {
    const url = service.getDownloadJobResultLink(10);
    expect(url).toBe('//localhost:8000/api/jobs/10/file/result');
  });

  it('should create config file download link', () => {
    const url = service.getJobConfigLink(10);
    expect(url).toBe('//localhost:8000/api/jobs/10/file/config');
  });

  it('should create input file download link', () => {
    const url = service.getJobInputDownloadLink(10);
    expect(url).toBe('//localhost:8000/api/jobs/10/file/input');
  });

  it('should get correct class name for waiting status', () => {
    expect(getStatusClassName('waiting')).toBe('waiting-status');
  });

  it('should get correct class name for in progress status', () => {
    expect(getStatusClassName('in progress')).toBe('in-progress-status');
  });

  it('should get correct class name for success status', () => {
    expect(getStatusClassName('success')).toBe('success-status');
  });

  it('should get correct class name for fail status', () => {
    expect(getStatusClassName('failed')).toBe('fail-status');
  });

  it('should return empty string as class name for invalid status', () => {
    expect(getStatusClassName('nonexisting')).toBe('');
  });

  it('should get pipeline list', async() => {
    const httpGetSpy = jest.spyOn(HttpClient.prototype, 'get');
    httpGetSpy.mockReturnValue(of([
      { id: '1', name: 'pipeline1', content: '', type: 'default', status: 'loaded' },
      { id: '2', name: 'pipeline2', content: '', type: 'default', status: 'loaded' },
      { id: '3', name: 'pipeline3', content: '', type: 'default', status: 'loaded' },
    ]));

    const getResponse = service.getAnnotationPipelines();

    expect(httpGetSpy).toHaveBeenCalledWith(
      '//localhost:8000/api/pipelines',
      { withCredentials: true }
    );
    const res = await lastValueFrom(getResponse.pipe(take(1)));
    expect(res).toStrictEqual([
      new Pipeline('1', 'pipeline1', '', 'default', 'loaded'),
      new Pipeline('2', 'pipeline2', '', 'default', 'loaded'),
      new Pipeline('3', 'pipeline3', '', 'default', 'loaded'),
    ]);
  });

  it('should return undefined if json is invalid when getting pipelines', async() => {
    const httpGetSpy = jest.spyOn(HttpClient.prototype, 'get');
    httpGetSpy.mockReturnValue(of(null));

    const getResponse = service.getAnnotationPipelines();

    expect(httpGetSpy).toHaveBeenCalledWith(
      '//localhost:8000/api/pipelines',
      { withCredentials: true }
    );
    const res = await lastValueFrom(getResponse.pipe(take(1)));
    expect(res).toBeUndefined();
  });

  it('should return undefined for each invalid pipeline from response array', async() => {
    const httpGetSpy = jest.spyOn(HttpClient.prototype, 'get');
    httpGetSpy.mockReturnValue(of([
      { id: '1', name: 'pipeline1', content: '', type: 'default', status: 'loaded' },
      null
    ]));

    const getResponse = service.getAnnotationPipelines();

    expect(httpGetSpy).toHaveBeenCalledWith(
      '//localhost:8000/api/pipelines',
      { withCredentials: true }
    );
    const res = await lastValueFrom(getResponse.pipe(take(1)));
    expect(res).toStrictEqual([
      new Pipeline('1', 'pipeline1', '', 'default', 'loaded'),
      undefined
    ]);
  });

  it('should delete job', () => {
    const httpDeleteSpy = jest.spyOn(HttpClient.prototype, 'delete');
    httpDeleteSpy.mockReturnValue(of({}));

    const mockCookie = 'csrftoken=mockToken';
    document.cookie = mockCookie;

    service.deleteJob(10);
    const options = { headers: {'X-CSRFToken': 'mockToken' }, withCredentials: true };
    expect(httpDeleteSpy).toHaveBeenCalledWith(
      '//localhost:8000/api/jobs/10',
      options
    );
  });

  it('should send file and get part of file content and separator', async() => {
    const mockPreview = {
      separator: '\t',
      columns: [
        'CHROM',
        'POS',
        'REF',
        'ALT'
      ],
      preview: [
        {
          CHROM: 'chr1',
          POS: '151405427',
          REF: 'T',
          ALT: 'TCGTCATCA'
        }
      ]
    };
    const httpPostSpy = jest.spyOn(HttpClient.prototype, 'post');
    httpPostSpy.mockReturnValue(of(mockPreview));

    const postResult = service.createFilePreview(new File(['mockData'], 'mockInput.tsv'));

    const res = await lastValueFrom(postResult.pipe(take(1)));
    expect(res).toStrictEqual(
      new FileContent('\t', ['CHROM', 'POS', 'REF', 'ALT'], [['chr1', '151405427', 'T', 'TCGTCATCA']]));
  });
});
