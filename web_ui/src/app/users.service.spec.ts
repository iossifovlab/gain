import { TestBed } from '@angular/core/testing';
import { UserData, UsersService } from './users.service';
import { HttpClient, provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { lastValueFrom, of, take } from 'rxjs';
import { CookieService } from 'ngx-cookie-service';
import { Router } from '@angular/router';

class CookieServiceMock {
  public get(): string {
    return 'csrfMockToken';
  }

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public delete(token: string): void { }
}
describe('UsersService', () => {
  let service: UsersService;
  const cookieServiceMock = new CookieServiceMock();
  let router: Router;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        UsersService,
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: CookieService, useValue: cookieServiceMock }
      ]
    });

    router = TestBed.inject(Router);
    service = TestBed.inject(UsersService);
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  it('should create user', async() => {
    const httpPostSpy = jest.spyOn(HttpClient.prototype, 'post');
    httpPostSpy.mockReturnValue(of('mockResponse'));

    const postResult = service.registerUser('mockEmail@email.com', 'mockPassword');
    expect(httpPostSpy).toHaveBeenCalledWith(
      '//localhost:8000/api/register',
      {
        email: 'mockEmail@email.com',
        password: 'mockPassword',
      }
    );

    const res = await lastValueFrom(postResult.pipe(take(1)));
    expect(res).toBe('mockResponse');
  });

  it('should check post request params when login user', () => {
    const mockUserData = { email: 'mockEmail@email.com', loggedIn: false } as UserData;
    const httpPostSpy = jest.spyOn(HttpClient.prototype, 'post');
    httpPostSpy.mockReturnValue(of(mockUserData));

    service.loginUser('mockEmail@email.com', 'mockPassword');
    expect(httpPostSpy).toHaveBeenCalledWith(
      '//localhost:8000/api/login',
      {
        email: 'mockEmail@email.com',
        password: 'mockPassword'
      },
      {
        headers: { 'X-CSRFToken': 'csrfMockToken' },
        withCredentials: true
      }
    );
  });

  it('should store current user in subject after login', async() => {
    const mockUserData = {
      email: 'mockEmail@email.com',
      isAdmin: false,
      loggedIn: true,
      limitations: {
        dailyJobs: 5,
        filesize: '30M',
        todayJobsCount: 4,
        variantCount: 100,
      }
    } as UserData;
    const httpPostSpy = jest.spyOn(HttpClient.prototype, 'post');
    httpPostSpy.mockReturnValue(of(mockUserData));
    const httpGetSpy = jest.spyOn(HttpClient.prototype, 'get');
    httpGetSpy.mockReturnValue(of(mockUserData));

    const postResult = service.loginUser('mockEmail@email.com', 'mockPassword');

    await lastValueFrom(postResult.pipe(take(1)));
    expect(service.userData.value).toStrictEqual(mockUserData);
  });

  it('should check get user data query params', () => {
    const httpGetSpy = jest.spyOn(HttpClient.prototype, 'get');

    service.getUserData();
    expect(httpGetSpy).toHaveBeenCalledWith(
      '//localhost:8000/api/user_info',
      { withCredentials: true }
    );
  });

  it('should check params of logout query', () => {
    const httpGetSpy = jest.spyOn(HttpClient.prototype, 'get');
    httpGetSpy.mockReturnValue(of({}));

    service.logout();

    const headers = { 'X-CSRFToken': 'csrfMockToken' };
    const options = { headers: headers, withCredentials: true };
    expect(httpGetSpy).toHaveBeenCalledWith(
      '//localhost:8000/api/logout',
      options
    );
  });

  it('should redirect to single annotation page after logout', async() => {
    const httpGetSpy = jest.spyOn(HttpClient.prototype, 'get');
    httpGetSpy.mockReturnValue(of({}));

    const navigateSpy = jest.spyOn(router, 'navigate');
    const queryResponse = service.logout();
    await lastValueFrom(queryResponse.pipe(take(1)));
    expect(navigateSpy).toHaveBeenCalledWith(['/single-allele-annotation']);
  });

  it('should delete csrf token from cookies after logout', async() => {
    const httpGetSpy = jest.spyOn(HttpClient.prototype, 'get');
    httpGetSpy.mockReturnValue(of({}));

    const deleteTokenSpy = jest.spyOn(cookieServiceMock, 'delete');
    const queryResponse = service.logout();
    await lastValueFrom(queryResponse.pipe(take(1)));

    expect(deleteTokenSpy).toHaveBeenCalledWith('csrftoken');
  });

  it('should clean current user data after logout', async() => {
    const httpGetSpy = jest.spyOn(HttpClient.prototype, 'get');
    httpGetSpy.mockReturnValue(of({}));

    const queryResponse = service.logout();
    await lastValueFrom(queryResponse.pipe(take(1)));

    expect(service.userData.value).toBeNull();
  });
});
