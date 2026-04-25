import { CommonModule } from '@angular/common';
import { Component, Inject, OnInit } from '@angular/core';
import { MAT_DIALOG_DATA, MatDialogContent } from '@angular/material/dialog';
import { JobsService } from '../job-creation/jobs.service';
import { getStatusClassName, Job } from '../job-creation/jobs';

@Component({
  selector: 'app-job-details',
  imports: [MatDialogContent, CommonModule],
  templateUrl: './job-details.component.html',
  styleUrl: './job-details.component.css'
})
export class JobDetailsComponent implements OnInit {
  public job: Job;
  public annotatedFileLink: string;
  public uploadedFileLink: string;
  public configFileLink: string;

  public constructor(
    @Inject(MAT_DIALOG_DATA) public jobId: number,
    private jobsService: JobsService
  ) { }

  public ngOnInit(): void {
    this.jobsService.getJobDetails(this.jobId).subscribe(res => {
      this.job = res;
      this.annotatedFileLink = this.jobsService.getDownloadJobResultLink(this.jobId);
      this.uploadedFileLink = this.jobsService.getJobInputDownloadLink(this.jobId);
      this.configFileLink = this.jobsService.getJobConfigLink(this.jobId);
    });
  }

  public getStatusClass(status: string): string {
    return getStatusClassName(status);
  }
}
