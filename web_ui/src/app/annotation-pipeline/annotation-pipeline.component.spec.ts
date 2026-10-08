import { ComponentFixture, TestBed } from '@angular/core/testing';
import { AnnotationPipelineComponent, VALIDATE_DEBOUNCE_MS } from './annotation-pipeline.component';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { BehaviorSubject, NEVER, Observable, of, Subject, throwError } from 'rxjs';
import { JobCreationComponent } from '../job-creation/job-creation.component';
import { FileContent } from '../job-creation/jobs';
import { JobsService } from '../job-creation/jobs.service';
import { Pipeline } from '../job-creation/pipelines';
import { UserData } from '../users';
import { UsersService } from '../users.service';
import { MatDialog, MatDialogConfig, MatDialogRef } from '@angular/material/dialog';
import { AnnotationPipelineService } from '../annotation-pipeline.service';
import { ElementRef, TemplateRef } from '@angular/core';
import { provideMonacoEditor } from 'ngx-monaco-editor-v2';
import { By } from '@angular/platform-browser';
import { SocketNotificationsService } from '../socket-notifications/socket-notifications.service';
import { PipelineNotification } from '../socket-notifications/socket-notifications';
import { PipelineInfo } from '../annotation-pipeline';
import { NewAnnotatorComponent } from '../new-annotator/new-annotator.component';
import { AnnotationPipelineStateService } from './annotation-pipeline-state.service';

const mockPipelines = [
  new Pipeline('id1', 'name1', 'content1', 'default', 'loaded'),
  new Pipeline('id2', 'name2', 'content2', 'default', 'loaded'),
  new Pipeline('id3', 'name3', 'content3', 'user', 'loaded'),
];
class JobsServiceMock {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public createVcfJob(file1: File, pipeline: string, content: string, genome: string): Observable<object> {
    return of({});
  }

  // eslint-disable-next-line @typescript-eslint/no-unused-vars, @stylistic/max-len
  public createNonVcfJob(file1: File, pipeline: string, config: string, genome: string, fileSeparator: string): Observable<object> {
    return of({});
  }

  public getAnnotationPipelines(): Observable<Pipeline[]> {
    return of(mockPipelines);
  }

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public createFilePreview(file: File): Observable<FileContent> {
    return of(new FileContent(',', ['chr', 'pos'], [['1', '123']]));
  }

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public validatePipelineConfig(config: string): Observable<string> {
    return of('');
  }
}

class UserServiceMock {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public userData = new BehaviorSubject<UserData>({
    email: 'email',
    loggedIn: true,
    isAdmin: false,
    limitations: {
      dailyJobs: 5,
      filesize: '64M',
      todayJobsCount: 4,
      diskSpace: '1000'
    }
  });
}

class MatDialogRefMock {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public close(name: string): void { }

  public afterClosed(): Observable<string> {
    return of('pipeline-name');
  }
}

class MatDialogMock {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public getDialogById(id: string): MatDialogRefMock {
    return new MatDialogRefMock();
  }

  // eslint-disable-next-line @typescript-eslint/no-unused-vars, @stylistic/max-len
  public open(templateRef: TemplateRef<ElementRef>, config: MatDialogConfig<string>): MatDialogRefMock {
    return new MatDialogRefMock();
  }
}

class AnnotationPipelineServiceMock {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public savePipeline(id: string, name: string, content: string): Observable<string> {
    return of(id);
  }

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public deletePipeline(id: string): Observable<object> {
    return of({});
  }

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public loadPipeline(id: string): Observable<void> {
    return of(void 0);
  }

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public getPipelineInfo(id: string): Observable<PipelineInfo> {
    return of(new PipelineInfo(20, 4, ['hg19_annotatable'], ['gene_list']));
  }

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public getDownloadAnnotateDocumentationUrl(id: string): string {
    return `//localhost:8000/api/pipelines/doc?pipeline_id=${id}`;
  }

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public invalidateCache(id: string): void {
    // Mock implementation - no-op
  }
}

class SocketNotificationsServiceMock {
  public readonly opened = new Subject<void>();
  public readonly connectionOpened$ = this.opened.asObservable();

  public getPipelineNotifications(): Observable<PipelineNotification> {
    return of(new PipelineNotification('id1', 'unloaded'));
  }

  public reopenConnection(): Observable<void> {
    return of(undefined);
  }
}

// eslint-disable-next-line @typescript-eslint/no-unsafe-member-access, @typescript-eslint/no-explicit-any
(global as any).ResizeObserver = class {
  public observe(): void {}
  public unobserve(): void {}
  public disconnect(): void {}
};


