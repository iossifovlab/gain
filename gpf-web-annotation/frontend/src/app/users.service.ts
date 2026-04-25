import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Router } from '@angular/router';
import { CookieService } from 'ngx-cookie-service';
import { BehaviorSubject, Observable, map, switchMap, take, tap } from 'rxjs';
import { environment } from '../../environments/environment';

export interface UserData {
  email: string;
  loggedIn: boolean;
  isAdmin: boolean;
  limitations: {
    dailyJobs: number;
    filesize: string;
    todayJobsCount: number;
    variantCount: number;
    diskSpace: string;
  }
}

@Injectable()
export class UsersService {
  private readonly registerUrl = `${environment.apiPath}/register`;
  private readonly loginUrl = `${environment.apiPath}/login`;
  private readonly logoutUrl = `${environment.apiPath}/logout`;
  private readonly userDataUrl = `${environment.apiPath}/user_info`;
  public userData = new BehaviorSubject<UserData>(null);

  public constructor(
    private http: HttpClient,
    private cookieService: CookieService,
    private router: Router,
  ) { }

  public logout(): Observable<object> {
    const csrfToken = this.cookieService.get('csrftoken');
    const headers = { 'X-CSRFToken': csrfToken };
    const options = { headers: headers, withCredentials: true };

    return this.http.get(this.logoutUrl, options).pipe(
      take(1),
      tap(() => {
        this.cookieService.delete('csrftoken');
        this.userData.next(null);
        this.router.navigate(['/single-allele-annotation']);
      })
    );
  }

  public refreshUserData(): void {
    this.getUserData().pipe(take(1)).subscribe(userData => {
      this.userData.next(userData);
    });
  }

  public getUserData(): Observable<UserData> {
    const options = { withCredentials: true };
    return this.http.get<UserData>(this.userDataUrl, options);
  }

  public registerUser(email: string, password: string): Observable<object> {
    return this.http.post(
      this.registerUrl,
      {
        email: email,
        password: password,
      },
    );
  }

  public loginUser(email: string, password: string): Observable<void> {
    const csrfToken = this.cookieService.get('csrftoken');
    const headers = { 'X-CSRFToken': csrfToken };
    const options = { headers: headers, withCredentials: true };
    return this.http.post<UserData>(
      this.loginUrl,
      {
        email: email,
        password: password
      },
      options,
    ).pipe(
      switchMap(() => this.getUserData()),
      map((userData: UserData) => {
        this.userData.next(userData);
      })
    );
  }
}
