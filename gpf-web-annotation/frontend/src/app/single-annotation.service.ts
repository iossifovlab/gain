import { Injectable } from '@angular/core';
import { map, Observable } from 'rxjs';
import { environment } from '../../environments/environment';
import { HttpClient } from '@angular/common/http';
import {
  Allele,
  CategoricalHistogram,
  NumberHistogram,
  SingleAnnotationReport,
  Annotatable
} from './single-annotation';


@Injectable()
export class SingleAnnotationService {
  private readonly getReportUrl = `${environment.apiPath}/single_allele/annotate`;
  private readonly getGenomesUrl = `${environment.apiPath}/jobs/genomes`;
  private readonly allelesHistoryUrl = `${environment.apiPath}/single_allele/history`;
  private readonly getHistogramUrl = `${environment.apiPath}/single_allele`;
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
    ).pipe(map((response: object) => SingleAnnotationReport.fromJson(response)));
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

  public getAllelesHistory(): Observable<Allele[]> {
    const options = { headers: {'X-CSRFToken': this.getCSRFToken()}, withCredentials: true };
    return this.http.get<Allele[]>(
      this.allelesHistoryUrl,
      options
    ).pipe(map((rawAlleles: object[]) => Allele.fromJsonArray(rawAlleles)));
  }

  public deleteAllele(alleleId: number): Observable<object> {
    const options = { headers: {'X-CSRFToken': this.getCSRFToken()}, withCredentials: true };
    return this.http.delete(`${this.allelesHistoryUrl}?id=${alleleId}`, options);
  }
}
