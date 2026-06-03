import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs/internal/Observable';
import { environment } from '../../../environments/environment';

@Injectable({
  providedIn: 'root',
})
export class AboutPageService {
  private readonly getContentUrl = `${environment.apiPath}/about`;
  private readonly getVersionUrl = `${environment.apiPath}/version`;

  public constructor(private http: HttpClient) {}

  public getContent(): Observable<string> {
    return this.http.get<string>(this.getContentUrl);
  }

  public getVersion(): Observable<{version: string}> {
    return this.http.get<{version: string}>(this.getVersionUrl);
  }
}
