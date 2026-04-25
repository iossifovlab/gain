import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs/internal/Observable';
import { environment } from '../../../environments/environment';

@Injectable({
  providedIn: 'root',
})
export class AboutPageService {
  private readonly getContentUrl = `${environment.apiPath}/about`;

  public constructor(private http: HttpClient) {}

  public getContent(): Observable<string> {
    return this.http.get<string>(this.getContentUrl);
  }
}
