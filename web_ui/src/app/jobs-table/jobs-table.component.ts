import { CommonModule } from '@angular/common';
import { Component, EventEmitter, OnDestroy, OnInit, Output } from '@angular/core';
import { MatDialog } from '@angular/material/dialog';
import { JobsService } from '../job-creation/jobs.service';
import { Subscription, take } from 'rxjs';
import { getStatusClassName, Job } from '../job-creation/jobs';
import { JobDetailsComponent } from '../job-details/job-details.component';

@Component({
  selector: 'app-jobs-table',
  imports: [CommonModule],
  templateUrl: './jobs-table.component.html',
  styleUrl: './jobs-table.component.css'
})
export class JobsTableComponent implements OnInit, OnDestroy {
  public jobs: Job[] = [];
  @Output() public jobDelete = new EventEmitter<void>();
  private refreshJobsSubscription = new Subscription();

  public constructor(
    private dialog: MatDialog,
    private jobsService: JobsService,
  ) {}

  public ngOnInit(): void {
    this.refreshTable();
  }

  public refreshTable(): void {
    this.refreshJobsSubscription.unsubscribe();
    this.refreshJobsSubscription = this.jobsService.getJobs().pipe(
      take(1),
    ).subscribe(jobs => {
      this.jobs = jobs.reverse();
    });
  }


  public openDetailsModal(jobId: number): void {
    const detailsModalRef = this.dialog.open(JobDetailsComponent, {
      data: jobId,
      height: '40vh',
      width: '30vw',
      maxWidth: '1000px',
      minHeight: '400px'
    });

    detailsModalRef.afterClosed().subscribe(isJobDeleted => {
      if (isJobDeleted) {
        this.refreshTable();
      }
    });
  }

  public getDownloadLink(jobId: number): string {
    return this.jobsService.getDownloadJobResultLink(jobId);
  }

  public getStatusClass(status: string): string {
    return getStatusClassName(status);
  }

  public onDelete(jobId: number): void {
    this.jobDelete.emit();
    this.jobsService.deleteJob(jobId).subscribe(() => this.refreshTable());
  }

  public ngOnDestroy(): void {
    this.refreshJobsSubscription.unsubscribe();
  }
}
