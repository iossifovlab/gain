import { Component, ViewChild, OnInit, NgZone, HostListener, effect } from '@angular/core';
import { JobsTableComponent } from '../jobs-table/jobs-table.component';
import { Observable, Subscription, take } from 'rxjs';
import { JobsService } from '../job-creation/jobs.service';
import { AnnotationPipelineComponent } from '../annotation-pipeline/annotation-pipeline.component';
import { getStatusClassName, Job, JobStatus } from '../job-creation/jobs';
import { JobCreationComponent } from '../job-creation/job-creation.component';
import { CommonModule } from '@angular/common';
import { UsersService } from '../users.service';
import { SocketNotificationsService } from '../socket-notifications/socket-notifications.service';
import { JobNotification } from '../socket-notifications/socket-notifications';
import { AnnotationPipelineService } from '../annotation-pipeline.service';
import { AnnotationPipelineStateService } from '../annotation-pipeline/annotation-pipeline-state.service';

@Component({
  selector: 'app-annotation-jobs-wrapper',
  imports: [
    CommonModule,
    JobsTableComponent,
    AnnotationPipelineComponent,
    JobCreationComponent,
  ],
  templateUrl: './annotation-jobs-wrapper.component.html',
  styleUrl: './annotation-jobs-wrapper.component.css'
})

export class AnnotationJobsWrapperComponent implements OnInit {
  public file: File = null;
  public fileSeparator: string = null;
  public fileHeader: Map<string, string> = null;
  public creationError = '';
  public selectedGenome = '';
  public isCreationFormVisible = true;
  @ViewChild(AnnotationPipelineComponent) public pipelinesComponent: AnnotationPipelineComponent;
  @ViewChild(JobCreationComponent) public createJobComponent: JobCreationComponent;
  @ViewChild(JobsTableComponent) public jobsTableComponent: JobsTableComponent;
  public downloadLink = '';
  public currentJob: Job = null;
  public currentJobId: number = null;
  public hideComponents = false;
  public hideHistory = false;
  public isUserLoggedIn = false;
  public blockCreate: boolean = false;
  public socketNotificationSubscription: Subscription = new Subscription();


  public constructor(
      private jobsService: JobsService,
      private userService: UsersService,
      private ngZone: NgZone,
      private socketNotificationsService: SocketNotificationsService,
      private annotationPipelineService: AnnotationPipelineService,
      private pipelineStateService: AnnotationPipelineStateService,
  ) {
    effect(() => {
      const id = this.pipelineStateService.currentTemporaryPipelineId() ||
        this.pipelineStateService.selectedPipelineId();
      if (id) {
        this.annotationPipelineService.loadPipeline(id).pipe(take(1)).subscribe();
      }
    });
  }

  public ngOnInit(): void {
    this.setupJobWebSocketConnection();
    this.userService.userData.pipe(
    ).subscribe((userData) => {
      this.isUserLoggedIn = userData.loggedIn;
    });
  }

  @HostListener('window:beforeunload', ['$event'])
  public beforeUnload(event: BeforeUnloadEvent): void {
    if (this.hasUnsavedChanges()) {
      event.preventDefault(); // display the confirmation dialog
    }
  }

  private hasUnsavedChanges(): boolean {
    if (this.pipelinesComponent.isPipelineChanged()) {
      return true;
    }

    if (this.file) {
      return true;
    }
    return false;
  }


  private setupJobWebSocketConnection(): void {
    this.socketNotificationSubscription = this.socketNotificationsService.getJobNotifications().pipe(
    ).subscribe({
      next: (notification: JobNotification) => {
        this.userService.refreshUserData();
        if (notification.jobId === this.currentJobId) {
          this.getCurrentJobDetails(notification.jobId);
        }
        if (this.isUserLoggedIn) {
          this.jobsTableComponent.refreshTable();
        }
      },
      error: err => {
        console.error(err);
        if (err instanceof CloseEvent && err.type === 'close') {
          this.socketNotificationSubscription.unsubscribe();
          this.setupJobWebSocketConnection();
        }
      }
    });
  }

