import { ComponentFixture, TestBed } from '@angular/core/testing';

import { JobDetailsComponent } from './job-details.component';
import { MatDialogModule, MAT_DIALOG_DATA } from '@angular/material/dialog';
import { Observable, of } from 'rxjs';
import { JobsService } from '../job-creation/jobs.service';
import { Job } from '../job-creation/jobs';

class JobsServiceMock {
  public getJobDetails(jobId: number): Observable<Job> {
    return of(new Job(jobId, jobId, new Date('12.12.12'), 'test@email.com', 'success', 2.5, '', '9.7 KB', ''));
  }

  public getDownloadJobResultLink(jobId: number): string {
    return `jobs/result/${jobId}`;
  }

  public getJobInputDownloadLink(jobId: number): string {
    return `jobs/input/${jobId}`;
  }

  public getJobConfigLink(jobId: number): string {
    return `jobs/config/${jobId}`;
  }
}

describe('JobDetailsComponent', () => {
  let component: JobDetailsComponent;
  let fixture: ComponentFixture<JobDetailsComponent>;
  const jobsServiceMock = new JobsServiceMock();

  beforeEach(() => {
    const mockJobId = 3;

    TestBed.configureTestingModule({
      imports: [JobDetailsComponent, MatDialogModule],
      providers: [
        { provide: MAT_DIALOG_DATA, useValue: mockJobId },
        { provide: JobsService, useValue: jobsServiceMock },
      ]
    }).compileComponents();

    fixture = TestBed.createComponent(JobDetailsComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should get job details on init', () => {
    component.ngOnInit();
    expect(component.job).toStrictEqual(
      new Job(3, 3, new Date('12.12.12'), 'test@email.com', 'success', 2.5, '', '9.7 KB', '')
    );
  });

  it('should get links for all files on init', () => {
    component.ngOnInit();
    expect(component.annotatedFileLink).toBe('jobs/result/3');
    expect(component.uploadedFileLink).toBe('jobs/input/3');
    expect(component.configFileLink).toBe('jobs/config/3');
  });

  it('should get correct css class for status success', () => {
    expect(component.getStatusClass('success')).toBe('success-status');
  });
});
