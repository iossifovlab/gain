import { ComponentFixture, TestBed } from '@angular/core/testing';
import { AnnotationJobsWrapperComponent } from './annotation-jobs-wrapper.component';
import { JobsService } from '../job-creation/jobs.service';
import { provideHttpClient } from '@angular/common/http';
import { UserData, UsersService } from '../users.service';
import { SingleAnnotationService } from '../single-annotation.service';
import { BehaviorSubject, Observable, of, throwError } from 'rxjs';
import { FileContent, Job } from '../job-creation/jobs';
import { Pipeline } from '../job-creation/pipelines';
import { provideMonacoEditor } from 'ngx-monaco-editor-v2';
import { SocketNotificationsService } from '../socket-notifications/socket-notifications.service';
import { JobNotification, PipelineNotification } from '../socket-notifications/socket-notifications';
import { AnnotationPipelineService } from '../annotation-pipeline.service';
import { MatTooltip } from '@angular/material/tooltip';
import { AnnotationPipelineStateService } from '../annotation-pipeline/annotation-pipeline-state.service';

class UserServiceMock {
  public userData = new BehaviorSubject<UserData>({
    email: 'email',
    loggedIn: true,
    isAdmin: false,
    limitations: {
      dailyJobs: 5,
      filesize: '64M',
      todayJobsCount: 4,
      variantCount: 1000,
      diskSpace: '1000'
    }
  });

  public refreshUserData(): void { }
}

const mockPipelines = [
  new Pipeline('id1', 'name1', 'content1', 'default', 'loaded'),
  new Pipeline('id2', 'name2', 'content2', 'default', 'loaded'),
  new Pipeline('id3', 'name3', 'content3', 'default', 'loaded'),
];

const jobs = [
  new Job(1, 1, new Date('1.10.2025'), 'test@email.com', 'success', 3.2, 'fileName1', '12 KB', ''),
  new Job(2, 2, new Date('1.10.2025'), 'test@email.com', 'failed', 2.7, 'fileName2', '12 KB', ''),
];
class JobsServiceMock {
  public getJobs(): Observable<Job[]> {
    return of(jobs);
  }
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public createVcfJob(file1: File, pipeline: string, content: string, genome: string): Observable<number> {
    return of(2);
  }

  // eslint-disable-next-line @typescript-eslint/no-unused-vars, @stylistic/max-len
  public createNonVcfJob(file1: File, pipeline: string, config: string, genome: string, fileSeparator: string): Observable<number> {
    return of(1);
  }

  public getAnnotationPipelines(): Observable<Pipeline[]> {
    return of(mockPipelines);
  }

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public getJobDetails(jobId: number): Observable<Job> {
    return of(jobs.find(j => j.id === jobId));
  }

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public createFilePreview(file: File): Observable<FileContent> {
    return of(new FileContent(',', ['chr', 'pos'], [['1', '123']]));
  }

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public submitSeparator(file: File, separator: string): Observable<FileContent> {
    return of(new FileContent(',', ['chr', 'pos'], [['1', '123']]));
  }

  public getDownloadJobResultLink(jobId: string): string {
    return `url/${jobId}`;
  }

  public getSocketNotifications(): Observable<object> {
    return of({});
  }

  public closeConnection(): void { }
}

class SocketNotificationsServiceMock {
  public getPipelineNotifications(): Observable<PipelineNotification> {
    return of(null);
  }

  public getJobNotifications(): Observable<JobNotification> {
    return of(new JobNotification(1, 'failed'));
  }

  public closeConnection(): void { }
}

class AnnotationPipelineServiceMock {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public loadPipeline(name: string): Observable<void> {
    return of();
  }

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public savePipeline(id: string, name: string, config: string): Observable<string> {
    return of('id1');
  }
}


// eslint-disable-next-line @typescript-eslint/no-unsafe-member-access, @typescript-eslint/no-explicit-any
(global as any).ResizeObserver = class {
  public observe(): void {}
  public unobserve(): void {}
  public disconnect(): void {}
};


