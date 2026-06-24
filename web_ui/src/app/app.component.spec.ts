import { ComponentFixture, TestBed } from '@angular/core/testing';
import { AppComponent } from './app.component';
import { UsersService } from './users.service';
import { UserData } from './users';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { provideHttpClient } from '@angular/common/http';
import { BehaviorSubject, Observable, of } from 'rxjs';
import { provideRouter, Router } from '@angular/router';

class UsersServiceMock {
  public userData = new BehaviorSubject<UserData>(null);
  public logout(): Observable<object> {
    return of({});
  }

  public refreshUserData(): void { }
}
describe('AppComponent', () => {
  let component: AppComponent;
  let fixture: ComponentFixture<AppComponent>;
  const usersServiceMock = new UsersServiceMock();
  let router: Router;

  beforeEach(async() => {
    await TestBed.configureTestingModule({
      imports: [AppComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
      ],
    }).compileComponents();

    TestBed.overrideProvider(UsersService, {useValue: usersServiceMock});

    fixture = TestBed.createComponent(AppComponent);
    router = TestBed.inject(Router);
    component = fixture.componentInstance;

    jest.resetAllMocks();
    jest.clearAllMocks();
  });

  it('should create the app', () => {
    expect(component).toBeTruthy();
  });

  it('should trigger refresh user data on component init', () => {
    const refreshUserDataSpy = jest.spyOn(usersServiceMock, 'refreshUserData');
    component.ngOnInit();
    expect(refreshUserDataSpy).toHaveBeenCalledWith();
  });

  it('should set current user to null when logout', () => {
    component.currentUserData = {
      email: 'mockEmail@email.com',
      isAdmin: false,
      loggedIn: true,
      limitations:
      {
        dailyJobs: 100,
        filesize: '1GB',
        todayJobsCount: 5,
        diskSpace: '10GB'
      }
    };
    const logoutSpy = jest.spyOn(usersServiceMock, 'logout');
    component.logout();
    expect(logoutSpy).toHaveBeenCalledWith();
    expect(component.currentUserData).toBeNull();
  });

  it('should get last logged in user from service', () => {
    const mockUserData = {
      email: 'mockEmail@email.com',
      isAdmin: false,
      loggedIn: true,
      limitations:
      {
        dailyJobs: 100,
        filesize: '1GB',
        todayJobsCount: 5,
        diskSpace: '10GB'
      }
    };
    component.ngOnInit();
    component.currentUserData = null;
    usersServiceMock.userData.next(mockUserData);
    component.ngDoCheck();
    expect(component.currentUserData).toStrictEqual(mockUserData);
  });

  it('should hide app header if login or register page is loaded', () => {
    jest.spyOn(router, 'url', 'get').mockReturnValue('/login');
    component.isAppHeaderVisible();
    expect(component.isAppHeaderVisible()).toBe(false);

    jest.spyOn(router, 'url', 'get').mockReturnValue('/register');
    component.isAppHeaderVisible();
    expect(component.isAppHeaderVisible()).toBe(false);

    jest.spyOn(router, 'url', 'get').mockReturnValue('/single-annotation');
    component.isAppHeaderVisible();
    expect(component.isAppHeaderVisible()).toBe(true);
  });

  it('should navigate to login page on login button click', () => {
    const navigateSpy = jest.spyOn(router, 'navigate');
    component.login();
    expect(navigateSpy).toHaveBeenCalledWith(['/login']);
  });

  it('should call refreshUserData after logout completes', () => {
    const reloadSpy = jest.fn();
    const originalLocation = window.location;
    delete (window as any).location;
    (window as any).location = { reload: reloadSpy };
    jest.spyOn(usersServiceMock, 'logout').mockReturnValue(of({}));
    const refreshUserDataSpy = jest.spyOn(usersServiceMock, 'refreshUserData');
    component.logout();
    expect(refreshUserDataSpy).toHaveBeenCalledWith();
    (window as any).location = originalLocation;
  });
});
