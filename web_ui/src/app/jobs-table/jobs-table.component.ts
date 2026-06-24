import { CommonModule } from '@angular/common';
import { Component, EventEmitter, inject, OnDestroy, OnInit, Output } from '@angular/core';
import { MatDialog } from '@angular/material/dialog';
import { JobsService } from '../job-creation/jobs.service';
import { Subscription, take } from 'rxjs';
import { getStatusClassName, Job } from '../job-creation/jobs';
import { JobDetailsComponent } from '../job-details/job-details.component';
import { ViewportService } from '../viewport.service';

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

  private readonly dialog = inject(MatDialog);
  private readonly jobsService = inject(JobsService);
  private readonly viewportService = inject(ViewportService);

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
    const isMobile = this.viewportService.isMobile();
    const detailsModalRef = this.dialog.open(JobDetailsComponent, {
      data: jobId,
      height: '40vh',
      width: isMobile ? '60vw' : '30vw',
      maxWidth: isMobile ? '60vw' : '1000px',
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
