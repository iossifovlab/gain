import { Injectable } from '@angular/core';
import { environment } from '../../environments/environment';
import { HttpClient } from '@angular/common/http';
import { map, Observable } from 'rxjs';
import { PipelineInfo } from './annotation-pipeline';

@Injectable({
  providedIn: 'root'
})
export class AnnotationPipelineService {
  private readonly pipelineUrl = `${environment.apiPath}/pipelines/user`;
  private readonly getPipelineStatus = `${environment.apiPath}/editor/pipeline_status`;

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
      map((response: object) => response['id'] as string)
    );
  }

  public deletePipeline(id: string): Observable<object> {
    const options = { headers: {'X-CSRFToken': this.getCSRFToken()}, withCredentials: true };
    return this.http.delete(
      `${this.pipelineUrl}?id=${id}`,
      options
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
    const options = { headers: {'X-CSRFToken': this.getCSRFToken()}, withCredentials: true };
    return this.http.get<PipelineInfo>(
      `${this.getPipelineStatus}?pipeline_id=${id}`,
      options
    ).pipe(
      map(response => PipelineInfo.fromJson(response))
    );
  }
}
