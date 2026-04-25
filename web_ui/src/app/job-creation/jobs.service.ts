import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { catchError, map, Observable, throwError } from 'rxjs';
import { FileContent, Job } from './jobs';
import { Pipeline } from './pipelines';
import { environment } from '../../../environments/environment';

@Injectable()
export class JobsService {
  private readonly validatePipelineConfigUrl = `${environment.apiPath}/pipelines/validate`;
  private readonly validateColumnsUrl = `${environment.apiPath}/jobs/validate_columns`;
  private readonly jobsUrl = `${environment.apiPath}/jobs`;
  private readonly jobPreviewUrl = `${environment.apiPath}/jobs/preview`;
  private readonly getPipelinesUrl = `${environment.apiPath}/pipelines`;
  private readonly annotateColumnsUrl = `${environment.apiPath}/jobs/annotate_columns`;
  private readonly annotateVcfUrl = `${environment.apiPath}/jobs/annotate_vcf`;

  public constructor(private http: HttpClient) { }

  private getCSRFToken(): string {
    let res = '';
    const value = `; ${document.cookie}`;
    const parts = value.split('; csrftoken=');
    if (parts.length === 2) {
      res = parts.pop().split(';').shift();
    }
    return res;
  }

  public createVcfJob(
    file: File,
    pipeline: string,
    genome: string,
  ): Observable<number> {
    const options = { headers: {'X-CSRFToken': this.getCSRFToken()}, withCredentials: true };
    const formData = new FormData();
    formData.append('data', file);
    if (genome) {
      formData.append('genome', genome);
    }
    formData.append('pipeline_id', pipeline);

    return this.http.post(
      this.annotateVcfUrl,
      formData,
      options
    ).pipe(
      map((response: object) => response['job_id'] as number),
      catchError((err: HttpErrorResponse) => {
        switch (err.status) {
          case 403: return throwError(() => new Error((err.error as {reason: string})['reason']));
          case 413: return throwError(() => new Error('Upload limit reached!'));
          case 400: return throwError(() => new Error((err.error as {reason: string})['reason']));
          default: return throwError(() => new Error('Error occurred!'));
        }
      })
    );
  }

  public createNonVcfJob(
    file: File,
    pipeline: string,
    genome: string,
    fileSeparator: string,
    columns: Map<string, string>
  ): Observable<number> {
    const options = { headers: {'X-CSRFToken': this.getCSRFToken()}, withCredentials: true };
    const formData = new FormData();
    formData.append('data', file);
    if (genome) {
      formData.append('genome', genome);
    }

    if (fileSeparator) {
      formData.append('separator', fileSeparator);
    }
    formData.append('pipeline_id', pipeline);

    formData.append('col_chrom', columns.get('chrom') || '-');
    formData.append('col_pos', columns.get('pos') || '-');
    formData.append('col_ref', columns.get('ref') || '-');
    formData.append('col_alt', columns.get('alt') || '-');
    formData.append('col_pos_beg', columns.get('position_begin') || '-');
    formData.append('col_pos_end', columns.get('position_end') || '-');
    formData.append('col_cnv_type', columns.get('cnv_type') || '-');
    formData.append('col_vcf_like', columns.get('vcf_like') || '-');
    formData.append('col_variant', columns.get('variant') || '-');
    formData.append('col_location', columns.get('location') || '-');
    return this.http.post(
      this.annotateColumnsUrl,
      formData,
      options
    ).pipe(
      map((response: object) => response['job_id'] as number),
      catchError((err: HttpErrorResponse) => {
        switch (err.status) {
          case 403: return throwError(() => new Error((err.error as {reason: string})['reason']));
          case 413: return throwError(() => new Error('Upload limit reached!'));
          case 400: return throwError(() => new Error((err.error as {reason: string})['reason']));
          default: return throwError(() => new Error('Error occurred!'));
        }
      })
    );
  }

  public createFilePreview(file: File) : Observable<FileContent> {
    const options = { headers: {'X-CSRFToken': this.getCSRFToken()}, withCredentials: true };
    const formData = new FormData();
    formData.append('data', file);
    return this.http.post(
      this.jobPreviewUrl,
      formData,
      options
    ).pipe(
      map((response: object) => FileContent.fromJson(response))
    );
  }

