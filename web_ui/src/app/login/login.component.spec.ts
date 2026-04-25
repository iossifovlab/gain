import { ComponentFixture, TestBed } from '@angular/core/testing';

import { LoginComponent } from './login.component';
import { HttpErrorResponse, provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { UserData, UsersService } from '../users.service';
import { BehaviorSubject, Observable, of, throwError } from 'rxjs';
import { provideRouter, Router } from '@angular/router';

class UsersServiceMock {
  public userData = new BehaviorSubject<UserData>(null);
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public loginUser(email: string, password: string): Observable<object> {
    return of({ email: email, isAdmin: false, loggedIn: true });
  }

  public isUserLoggedIn(): boolean {
    return true;
  }
}

describe('LoginComponent', () => {
  let component: LoginComponent;
  let fixture: ComponentFixture<LoginComponent>;
  const usersServiceMock = new UsersServiceMock();
  let emailInput: HTMLInputElement;
  let passwordInput: HTMLInputElement;
  let templateRef: HTMLElement;
  let router: Router;

  beforeEach(async() => {
    await TestBed.configureTestingModule({
      imports: [LoginComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([])
      ]
    }).compileComponents();

    TestBed.overrideProvider(UsersService, {useValue: usersServiceMock});
    router = TestBed.inject(Router);

    fixture = TestBed.createComponent(LoginComponent);
    component = fixture.componentInstance;

    templateRef = fixture.debugElement.nativeElement as HTMLElement;
    emailInput = templateRef.querySelector('#email');
    passwordInput = templateRef.querySelector('#password');

    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should successfully login user and navigate to jobs table page', () => {
    emailInput.value = 'mockEmail@email.com';
    passwordInput.value = 'mockPassword';
    const loginSpy = jest.spyOn(usersServiceMock, 'loginUser');
    const navigateSpy = jest.spyOn(router, 'navigate');

    component.login();
    expect(loginSpy).toHaveBeenCalledWith('mockEmail@email.com', 'mockPassword');
    expect(navigateSpy).toHaveBeenCalledWith(['/single-allele-annotation']);
  });

  it('should clear inputs when login is successful', () => {
    emailInput.value = 'mockEmail@email.com';
    passwordInput.value = 'mockPassword';

    component.login();
    expect(emailInput.value).toBe('');
    expect(passwordInput.value).toBe('');
  });

  it('should show error message from response when fail to login', () => {
    emailInput.value = 'mockEmail@email.com';
    passwordInput.value = 'mockPassword';

    const errorMock = new HttpErrorResponse({error: {error: 'Invalid email or password!'}});
    jest.spyOn(usersServiceMock, 'loginUser')
      .mockReturnValue(throwError(() => errorMock));

    component.login();
    expect(component.responseMessage).toBe('Invalid email or password!');
  });

  it('should show default error message when response doesnt\'t contain one', () => {
    emailInput.value = 'mockEmail@email.com';
    passwordInput.value = 'mockPassword';

    const errorMock = new HttpErrorResponse({error: {}});
    jest.spyOn(usersServiceMock, 'loginUser')
      .mockReturnValue(throwError(() => errorMock));

    component.login();
    expect(component.responseMessage).toBe('Login failed!');
  });

  it('should not clear input data when fail to login', () => {
    emailInput.value = 'mockEmail@email.com';
    passwordInput.value = 'mockPassword';
    jest.spyOn(usersServiceMock, 'loginUser')
      .mockReturnValue(throwError(() => new Error()));

    component.login();
    expect(emailInput.value).toBe('mockEmail@email.com');
    expect(passwordInput.value).toBe('mockPassword');
  });
});
