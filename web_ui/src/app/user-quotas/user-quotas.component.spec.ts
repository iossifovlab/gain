import { ComponentFixture, TestBed } from '@angular/core/testing';
import { UserQuotasComponent } from './user-quotas.component';
import { UsersService } from '../users.service';
import { BehaviorSubject, Observable, of } from 'rxjs';
import { RateLimits, UserData } from '../users';

const mockRateLimits: RateLimits = {
  jobs: {
    daily: { current: 3, max: 10 },
    monthly: { current: 15, max: 100 },
    extra: 0,
  }
};

const mockLoggedInUser: UserData = {
  email: 'test@example.com',
  loggedIn: true,
  isAdmin: false,
  limitations: {
    dailyJobs: 10,
    filesize: '10MB',
    todayJobsCount: 3,
    diskSpace: '1GB'
  }
};

class MockUsersService {
  public userData = new BehaviorSubject<UserData>(mockLoggedInUser);

  public getQuotas(): Observable<RateLimits> {
    return of(mockRateLimits);
  }
}

describe('UserQuotasComponent', () => {
  let component: UserQuotasComponent;
  let fixture: ComponentFixture<UserQuotasComponent>;
  let mockUsersService: MockUsersService;

  beforeEach(async() => {
    mockUsersService = new MockUsersService();

    await TestBed.configureTestingModule({
      imports: [UserQuotasComponent],
      providers: [
        { provide: UsersService, useValue: mockUsersService }
      ]
    }).compileComponents();

    fixture = TestBed.createComponent(UserQuotasComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should call getQuotas on init', () => {
    const getQuotasSpy = jest.spyOn(mockUsersService, 'getQuotas');
    component.ngOnInit();
    expect(getQuotasSpy).toHaveBeenCalledWith();
    expect(component.quotas).toStrictEqual(mockRateLimits);
  });

  it('should set isUserLoggedIn to true when user is logged in', () => {
    expect(component.isUserLoggedIn).toBe(true);
  });

  it('should set isUserLoggedIn to false when user is not logged in', () => {
    mockUsersService.userData.next({ ...mockLoggedInUser, loggedIn: false });
    fixture.detectChanges();
    expect(component.isUserLoggedIn).toBe(false);
  });

  it('should display correct daily current and max values', () => {
    const cells = (fixture.nativeElement as HTMLElement).querySelectorAll('.cell:not(.header):not(.corner)');
    expect(cells[0].textContent.trim()).toBe('3');
    expect(cells[1].textContent.trim()).toBe('10');
  });

  it('should display correct monthly current and max values', () => {
    const cells = (fixture.nativeElement as HTMLElement).querySelectorAll('.cell:not(.header):not(.corner)');
    expect(cells[2].textContent.trim()).toBe('15');
    expect(cells[3].textContent.trim()).toBe('100');
  });

  it('should show the extra row when user is logged in', () => {
    const extraCell = (fixture.nativeElement as HTMLElement).querySelectorAll('.cell.extra');
    expect(extraCell).not.toBeNull();
    expect(extraCell[0].textContent.trim()).toBe('0');
    expect(extraCell[1].textContent.trim()).toBe('0');
  });

  it('should display \'-\' for daily quotas', () => {
    jest.spyOn(mockUsersService, 'getQuotas').mockReturnValue(of({
      jobs: {
        daily: { current: 3, max: 10 },
        monthly: { current: 15, max: 100 },
        extra: 10,
      }
    }));
    component.ngOnInit();
    fixture.detectChanges();
    const dailyCells = (fixture.nativeElement as HTMLElement).querySelectorAll('.cell:not(.header):not(.corner)');
    expect(dailyCells[0].textContent.trim()).toBe('-');
    expect(dailyCells[1].textContent.trim()).toBe('-');

    const monthlyCells = (fixture.nativeElement as HTMLElement).querySelectorAll('.cell:not(.header):not(.corner)');
    expect(monthlyCells[2].textContent.trim()).toBe('15');
    expect(monthlyCells[3].textContent.trim()).toBe('100');

    const extraCell = (fixture.nativeElement as HTMLElement).querySelectorAll('.cell.extra');
    expect(extraCell[0].textContent.trim()).toBe('10');
    expect(extraCell[1].textContent.trim()).toBe('10');
  });

  it('should hide the extra row when user is not logged in', () => {
    mockUsersService.userData.next({ ...mockLoggedInUser, loggedIn: false });
    fixture.detectChanges();
    const extraCell = (fixture.nativeElement as HTMLElement).querySelectorAll('.cell.extra');
    expect(extraCell[0]).toBeUndefined();
    expect(extraCell[1]).toBeUndefined();
  });

  it('should apply 3-row grid style when user is logged in', () => {
    // eslint-disable-next-line @typescript-eslint/no-unnecessary-type-assertion
    const table = (fixture.nativeElement as HTMLElement).querySelector('.category-table') as HTMLElement;
    expect(table.style.gridTemplateRows).toBe('repeat(3, auto)');
  });

  it('should apply 2-row grid style when user is not logged in', () => {
    mockUsersService.userData.next({ ...mockLoggedInUser, loggedIn: false });
    fixture.detectChanges();
    // eslint-disable-next-line @typescript-eslint/no-unnecessary-type-assertion
    const table = (fixture.nativeElement as HTMLElement).querySelector('.category-table') as HTMLElement;
    expect(table.style.gridTemplateRows).toBe('repeat(2, auto)');
  });
});