  public submitSeparator(file: File, separator: string) : Observable<FileContent> {
    const options = { headers: {'X-CSRFToken': this.getCSRFToken()}, withCredentials: true };
    const formData = new FormData();
    formData.append('data', file);
    formData.append('separator', separator);
    return this.http.post(
      this.jobPreviewUrl,
      formData,
      options
    ).pipe(
      map((response: object) => FileContent.fromJson(response))
    );
  }

  public getJobs(): Observable<Job[]> {
    const options = { headers: {'X-CSRFToken': this.getCSRFToken()}, withCredentials: true };
    return this.http.get<object[]>(
      this.jobsUrl,
      options
    ).pipe(map((response: object[]) => Job.fromJsonArray(response)));
  }

  public getJobDetails(jobId: number): Observable<Job> {
    const options = { headers: {'X-CSRFToken': this.getCSRFToken()}, withCredentials: true };
    return this.http.get(
      this.jobsUrl + '/' + jobId,
      options
    ).pipe(map((response: object) => Job.fromJson(response)));
  }

  public getFileData(jobId: number): Observable<FileContent> {
    const options = { headers: {'X-CSRFToken': this.getCSRFToken()}, withCredentials: true };
    return this.http.get(
      this.jobsUrl + '/' + jobId,
      options
    ).pipe(map((response: object) => FileContent.fromJson({
      /* eslint-disable @typescript-eslint/no-unsafe-assignment */
      id: response['id'],
      columns: response['columns'],
      head: response['head']
      /* eslint-enable */
    })));
  }


  public validatePipelineConfig(config: string): Observable<string> {
    const options = { headers: {'X-CSRFToken': this.getCSRFToken()}, withCredentials: true };

    return this.http.post(
      this.validatePipelineConfigUrl,
      {config: config},
      options
    ).pipe(
      map((response: object) => {
        return response['errors'] as string;
      }),
    );
  }

  public validateColumnSpecification(
    fileHeader: string[],
    columnSpecification: Map<string, string>,
  ): Observable<[string, string]> {
    const options = { headers: {'X-CSRFToken': this.getCSRFToken()}, withCredentials: true };
    const columnsSpec = new Map<string, string>;

    columnsSpec.set('col_chrom', columnSpecification.get('chrom') || '-');
    columnsSpec.set('col_pos', columnSpecification.get('pos') || '-');
    columnsSpec.set('col_ref', columnSpecification.get('ref') || '-');
    columnsSpec.set('col_alt', columnSpecification.get('alt') || '-');
    columnsSpec.set('col_pos_beg', columnSpecification.get('position_begin') || '-');
    columnsSpec.set('col_pos_end', columnSpecification.get('position_end') || '-');
    columnsSpec.set('col_cnv_type', columnSpecification.get('cnv_type') || '-');
    columnsSpec.set('col_vcf_like', columnSpecification.get('vcf_like') || '-');
    columnsSpec.set('col_variant', columnSpecification.get('variant') || '-');
    columnsSpec.set('col_location', columnSpecification.get('location') || '-');
    return this.http.post(
      this.validateColumnsUrl,
      {
        /* eslint-disable */
        file_columns: fileHeader,
        column_mapping: Object.fromEntries(columnsSpec),
        /* eslint-enable */
      },
      options
    ).pipe(
      map((response: object) => {
        return [response['annotatable'] as string, response['errors'] as string];
      }),
    );
  }

  public getDownloadJobResultLink(jobId: number): string {
    return `${environment.apiPath}/jobs/${jobId}/file/result`;
  }

  public getJobInputDownloadLink(jobId: number): string {
    return `${environment.apiPath}/jobs/${jobId}/file/input`;
  }

  public getJobConfigLink(jobId: number): string {
    return `${environment.apiPath}/jobs/${jobId}/file/config`;
  }

  public getAnnotationPipelines(): Observable<Pipeline[]> {
    return this.http.get<object[]>(
      this.getPipelinesUrl,
      { withCredentials: true }
    ).pipe(map((response: object[]) => Pipeline.fromJsonArray(response)));
  }

  public deleteJob(jobId: number): Observable<object> {
    const options = { headers: {'X-CSRFToken': this.getCSRFToken()}, withCredentials: true };
    return this.http.delete(
      `${this.jobsUrl}/${jobId}`,
      options
    );
  }
}
