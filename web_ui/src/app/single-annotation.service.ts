import { Injectable, inject } from '@angular/core';
import { catchError, map, Observable, throwError } from 'rxjs';
import { environment } from '../../environments/environment';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import {
  AnnotatableHistory,
  CategoricalHistogram,
  NumberHistogram,
  SingleAnnotationReport,
  Annotatable
} from './single-annotation';


@Injectable()
export class SingleAnnotationService {
  private readonly getReportUrl = `${environment.apiPath}/single_allele/annotate`;
  private readonly getGenomesUrl = `${environment.apiPath}/jobs/genomes`;
  private readonly annotatablesHistoryUrl = `${environment.apiPath}/single_allele/history`;
  private readonly getHistogramUrl = `${environment.apiPath}/single_allele`;
  private readonly updateNoteUrl = `${environment.apiPath}/single_allele/note`;
  private readonly http: HttpClient = inject(HttpClient);

  private getCSRFToken(): string {
    let res = '';
    const value = `; ${document.cookie}`;
    const parts = value.split('; csrftoken=');
    if (parts.length === 2) {
      res = parts.pop().split(';').shift();
    }
    return res;
  }

  public getReport(annotatable: Annotatable, pipeline: string): Observable<SingleAnnotationReport> {
    const annotatableJson = {
      chrom: annotatable.chromosome,
      pos: annotatable.position || undefined,
      ref: annotatable.reference || undefined,
      alt: annotatable.alternative || undefined,
      // eslint-disable-next-line camelcase
      pos_beg: annotatable.positionStart || undefined,
      // eslint-disable-next-line camelcase
      pos_end: annotatable.positionEnd || undefined
    };

    const userToken = this.getCSRFToken();
    const options = { withCredentials: true };
    if (userToken) {
      options['headers'] = {'X-CSRFToken': userToken};
    }

    return this.http.post<object>(
      this.getReportUrl,
      {
        annotatable: annotatableJson,
        // eslint-disable-next-line camelcase
        pipeline_id: pipeline,
      },
      options
    ).pipe(map((response: object) => SingleAnnotationReport.fromJson(response)),
      catchError((err: HttpErrorResponse) => {
        switch (err.status) {
          case 429: return throwError(() => new Error((err.error as {reason: string})['reason']));
          default: return throwError(() => new Error('Error occurred!'));
        }
      }));
  }

  public getHistogram(histogramUrl: string): Observable<NumberHistogram | CategoricalHistogram> {
    return this.http.get<object>(
      `${this.getHistogramUrl}/${histogramUrl}`,
    ).pipe(map((response: object) => {
      // eslint-disable-next-line @typescript-eslint/no-unsafe-member-access
      return response['config']['type'] === 'number' ?
        NumberHistogram.fromJson(response) : CategoricalHistogram.fromJson(response);
    }));
  }

  public getGenomes(): Observable<string[]> {
    return this.http.get<string[]>(this.getGenomesUrl);
  }

  public getAnnotatablesHistory(): Observable<AnnotatableHistory[]> {
    const options = { headers: {'X-CSRFToken': this.getCSRFToken()}, withCredentials: true };
    return this.http.get<AnnotatableHistory[]>(
      this.annotatablesHistoryUrl,
      options
    ).pipe(map((rawAnnotatables: object[]) => AnnotatableHistory.fromJsonArray(rawAnnotatables)));
  }

  public deleteAnnotatable(annotatableId: number): Observable<object> {
    const options = { headers: {'X-CSRFToken': this.getCSRFToken()}, withCredentials: true };
    return this.http.delete(`${this.annotatablesHistoryUrl}?id=${annotatableId}`, options);
  }

  public updateNote(allele: string, note: string): Observable<object> {
    const options = { headers: {'X-CSRFToken': this.getCSRFToken()}, withCredentials: true };
    return this.http.post(this.updateNoteUrl, { allele: allele, note: note }, options);
  }
}
