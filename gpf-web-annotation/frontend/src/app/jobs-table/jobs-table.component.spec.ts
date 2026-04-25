import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Job } from '../job-creation/jobs';
import { JobsTableComponent } from './jobs-table.component';
import { Observable } from 'rxjs/internal/Observable';
import { of } from 'rxjs';
import { JobsService } from '../job-creation/jobs.service';
import { UsersService } from '../users.service';
import { provideHttpClient } from '@angular/common/http';
import { SingleAnnotationService } from '../single-annotation.service';
import { Pipeline } from '../job-creation/pipelines';
import { ElementRef, TemplateRef } from '@angular/core';
import { MatDialog, MatDialogConfig } from '@angular/material/dialog';

const jobs = [
  new Job(1, 1, new Date('1.10.2025'), 'test@email.com', 'in progress', 3.2, '', '9.7 KB', ''),
  new Job(2, 2, new Date('1.10.2025'), 'test@email.com', 'failed', 2.7, '', '9.7 KB', ''),
];
class JobsServiceMock {
  public getJobs(): Observable<Job[]> {
    return of(jobs);
  }

  public getDownloadJobResultLink(jobId: number): string {
    return `/jobs/mockUrl/${jobId}`;
  }

  public getAnnotationPipelines(): Observable<Pipeline[]> {
    return of([
      new Pipeline('id1', 'name1', 'content1', 'default', 'loaded'),
      new Pipeline('id2', 'name2', 'content2', 'default', 'loaded'),
      new Pipeline('id3', 'name3', 'content3', 'default', 'loaded'),
    ]);
  }

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public deleteJob(id: number): Observable<object> {
    return of({});
  }
}

class UserServiceMock {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public userData = {
    value: {
      limitations: {
        dailyJobs: 5,
        filesize: '64M',
        todayJobsCount: 4,
        variantCount: 1000,
      }
    }
  };
}
class MatDialogRefMock {
  public afterClosed(): Observable<number> {
    return of(2);
  }
}

class MatDialogMock {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars, @stylistic/max-len
  public open(templateRef: TemplateRef<ElementRef>, config: MatDialogConfig<string>): MatDialogRefMock {
    return new MatDialogRefMock();
  }
}

describe('JobsTableComponent', () => {
  let component: JobsTableComponent;
  let fixture: ComponentFixture<JobsTableComponent>;
  const jobsServiceMock = new JobsServiceMock();
  const userServiceMock = new UserServiceMock();

  const mockMatRef = new MatDialogMock();

  beforeEach(async() => {
    await TestBed.configureTestingModule({
      imports: [JobsTableComponent],
      providers: [
        {
          provide: UsersService,
          useValue: userServiceMock
        },
        {
          provide: MatDialog,
          useValue: mockMatRef
        },
        provideHttpClient(),
        SingleAnnotationService,
        { provide: JobsService, useValue: jobsServiceMock }
      ]
    }).compileComponents();

    fixture = TestBed.createComponent(JobsTableComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should get users jobs from service', () => {
    component.ngOnInit();
    expect(component.jobs).toStrictEqual(jobs);
  });

  it('should get download link for annotated file from service', () => {
    const url = component.getDownloadLink(15);
    expect(url).toBe('/jobs/mockUrl/15');
  });

  it('should call delete job from service and get list of jobs after deletion is ready', () => {
    const deleteSpy = jest.spyOn(jobsServiceMock, 'deleteJob');
    const getJobsSpy = jest.spyOn(jobsServiceMock, 'getJobs');
    component.onDelete(12);
    expect(deleteSpy).toHaveBeenCalledWith(12);
    expect(getJobsSpy).toHaveBeenCalledWith();
  });

  it('should refresh table when delete a job from modal', () => {
    const refreshTableSpy = jest.spyOn(component, 'refreshTable');
    component.openDetailsModal(2);
    expect(refreshTableSpy).toHaveBeenCalledWith();
  });
});