describe('AnnotationJobsWrapperComponent', () => {
  let component: AnnotationJobsWrapperComponent;
  let fixture: ComponentFixture<AnnotationJobsWrapperComponent>;
  let pipelineStateService: AnnotationPipelineStateService;
  const jobsServiceMock = new JobsServiceMock();
  const userServiceMock = new UserServiceMock();
  const socketNotificationsServiceMock = new SocketNotificationsServiceMock();
  const annotationPipelineServiceMock = new AnnotationPipelineServiceMock();

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [AnnotationJobsWrapperComponent],
      providers: [
        JobsService,
        MatTooltip,
        {
          provide: UsersService,
          useValue: userServiceMock
        },
        {
          provide: JobsService,
          useValue: jobsServiceMock
        },
        {
          provide: SocketNotificationsService,
          useValue: socketNotificationsServiceMock
        },
        {
          provide: AnnotationPipelineService,
          useValue: annotationPipelineServiceMock
        },
        SingleAnnotationService,
        provideHttpClient(),
        provideMonacoEditor()
      ]
    }).compileComponents();

    fixture = TestBed.createComponent(AnnotationJobsWrapperComponent);
    component = fixture.componentInstance;

    pipelineStateService = TestBed.inject(AnnotationPipelineStateService);
    pipelineStateService.pipelines.set([]);

    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should check if user is logged in on component init', () => {
    component.ngOnInit();
    expect(component.isUserLoggedIn).toBe(true);
  });

  it('should set up web socket communication on component init', () => {
    const getSocketNotificationSpy = jest.spyOn(socketNotificationsServiceMock, 'getJobNotifications');
    const refreshUserDataSpy = jest.spyOn(userServiceMock, 'refreshUserData');
    const refreshJobsTableSpy = jest.spyOn(component.jobsTableComponent, 'refreshTable');
    component.currentJobId = 1;
    component.isUserLoggedIn = true;
    component.ngOnInit();
    expect(getSocketNotificationSpy).toHaveBeenCalledWith();
    expect(refreshUserDataSpy).toHaveBeenCalledWith();
    expect(refreshJobsTableSpy).toHaveBeenCalledWith();
  });

  it('should get job details when setting up web socket communication', () => {
    const getJobDetailsSpy = jest.spyOn(jobsServiceMock, 'getJobDetails');

    component.currentJobId = 1;
    component.isUserLoggedIn = true;
    component.currentJob = new Job(123, 1, null, 'user1', 'in progress', 12, 'fileName', '12K', '');
    component.ngOnInit();
    expect(getJobDetailsSpy).toHaveBeenCalledWith(1);
    expect(component.currentJob).toStrictEqual(jobs[1]);
    expect(component.downloadLink).toBe('url/1');
  });

  it('should reconnects to socket notifications on close event', () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const setupSpy = jest.spyOn(component as any, 'setupJobWebSocketConnection');
    jest.spyOn(socketNotificationsServiceMock, 'getJobNotifications')
      .mockReturnValueOnce(throwError(new CloseEvent('close')));
    const unsubSpy = jest.spyOn(component.socketNotificationSubscription, 'unsubscribe');

    component.ngOnInit();

    expect(unsubSpy).toHaveBeenCalledWith();
    expect(setupSpy).toHaveBeenCalledTimes(2);
  });

  it('does not reconnect for non-close events', () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const setupSpy = jest.spyOn(component as any, 'setupJobWebSocketConnection');
    jest.spyOn(socketNotificationsServiceMock, 'getJobNotifications')
      .mockReturnValueOnce(throwError({ type: 'other' }));

    component.ngOnInit();
    expect(setupSpy).toHaveBeenCalledTimes(1);

    const unsubSpy = jest.spyOn(component.socketNotificationSubscription, 'unsubscribe');

    expect(unsubSpy).not.toHaveBeenCalled();
    expect(setupSpy).toHaveBeenCalledTimes(1);
  });

  it('should disable Create button if no file is uploaded', () => {
    component.file = null;
    pipelineStateService.selectedPipelineId.set('autism');
    expect(component.disableCreate()).toBe(true);
  });

  it('should disable Create button if yml config is invalid', () => {
    pipelineStateService.isConfigValid.set(false);
    expect(component.disableCreate()).toBe(true);
  });

  it('should hide creation form after creating a job', () => {
    const mockFile = new File([], 'mockFile', { type: 'text/vcard' });
    component.file = mockFile;
    component.selectedGenome = 'hg38';
    component.isCreationFormVisible = true;

    component.autoSavePipeline();
    expect(component.isCreationFormVisible).toBe(false);
  });

  it('should show creation job form and reset file', () => {
    component.isCreationFormVisible = false;
    component.showCreateMode();
    expect(component.isCreationFormVisible).toBe(true);
    expect(component.currentJob).toBeNull();
    expect(component.downloadLink).toBe('');
    expect(component.file).toBeNull();
  });

  it('should auto save and set temporary pipeline id', () => {
    jest.spyOn(annotationPipelineServiceMock, 'savePipeline').mockReturnValueOnce(of('temp'));
    const pipelinesComponentSpy = jest.spyOn(component.pipelinesComponent, 'autoSave');
    jest.spyOn(component.pipelinesComponent, 'isPipelineChanged').mockReturnValue(true);

    component.autoSavePipeline();
    expect(pipelinesComponentSpy).toHaveBeenCalledWith();
    expect(pipelineStateService.currentTemporaryPipelineId()).toBe('temp');
  });

  it('should trigger auto save pipeline when editor is empty', () => {
    const pipelinesComponentSpy = jest.spyOn(component.pipelinesComponent, 'autoSave');
    component.pipelinesComponent.currentPipelineText = '';

    component.autoSavePipeline();
    expect(pipelinesComponentSpy).toHaveBeenCalledWith();
  });

  it('should create job with vcf file and auto save pipeline', () => {
    component.file = new File([], 'mock.vcf', { type: 'text/vcard' });
    component.selectedGenome = 'hg38';
    component.fileSeparator = '';
    component.fileHeader = null;
    const createJobSpy = jest.spyOn(jobsServiceMock, 'createVcfJob');
    jest.spyOn(component.pipelinesComponent, 'isPipelineChanged').mockReturnValue(true);

    component.autoSavePipeline();

    expect(createJobSpy).toHaveBeenCalledWith(
      new File([], 'mock.vcf'),
      'id1',
      'hg38',
    );
    expect(component.creationError).toBe('');
    expect(component.currentJobId).toBe(2);
    expect(component.currentJob).toStrictEqual(
      new Job(2, 2, new Date('1.10.2025'), 'test@email.com', 'failed', 2.7, 'fileName2', '12 KB', '')
    );
  });

  it('should create job with csv file and auto save pipeline', () => {
    component.file = new File([], 'mock.csv', { type: 'text/comma-separated-values' });
    component.selectedGenome = 'hg38';
    component.fileSeparator = ',';
    component.fileHeader = new Map<string, string>([['a', '1']]);
    const createJobSpy = jest.spyOn(jobsServiceMock, 'createNonVcfJob');
    jest.spyOn(component.pipelinesComponent, 'isPipelineChanged').mockReturnValue(true);

    component.autoSavePipeline();

    expect(createJobSpy).toHaveBeenCalledWith(
      new File([], 'mock.csv'),
      'id1',
      'hg38',
      ',',
      new Map<string, string>([['a', '1']])
    );
    expect(component.creationError).toBe('');
    expect(component.currentJobId).toBe(1);
  });

  it('should load pipeline on pipeline selection', () => {
    const loadPipelineSpy = jest.spyOn(annotationPipelineServiceMock, 'loadPipeline');
    const disableCreateSpy = jest.spyOn(component, 'disableCreate');
    pipelineStateService.selectedPipelineId.set('new_pipeline');
    fixture.detectChanges();
    expect(loadPipelineSpy).toHaveBeenCalledWith('new_pipeline');
    expect(disableCreateSpy).toHaveBeenCalledWith();
  });

  it('should set genome', () => {
    component.selectedGenome = 'hg19';
    component.setGenome('hg38');
    expect(component.selectedGenome).toBe('hg38');
  });

  it('should set job\'s file', () => {
    component.file = null;
    component.creationError = 'some error';
    const disableCreateSpy = jest.spyOn(component, 'disableCreate');
    const mockFile = new File([], 'mockFile');
    component.setFile(mockFile);
    expect(component.file).toStrictEqual(mockFile);
    expect(disableCreateSpy).toHaveBeenCalledWith();
  });

  it('should set file separator', () => {
    component.fileSeparator = null;
    component.setFileSeparator('\t');
    expect(component.fileSeparator).toBe('\t');
  });

  it('should set the new headers of the file', () => {
    const newHeader = new Map<string, string>([['chr', 'CHR']]);
    const disableCreateSpy = jest.spyOn(component, 'disableCreate');

    component.setUpdatedFileHeader(newHeader);
    expect(component.fileHeader).toStrictEqual(newHeader);
    expect(disableCreateSpy).toHaveBeenCalledWith();
  });

  it('should check if genome is required and not selected', () => {
    component.fileHeader = new Map<string, string>([['location', 'LOC']]);
    component.selectedGenome = '';
    expect(component.isGenomeValid()).toBe(false);

    component.fileHeader = new Map<string, string>([['location', 'LOC']]);
    component.selectedGenome = 'hg38';
    expect(component.isGenomeValid()).toBe(true);

    component.fileHeader = new Map<string, string>([['chr', 'CHR']]);
    component.selectedGenome = '';
    expect(component.isGenomeValid()).toBe(true);
  });

  it('should get css class names of different job statuses', () => {
    component.currentJob = new Job(123, 1, null, 'user1', 'in progress', 12, 'fileName', '12K', '');
    expect(component.getStatusClass()).toBe('in-progress-status');

    component.currentJob = new Job(123, 1, null, 'user1', 'waiting', 12, 'fileName', '12K', '');
    expect(component.getStatusClass()).toBe('waiting-status');

    component.currentJob = new Job(123, 1, null, 'user1', 'success', 12, 'fileName', '12K', '');
    expect(component.getStatusClass()).toBe('success-status');

    component.currentJob = new Job(123, 1, null, 'user1', 'failed', 12, 'fileName', '12K', '');
    expect(component.getStatusClass()).toBe('fail-status');
  });

  it('should disable create job button', () => {
    component.blockCreate = true;
    component.file = new File([], 'mockFile', { type: 'text/vcard' });
    component.selectedGenome = 'hg38';
    pipelineStateService.isConfigValid.set(true);
    expect(component.disableCreate()).toBe(true);
  });

  it('should disable create job button when no pipeline is selected', () => {
    component.blockCreate = false;
    pipelineStateService.selectedPipelineId.set('');
    component.file = new File([], 'mockFile', { type: 'text/vcard' });
    component.selectedGenome = 'hg38';
    pipelineStateService.isConfigValid.set(true);
    expect(component.disableCreate()).toBe(true);
  });

  it('should disable create job button when no file is uploaded', () => {
    component.blockCreate = false;
    pipelineStateService.selectedPipelineId.set('pipeline_autism');
    component.file = null;
    component.selectedGenome = 'hg38';
    pipelineStateService.isConfigValid.set(true);
    expect(component.disableCreate()).toBe(true);
  });

  it('should disable create job button when no genome is selected when required', () => {
    component.blockCreate = false;
    pipelineStateService.selectedPipelineId.set('pipeline_autism');
    component.file = new File([], 'mockFile', { type: 'text/comma-separated-values' });
    component.fileHeader = new Map<string, string>([['location', 'LOC']]);
    component.selectedGenome = '';
    pipelineStateService.isConfigValid.set(true);
    expect(component.disableCreate()).toBe(true);
  });

  it('should disable create job button when no new columns are selected', () => {
    component.blockCreate = false;
    pipelineStateService.selectedPipelineId.set('pipeline_autism');
    component.file = new File([], 'mockFile', { type: 'text/comma-separated-values' });
    component.fileHeader = null;
    pipelineStateService.isConfigValid.set(true);
    expect(component.disableCreate()).toBe(true);
  });

  it('should disable create job button when pipeline config is not valid', () => {
    component.blockCreate = false;
    pipelineStateService.selectedPipelineId.set('pipeline_autism');
    component.file = new File([], 'mockFile', { type: 'text/vcard' });
    component.selectedGenome = 'hg38';
    pipelineStateService.isConfigValid.set(false);
    expect(component.disableCreate()).toBe(true);
  });

  it('should check if job is finished', () => {
    expect(component.isJobFinished('success')).toBe(true);
    expect(component.isJobFinished('failed')).toBe(true);
    expect(component.isJobFinished('in progress')).toBe(false);
    expect(component.isJobFinished('waiting')).toBe(false);
  });

  it('should display hidden components and trigger shrinking the editor\'s size', () => {
    const updateComponentsVisibilitySpy = jest.spyOn(component, 'updateComponentsVisibility');
    const shrinkTextareaSpy = jest.spyOn(component.pipelinesComponent, 'shrinkTextarea');

    component.showComponents();
    expect(updateComponentsVisibilitySpy).toHaveBeenCalledWith(false);
    expect(shrinkTextareaSpy).toHaveBeenCalledWith();
  });

  it('should trigger user data refresh on job deletion', () => {
    const refreshUserDataSpy = jest.spyOn(userServiceMock, 'refreshUserData');
    component.refreshUserQuota();
    expect(refreshUserDataSpy).toHaveBeenCalledWith();
  });

  it('should display confirmation dialog on beforeUnload event when pipeline is not saved', () => {
    const mockBoeforeUnloadEvent = { preventDefault: jest.fn() } as unknown as BeforeUnloadEvent;
    jest.spyOn(component.pipelinesComponent, 'isPipelineChanged').mockReturnValue(true);
    component.beforeUnload(mockBoeforeUnloadEvent);
    expect(mockBoeforeUnloadEvent.preventDefault).toHaveBeenCalledWith();
  });

  it('should display confirmation dialog on beforeUnload event when creating job is not finished', () => {
    const mockBoeforeUnloadEvent = { preventDefault: jest.fn() } as unknown as BeforeUnloadEvent;
    component.file = new File([], 'mock.vcf', { type: 'text/vcard' });
    component.beforeUnload(mockBoeforeUnloadEvent);
    expect(mockBoeforeUnloadEvent.preventDefault).toHaveBeenCalledWith();
  });

  it('should not display confirmation dialog on beforeUnload event when no unsaved changes', () => {
    const mockBoeforeUnloadEvent = { preventDefault: jest.fn() } as unknown as BeforeUnloadEvent;
    component.beforeUnload(mockBoeforeUnloadEvent);
    expect(mockBoeforeUnloadEvent.preventDefault).not.toHaveBeenCalledWith();
  });

  it('should set creation error when job creation fails', () => {
    jest.spyOn(jobsServiceMock, 'createVcfJob').mockReturnValueOnce(throwError(new Error('Server error')));
    component.file = new File([], 'mock.vcf', { type: 'text/vcard' });
    component.selectedGenome = 'hg38';

    component.autoSavePipeline();
    expect(component.creationError).toBe('Server error');
    expect(component.blockCreate).toBe(false);
  });

  it('should disable create button when there is a creation error', () => {
    component.file = new File([], 'mockFile', { type: 'text/vcard' });
    pipelineStateService.selectedPipelineId.set('autism');
    pipelineStateService.isConfigValid.set(true);
    component.creationError = 'some error';
    expect(component.disableCreate()).toBe(true);
  });
});