  public autoSavePipeline(): void {
    if (!this.pipelineStateService.isConfigValid()) {
      return;
    }
    if (this.pipelinesComponent.isPipelineChanged()) {
      this.pipelinesComponent.autoSave().pipe(take(1)).subscribe(() => {
        this.create();
      });
    } else {
      this.create();
    }
  }

  private create(): void {
    if (this.file) {
      let createObservable: Observable<number>;
      if (this.file.type === 'text/vcard') {
        createObservable = this.jobsService.createVcfJob(
          this.file,
          this.pipelineStateService.currentTemporaryPipelineId() ||
            this.pipelineStateService.selectedPipelineId() ||
            '',
          this.selectedGenome,
        );
      } else {
        createObservable = this.jobsService.createNonVcfJob(
          this.file,
          this.pipelineStateService.currentTemporaryPipelineId() ||
            this.pipelineStateService.selectedPipelineId() ||
            '',
          this.selectedGenome,
          this.fileSeparator,
          this.fileHeader
        );
      }

      this.blockCreate = true;
      createObservable.pipe(
        take(1),
      ).subscribe({
        next: (jobId: number) => {
          this.isCreationFormVisible = false;
          this.currentJobId = jobId;
          this.getCurrentJobDetails(jobId);
        },
        complete: () => {
          this.blockCreate = false;
        },
        error: (err: Error) => {
          this.creationError = err.message;
          this.blockCreate = false;
        }
      });
    }
  }

  private getCurrentJobDetails(jobId: number): void {
    this.jobsService.getJobDetails(jobId).subscribe({
      next: (job: Job) => {
        if (!this.currentJob || this.currentJob.status !== job.status) {
          this.currentJob = job;
          this.downloadLink = this.jobsService.getDownloadJobResultLink(job.id);
        }
      },
      error: (err: Error) => {
        this.creationError = err.message;
      }
    });
  }

  public showCreateMode(): void {
    this.isCreationFormVisible = true;
    this.creationError = '';
    this.resetJobState();
  }

  private resetJobState(): void {
    this.currentJob = null;
    this.downloadLink = '';
    this.file = null;
    this.fileHeader = null;
  }

  public setGenome(genome: string): void {
    this.selectedGenome = genome;
  }

  public setFile(newFile: File): void {
    this.file = newFile;
    this.creationError = '';
    this.disableCreate();
  }

  public setFileSeparator(newSeparator: string): void {
    this.fileSeparator = newSeparator;
  }

  public setUpdatedFileHeader(newHeader: Map<string, string>): void {
    this.fileHeader = newHeader;
    this.disableCreate();
  }

  public isGenomeValid(): boolean {
    const genomeRequired = this.fileHeader.has('location') || this.fileHeader.has('variant');
    if (genomeRequired && this.selectedGenome === '') {
      return false;
    }
    return true;
  }

  public getStatusClass(): string {
    return getStatusClassName(this.currentJob.status);
  }

  public disableCreate(): boolean {
    return this.blockCreate
      || this.creationError !== ''
      || !this.file
      || (this.file.type !== 'text/vcard' && !this.fileHeader)
      || !(this.pipelineStateService.currentTemporaryPipelineId() || this.pipelineStateService.selectedPipelineId())
      || !this.pipelineStateService.isConfigValid()
      || (this.file.type !== 'text/vcard' && !this.isGenomeValid());
  }

  public isJobFinished(status: JobStatus): boolean {
    return status === 'success' || status === 'failed';
  }

  public updateComponentsVisibility(toHide: boolean): void {
    this.ngZone.run(() => {
      this.hideComponents = toHide;
      this.hideHistory = toHide;
    });
  }

  public showComponents(): void {
    this.updateComponentsVisibility(false);
    this.pipelinesComponent.shrinkTextarea();
  }

  public refreshUserQuota(): void {
    this.userService.refreshUserData();
  }
}

