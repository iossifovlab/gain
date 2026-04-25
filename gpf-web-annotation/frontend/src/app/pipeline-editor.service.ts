import { Injectable } from '@angular/core';
import { environment } from '../../environments/environment';
import { HttpClient, HttpErrorResponse, HttpParams } from '@angular/common/http';
import { catchError, forkJoin, map, Observable, of, switchMap, tap, throwError } from 'rxjs';
import {
  AnnotatorConfig,
  AttributeData,
  AttributePage,
  ResourceAnnotatorConfigs,
  ResourcePage
} from './new-annotator/annotator';

@Injectable({
  providedIn: 'root',
})
export class PipelineEditorService {
  private getAnnotatorsUrl = `${environment.apiPath}/editor/annotator_types`;
  private getAnnotatorConfigUrl = `${environment.apiPath}/editor/annotator_config`;
  private getResourcesUrl = `${environment.apiPath}/resources`;
  private getAttributesUrl = `${environment.apiPath}/editor/annotator_attributes`;
  private getAnnotatorYmlUrl = `${environment.apiPath}/editor/annotator_yaml`;
  private getPipelineAttributesUrl = `${environment.apiPath}/editor/pipeline_attributes`;
  private getResourceTypesUrl = `${environment.apiPath}/resources/types`;
  private getResourceAnnotatorsUrl = `${environment.apiPath}/editor/resource_annotators`;

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

  public getAnnotators(): Observable<string[]> {
    return this.http.get<string[]>(this.getAnnotatorsUrl);
  }

  public getAnnotatorConfig(annotator: string, resourcesJsonString?: string): Observable<AnnotatorConfig> {
    const options = { headers: {'X-CSRFToken': this.getCSRFToken()}, withCredentials: true };
    // eslint-disable-next-line camelcase
    const body = { annotator_type: annotator };
    if (resourcesJsonString) {
      Object.assign(body, JSON.parse(resourcesJsonString));
    }

    return this.http.post(
      this.getAnnotatorConfigUrl,
      body,
      options
    ).pipe(
      map((response: object) => AnnotatorConfig.fromJson(response)),
      switchMap(config => {
        const resourceObservables = config.resources.filter(r => r.fieldType === 'resource')
          .map(r =>
            this.getResources(r.resourceType).pipe(
              tap(values => {
                r.possibleValues = values;
              })
            )
          );

        if (resourceObservables.length === 0) {
          return of(config);
        }

        return forkJoin(resourceObservables).pipe(map(() => config));
      })
    );
  }


  public getResources(annotatorType: string): Observable<string[]> {
    const options = { headers: {'X-CSRFToken': this.getCSRFToken()}, withCredentials: true };
    return this.http.get<string[]>(
      `${this.getResourcesUrl}?type=${annotatorType}`,
      options
    );
  }

  public getPipelineAttributes(
    pipelineId: string,
    attributeType: string,
  ): Observable<string[]> {
    const options = { headers: {'X-CSRFToken': this.getCSRFToken()}, withCredentials: true };
    return this.http.get<string[]>(
      `${this.getPipelineAttributesUrl}?pipeline_id=${pipelineId}&attribute_type=${attributeType}`,
      options
    );
  }

  public getAttributes(
    pipelineId: string,
    annotatorType: string,
    resources: object,
    searchValue?: string
  ): Observable<AttributePage> {
    const options = { headers: {'X-CSRFToken': this.getCSRFToken()}, withCredentials: true };

    const body = {
      // eslint-disable-next-line camelcase
      pipeline_id: pipelineId,
      // eslint-disable-next-line camelcase
      annotator_type: annotatorType,
      search: searchValue
    };

    return this.http.post<AttributePage>(
      this.getAttributesUrl,
      Object.assign(body, resources),
      options
    ).pipe(
      map((response: object) => AttributePage.fromJson(response)),
      catchError((err: HttpErrorResponse) => {
        switch (err.status) {
          case 400: return throwError(() => new Error((err.error as {error: string})['error']));
          default: return throwError(() => new Error('Error occurred!'));
        }
      })
    );
  }

  public getAnnotatorYml(
    pipelineId: string,
    annotatorType: string,
    resources: object,
    attributes: AttributeData[],
  ): Observable<string> {
    const options = { headers: {'X-CSRFToken': this.getCSRFToken()}, withCredentials: true };

    const extractedAttributes = attributes.map(({ name, source, internal }) => ({
      name: name,
      source: source,
      internal: internal
    }));

    const body = {
      // eslint-disable-next-line camelcase
      pipeline_id: pipelineId,
      // eslint-disable-next-line camelcase
      attributes: extractedAttributes,
      // eslint-disable-next-line camelcase
      annotator_type: annotatorType,
    };
    return this.http.post<string>(
      this.getAnnotatorYmlUrl,
      Object.assign(body, resources),
      options
    ).pipe(
      catchError((err: HttpErrorResponse) => {
        switch (err.status) {
          case 400: return throwError(() => new Error((err.error as {error: string})['error']));
          default: return throwError(() => new Error('Error occurred!'));
        }
      }));
  }

  public getResourceTypes(): Observable<string[]> {
    return this.http.get<string[]>(this.getResourceTypesUrl);
  }

  public getResourcesBySearch(value: string, type: string, page?: number): Observable<ResourcePage> {
    let params = new HttpParams();
    if (type && type !== 'All') {
      params = params.set('type', type);
    }
    if (value !== '') {
      params = params.set('search', value);
    }
    if (page) {
      params = params.set('page', page);
    }
    return this.http.get<ResourcePage>(`${this.getResourcesUrl}/search`, { params: params })
      .pipe(
        map((response) => ResourcePage.fromJson(response)),
        catchError((err: HttpErrorResponse) => {
          switch (err.status) {
            case 500: return throwError(() => new Error('Invalid search value'));
            default: return throwError(() => new Error('Error occurred!'));
          }
        }
        )
      );
  }

  public getResourceAnnotators(resourceId: string): Observable<ResourceAnnotatorConfigs> {
    return this.http.get<ResourceAnnotatorConfigs[]>(`${this.getResourceAnnotatorsUrl}?resource_id=${resourceId}`)
      .pipe(map((response) => ResourceAnnotatorConfigs.fromJson(response)));
  }

  public getPipelineAttributesNames(
    pipelineId: string,
    attributeType?: string,
  ): Observable<string[]> {
    const options = { headers: {'X-CSRFToken': this.getCSRFToken()}, withCredentials: true };

    let url = `${this.getPipelineAttributesUrl}?pipeline_id=${pipelineId}`;
    if (attributeType) {
      url += `&attribute_type=${attributeType}`;
    }
    return this.http.get<string[]>(
      url,
      options
    );
  }
}
