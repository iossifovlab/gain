import { Injectable } from '@angular/core';
import { environment } from '../../environments/environment';
import { HttpClient } from '@angular/common/http';
import { map, Observable, shareReplay } from 'rxjs';
import { PipelineInfo } from './annotation-pipeline';
import { AnnotationPipelineStateService } from './annotation-pipeline/annotation-pipeline-state.service';

@Injectable({
  providedIn: 'root'
})
export class AnnotationPipelineService {
  private readonly pipelineUrl = `${environment.apiPath}/pipelines/user`;
  private readonly getPipelineStatus = `${environment.apiPath}/editor/pipeline_status`;
  private readonly annotateDocumentationUrl = `${environment.apiPath}/pipelines/doc`;

  public constructor(
    private http: HttpClient,
    private stateService: AnnotationPipelineStateService
  ) { }

  private getCSRFToken(): string {
    let res = '';
    const value = `; ${document.cookie}`;
    const parts = value.split('; csrftoken=');
    if (parts.length === 2) {
      res = parts.pop().split(';').shift();
    }
    return res;
  }

  public savePipeline(id: string, name: string, config: string): Observable<string> {
    const options = { headers: {'X-CSRFToken': this.getCSRFToken()}, withCredentials: true };
    const formData = new FormData();
    const configFile = new File([config], 'config.yml');
    formData.append('id', id);
    formData.append('name', name);
    formData.append('config', configFile);

    return this.http.post(
      this.pipelineUrl,
      formData,
      options
    ).pipe(
      map((response: object) => {
        const pipelineId = response['id'] as string;
        this.stateService.clearPipelineInfoCache(pipelineId || id);
        return pipelineId;
      })
    );
  }

  public deletePipeline(id: string): Observable<object> {
    const options = { headers: {'X-CSRFToken': this.getCSRFToken()}, withCredentials: true };
    return this.http.delete(
      `${this.pipelineUrl}?id=${id}`,
      options
    ).pipe(
      map((response) => {
        this.stateService.clearPipelineInfoCache(id);
        return response;
      })
    );
  }

  public loadPipeline(id: string): Observable<void> {
    const options = { headers: {'X-CSRFToken': this.getCSRFToken()}, withCredentials: true };
    return this.http.post<void>(
      `${environment.apiPath}/pipelines/load`,
      {id: id},
      options
    );
  }

  public getPipelineInfo(id: string): Observable<PipelineInfo> {
    let request$ = this.stateService.getPendingPipelineInfoRequest(id);
    if (!request$) {
      const options = { headers: {'X-CSRFToken': this.getCSRFToken()}, withCredentials: true };
      request$ = this.http.get<PipelineInfo>(
        `${this.getPipelineStatus}?pipeline_id=${id}`,
        options
      ).pipe(
        map(response => PipelineInfo.fromJson(response)),
        shareReplay(1)
      );
      this.stateService.setPendingPipelineInfoRequest(id, request$);
    }
    return request$;
  }

  public getDownloadAnnotateDocumentationUrl(id: string): string {
    return `${this.annotateDocumentationUrl}?pipeline_id=${id}`;
  }

  public invalidateCache(id: string): void {
    this.stateService.clearPipelineInfoCache(id);
  }
}