describe('AnnotationPipelineComponent', () => {
  let component: AnnotationPipelineComponent;
  let fixture: ComponentFixture<AnnotationPipelineComponent>;
  let pipelineStateService: AnnotationPipelineStateService;
  const jobsServiceMock = new JobsServiceMock();
  const userServiceMock = new UserServiceMock();
  const mockMatDialogRef = new MatDialogRefMock();
  const mockMatRef = new MatDialogMock();
  const annotationPipelineServiceMock = new AnnotationPipelineServiceMock();
  const socketNotificationsServiceMock = new SocketNotificationsServiceMock();

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [JobCreationComponent],
      providers: [
        {
          provide: JobsService,
          useValue: jobsServiceMock
        },
        {
          provide: UsersService,
          useValue: userServiceMock
        },
        {
          provide: MatDialogRef,
          useValue: mockMatDialogRef
        },
        {
          provide: MatDialog,
          useValue: mockMatRef
        },
        {
          provide: AnnotationPipelineService,
          useValue: annotationPipelineServiceMock
        },
        {
          provide: SocketNotificationsService,
          useValue: socketNotificationsServiceMock
        },
        provideHttpClient(),
        provideHttpClientTesting(),
        provideMonacoEditor(),
      ]
    }).compileComponents();
    fixture = TestBed.createComponent(AnnotationPipelineComponent);
    component = fixture.componentInstance;

    jest.spyOn(mockMatRef, 'getDialogById').mockReturnValue(mockMatDialogRef);
    jest.spyOn(mockMatRef, 'open').mockReturnValue(mockMatDialogRef);

    // Mock monaco
    // eslint-disable-next-line @typescript-eslint/no-explicit-any, @typescript-eslint/no-unsafe-member-access
    (window as any).monaco = {
      editor: {
        defineTheme: jest.fn(),
        setTheme: jest.fn(),
        create: jest.fn(),
        onInit: jest.fn(),
        dispose: jest.fn(),
      }
    };

    fixture.detectChanges();
    pipelineStateService = TestBed.inject(AnnotationPipelineStateService);
    pipelineStateService.pipelines.set([]);
    jest.clearAllMocks();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should get pipelines list on component init', () => {
    const getPipelinesSpy = jest.spyOn(jobsServiceMock, 'getAnnotationPipelines');
    component.ngOnInit();
    expect(getPipelinesSpy).toHaveBeenCalledWith();
    expect(component.pipelines).toStrictEqual(mockPipelines);
  });

  it('should set up web socket communication on component init', () => {
    const getSocketNotificationSpy = jest.spyOn(socketNotificationsServiceMock, 'getPipelineNotifications');
    component.ngOnInit();
    expect(getSocketNotificationSpy).toHaveBeenCalledWith();
    expect(component.pipelines[0].status).toBe('unloaded');
  });

  it('should set temporary pipeline id on notification arrival if id is not set', () => {
    const notifications = new Subject<PipelineNotification>();
    jest.spyOn(socketNotificationsServiceMock, 'getPipelineNotifications')
      .mockReturnValue(notifications.asObservable());
    component.currentTemporaryPipelineId = '';
    component.currentTemporaryPipelineStatus = null;
    component.ngOnInit();

    // The user has unsaved edits (an autoSave is in flight, so the client is
    // awaiting a temp id) -- a WS frame for an unknown pipeline is adopted.
    component.currentPipelineText = 'edited config';
    notifications.next(new PipelineNotification('215', 'loading'));

    expect(component.currentTemporaryPipelineId).toBe('215');
    expect(component.currentTemporaryPipelineStatus).toBe('loading');
    expect(pipelineStateService.currentTemporaryPipelineId()).toBe('215');
  });

  it('ignores an unknown-pipeline resync frame when there are no unsaved edits (post-refresh)', () => {
    // On (re)connect the backend resyncs the session's temporary pipeline
    // status. After a refresh the editor shows the default pipeline unchanged,
    // so that frame must not resurrect the stale temp id and hijack queries.
    const notifications = new Subject<PipelineNotification>();
    jest.spyOn(socketNotificationsServiceMock, 'getPipelineNotifications')
      .mockReturnValue(notifications.asObservable());
    component.ngOnInit();

    // ngOnInit loaded the default pipeline; currentPipelineText matches it, so
    // isPipelineChanged() is false.
    notifications.next(new PipelineNotification('215', 'loading'));

    expect(component.currentTemporaryPipelineId).toBe('');
    expect(pipelineStateService.currentTemporaryPipelineId()).toBe('');
  });

  it('ignores a resync frame that arrives before pipelines finish loading (refresh race)', () => {
    // On refresh the backend resync frame can beat the getPipelines response.
    // In that window pipelinesLoaded is false, no pipeline is selected and the
    // editor is empty -- where isPipelineChanged() is spuriously true. The stale
    // temp id must NOT be adopted, otherwise the status bar fires a second
    // getPipelineInfo request for the temp pipeline that overrides the default.
    const pipelines = new Subject<Pipeline[]>();
    // mockReturnValueOnce so the never-emitting fetch doesn't leak to later
    // tests (beforeEach only clears mock calls, not implementations).
    jest.spyOn(jobsServiceMock, 'getAnnotationPipelines').mockReturnValueOnce(pipelines.asObservable());
    const notifications = new Subject<PipelineNotification>();
    jest.spyOn(socketNotificationsServiceMock, 'getPipelineNotifications')
      .mockReturnValueOnce(notifications.asObservable());
    // Force the async fetch path (empty cache) so getPipelines stays pending.
    pipelineStateService.pipelines.set([]);

    component.ngOnInit();
    // Reproduce the fresh-load window: nothing selected, editor empty, no temp.
    component.selectedPipeline = null;
    component.currentPipelineText = '';
    component.currentTemporaryPipelineId = '';
    notifications.next(new PipelineNotification('215', 'loading'));

    expect(component.pipelinesLoaded).toBe(false);
    expect(component.currentTemporaryPipelineId).toBe('');
    expect(pipelineStateService.currentTemporaryPipelineId()).toBe('');
  });

  it('should update the tracked temporary pipeline when a follow-up notification arrives', () => {
    const notifications = new Subject<PipelineNotification>();
    jest.spyOn(socketNotificationsServiceMock, 'getPipelineNotifications')
      .mockReturnValue(notifications.asObservable());
    component.ngOnInit();
    component.currentPipelineText = 'edited config';

    // The first notification adopts '215' as the temporary pipeline being built.
    notifications.next(new PipelineNotification('215', 'loading'));
    expect(component.currentTemporaryPipelineId).toBe('215');

    // A follow-up notification for that same temp id updates its status/error.
    notifications.next(
      new PipelineNotification('215', 'failed', 'Invalid configuration, reason: boom')
    );
    expect(component.currentTemporaryPipelineStatus).toBe('failed');
    expect(component.currentTemporaryPipelineError).toBe('Invalid configuration, reason: boom');
    expect(pipelineStateService.currentTemporaryPipelineStatus()).toBe('failed');
  });

  it('does not reconnect for non-close events', () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const setupSpy = jest.spyOn(component as any, 'setupPipelineWebSocketConnection');
    jest.spyOn(socketNotificationsServiceMock, 'getPipelineNotifications')
      .mockReturnValueOnce(throwError(() => ({ type: 'other' })));

    component.ngOnInit();
    expect(setupSpy).toHaveBeenCalledTimes(1);

    const unsubSpy = jest.spyOn(component.socketNotificationSubscription, 'unsubscribe');

    expect(unsubSpy).not.toHaveBeenCalled();
    expect(setupSpy).toHaveBeenCalledTimes(1);
  });

  it('reconnects on an Event error (unified single reconnect path)', () => {
    // The service no longer swallows Event errors; the abnormal-drop Event
    // surfaces here and must drive the one consumer-owned reconnect, same as a
    // CloseEvent.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const setupSpy = jest.spyOn(component as any, 'setupPipelineWebSocketConnection');
    const reopenSpy = jest.spyOn(socketNotificationsServiceMock, 'reopenConnection').mockClear();
    const unsubSpy = jest.spyOn(component.socketNotificationSubscription, 'unsubscribe');
    jest.spyOn(socketNotificationsServiceMock, 'getPipelineNotifications')
      .mockReturnValueOnce(throwError(() => new Event('network error')))
      .mockReturnValueOnce(of(new PipelineNotification('id1', 'unloaded')));

    component.ngOnInit();

    expect(unsubSpy).toHaveBeenCalledWith();
    expect(reopenSpy).toHaveBeenCalledWith();
    expect(setupSpy).toHaveBeenCalledTimes(2);
  });

  it('should check if user is logged in on component init', () => {
    component.ngOnInit();
    expect(component.isUserLoggedIn).toBe(true);
  });

  it('should check editor\'s options on component init', () => {
    expect(component.yamlEditorOptions).toStrictEqual({
      language: 'yaml',
      minimap: {
        enabled: false
      },
      lineNumbers: 'off',
      folding: false,
      stickyScroll: {
        enabled: false,
      },
      scrollBeyondLastLine: false,
      theme: 'annotationPipelineTheme',
      automaticLayout: true
    });
  });

  it('should filter pipelines list in dropdown when typing in input', () => {
    expect(component.filteredPipelines).toStrictEqual(mockPipelines);
    component.dropdownControl.setValue('nAmE2');
    expect(component.filteredPipelines).toStrictEqual([mockPipelines[1]]);
  });

  it('should not filter pipelines list in dropdown when typing spaces in input', () => {
    expect(component.filteredPipelines).toStrictEqual(mockPipelines);
    component.dropdownControl.setValue('  ');
    expect(component.filteredPipelines).toStrictEqual(mockPipelines);
  });

  it('should expand editor and hide elements', () => {
    Object.defineProperty(window, 'innerWidth', {
      writable: true,
      configurable: true,
      value: 1500,
    });
    component.expandTextarea();
    expect(pipelineStateService.hideComponents()).toBe(true);
  });

  it('should auto shrink editor and display elements', () => {
    component.shrinkTextarea();
    expect(pipelineStateService.hideComponents()).toBe(false);
  });


  it('should select new pipeline and emit to parent', () => {
    const setDropdownValueSpy = jest.spyOn(component.dropdownControl, 'setValue');
    component.onPipelineClick(new Pipeline('1', 'other pipeline', 'config', 'default', 'loaded'));
    expect(component.selectedPipeline.id).toBe('1');
    expect(component.selectedPipeline.name).toBe('other pipeline');
    expect(component.selectedPipeline.content).toBe('config');
    expect(pipelineStateService.selectedPipelineId()).toBe('1');
    expect(setDropdownValueSpy).toHaveBeenCalledWith('other pipeline');
  });

  it('should not set new pipeline if invalid one', () => {
    const initialId = pipelineStateService.selectedPipelineId();
    const setDropdownValueSpy = jest.spyOn(component.dropdownControl, 'setValue');

    component.onPipelineClick(null);
    expect(component.selectedPipeline.id).toBe('id1');
    expect(component.selectedPipeline.content).toBe('content1');
    expect(pipelineStateService.selectedPipelineId()).toBe(initialId);
    expect(setDropdownValueSpy).not.toHaveBeenCalledWith();
  });

  it('should select pipeline by providing its name', () => {
    component.selectedPipeline = null;
    component.selectPipelineByName('name3');
    expect(component.selectedPipeline).toStrictEqual(new Pipeline('id3', 'name3', 'content3', 'user', 'loaded'));
  });

  it('should get pipeline status after each pipeline select', () => {
    component.onPipelineClick(new Pipeline('1', 'other pipeline', 'config', 'default', 'loaded'));
    expect(component.pipelineInfo).toStrictEqual(new PipelineInfo(20, 4, ['hg19_annotatable'], ['gene_list']));
  });

  it('should reset component state', () => {
    component.selectedPipeline = mockPipelines[2];
    component.resetState();
    expect(component.selectedPipeline.id).toBe('id1');
    expect(component.selectedPipeline.content).toBe('content1');
  });

  it('should success config validation', () => {
    const configValidationSpy = jest.spyOn(jobsServiceMock, 'validatePipelineConfig');
    component.currentPipelineText = 'config content';
    component.isConfigValid();
    expect(configValidationSpy).toHaveBeenCalledWith('config content');
    expect(component.configError).toBe('');
    expect(pipelineStateService.isConfigValid()).toBe(true);
  });

  // The editor is bound to onConfigChanged, not to isConfigValid: a keystroke
  // used to be a POST to /api/pipelines/validate, which both floods the
  // endpoint and makes it impossible to rate-limit (iossifovlab/gain#635).
  it('should validate once for a burst of edits, not once per keystroke', () => {
    jest.useFakeTimers();
    const configValidationSpy = jest.spyOn(jobsServiceMock, 'validatePipelineConfig');
    component.pipelinesLoaded = true;

    component.currentPipelineText = 'a';
    component.onConfigChanged();
    component.currentPipelineText = 'ab';
    component.onConfigChanged();
    component.currentPipelineText = 'abc';
    component.onConfigChanged();

    expect(configValidationSpy).not.toHaveBeenCalled();

    jest.advanceTimersByTime(VALIDATE_DEBOUNCE_MS);

    expect(configValidationSpy).toHaveBeenCalledTimes(1);
    // The last edit wins -- validating an intermediate state would report
    // errors about text the user has already moved past.
    expect(configValidationSpy).toHaveBeenCalledWith('abc');
    jest.useRealTimers();
  });

  // Debouncing the request must not debounce the *invalidation*. Consumers
  // gate on isConfigValid (the Annotate/Create buttons, autoSavePipeline);
  // if it stayed true while the user typed, a click landing inside the
  // debounce window would act on text nothing had validated.
  it('should mark the config invalid immediately on edit, before validating', () => {
    jest.useFakeTimers();
    component.pipelinesLoaded = true;
    pipelineStateService.isConfigValid.set(true);

    component.currentPipelineText = 'edited';
    component.onConfigChanged();

    expect(pipelineStateService.isConfigValid()).toBe(false);
    jest.useRealTimers();
  });

  it('should publish the edited text immediately on edit', () => {
    jest.useFakeTimers();
    component.pipelinesLoaded = true;

    component.currentPipelineText = 'edited';
    component.onConfigChanged();

    expect(pipelineStateService.currentPipelineText()).toBe('edited');
    jest.useRealTimers();
  });

  // Without this, the whole debounce is bypassable by a one-word edit to the
  // template: binding (ngModelChange) straight back to isConfigValid leaves
  // every other spec in this file green, because they drive the component
  // API rather than the DOM.
  it('should route editor changes through the debounced handler', () => {
    jest.useFakeTimers();
    component.pipelines = mockPipelines;
    component.pipelinesLoaded = true;
    const configValidationSpy = jest.spyOn(jobsServiceMock, 'validatePipelineConfig');

    const monacoEditor = fixture.debugElement.query(By.css('ngx-monaco-editor'));
    monacoEditor.triggerEventHandler('ngModelChange', 'edited');

    expect(configValidationSpy).not.toHaveBeenCalled();

    jest.advanceTimersByTime(VALIDATE_DEBOUNCE_MS);

    expect(configValidationSpy).toHaveBeenCalledTimes(1);
    jest.useRealTimers();
  });

  it('should validate again after a later burst', () => {
    jest.useFakeTimers();
    const configValidationSpy = jest.spyOn(jobsServiceMock, 'validatePipelineConfig');
    component.pipelinesLoaded = true;

    component.currentPipelineText = 'first';
    component.onConfigChanged();
    jest.advanceTimersByTime(VALIDATE_DEBOUNCE_MS);

    component.currentPipelineText = 'second';
    component.onConfigChanged();
    jest.advanceTimersByTime(VALIDATE_DEBOUNCE_MS);

    expect(configValidationSpy).toHaveBeenCalledTimes(2);
    expect(configValidationSpy).toHaveBeenLastCalledWith('second');
    jest.useRealTimers();
  });

  it('should fail config validation', () => {
    const configValidationSpy = jest.spyOn(jobsServiceMock, 'validatePipelineConfig')
      .mockReturnValue(of('error message'));
    component.currentPipelineText = 'config content';
    component.isConfigValid();
    expect(configValidationSpy).toHaveBeenCalledWith('config content');
    expect(component.configError).toBe('error message');
    expect(pipelineStateService.isConfigValid()).toBe(false);
  });

  // A failed validation request is not a verdict on the config. The server
  // sheds validations when its bounded pool is full (iossifovlab/gain#659),
  // and the editor must not report that as "your pipeline is wrong": the
  // config was never looked at, and the same text will validate once the
  // backlog drains. Without an error handler the observable simply errors
  // out -- no message at all, and a disabled Annotate button that is
  // indistinguishable from an invalid config.
  it('should report a shed validation apart from an invalid config', () => {
    jest.spyOn(jobsServiceMock, 'validatePipelineConfig')
      .mockReturnValue(throwError(() => new Error('server is busy')));
    component.currentPipelineText = 'config content';

    component.isConfigValid();

    expect(component.validationUnavailable).toBe('server is busy');
    // Not an invalid config: configError is what says "your pipeline is
    // wrong", and it also disables the editing actions.
    expect(component.configError).toBe('');
    // ...but nothing confirmed the config either, so it stays unvalidated.
    expect(pipelineStateService.isConfigValid()).toBe(false);
  });

  it('should clear a shed-validation message once validation succeeds', () => {
    jest.spyOn(jobsServiceMock, 'validatePipelineConfig')
      .mockReturnValue(throwError(() => new Error('server is busy')));
    component.currentPipelineText = 'config content';
    component.isConfigValid();

    jest.spyOn(jobsServiceMock, 'validatePipelineConfig').mockReturnValue(of(''));
    component.isConfigValid();

    expect(component.validationUnavailable).toBe('');
    expect(pipelineStateService.isConfigValid()).toBe(true);
  });

  // Distinct in the DOM, not just in a field: the whole point is that the
  // user can tell "the server is busy" from "your pipeline is wrong", and
  // .error-message is what the latter looks like.
  it('should render a shed validation in its own message element', () => {
    jest.spyOn(jobsServiceMock, 'validatePipelineConfig')
      .mockReturnValue(throwError(() => new Error('server is busy')));
    component.pipelines = mockPipelines;
    component.currentPipelineText = 'config content';

    component.isConfigValid();
    fixture.detectChanges();

    const message = (fixture.nativeElement as HTMLElement).querySelector('.validation-unavailable-message');
    expect(message.textContent).toContain('server is busy');
    expect(fixture.debugElement.query(By.css('.error-message'))).toBeNull();
  });

  // The message belongs to the text that was being validated. Loading
  // another pipeline replaces that text, so keeping it would report a stale
  // failure about a config the user has moved away from.
  it('should drop a shed-validation message when another pipeline is loaded', () => {
    jest.spyOn(jobsServiceMock, 'validatePipelineConfig')
      .mockReturnValue(throwError(() => new Error('server is busy')));
    component.currentPipelineText = 'config content';
    component.isConfigValid();

    component.onPipelineClick(mockPipelines[1]);

    expect(component.validationUnavailable).toBe('');
  });

  it('should get pipeline status info after successful validation', () => {
    jest.spyOn(jobsServiceMock, 'validatePipelineConfig').mockReturnValue(of(''));
    jest.spyOn(component, 'isPipelineChanged').mockReturnValue(true);
    jest.spyOn(component, 'autoSave').mockReturnValue(of('id'));
    const getPipelineInfoSpy = jest.spyOn(
      component as unknown as { getPipelineInfo: () => void },
      'getPipelineInfo'
    );

    component.selectedPipeline = mockPipelines[0];
    component.currentPipelineText = 'config content';
    component.pipelinesLoaded = true;

    component.isConfigValid();
    expect(getPipelineInfoSpy).toHaveBeenCalledWith();
  });

  it('should display \' *\' when pipeline config is changed and not saved', () => {
    jest.spyOn(component, 'isPipelineChanged').mockReturnValue(true);
    component.dropdownControl.setValue('pipeline-name');
    component.selectedPipeline = new Pipeline('1', 'pipeline-name', 'content', 'user', 'loaded');
    component.isConfigValid();
    expect(component.dropdownControl.value).toBe('pipeline-name *');
  });

  it('should not display \' *\' if it is already displayed', () => {
    jest.spyOn(component, 'isPipelineChanged').mockReturnValue(true);
    component.dropdownControl.setValue('pipeline-name *');
    component.selectedPipeline = new Pipeline('1', 'pipeline-name', 'content', 'user', 'loaded');
    component.isConfigValid();
    expect(component.dropdownControl.value).toBe('pipeline-name *');
  });

  it('should remove \' *\' when pipeline changes are reverted, update id in parent and clear temporary id', () => {
    jest.spyOn(component, 'isPipelineChanged').mockReturnValue(false);
    component.dropdownControl.setValue('pipeline-name *');
    component.currentTemporaryPipelineId = '1234';
    component.selectedPipeline = new Pipeline('1', 'pipeline-name', 'content', 'user', 'loaded');

    component.isConfigValid();
    expect(component.dropdownControl.value).toBe('pipeline-name');
    expect(pipelineStateService.selectedPipelineId()).toBe('1');
    expect(component.currentTemporaryPipelineId).toBe('');
  });

  it('should not add \' *\' when pipeline config is has not been changed', () => {
    jest.spyOn(component, 'isPipelineChanged').mockReturnValue(false);
    component.dropdownControl.setValue('pipeline-name');
    component.selectedPipeline = new Pipeline('1', 'pipeline-name', 'content', 'user', 'loaded');
    component.isConfigValid();
    expect(component.dropdownControl.value).toBe('pipeline-name');
  });

  it('should clear selected pipeline', () => {
    const setDropdownValueSpy = jest.spyOn(component.dropdownControl, 'setValue');
    component.clearPipeline();
    expect(component.selectedPipeline).toBeNull();
    expect(component.currentPipelineText).toBe('');
    expect(setDropdownValueSpy).toHaveBeenCalledWith('');
  });

  it('should not set pipeline name in dropdown input if it contains text', () => {
    const setDropdownValueSpy = jest.spyOn(component.dropdownControl, 'setValue');
    component.displayPipelineNameInInput();
    expect(setDropdownValueSpy).not.toHaveBeenCalledWith();
  });

  it('should set current pipeline name in dropdown input if it is empty', () => {
    const setDropdownValueSpy = jest.spyOn(component.dropdownControl, 'setValue');
    component.selectedPipeline = new Pipeline('1', 'pipeline-name', 'content', 'user', 'loaded');
    component.dropdownControl.setValue('');
    component.displayPipelineNameInInput();
    expect(setDropdownValueSpy).toHaveBeenCalledWith('pipeline-name');
  });

  it('should save pipeline name', () => {
    const closeModalSpy = jest.spyOn(mockMatDialogRef, 'close');
    component.saveName('pipeline-name');
    expect(closeModalSpy).toHaveBeenCalledWith('pipeline-name');
  });

  it('should cancel setting pipeline name', () => {
    const closeModalSpy = jest.spyOn(mockMatDialogRef, 'close');
    component.cancel();
    expect(closeModalSpy).toHaveBeenCalledWith();
  });

  it('should not save pipeline if the name exists', () => {
    const closeModalSpy = jest.spyOn(mockMatDialogRef, 'close');
    component.saveName('name3');
    expect(component.invalidPipelineName).toBe(true);
    expect(closeModalSpy).not.toHaveBeenCalledWith();
  });

  it('should save pipeline and trigger pipelines query if new name is set', () => {
    // The post-save GET response carries the just-saved content (server's
    // view of pipeline 4 matches what the user typed).
    const updatedMockPipelines = [
      new Pipeline('1', 'id1', 'content1', 'default', 'loaded'),
      new Pipeline('2', 'id2', 'content2', 'default', 'loaded'),
      new Pipeline('3', 'id3', 'content3', 'default', 'loaded'),
      new Pipeline('4', 'pipeline-name', 'mock config', 'user', 'loaded'),
    ];

    jest.spyOn(mockMatRef, 'open').mockReturnValueOnce(mockMatDialogRef);
    jest.spyOn(mockMatDialogRef, 'afterClosed').mockReturnValueOnce(of('pipeline-name'));
    const savePipelineSpy = jest.spyOn(annotationPipelineServiceMock, 'savePipeline').mockReturnValueOnce(of('4'));
    const getAnnotationPipelinesSpy = jest.spyOn(jobsServiceMock, 'getAnnotationPipelines')
      .mockReturnValueOnce(of(updatedMockPipelines));

    component.currentPipelineText = 'mock config';

    component.saveAs();
    expect(savePipelineSpy).toHaveBeenCalledWith('', 'pipeline-name', 'mock config');
    expect(getAnnotationPipelinesSpy).toHaveBeenCalledWith();
    expect(component.selectedPipeline.id).toBe('4');
    expect(component.selectedPipeline.name).toBe('pipeline-name');
    // No asterisk: currentPipelineText matches the just-saved pipeline.
    expect(component.dropdownControl.value).toBe('pipeline-name');
    expect(component.isPipelineChanged()).toBe(false);
  });

  it('should not save pipeline and trigger pipelines query if new pipeline has no name', () => {
    jest.spyOn(mockMatRef, 'open').mockReturnValueOnce(mockMatDialogRef);
    jest.spyOn(mockMatDialogRef, 'afterClosed').mockReturnValueOnce(of(null));
    const savePipelineSpy = jest.spyOn(annotationPipelineServiceMock, 'savePipeline');
    const getAnnotationPipelinesSpy = jest.spyOn(jobsServiceMock, 'getAnnotationPipelines');

    component.currentPipelineText = 'mock config';

    component.saveAs();
    expect(savePipelineSpy).not.toHaveBeenCalledWith();
    expect(getAnnotationPipelinesSpy).not.toHaveBeenCalledTimes(2);
    expect(component.selectedPipeline.id).toBe('id1');
  });

  it('should delete pipeline', () => {
    const deletePipelineSpy = jest.spyOn(annotationPipelineServiceMock, 'deletePipeline');
    const selectNewPipelineSpy = jest.spyOn(component, 'onPipelineClick');

    component.selectedPipeline = new Pipeline('1', 'name', 'content', 'user', 'loaded');

    component.delete();
    expect(deletePipelineSpy).toHaveBeenCalledWith('1');
    expect(selectNewPipelineSpy).toHaveBeenCalledWith(mockPipelines[0]);
  });

  it('should save pipeline and update list with pipelines', () => {
    const updatedMockPipelines: Pipeline[] = [
      new Pipeline('id1', 'name1', 'content1', 'default', 'loaded'),
      new Pipeline('id2', 'name2', 'content2', 'default', 'loaded'),
      new Pipeline('id3', 'name3', 'new content', 'user', 'loaded'),
    ];

    const savePipelineSpy = jest.spyOn(annotationPipelineServiceMock, 'savePipeline').mockReturnValueOnce(of('id3'));
    jest.spyOn(jobsServiceMock, 'getAnnotationPipelines')
      .mockReturnValueOnce(of(updatedMockPipelines));

    component.selectedPipeline = mockPipelines[2];
    component.currentPipelineText = 'new content';

    component.save();
    expect(savePipelineSpy).toHaveBeenCalledWith('id3', 'name3', 'new content');
    expect(component.selectedPipeline.id).toBe('id3');
    expect(component.selectedPipeline.name).toBe('name3');
  });

  it('should request pipelines when user has logged in', () => {
    pipelineStateService.pipelines.set([
      new Pipeline(
        'pipeline/hg38_clinical_annotation',
        'pipeline/hg38_clinical_annotation',
        // eslint-disable-next-line @stylistic/max-len
        'preamble:\n   input_reference_genome: hg38/genomes/GRCh38-hg38\n   summary: Clinical Annotation Pipeline \n   description: This is a pipeline to annotate with Clinical resources  \n\nannotators:\n\n- effect_annotator:\n    gene_models: hg38/gene_models/MANE/1.3 \n    genome: hg38/genomes/GRCh38.p13\n    attributes:\n    - name: worst_effect_MANE_1_3\n      source: worst_effect \n    - name: effect_details_MANE_1_3\n      source: effect_details \n    - name: gene_effects_MANE_1_3\n      source: gene_effects \n      \n- normalize_allele_annotator:\n    genome: hg38/genomes/GRCh38-hg38\n\n- allele_score_annotator:\n    resource_id: hg38/scores/dbSNP\n    input_annotatable: normalized_allele\n    attributes:\n    - name: dbSNP_rs_number\n      source: RS    \n    \n- allele_score_annotator:\n    resource_id: hg38/variant_frequencies/gnomAD_4.1.0/exomes/ALL\n    input_annotatable: normalized_allele\n\n- allele_score_annotator:\n    resource_id: hg38/variant_frequencies/gnomAD_4.1.0/genomes/ALL\n    input_annotatable: normalized_allele\n\n- allele_score_annotator:\n    resource_id: hg38/scores/ClinVar_20240730\n    input_annotatable: normalized_allele\n    attributes:\n    - name: clinical_significance\n      source: CLNSIG    \n    - name: clinical_disease_name\n      source: CLNDN \n\n- allele_score_annotator: \n    resource_id: hg38/scores/CADD_v1.7\n    attributes:\n    - name: CADD_raw_score\n      source: cadd_raw    \n    - name: CADD_phred_score\n      source: cadd_phred \n\n- allele_score_annotator: \n    resource_id: hg38/scores/AlphaMissense\n    attributes:\n    - name: AlphaMissense_pathogenicity\n      source: am_pathogenicity\n    - name: AlphaMissense_class\n      source: am_class \n\n- liftover_annotator:\n    chain: liftover/hg38_to_hg19\n    source_genome: hg38/genomes/GRCh38-hg38\n    target_genome: hg19/genomes/GATK_ResourceBundle_5777_b37_phiX174\n    attributes:\n    - source: liftover_annotatable\n      name: hg19_annotatable\n      internal: true\n\n- allele_score_annotator:\n    resource_id: hg19/scores/MPC\n    input_annotatable: hg19_annotatable\n    attributes:\n    - name: MPC_score\n      source: MPC\n\n- effect_annotator:\n    gene_models: hg38/gene_models/GENCODE/48/basic/ALL\n    genome: hg38/genomes/GRCh38.p13\n    attributes:\n    - name: worst_effect_GENCODE_48\n      source: worst_effect \n    - name: effect_details_GENCODE_48\n      source: effect_details \n    - name: gene_effects_GENCODE_48\n      source: gene_effects \n    - name: gene_list \n      internal: true\n          \n- gene_score_annotator:\n    resource_id: gene_properties/gene_scores/pLI\n    input_gene_list: gene_list\n    attributes:\n    - name: pLI_rank_all\n      source: pLI_rank\n    - name: pLI_rank_min\n      source: pLI_rank\n      gene_aggregator: min \n      \n- gene_score_annotator:\n    resource_id: gene_properties/gene_scores/LOEUF\n    input_gene_list: gene_list\n    attributes:\n    - name: LOEUF_rank_all\n      source: LOEUF_rank\n    - name: LOEUF_rank_min\n      source: LOEUF_rank\n      gene_aggregator: min ',
        'default',
        'loaded'
      ),
      new Pipeline(
        '1946',
        'cadd',
        // eslint-disable-next-line @stylistic/max-len
        '\n- allele_score_annotator:\n    resource_id: hg38/scores/CADD_v1.7\n    attributes:\n    - name: cadd_raw\n      source: cadd_raw\n      internal: null\n    - name: cadd_phred\n      source: cadd_phred\n      internal: null\n',
        'user',
        'unloaded'
      ),
    ]);
    pipelineStateService.loadedWhileLoggedIn.set(false);

    const getPipelinesSpy = jest.spyOn(jobsServiceMock, 'getAnnotationPipelines');
    component.ngOnInit();

    expect(getPipelinesSpy).toHaveBeenCalledWith();
  });

  it('should not request pipelines when user has not changed', () => {
    pipelineStateService.pipelines.set([
      new Pipeline(
        'pipeline/hg38_clinical_annotation',
        'pipeline/hg38_clinical_annotation',
        // eslint-disable-next-line @stylistic/max-len
        'preamble:\n   input_reference_genome: hg38/genomes/GRCh38-hg38\n   summary: Clinical Annotation Pipeline \n   description: This is a pipeline to annotate with Clinical resources  \n\nannotators:\n\n- effect_annotator:\n    gene_models: hg38/gene_models/MANE/1.3 \n    genome: hg38/genomes/GRCh38.p13\n    attributes:\n    - name: worst_effect_MANE_1_3\n      source: worst_effect \n    - name: effect_details_MANE_1_3\n      source: effect_details \n    - name: gene_effects_MANE_1_3\n      source: gene_effects \n      \n- normalize_allele_annotator:\n    genome: hg38/genomes/GRCh38-hg38\n\n- allele_score_annotator:\n    resource_id: hg38/scores/dbSNP\n    input_annotatable: normalized_allele\n    attributes:\n    - name: dbSNP_rs_number\n      source: RS    \n    \n- allele_score_annotator:\n    resource_id: hg38/variant_frequencies/gnomAD_4.1.0/exomes/ALL\n    input_annotatable: normalized_allele\n\n- allele_score_annotator:\n    resource_id: hg38/variant_frequencies/gnomAD_4.1.0/genomes/ALL\n    input_annotatable: normalized_allele\n\n- allele_score_annotator:\n    resource_id: hg38/scores/ClinVar_20240730\n    input_annotatable: normalized_allele\n    attributes:\n    - name: clinical_significance\n      source: CLNSIG    \n    - name: clinical_disease_name\n      source: CLNDN \n\n- allele_score_annotator: \n    resource_id: hg38/scores/CADD_v1.7\n    attributes:\n    - name: CADD_raw_score\n      source: cadd_raw    \n    - name: CADD_phred_score\n      source: cadd_phred \n\n- allele_score_annotator: \n    resource_id: hg38/scores/AlphaMissense\n    attributes:\n    - name: AlphaMissense_pathogenicity\n      source: am_pathogenicity\n    - name: AlphaMissense_class\n      source: am_class \n\n- liftover_annotator:\n    chain: liftover/hg38_to_hg19\n    source_genome: hg38/genomes/GRCh38-hg38\n    target_genome: hg19/genomes/GATK_ResourceBundle_5777_b37_phiX174\n    attributes:\n    - source: liftover_annotatable\n      name: hg19_annotatable\n      internal: true\n\n- allele_score_annotator:\n    resource_id: hg19/scores/MPC\n    input_annotatable: hg19_annotatable\n    attributes:\n    - name: MPC_score\n      source: MPC\n\n- effect_annotator:\n    gene_models: hg38/gene_models/GENCODE/48/basic/ALL\n    genome: hg38/genomes/GRCh38.p13\n    attributes:\n    - name: worst_effect_GENCODE_48\n      source: worst_effect \n    - name: effect_details_GENCODE_48\n      source: effect_details \n    - name: gene_effects_GENCODE_48\n      source: gene_effects \n    - name: gene_list \n      internal: true\n          \n- gene_score_annotator:\n    resource_id: gene_properties/gene_scores/pLI\n    input_gene_list: gene_list\n    attributes:\n    - name: pLI_rank_all\n      source: pLI_rank\n    - name: pLI_rank_min\n      source: pLI_rank\n      gene_aggregator: min \n      \n- gene_score_annotator:\n    resource_id: gene_properties/gene_scores/LOEUF\n    input_gene_list: gene_list\n    attributes:\n    - name: LOEUF_rank_all\n      source: LOEUF_rank\n    - name: LOEUF_rank_min\n      source: LOEUF_rank\n      gene_aggregator: min ',
        'default',
        'loaded'
      ),
      new Pipeline(
        '1946',
        'cadd',
        // eslint-disable-next-line @stylistic/max-len
        '\n- allele_score_annotator:\n    resource_id: hg38/scores/CADD_v1.7\n    attributes:\n    - name: cadd_raw\n      source: cadd_raw\n      internal: null\n    - name: cadd_phred\n      source: cadd_phred\n      internal: null\n',
        'user',
        'unloaded'
      ),
    ]);
    pipelineStateService.loadedWhileLoggedIn.set(false);

    userServiceMock.userData.next({
      email: 'email', loggedIn: false, isAdmin: false,
      limitations: { dailyJobs: 5, filesize: '64M', todayJobsCount: 4, diskSpace: '1000' }
    });

    const getPipelinesSpy = jest.spyOn(jobsServiceMock, 'getAnnotationPipelines');
    component.ngOnInit();
    expect(getPipelinesSpy).not.toHaveBeenCalledWith();

    // Reset for subsequent tests.
    userServiceMock.userData.next({
      email: 'email', loggedIn: true, isAdmin: false,
      limitations: { dailyJobs: 5, filesize: '64M', todayJobsCount: 4, diskSpace: '1000' }
    });
  });

  it('initial-load preserves user-typed text that arrives during a slow GET /api/pipelines (tb-l7c)', () => {
    // Regression test for tb-l7c (CI gain-web-e2e #158). When the user
    // navigates mid-flight before the first getPipelines GET returns,
    // the new component's ngOnInit fires a SECOND GET. If the user
    // types into the editor before the late GET response lands, the
    // OLD onPipelineClick(pipelines[0]) call would reset
    // currentPipelineText to the default pipeline's content — silently
    // dropping the user's input so isPipelineChanged() returns false
    // and the autoSave that customDefaultPipeline waits for never
    // fires.
    pipelineStateService.pipelines.set([]);
    component.pipelines = [];
    component.selectedPipeline = null;
    component.currentPipelineText = '';

    const initialPipelines = new Subject<Pipeline[]>();
    jest.spyOn(jobsServiceMock, 'getAnnotationPipelines')
      .mockReturnValueOnce(initialPipelines.asObservable());

    component.ngOnInit();

    // The GET is in flight. The user clicks "draft New pipeline" then
    // types yaml — currentPipelineText now reflects the typed text.
    component.currentPipelineText = 'user-typed effect_annotator yaml';

    // Late GET response lands.
    initialPipelines.next(mockPipelines);
    initialPipelines.complete();

    // The typed text must survive — not clobbered by mockPipelines[0].content.
    expect(component.currentPipelineText).toBe('user-typed effect_annotator yaml');
    // The first pipeline is selected for dropdown / state purposes.
    expect(component.selectedPipeline.id).toBe('id1');
    // isPipelineChanged() reflects the divergence.
    expect(component.isPipelineChanged()).toBe(true);
  });

  it('initial-load with empty buffer falls back to onPipelineClick semantics (tb-l7c)', () => {
    // Counterpart to the above: a genuine fresh-load (currentPipelineText
    // empty) should still adopt the first pipeline's content via
    // onPipelineClick — only the user-has-typed path goes through
    // selectPipelineAfterSave.
    pipelineStateService.pipelines.set([]);
    component.pipelines = [];
    component.selectedPipeline = null;
    component.currentPipelineText = '';

    component.ngOnInit();

    expect(component.currentPipelineText).toBe('content1');
    expect(component.selectedPipeline.id).toBe('id1');
    expect(component.isPipelineChanged()).toBe(false);
  });

  // Arm one websocket drop and reconnect. The test drives each step: the
  // drop, the reconnect emission, and the open of the new socket.
  function armReconnectSteps(): {
    drop: () => void;
    reconnected: Subject<void>;
    resubscribed: Subject<PipelineNotification>;
    } {
    const notifications = new Subject<PipelineNotification>();
    const resubscribed = new Subject<PipelineNotification>();
    jest.spyOn(socketNotificationsServiceMock, 'getPipelineNotifications')
      .mockReturnValueOnce(notifications.asObservable())
      .mockReturnValueOnce(resubscribed.asObservable());
    const reconnected = new Subject<void>();
    jest.spyOn(socketNotificationsServiceMock, 'reopenConnection')
      .mockReturnValueOnce(reconnected.asObservable());
    return {
      drop: () => notifications.error(new Event('network error')),
      reconnected: reconnected,
      resubscribed: resubscribed,
    };
  }

  // Arm one websocket drop and reconnect; the returned function fires the
  // drop, the reconnect and the open of the new socket.
  function armReconnect(): () => void {
    const { drop, reconnected } = armReconnectSteps();
    return () => {
      drop();
      reconnected.next();
      socketNotificationsServiceMock.opened.next();
    };
  }

  it('refetches the pipelines after a reconnect only once the new socket opens', () => {
    const { drop, reconnected } = armReconnectSteps();
    component.ngOnInit();
    // A list loaded under another identity makes the refetch a server GET.
    pipelineStateService.loadedWhileLoggedIn.set(!component.isUserLoggedIn);
    const getPipelinesSpy = jest.spyOn(jobsServiceMock, 'getAnnotationPipelines').mockClear();

    drop();
    reconnected.next();
    expect(getPipelinesSpy).not.toHaveBeenCalled();

    socketNotificationsServiceMock.opened.next();
    expect(getPipelinesSpy).toHaveBeenCalledTimes(1);
  });

  it('sends no catch-up request for a reconnect that drops before it opens', () => {
    const { drop, reconnected, resubscribed } = armReconnectSteps();
    jest.spyOn(socketNotificationsServiceMock, 'getPipelineNotifications')
      .mockReturnValueOnce(NEVER);
    jest.spyOn(socketNotificationsServiceMock, 'reopenConnection')
      .mockReturnValueOnce(NEVER);
    component.ngOnInit();
    pipelineStateService.loadedWhileLoggedIn.set(!component.isUserLoggedIn);
    const getPipelinesSpy = jest.spyOn(jobsServiceMock, 'getAnnotationPipelines').mockClear();

    drop();
    reconnected.next();
    resubscribed.error(new Event('network error'));
    // The second reconnect never emits, so this open belongs to no
    // reconnect that a consumer still waits on.
    socketNotificationsServiceMock.opened.next();

    expect(getPipelinesSpy).not.toHaveBeenCalled();
  });

  it('subscribes to the pipeline notifications again before the catch-up refetch starts', () => {
    const { drop, reconnected, resubscribed } = armReconnectSteps();
    component.ngOnInit();
    pipelineStateService.loadedWhileLoggedIn.set(!component.isUserLoggedIn);
    let subscribedAtRefetch: boolean | undefined;
    jest.spyOn(jobsServiceMock, 'getAnnotationPipelines').mockImplementationOnce(() => {
      subscribedAtRefetch = resubscribed.observed;
      return of(mockPipelines);
    });

    drop();
    reconnected.next();
    socketNotificationsServiceMock.opened.next();

    expect(subscribedAtRefetch).toBe(true);
  });

  it('New pipeline validates the cleared editor without waiting for the editor to report the change (#693)', () => {
    // Until monaco has loaded, the editor only stores the text it is given
    // and later loads it without reporting a change, so the clear must start
    // its own validation. The test bed's editor reports nothing either.
    jest.useFakeTimers();
    try {
      component.ngOnInit();
      const validateSpy = jest.spyOn(jobsServiceMock, 'validatePipelineConfig');

      component.clearPipeline();
      jest.advanceTimersByTime(VALIDATE_DEBOUNCE_MS);

      expect(validateSpy).toHaveBeenCalledTimes(1);
      expect(validateSpy).toHaveBeenCalledWith('');
      expect(pipelineStateService.isConfigValid()).toBe(true);
    } finally {
      jest.useRealTimers();
    }
  });

  it('a reconnect-driven pipeline-list refetch keeps an editor cleared by New pipeline (#693)', () => {
    const reconnect = armReconnect();
    component.ngOnInit();
    expect(component.selectedPipeline.id).toBe('id1');
    component.clearPipeline();
    // The cached list was loaded under another identity, so the refetch
    // goes to the server rather than restoring from the cache.
    pipelineStateService.loadedWhileLoggedIn.set(!component.isUserLoggedIn);

    reconnect();

    expect(component.currentPipelineText).toBe('');
    expect(component.selectedPipeline).toBeNull();
    expect(pipelineStateService.currentPipelineText()).toBe('');
    expect(pipelineStateService.selectedPipelineId()).toBe('');
  });

  it('a reconnect refetch sent during the initial load keeps an editor cleared before it answers (#693)', () => {
    // The refetch is sent while the initial GET is still in flight, but its
    // answer lands after the initial load selected a pipeline and the user
    // cleared the editor: whether to keep the editor is an answer-time call.
    pipelineStateService.pipelines.set([]);
    const reconnect = armReconnect();
    const initialLoad = new Subject<Pipeline[]>();
    const refetch = new Subject<Pipeline[]>();
    jest.spyOn(jobsServiceMock, 'getAnnotationPipelines')
      .mockReturnValueOnce(initialLoad.asObservable())
      .mockReturnValueOnce(refetch.asObservable());
    const fresh = TestBed.createComponent(AnnotationPipelineComponent).componentInstance;
    fresh.ngOnInit();
    reconnect();
    initialLoad.next(mockPipelines);
    initialLoad.complete();
    expect(fresh.selectedPipeline.id).toBe('id1');
    fresh.clearPipeline();

    refetch.next(mockPipelines);
    refetch.complete();

    expect(fresh.currentPipelineText).toBe('');
    expect(fresh.selectedPipeline).toBeNull();
    expect(pipelineStateService.currentPipelineText()).toBe('');
    expect(pipelineStateService.selectedPipelineId()).toBe('');
  });

  it('a reconnect-driven refetch selects the first pipeline when the initial load failed (#693)', () => {
    // The page opened while the backend was down: the initial GET errored,
    // nothing was ever loaded, and the reconnect refetch is what recovers it.
    pipelineStateService.pipelines.set([]);
    const reconnect = armReconnect();
    jest.spyOn(jobsServiceMock, 'getAnnotationPipelines')
      .mockReturnValueOnce(throwError(() => new Error('backend restarting')));
    const freshFixture = TestBed.createComponent(AnnotationPipelineComponent);
    const fresh = freshFixture.componentInstance;
    fresh.ngOnInit();
    expect(fresh.selectedPipeline).toBeFalsy();

    reconnect();

    expect(fresh.selectedPipeline.id).toBe('id1');
    expect(fresh.currentPipelineText).toBe('content1');
  });

  it('sends a validation suppressed during a pipeline-list reload once the reload completes (#693)', () => {
    jest.useFakeTimers();
    try {
      const reconnect = armReconnect();
      component.ngOnInit();
      pipelineStateService.loadedWhileLoggedIn.set(!component.isUserLoggedIn);
      const reloaded = new Subject<Pipeline[]>();
      jest.spyOn(jobsServiceMock, 'getAnnotationPipelines')
        .mockReturnValueOnce(reloaded.asObservable());
      reconnect();
      const validateSpy = jest.spyOn(jobsServiceMock, 'validatePipelineConfig');

      component.currentPipelineText = 'typed during the reload';
      component.onConfigChanged();
      jest.advanceTimersByTime(VALIDATE_DEBOUNCE_MS);
      expect(validateSpy).not.toHaveBeenCalled();
      component.currentPipelineText = 'typed during the reload, then more';
      reloaded.next(mockPipelines);
      reloaded.complete();

      expect(validateSpy).toHaveBeenCalledTimes(1);
      expect(validateSpy).toHaveBeenCalledWith('typed during the reload, then more');
      expect(pipelineStateService.isConfigValid()).toBe(true);
    } finally {
      jest.useRealTimers();
    }
  });

  // Load the list, then fire a reconnect whose refetch GET stays open until
  // the returned subject answers.
  function startHeldReconnectRefetch(): Subject<Pipeline[]> {
    const reconnect = armReconnect();
    component.ngOnInit();
    pipelineStateService.loadedWhileLoggedIn.set(!component.isUserLoggedIn);
    const reloaded = new Subject<Pipeline[]>();
    jest.spyOn(jobsServiceMock, 'getAnnotationPipelines')
      .mockReturnValueOnce(reloaded.asObservable());
    reconnect();
    return reloaded;
  }

  it('marks the config not valid on a keystroke during a pipeline-list reload, before it completes (#1777)', () => {
    jest.useFakeTimers();
    try {
      startHeldReconnectRefetch();
      expect(pipelineStateService.isConfigValid()).toBe(true);
      const validateSpy = jest.spyOn(jobsServiceMock, 'validatePipelineConfig');

      component.currentPipelineText = 'typed during the reload';
      component.onConfigChanged();

      // On the keystroke itself, before the debounce fires.
      expect(pipelineStateService.isConfigValid()).toBe(false);
      expect(pipelineStateService.currentPipelineText()).toBe('typed during the reload');

      jest.advanceTimersByTime(VALIDATE_DEBOUNCE_MS);

      expect(pipelineStateService.isConfigValid()).toBe(false);
      expect(validateSpy).not.toHaveBeenCalled();
    } finally {
      jest.useRealTimers();
    }
  });

  it('validates the latest text typed during a reload after it completes; an error stays not valid (#1777)', () => {
    jest.useFakeTimers();
    try {
      const reloaded = startHeldReconnectRefetch();
      const validateSpy = jest.spyOn(jobsServiceMock, 'validatePipelineConfig')
        .mockReturnValueOnce(of('invalid: bad yaml'));
      component.currentPipelineText = 'typed during the reload';
      component.onConfigChanged();
      jest.advanceTimersByTime(VALIDATE_DEBOUNCE_MS);
      component.currentPipelineText = 'typed during the reload, then more';
      component.onConfigChanged();
      jest.advanceTimersByTime(VALIDATE_DEBOUNCE_MS);

      reloaded.next(mockPipelines);
      reloaded.complete();

      expect(validateSpy).toHaveBeenCalledTimes(1);
      expect(validateSpy).toHaveBeenCalledWith('typed during the reload, then more');
      expect(pipelineStateService.isConfigValid()).toBe(false);
      expect(pipelineStateService.currentPipelineText()).toBe('typed during the reload, then more');
    } finally {
      jest.useRealTimers();
    }
  });

  it('validates the text typed during a reload after it completes; a valid answer marks it valid (#1777)', () => {
    jest.useFakeTimers();
    try {
      const reloaded = startHeldReconnectRefetch();
      const validateSpy = jest.spyOn(jobsServiceMock, 'validatePipelineConfig')
        .mockReturnValueOnce(of(''));
      component.currentPipelineText = 'typed during the reload';
      component.onConfigChanged();
      jest.advanceTimersByTime(VALIDATE_DEBOUNCE_MS);
      expect(pipelineStateService.isConfigValid()).toBe(false);

      reloaded.next(mockPipelines);
      reloaded.complete();

      expect(validateSpy).toHaveBeenCalledTimes(1);
      expect(validateSpy).toHaveBeenCalledWith('typed during the reload');
      expect(pipelineStateService.isConfigValid()).toBe(true);
    } finally {
      jest.useRealTimers();
    }
  });

  it('ignores a validation answer for older text that lands during a reload after a newer keystroke (#1777)', () => {
    jest.useFakeTimers();
    try {
      const reconnect = armReconnect();
      component.ngOnInit();
      const olderAnswer = new Subject<string>();
      const validateSpy = jest.spyOn(jobsServiceMock, 'validatePipelineConfig')
        .mockReturnValueOnce(olderAnswer.asObservable());
      const savePipelineSpy = jest.spyOn(annotationPipelineServiceMock, 'savePipeline');
      component.currentPipelineText = 'older valid text';
      component.onConfigChanged();
      jest.advanceTimersByTime(VALIDATE_DEBOUNCE_MS);
      expect(validateSpy).toHaveBeenCalledWith('older valid text');
      pipelineStateService.loadedWhileLoggedIn.set(!component.isUserLoggedIn);
      const reloaded = new Subject<Pipeline[]>();
      jest.spyOn(jobsServiceMock, 'getAnnotationPipelines')
        .mockReturnValueOnce(reloaded.asObservable());
      reconnect();
      component.currentPipelineText = 'newer invalid text';
      component.onConfigChanged();
      jest.advanceTimersByTime(VALIDATE_DEBOUNCE_MS);

      olderAnswer.next('');
      olderAnswer.complete();

      expect(pipelineStateService.isConfigValid()).toBe(false);
      expect(pipelineStateService.currentPipelineText()).toBe('newer invalid text');
      expect(savePipelineSpy).not.toHaveBeenCalled();
    } finally {
      jest.useRealTimers();
    }
  });

  it('keeps text typed during the initial load not valid when the load completes inside the debounce (#1777)', () => {
    jest.useFakeTimers();
    try {
      pipelineStateService.pipelines.set([]);
      component.currentPipelineText = '';
      const initialPipelines = new Subject<Pipeline[]>();
      jest.spyOn(jobsServiceMock, 'getAnnotationPipelines')
        .mockReturnValueOnce(initialPipelines.asObservable());
      component.ngOnInit();
      const validateSpy = jest.spyOn(jobsServiceMock, 'validatePipelineConfig')
        .mockReturnValueOnce(of(''));
      component.currentPipelineText = 'typed during the initial load';
      component.onConfigChanged();

      initialPipelines.next(mockPipelines);
      initialPipelines.complete();

      expect(pipelineStateService.isConfigValid()).toBe(false);
      expect(validateSpy).not.toHaveBeenCalled();

      jest.advanceTimersByTime(VALIDATE_DEBOUNCE_MS);

      expect(validateSpy).toHaveBeenCalledTimes(1);
      expect(validateSpy).toHaveBeenCalledWith('typed during the initial load');
      expect(pipelineStateService.isConfigValid()).toBe(true);
    } finally {
      jest.useRealTimers();
    }
  });

  it('keeps text typed during the post-save-as fetch not valid when it completes inside the debounce (#1777)', () => {
    jest.useFakeTimers();
    try {
      component.ngOnInit();
      jest.spyOn(mockMatRef, 'open').mockReturnValueOnce(mockMatDialogRef);
      jest.spyOn(mockMatDialogRef, 'afterClosed').mockReturnValueOnce(of('My Pipeline'));
      jest.spyOn(annotationPipelineServiceMock, 'savePipeline').mockReturnValueOnce(of('4'));
      const pipelinesAfterSave = new Subject<Pipeline[]>();
      jest.spyOn(jobsServiceMock, 'getAnnotationPipelines')
        .mockReturnValueOnce(pipelinesAfterSave.asObservable());
      component.currentPipelineText = 'saved yaml';
      component.saveAs();
      const validateSpy = jest.spyOn(jobsServiceMock, 'validatePipelineConfig')
        .mockReturnValueOnce(of('invalid: bad yaml'));
      component.currentPipelineText = 'invalid: [yaml';
      component.onConfigChanged();

      pipelinesAfterSave.next([
        ...mockPipelines,
        new Pipeline('4', 'My Pipeline', 'saved yaml', 'user', 'loaded'),
      ]);
      pipelinesAfterSave.complete();

      expect(pipelineStateService.isConfigValid()).toBe(false);
      expect(validateSpy).not.toHaveBeenCalled();

      jest.advanceTimersByTime(VALIDATE_DEBOUNCE_MS);

      expect(validateSpy).toHaveBeenCalledTimes(1);
      expect(validateSpy).toHaveBeenCalledWith('invalid: [yaml');
      expect(pipelineStateService.isConfigValid()).toBe(false);
    } finally {
      jest.useRealTimers();
    }
  });

  it('marks the config valid after a save-as whose fetch returns the unchanged text (#1777)', () => {
    component.ngOnInit();
    jest.spyOn(mockMatRef, 'open').mockReturnValueOnce(mockMatDialogRef);
    jest.spyOn(mockMatDialogRef, 'afterClosed').mockReturnValueOnce(of('My Pipeline'));
    jest.spyOn(annotationPipelineServiceMock, 'savePipeline').mockReturnValueOnce(of('4'));
    jest.spyOn(jobsServiceMock, 'getAnnotationPipelines').mockReturnValueOnce(of([
      ...mockPipelines,
      new Pipeline('4', 'My Pipeline', 'saved yaml', 'user', 'loaded'),
    ]));
    const validateSpy = jest.spyOn(jobsServiceMock, 'validatePipelineConfig');
    component.currentPipelineText = 'saved yaml';
    pipelineStateService.isConfigValid.set(false);

    component.saveAs();

    expect(component.selectedPipeline.id).toBe('4');
    expect(validateSpy).not.toHaveBeenCalled();
    expect(pipelineStateService.isConfigValid()).toBe(true);
  });

  it('keeps text validated while a save was in flight valid after the post-save fetch (#1777)', () => {
    jest.useFakeTimers();
    try {
      component.ngOnInit();
      component.selectedPipeline = mockPipelines[2];
      component.currentPipelineText = 'saved yaml';
      const saveResponse = new Subject<string>();
      jest.spyOn(annotationPipelineServiceMock, 'savePipeline')
        .mockReturnValueOnce(saveResponse.asObservable());
      jest.spyOn(jobsServiceMock, 'getAnnotationPipelines').mockReturnValueOnce(of([
        mockPipelines[0],
        mockPipelines[1],
        new Pipeline('id3', 'name3', 'saved yaml', 'user', 'loaded'),
      ]));
      component.save();
      jest.spyOn(jobsServiceMock, 'validatePipelineConfig').mockReturnValueOnce(of(''));
      component.currentPipelineText = 'typed while saving';
      component.onConfigChanged();
      jest.advanceTimersByTime(VALIDATE_DEBOUNCE_MS);
      expect(pipelineStateService.isConfigValid()).toBe(true);

      saveResponse.next('id3');
      saveResponse.complete();

      expect(component.selectedPipeline.content).toBe('saved yaml');
      expect(pipelineStateService.isConfigValid()).toBe(true);
    } finally {
      jest.useRealTimers();
    }
  });

  it('keeps the error for text found invalid while a save was in flight after the post-save fetch (#1777)', () => {
    jest.useFakeTimers();
    try {
      component.ngOnInit();
      component.selectedPipeline = mockPipelines[2];
      component.currentPipelineText = 'saved yaml';
      const saveResponse = new Subject<string>();
      jest.spyOn(annotationPipelineServiceMock, 'savePipeline')
        .mockReturnValueOnce(saveResponse.asObservable());
      jest.spyOn(jobsServiceMock, 'getAnnotationPipelines').mockReturnValueOnce(of([
        mockPipelines[0],
        mockPipelines[1],
        new Pipeline('id3', 'name3', 'saved yaml', 'user', 'loaded'),
      ]));
      component.save();
      jest.spyOn(jobsServiceMock, 'validatePipelineConfig').mockReturnValueOnce(of('invalid: bad yaml'));
      component.currentPipelineText = 'typed while saving';
      component.onConfigChanged();
      jest.advanceTimersByTime(VALIDATE_DEBOUNCE_MS);
      expect(component.configError).toBe('invalid: bad yaml');

      saveResponse.next('id3');
      saveResponse.complete();

      expect(pipelineStateService.isConfigValid()).toBe(false);
      expect(component.configError).toBe('invalid: bad yaml');
    } finally {
      jest.useRealTimers();
    }
  });

  it('unselects an edited default pipeline on a keystroke during a pipeline-list reload (#1777)', () => {
    startHeldReconnectRefetch();
    expect(component.selectedPipeline.type).toBe('default');
    expect(pipelineStateService.selectedPipelineId()).toBe('id1');

    component.currentPipelineText = 'typed during the reload';
    component.onConfigChanged();

    expect(component.selectedPipeline).toBeNull();
    expect(pipelineStateService.selectedPipelineId()).toBe('');
    expect(component.dropdownControl.value).toBe('');
  });

  it('marks an edited user pipeline unsaved on a keystroke during a pipeline-list reload (#1777)', () => {
    startHeldReconnectRefetch();
    component.onPipelineClick(mockPipelines[2]);

    component.currentPipelineText = 'typed during the reload';
    component.onConfigChanged();

    expect(component.selectedPipeline.id).toBe('id3');
    expect(component.dropdownControl.value).toBe('name3 *');
  });

  it('validates the next edit after a reconnect-driven refetch fails (#1779)', () => {
    jest.useFakeTimers();
    try {
      const reconnect = armReconnect();
      component.ngOnInit();
      pipelineStateService.loadedWhileLoggedIn.set(!component.isUserLoggedIn);
      jest.spyOn(jobsServiceMock, 'getAnnotationPipelines')
        .mockReturnValueOnce(throwError(() => new Error('backend restarting')));
      reconnect();
      const validateSpy = jest.spyOn(jobsServiceMock, 'validatePipelineConfig');

      component.currentPipelineText = 'typed after the failed refetch';
      component.onConfigChanged();
      jest.advanceTimersByTime(VALIDATE_DEBOUNCE_MS);

      expect(validateSpy).toHaveBeenCalledTimes(1);
      expect(validateSpy).toHaveBeenCalledWith('typed after the failed refetch');
    } finally {
      jest.useRealTimers();
    }
  });

  it('sends a validation suppressed during a failing pipeline-list refetch once the error lands (#1779)', () => {
    jest.useFakeTimers();
    try {
      const reconnect = armReconnect();
      component.ngOnInit();
      pipelineStateService.loadedWhileLoggedIn.set(!component.isUserLoggedIn);
      const refetch = new Subject<Pipeline[]>();
      jest.spyOn(jobsServiceMock, 'getAnnotationPipelines')
        .mockReturnValueOnce(refetch.asObservable());
      reconnect();
      const validateSpy = jest.spyOn(jobsServiceMock, 'validatePipelineConfig');
      component.currentPipelineText = 'typed during the refetch';
      component.onConfigChanged();
      jest.advanceTimersByTime(VALIDATE_DEBOUNCE_MS);
      expect(validateSpy).not.toHaveBeenCalled();
      component.currentPipelineText = 'typed during the refetch, then more';

      refetch.error(new Error('backend restarting'));
      jest.advanceTimersByTime(VALIDATE_DEBOUNCE_MS);

      expect(validateSpy).toHaveBeenCalledTimes(1);
      expect(validateSpy).toHaveBeenCalledWith('typed during the refetch, then more');
    } finally {
      jest.useRealTimers();
    }
  });

  it('a failed reconnect-driven refetch leaves the editor and the selection as they were (#1779)', () => {
    const reconnect = armReconnect();
    component.ngOnInit();
    component.onPipelineClick(mockPipelines[1]);
    component.currentPipelineText = 'edited before the refetch';
    pipelineStateService.loadedWhileLoggedIn.set(!component.isUserLoggedIn);
    jest.spyOn(jobsServiceMock, 'getAnnotationPipelines')
      .mockReturnValueOnce(throwError(() => new Error('backend restarting')));

    reconnect();

    expect(component.currentPipelineText).toBe('edited before the refetch');
    expect(component.selectedPipeline.id).toBe('id2');
    expect(pipelineStateService.selectedPipelineId()).toBe('id2');
  });

  it('a failed initial load does not mark the list loaded or validate the next edit (#1779)', () => {
    jest.useFakeTimers();
    try {
      pipelineStateService.pipelines.set([]);
      jest.spyOn(jobsServiceMock, 'getAnnotationPipelines')
        .mockReturnValueOnce(throwError(() => new Error('backend down')));
      const fresh = TestBed.createComponent(AnnotationPipelineComponent).componentInstance;
      fresh.ngOnInit();
      const validateSpy = jest.spyOn(jobsServiceMock, 'validatePipelineConfig');

      fresh.currentPipelineText = 'typed after the failed initial load';
      fresh.onConfigChanged();
      jest.advanceTimersByTime(VALIDATE_DEBOUNCE_MS);

      expect(validateSpy).not.toHaveBeenCalled();
      expect(fresh.pipelinesLoaded).toBe(false);
    } finally {
      jest.useRealTimers();
    }
  });

  it('drops a validation suppressed during the initial load when the load selects the first pipeline (#693)', () => {
    pipelineStateService.pipelines.set([]);
    component.currentPipelineText = '';
    const initialPipelines = new Subject<Pipeline[]>();
    jest.spyOn(jobsServiceMock, 'getAnnotationPipelines')
      .mockReturnValueOnce(initialPipelines.asObservable());
    const validateSpy = jest.spyOn(jobsServiceMock, 'validatePipelineConfig');
    component.ngOnInit();
    component.isConfigValid();

    initialPipelines.next(mockPipelines);
    initialPipelines.complete();

    expect(component.currentPipelineText).toBe('content1');
    expect(validateSpy).not.toHaveBeenCalled();
  });

  it('saveAs preserves a user edit that lands during the post-save pipelines refresh (tb-348)', () => {
    // Regression test for tb-348 / H8 race: after savePipeline returns, the
    // post-save GET /api/pipelines is in flight. If the user edits the
    // editor before that GET responds, the OLD onPipelineClick path would
    // reset currentPipelineText to the GET-response's pre-edit content,
    // dropping the user's edit (the * indicator never appears).
    // selectPipelineAfterSave deliberately preserves currentPipelineText.
    jest.spyOn(mockMatRef, 'open').mockReturnValueOnce(mockMatDialogRef);
    jest.spyOn(mockMatDialogRef, 'afterClosed').mockReturnValueOnce(of('My Pipeline'));
    jest.spyOn(annotationPipelineServiceMock, 'savePipeline').mockReturnValueOnce(of('4'));

    // Hold the post-save GET response so we can simulate the user editing
    // BEFORE the GET completes.
    const pipelinesAfterSave = new Subject<Pipeline[]>();
    jest.spyOn(jobsServiceMock, 'getAnnotationPipelines')
      .mockReturnValueOnce(pipelinesAfterSave.asObservable());

    component.currentPipelineText = 'user typed yaml';
    component.saveAs();

    // savePipeline has resolved with id '4'; getAnnotationPipelines is in
    // flight. Now the user edits the editor.
    component.currentPipelineText = 'user typed yaml + edited';

    // The GET finally resolves. Server's view of pipeline 4 is the
    // pre-edit content (because the edit happened AFTER the save POST).
    pipelinesAfterSave.next([
      new Pipeline('id1', 'name1', 'content1', 'default', 'loaded'),
      new Pipeline('id2', 'name2', 'content2', 'default', 'loaded'),
      new Pipeline('id3', 'name3', 'content3', 'user', 'loaded'),
      new Pipeline('4', 'My Pipeline', 'user typed yaml', 'user', 'loaded'),
    ]);
    pipelinesAfterSave.complete();

    // The user's edit must survive — currentPipelineText is NOT reset to
    // the server's pre-edit content.
    expect(component.currentPipelineText).toBe('user typed yaml + edited');
    // The new pipeline is selected and named correctly.
    expect(component.selectedPipeline.id).toBe('4');
    expect(component.selectedPipeline.name).toBe('My Pipeline');
    // isPipelineChanged() now correctly reflects that the editor's text
    // differs from the saved pipeline's content.
    expect(component.isPipelineChanged()).toBe(true);
    // displayUnsavedPipelineIndication adds the * suffix to the dropdown.
    expect(component.dropdownControl.value).toBe('My Pipeline *');
  });

  it('delete clears the editor buffer so the default pipeline takes over without a stray * indicator', () => {
    // Regression test for the post-delete asterisk drift seen in
    // gain-web-e2e #163 / #164 (annotation-pipeline.spec.ts:180 'should
    // delete user pipeline'). delete() must reset currentPipelineText
    // before getPipelines() runs — otherwise the no-arg branch's
    // userHasTyped heuristic (tb-l7c) sees the deleted pipeline's content
    // as user-typed, routes through selectPipelineAfterSave +
    // displayUnsavedPipelineIndication, and appends ' *' to the default
    // pipeline that takes the deleted one's place. Verified that
    // reverting the delete()-side fix makes this test fail at the
    // dropdownControl.value assertion (received: "name1 *").
    component.pipelines = mockPipelines;
    component.selectedPipeline = mockPipelines[2]; // user pipeline, content3
    component.currentPipelineText = 'content3';
    pipelineStateService.pipelines.set(mockPipelines);
    pipelineStateService.selectedPipelineId.set('id3');

    jest.spyOn(annotationPipelineServiceMock, 'deletePipeline').mockReturnValue(of({}));
    jest.spyOn(jobsServiceMock, 'getAnnotationPipelines').mockReturnValueOnce(of([
      new Pipeline('id1', 'name1', 'content1', 'default', 'loaded'),
      new Pipeline('id2', 'name2', 'content2', 'default', 'loaded'),
    ]));

    component.delete();

    expect(component.selectedPipeline.id).toBe('id1');
    expect(component.dropdownControl.value).toBe('name1');
    expect(component.isPipelineChanged()).toBe(false);
    expect(component.currentPipelineText).toBe('content1');
  });

  it('should save pipeline and not update pipeline list when response is invalid', () => {
    const savePipelineSpy = jest.spyOn(annotationPipelineServiceMock, 'savePipeline').mockReturnValueOnce(of(null));
    const getAnnotationPipelinesSpy = jest.spyOn(jobsServiceMock, 'getAnnotationPipelines');

    component.selectedPipeline = mockPipelines[0];
    component.currentPipelineText = 'new content';

    component.save();
    expect(savePipelineSpy).toHaveBeenCalledWith('id1', 'name1', 'new content');
    expect(getAnnotationPipelinesSpy).not.toHaveBeenCalledTimes(2);
  });

  it('should not save pipeline when there are no changes', () => {
    const savePipelineSpy = jest.spyOn(annotationPipelineServiceMock, 'savePipeline').mockReturnValueOnce(of(null));

    component.selectedPipeline = mockPipelines[0];
    component.currentPipelineText = 'content1';

    component.save();
    expect(savePipelineSpy).not.toHaveBeenCalledWith();
  });

  it('should auto save current pipeline when editing', () => {
    const savePipelineSpy = jest.spyOn(annotationPipelineServiceMock, 'savePipeline').mockReturnValueOnce(of(null));

    component.selectedPipeline = mockPipelines[2];
    component.currentPipelineText = 'new content';

    component.autoSave();
    expect(savePipelineSpy).toHaveBeenCalledWith('', '', 'new content');
  });

  it('should save annonymous pipeline', () => {
    const savePipelineSpy = jest.spyOn(annotationPipelineServiceMock, 'savePipeline').mockReturnValueOnce(of(null));
    const saveSpy = jest.spyOn(component, 'save');

    component.currentTemporaryPipelineId = '';
    component.selectedPipeline = null;
    component.currentPipelineText = 'new content';

    component.autoSave();
    expect(savePipelineSpy).toHaveBeenCalledWith('', '', 'new content');
    expect(saveSpy).not.toHaveBeenCalledWith();
  });

  it('should save edited public pipeline as annonymous', () => {
    const savePipelineSpy = jest.spyOn(annotationPipelineServiceMock, 'savePipeline').mockReturnValueOnce(of(null));
    const saveSpy = jest.spyOn(component, 'save');

    component.currentTemporaryPipelineId = '';
    component.selectedPipeline = mockPipelines[0];
    component.currentPipelineText = 'new content';

    component.autoSave();
    expect(savePipelineSpy).toHaveBeenCalledWith('', '', 'new content');
    expect(saveSpy).not.toHaveBeenCalledWith();
  });

  it('should get pipeline editor config options on init', () => {
    component.pipelines = mockPipelines;
    const editorInitSpy = jest.spyOn(component, 'onEditorInit');

    const monacoEditor = fixture.debugElement.query(By.css('ngx-monaco-editor'));

    const mockEditor = { getLayoutInfo: jest.fn().mockReturnValue({ width: 400 }) };
    // Manually trigger (onInit) of editor
    monacoEditor.triggerEventHandler('onInit', mockEditor);

    expect(editorInitSpy).toHaveBeenCalledWith(mockEditor);
    expect(component.yamlEditorOptions).toStrictEqual(
      {
        language: 'yaml',
        minimap: {
          enabled: false
        },
        lineNumbers: 'off',
        folding: false,
        stickyScroll: {
          enabled: false,
        },
        scrollBeyondLastLine: false,
        theme: 'annotationPipelineTheme',
        automaticLayout: true,
      }
    );
  });

  it('should create theme on editor init', () => {
    component.pipelines = mockPipelines;
    // eslint-disable-next-line @typescript-eslint/no-unsafe-member-access, @typescript-eslint/no-explicit-any
    const defineThemeSpy = jest.spyOn((window as any).monaco.editor, 'defineTheme');

    const monacoEditor = fixture.debugElement.query(By.css('ngx-monaco-editor'));

    // Manually trigger (onInit) of editor
    monacoEditor.triggerEventHandler('onInit', { getLayoutInfo: jest.fn().mockReturnValue({ width: 400 }) });

    fixture.detectChanges();
    expect(defineThemeSpy).toHaveBeenCalledWith('annotationPipelineTheme', {
      base: 'vs-dark',
      inherit: true,
      rules: [
        {
          foreground: '#dd8108ff',
          token: 'type'
        },

        {
          foreground: '#85a2b9',
          token: 'string'
        },
        {
          foreground: '#85a2b9',
          token: 'number'
        },
        {
          foreground: '#2f404eff',
          token: 'keyword'
        },
        {
          foreground: '#75715e',
          token: 'comment'
        },
      ],
      colors: {
        'editor.foreground': '#2f404eff',
        'editor.background': '#FFFFFF',
        'editor.selectionForeground': '#915b15ff',
        'editor.selectionBackground': '#e7e6e4ff',
        'editor.inactiveSelectionBackground': '#ebeae8ff',
        'editor.lineHighlightBackground': '#f0efe9b0',
        'editorCursor.foreground': '#383838ff',
        'editorWhitespace.foreground': '#c9d2ddff',
        'editor.wordHighlightBackground': '#e9e6dfff',
        'scrollbar.shadow': '#c9d2ddff',
        'scrollbarSlider.background': '#dfdfdfa2',
        'scrollbarSlider.hoverBackground': '#b3bbc583',
        'scrollbarSlider.activeBackground': '#b3bbc583',
        'editorIndentGuide.background1': '#dbdbdbe0',
        'editorIndentGuide.activeBackground1': '#a4b6c7ff',
      }
    });
  });

  it('should send id of the selected pipeline when opening new annotator modal', () => {
    const openSpy = jest.spyOn(mockMatRef, 'open');
    component.selectedPipeline = mockPipelines[2];
    component.openAnnotatorFormModal();

    expect(openSpy).toHaveBeenCalledWith(
      NewAnnotatorComponent,
      {
        id: 'newAnnotator',
        data: {
          pipelineId: 'id3',
          isResourceWorkflow: false
        },
        height: '70vh',
        width: '80vw',
        maxWidth: '1500px',
        minWidth: '500px'
      });
  });

  it('should send temporary pipeline id when opening new annotator modal', () => {
    const openSpy = jest.spyOn(mockMatRef, 'open');
    component.selectedPipeline = mockPipelines[2];
    component.currentTemporaryPipelineId = 'temp123';
    component.openAnnotatorFormModal();

    expect(openSpy).toHaveBeenCalledWith(
      NewAnnotatorComponent,
      {
        id: 'newAnnotator',
        data: {
          pipelineId: 'temp123',
          isResourceWorkflow: false
        },
        height: '70vh',
        width: '80vw',
        maxWidth: '1500px',
        minWidth: '500px'
      });
  });

  it('should disable pipeline action buttons on save as click', () => {
    component.saveAs();
    expect(component.disableActions).toBe(true);
  });

  it('should disable pipeline action buttons on save click', () => {
    jest.spyOn(component, 'isPipelineChanged').mockReturnValue(true);
    jest.spyOn(annotationPipelineServiceMock, 'savePipeline').mockReturnValueOnce(of(null));

    component.save();
    expect(component.disableActions).toBe(true);
  });

  it('should enable pipeline action buttons on pipeline select', () => {
    component.disableActions = true;
    component.onPipelineClick(new Pipeline('id1', 'name1', '', 'user', 'loading'));
    expect(component.disableActions).toBe(false);
  });

  it('should enable pipeline action buttons when canceling setting name to pipeline', () => {
    component.disableActions = true;
    jest.spyOn(mockMatDialogRef, 'afterClosed').mockReturnValueOnce(of(null));
    component.saveAs();
    expect(component.disableActions).toBe(false);
  });

  it('should not validate config before pipelines are loaded', () => {
    const configValidationSpy = jest.spyOn(jobsServiceMock, 'validatePipelineConfig');
    component.pipelinesLoaded = false;
    component.isConfigValid();
    expect(configValidationSpy).not.toHaveBeenCalled();
  });

  it('should set pipelinesLoaded to true after pipelines are fetched', () => {
    component.pipelinesLoaded = false;
    component.ngOnInit();
    expect(component.pipelinesLoaded).toBe(true);
  });

  it('should reset pipelinesLoaded to false while reloading pipelines and restore it on completion', () => {
    const subject = new Subject<Pipeline[]>();
    jest.spyOn(jobsServiceMock, 'getAnnotationPipelines').mockReturnValueOnce(subject.asObservable());
    jest.spyOn(annotationPipelineServiceMock, 'deletePipeline').mockReturnValue(of({}));
    component.selectedPipeline = mockPipelines[2];

    component.delete();
    expect(component.pipelinesLoaded).toBe(false);

    subject.next(mockPipelines);
    subject.complete();
    expect(component.pipelinesLoaded).toBe(true);
  });

  it('should update temporary pipeline status in state when notification matches current temporary pipeline', () => {
    const notifications = new Subject<PipelineNotification>();
    jest.spyOn(socketNotificationsServiceMock, 'getPipelineNotifications')
      .mockReturnValue(notifications.asObservable());
    component.ngOnInit();

    // The client is already tracking temp '215'; a matching frame updates it.
    component.currentTemporaryPipelineId = '215';
    notifications.next(new PipelineNotification('215', 'loaded'));

    expect(component.currentTemporaryPipelineStatus).toBe('loaded');
    expect(pipelineStateService.currentTemporaryPipelineStatus()).toBe('loaded');
  });

  it('should also update temporary pipeline status in state when new id arrives from notification', () => {
    const notifications = new Subject<PipelineNotification>();
    jest.spyOn(socketNotificationsServiceMock, 'getPipelineNotifications')
      .mockReturnValue(notifications.asObservable());
    component.currentTemporaryPipelineId = '';
    component.ngOnInit();

    component.currentPipelineText = 'edited config';
    notifications.next(new PipelineNotification('215', 'loading'));

    expect(pipelineStateService.currentTemporaryPipelineStatus()).toBe('loading');
  });

  it('should surface the reason as loadError for a failed temporary pipeline', () => {
    const notifications = new Subject<PipelineNotification>();
    jest.spyOn(socketNotificationsServiceMock, 'getPipelineNotifications')
      .mockReturnValue(notifications.asObservable());
    component.ngOnInit();

    // New-pipeline editing flow: no saved pipeline selected, only the temp one
    // being built from the user's unsaved edits.
    component.selectedPipeline = null;
    component.currentPipelineText = 'edited config';
    notifications.next(new PipelineNotification('215', 'failed', 'Invalid configuration, reason: boom'));

    expect(component.currentTemporaryPipelineStatus).toBe('failed');
    expect(component.loadError).toBe('Invalid configuration, reason: boom');
  });

  it('should clear loadError when a temporary pipeline recovers', () => {
    const notifications = new Subject<PipelineNotification>();
    jest.spyOn(socketNotificationsServiceMock, 'getPipelineNotifications')
      .mockReturnValue(notifications.asObservable());
    component.ngOnInit();

    // A tracked temp failed, then a matching frame reports it recovered.
    component.selectedPipeline = null;
    component.currentTemporaryPipelineId = '215';
    component.currentTemporaryPipelineStatus = 'failed';
    component.currentTemporaryPipelineError = 'Invalid configuration, reason: boom';
    notifications.next(new PipelineNotification('215', 'loaded'));

    expect(component.currentTemporaryPipelineStatus).toBe('loaded');
    expect(component.loadError).toBe('');
  });

  it('should surface the failure reason when a failed pipeline is selected', () => {
    const failed = new Pipeline(
      '1', 'broken', 'content', 'user', 'failed', 'Invalid configuration, reason: boom');
    component.onPipelineClick(failed);
    expect(component.loadError).toBe('Invalid configuration, reason: boom');
  });

  it('should not leave a stale loadError when switching to a healthy pipeline', () => {
    const failed = new Pipeline(
      '1', 'broken', 'content', 'user', 'failed', 'Invalid configuration, reason: boom');
    const healthy = new Pipeline('2', 'good', 'content', 'user', 'loaded');
    component.onPipelineClick(failed);
    expect(component.loadError).toBe('Invalid configuration, reason: boom');
    component.onPipelineClick(healthy);
    expect(component.loadError).toBe('');
  });

  it('should clear loadError on New pipeline (doClear)', () => {
    const failed = new Pipeline(
      '1', 'broken', 'content', 'user', 'failed', 'Invalid configuration, reason: boom');
    component.onPipelineClick(failed);
    component.doClear();
    expect(component.loadError).toBe('');
  });

  it('should surface the reason when a failed notification targets the selected pipeline', () => {
    const listed = new Pipeline('215', 'broken', 'content', 'user', 'loaded');
    jest.spyOn(jobsServiceMock, 'getAnnotationPipelines').mockReturnValue(of([listed]));
    jest.spyOn(socketNotificationsServiceMock, 'getPipelineNotifications').mockReturnValue(
      of(new PipelineNotification('215', 'failed', 'Invalid configuration, reason: boom'))
    );
    component.ngOnInit();
    expect(listed.status).toBe('failed');
    expect(listed.error).toBe('Invalid configuration, reason: boom');
    expect(component.loadError).toBe('Invalid configuration, reason: boom');
  });

  it('should sync pipeline text to state when selecting a pipeline', () => {
    component.onPipelineClick(new Pipeline('1', 'other pipeline', 'config content', 'default', 'loaded'));
    expect(pipelineStateService.currentPipelineText()).toBe('config content');
  });

  it('should sync pipeline text to state on config validation', () => {
    component.currentPipelineText = 'new config';
    component.isConfigValid();
    expect(pipelineStateService.currentPipelineText()).toBe('new config');
  });

  it('should sync pipeline info to state after fetching it', () => {
    component.onPipelineClick(mockPipelines[0]);
    expect(pipelineStateService.pipelineInfo()).toStrictEqual(
      new PipelineInfo(20, 4, ['hg19_annotatable'], ['gene_list'])
    );
  });

  it('should clear pipeline id, text and info in state when clearing the pipeline', () => {
    component.selectedPipeline = mockPipelines[0];
    component.currentPipelineText = 'some content';
    component.pipelineInfo = new PipelineInfo(20, 4, ['hg19_annotatable'], ['gene_list']);

    component.doClear();

    expect(pipelineStateService.selectedPipelineId()).toBe('');
    expect(pipelineStateService.currentPipelineText()).toBe('');
    expect(pipelineStateService.pipelineInfo()).toBeNull();
  });

  it('should restore state from service when navigating back to the page', () => {
    pipelineStateService.pipelines.set(mockPipelines);
    pipelineStateService.selectedPipelineId.set('id3');
    pipelineStateService.currentPipelineText.set('content3');

    component.ngOnInit();

    expect(component.selectedPipeline).toStrictEqual(mockPipelines[2]);
    expect(component.currentPipelineText).toBe('content3');
    expect(component.dropdownControl.value).toBe('name3');
  });

  it('should set disableActions to false when pipelines fetch fails', () => {
    pipelineStateService.pipelines.set([]);
    jest.spyOn(jobsServiceMock, 'getAnnotationPipelines').mockReturnValueOnce(throwError(() => new Error('error')));
    component.disableActions = true;
    component['getPipelines']();
    expect(component.disableActions).toBe(false);
  });

  it('should set pipelineInfo to null when getPipelineInfo fails', () => {
    jest.spyOn(annotationPipelineServiceMock, 'getPipelineInfo')
      .mockReturnValueOnce(throwError(() => new Error('error')));
    component.selectedPipeline = mockPipelines[0];
    component['getPipelineInfo']();
    expect(component.pipelineInfo).toBeNull();
    expect(pipelineStateService.pipelineInfo()).toBeNull();
  });

  it('ignores a stale getPipelineInfo response after the pipeline is cleared', () => {
    // The pipeline_status endpoint blocks until the GRR build finishes, so a
    // request can still be in flight when the user clicks New pipeline. Its late
    // response must not repopulate the status bar that doClear() just zeroed.
    const info$ = new Subject<PipelineInfo>();
    jest.spyOn(annotationPipelineServiceMock, 'getPipelineInfo')
      .mockReturnValueOnce(info$.asObservable());
    pipelineStateService.currentTemporaryPipelineId.set('');
    pipelineStateService.selectedPipelineId.set('id1');

    component['getPipelineInfo'](); // request in flight for 'id1'

    // User clears the pipeline while the blocking request is still pending.
    pipelineStateService.selectedPipelineId.set('');

    // The late response for the now-stale 'id1' must be ignored.
    info$.next(new PipelineInfo(13, 23, [], []));

    expect(component.pipelineInfo).toBeNull();
    expect(pipelineStateService.pipelineInfo()).toBeNull();
  });

  it('marks the temporary pipeline loaded when the status fetch succeeds even if the WS frame was missed', () => {
    // Simulate: temp pipeline saved, GRR build pending, and the one-shot WS
    // 'pipeline_status: loaded' frame was missed (#160) -- the editor is stuck
    // on 'loading'. The blocking GET /api/editor/pipeline_status returns 200
    // only once the build has finished, so a successful fetch is authoritative
    // proof the pipeline is loaded and the editor must converge regardless.
    pipelineStateService.currentTemporaryPipelineId.set('temp-1');
    pipelineStateService.selectedPipelineId.set('');
    component.selectedPipeline = null;
    component.currentTemporaryPipelineStatus = 'loading';
    jest.spyOn(annotationPipelineServiceMock, 'getPipelineInfo')
      .mockReturnValueOnce(of(new PipelineInfo(20, 4, [], [])));

    component['getPipelineInfo']();

    expect(component.currentTemporaryPipelineStatus).toBe('loaded');
    expect(pipelineStateService.currentTemporaryPipelineStatus()).toBe('loaded');
  });

  it('should return early from clearPipeline when both text and pipeline are empty', () => {
    component.selectedPipeline = null;
    component.currentPipelineText = '';
    const doClearSpy = jest.spyOn(component, 'doClear');
    component.clearPipeline();
    expect(doClearSpy).not.toHaveBeenCalled();
  });

  it('should show confirm create popup when clearing pipeline with unsaved changes', () => {
    component.currentPipelineText = 'some content';
    component.currentTemporaryPipelineId = 'temp123';
    component.clearPipeline();
    expect(component.showConfimPipelineCreatePopup).toBe(true);
  });

  it('should call save on Ctrl+S when user pipeline is selected and config is valid', () => {
    component.selectedPipeline = mockPipelines[2]; // type 'user'
    component.configError = '';
    component.isUserLoggedIn = true;
    component.currentPipelineText = 'changed content';
    const saveSpy = jest.spyOn(component, 'save').mockImplementation(() => {});
    const mockEvent = { preventDefault: jest.fn() } as unknown as Event;
    component.onKeydownHandler(mockEvent);
    expect(mockEvent.preventDefault).toHaveBeenCalledWith();
    expect(saveSpy).toHaveBeenCalledWith();
  });

  it('should not call save on Ctrl+S when pipeline type is default', () => {
    component.selectedPipeline = mockPipelines[0]; // type 'default'
    component.configError = '';
    component.isUserLoggedIn = true;
    const saveSpy = jest.spyOn(component, 'save').mockImplementation(() => {});
    const mockEvent = { preventDefault: jest.fn() } as unknown as Event;
    component.onKeydownHandler(mockEvent);
    expect(mockEvent.preventDefault).toHaveBeenCalledWith();
    expect(saveSpy).not.toHaveBeenCalled();
  });

  it('should shrink textarea when window width is at most 1200 on window resize', () => {
    const shrinkSpy = jest.spyOn(component, 'shrinkTextarea');
    Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 1200 });
    component.onWindowResize();
    expect(shrinkSpy).toHaveBeenCalledWith();
  });

  it('should not shrink textarea when window width is above 1200 on window resize', () => {
    const shrinkSpy = jest.spyOn(component, 'shrinkTextarea');
    Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 1201 });
    component.onWindowResize();
    expect(shrinkSpy).not.toHaveBeenCalled();
  });

  it('should call shrinkTextarea in ResizeObserver callback when window is narrow', () => {
    let capturedCallback: ResizeObserverCallback;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any, @typescript-eslint/no-unsafe-argument
    jest.spyOn(global as any, 'ResizeObserver').mockImplementation((...args: unknown[]) => {
      capturedCallback = args[0] as ResizeObserverCallback;
      return { observe: jest.fn(), unobserve: jest.fn(), disconnect: jest.fn() };
    });

    Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 800 });
    const shrinkSpy = jest.spyOn(component, 'shrinkTextarea');
    component.ngAfterViewInit();
    capturedCallback([], null);
    expect(shrinkSpy).toHaveBeenCalledWith();

    // eslint-disable-next-line @typescript-eslint/no-explicit-any, @typescript-eslint/no-unsafe-member-access
    (global as any).ResizeObserver = class {
      public observe(): void {}
      public unobserve(): void {}
      public disconnect(): void {}
    };
  });

  it('should call resolveComponentsVisibility in ResizeObserver callback when window is wide', () => {
    let capturedCallback: ResizeObserverCallback;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any, @typescript-eslint/no-unsafe-argument
    jest.spyOn(global as any, 'ResizeObserver').mockImplementation((...args: unknown[]) => {
      capturedCallback = args[0] as ResizeObserverCallback;
      return { observe: jest.fn(), unobserve: jest.fn(), disconnect: jest.fn() };
    });

    Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 1500 });
    // Wide window: shrinkTextarea not called, showParentComponents via resolveComponentsVisibility
    const shrinkSpy = jest.spyOn(component, 'shrinkTextarea');
    component.expandTextarea();
    component.ngAfterViewInit();
    capturedCallback([], null);
    // remainingWidth = 1500 - 0 (clientWidth in jsdom) = 1500 > 750 → showParentComponents
    expect(pipelineStateService.hideComponents()).toBe(false);
    expect(shrinkSpy).not.toHaveBeenCalled();

    // eslint-disable-next-line @typescript-eslint/no-explicit-any, @typescript-eslint/no-unsafe-member-access
    (global as any).ResizeObserver = class {
      public observe(): void {}
      public unobserve(): void {}
      public disconnect(): void {}
    };
  });

  it('should set download link when pipeline is selected via onPipelineClick', () => {
    component.onPipelineClick(mockPipelines[0]);
    expect(component.downloadDocLink).toBe('//localhost:8000/api/pipelines/doc?pipeline_id=id1');
  });

  it('should update download link when selected pipeline changes', () => {
    component.onPipelineClick(mockPipelines[0]);
    expect(component.downloadDocLink).toBe('//localhost:8000/api/pipelines/doc?pipeline_id=id1');

    component.onPipelineClick(mockPipelines[2]);
    expect(component.downloadDocLink).toBe('//localhost:8000/api/pipelines/doc?pipeline_id=id3');
  });

  it('should clear download link when pipeline is cleared', () => {
    component.onPipelineClick(mockPipelines[0]);
    expect(component.downloadDocLink).toBe('//localhost:8000/api/pipelines/doc?pipeline_id=id1');

    component.doClear();
    expect(component.downloadDocLink).toBe('');
  });

  it('should use temporary pipeline id in download link after autosave', () => {
    jest.spyOn(annotationPipelineServiceMock, 'savePipeline').mockReturnValueOnce(of('temp-999'));
    component.currentTemporaryPipelineId = '';
    component.selectedPipeline = null;
    component.currentPipelineText = 'some yaml';

    component.autoSave().subscribe();

    expect(component.downloadDocLink).toBe('//localhost:8000/api/pipelines/doc?pipeline_id=temp-999');
  });

  it('should not update download link on subsequent autosaves once temp id is set', () => {
    jest.spyOn(annotationPipelineServiceMock, 'savePipeline').mockReturnValue(of('temp-999'));
    component.currentTemporaryPipelineId = 'temp-999';
    component.currentPipelineText = 'some yaml';

    const spy = jest.spyOn(annotationPipelineServiceMock, 'getDownloadAnnotateDocumentationUrl');
    component.autoSave().subscribe();

    expect(spy).not.toHaveBeenCalled();
  });

  it('should prefer temporary pipeline id over selected pipeline in download link', () => {
    component.selectedPipeline = mockPipelines[0];
    component.currentTemporaryPipelineId = 'temp-42';
    component['updateDownloadLink']();
    expect(component.downloadDocLink).toBe('//localhost:8000/api/pipelines/doc?pipeline_id=temp-42');
  });

  it('should fall back to selected pipeline id in download link when no temp id', () => {
    component.selectedPipeline = mockPipelines[1];
    component.currentTemporaryPipelineId = '';
    component['updateDownloadLink']();
    expect(component.downloadDocLink).toBe('//localhost:8000/api/pipelines/doc?pipeline_id=id2');
  });

  it('should set empty download link when no pipeline and no temp id', () => {
    component.selectedPipeline = null;
    component.currentTemporaryPipelineId = '';
    component['updateDownloadLink']();
    expect(component.downloadDocLink).toBe('');
  });
});
