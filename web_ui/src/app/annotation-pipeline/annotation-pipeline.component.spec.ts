import { ComponentFixture, TestBed } from '@angular/core/testing';
import { AnnotationPipelineComponent } from './annotation-pipeline.component';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { BehaviorSubject, Observable, of, Subject, throwError } from 'rxjs';
import { JobCreationComponent } from '../job-creation/job-creation.component';
import { FileContent } from '../job-creation/jobs';
import { JobsService } from '../job-creation/jobs.service';
import { Pipeline } from '../job-creation/pipelines';
import { UserData, UsersService } from '../users.service';
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
      variantCount: 1000,
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
  public getPipelineInfo(id: string): Observable<PipelineInfo> {
    return of(new PipelineInfo(20, 4, ['hg19_annotatable'], ['gene_list']));
  }
}

class SocketNotificationsServiceMock {
  public getPipelineNotifications(): Observable<PipelineNotification> {
    return of(new PipelineNotification('id1', 'unloaded'));
  }

  public closeConnection(): void { }
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
    jest.spyOn(socketNotificationsServiceMock, 'getPipelineNotifications').mockReturnValueOnce(
      of(new PipelineNotification('215', 'loading'))
    );
    component.currentTemporaryPipelineId = '';
    component.currentTemporaryPipelineStatus = null;
    component.ngOnInit();
    expect(component.currentTemporaryPipelineId).toBe('215');
    expect(component.currentTemporaryPipelineStatus).toBe('loading');
    expect(pipelineStateService.currentTemporaryPipelineId()).toBe('215');
  });

  it('should reconnects to socket notifications on close event', () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const setupSpy = jest.spyOn(component as any, 'setupPipelineWebSocketConnection');
    jest.spyOn(socketNotificationsServiceMock, 'getPipelineNotifications')
      .mockReturnValueOnce(throwError(new CloseEvent('close')));
    const unsubSpy = jest.spyOn(component.socketNotificationSubscription, 'unsubscribe');

    component.ngOnInit();

    expect(unsubSpy).toHaveBeenCalledWith();
    expect(setupSpy).toHaveBeenCalledWith();
  });

  it('does not reconnect for non-close events', () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const setupSpy = jest.spyOn(component as any, 'setupPipelineWebSocketConnection');
    jest.spyOn(socketNotificationsServiceMock, 'getPipelineNotifications')
      .mockReturnValueOnce(throwError({ type: 'other' }));

    component.ngOnInit();
    expect(setupSpy).toHaveBeenCalledTimes(1);

    const unsubSpy = jest.spyOn(component.socketNotificationSubscription, 'unsubscribe');

    expect(unsubSpy).not.toHaveBeenCalled();
    expect(setupSpy).toHaveBeenCalledTimes(1);
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

  it('should determine editor\'s width', () => {
    component.editorSize = 'custom';
    expect(component.editorWidth()).toBe('auto');

    component.editorSize = 'full';
    expect(component.editorWidth()).toBe('95vw');

    component.editorSize = 'small';
    expect(component.editorWidth()).toBe('40vw');
  });

  it('should determine editor\'s height', () => {
    component.editorSize = 'custom';
    expect(component.editorHeight()).toBe('auto');

    component.editorSize = 'full';
    expect(component.editorHeight()).toBe('70vh');

    component.editorSize = 'small';
    expect(component.editorHeight()).toBe('40vh');
  });

  it('should expand editor and hide elements', () => {
    const triggerHideElementsSpy = jest.spyOn(component.tiggerHidingComponents, 'emit');
    component.expandTextarea();
    expect(component.displayFullScreenButton).toBe(false);
    expect(component.displayResetScreenButton).toBe(true);
    expect(component.editorSize).toBe('full');
    expect(triggerHideElementsSpy).toHaveBeenCalledWith(true);
  });

  it('should auto shrink editor and display elements', () => {
    const triggerHideElementsSpy = jest.spyOn(component.tiggerHidingComponents, 'emit');
    component.shrinkTextarea();
    expect(component.displayFullScreenButton).toBe(true);
    expect(component.displayResetScreenButton).toBe(false);
    expect(component.editorSize).toBe('small');
    expect(triggerHideElementsSpy).toHaveBeenCalledWith(false);
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

  it('should fail config validation', () => {
    const configValidationSpy = jest.spyOn(jobsServiceMock, 'validatePipelineConfig')
      .mockReturnValue(of('error message'));
    component.currentPipelineText = 'config content';
    component.isConfigValid();
    expect(configValidationSpy).toHaveBeenCalledWith('config content');
    expect(component.configError).toBe('error message');
    expect(pipelineStateService.isConfigValid()).toBe(false);
  });

  it('should get pipeline status info after successful validation', () => {
    jest.spyOn(jobsServiceMock, 'validatePipelineConfig').mockReturnValue(of(''));
    jest.spyOn(component, 'isPipelineChanged').mockReturnValue(false);
    component.pipelineInfo = null;

    component.selectedPipeline = mockPipelines[0];
    component.currentPipelineText = 'config content';

    component.isConfigValid();
    expect(component.pipelineInfo).toStrictEqual(new PipelineInfo(20, 4, ['hg19_annotatable'], ['gene_list']));
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
    const updatedMockPipelines = [
      new Pipeline('1', 'id1', 'content1', 'default', 'loaded'),
      new Pipeline('2', 'id2', 'content2', 'default', 'loaded'),
      new Pipeline('3', 'id3', 'content3', 'default', 'loaded'),
      new Pipeline('4', 'pipeline-name', 'content', 'default', 'loaded'),
    ];

    jest.spyOn(mockMatRef, 'open').mockReturnValueOnce(mockMatDialogRef);
    jest.spyOn(mockMatDialogRef, 'afterClosed').mockReturnValueOnce(of('pipeline-name'));
    const savePipelineSpy = jest.spyOn(annotationPipelineServiceMock, 'savePipeline').mockReturnValueOnce(of('4'));
    const getAnnotationPipelinesSpy = jest.spyOn(jobsServiceMock, 'getAnnotationPipelines')
      .mockReturnValueOnce(of(updatedMockPipelines));
    const onPipelineClickSpy = jest.spyOn(component, 'onPipelineClick');

    component.currentPipelineText = 'mock config';

    component.saveAs();
    expect(savePipelineSpy).toHaveBeenCalledWith('', 'pipeline-name', 'mock config');
    expect(getAnnotationPipelinesSpy).toHaveBeenCalledWith();
    expect(onPipelineClickSpy).toHaveBeenCalledWith(new Pipeline('4', 'pipeline-name', 'content', 'default', 'loaded'));
    expect(component.selectedPipeline.id).toBe('4');
  });

  it('should not save pipeline and trigger pipelines query if new pipeline has no name', () => {
    jest.spyOn(mockMatRef, 'open').mockReturnValueOnce(mockMatDialogRef);
    jest.spyOn(mockMatDialogRef, 'afterClosed').mockReturnValueOnce(of(null));
    const savePipelineSpy = jest.spyOn(annotationPipelineServiceMock, 'savePipeline');
    const getAnnotationPipelinesSpy = jest.spyOn(jobsServiceMock, 'getAnnotationPipelines');
    const onPipelineClickSpy = jest.spyOn(component, 'onPipelineClick');

    component.currentPipelineText = 'mock config';

    component.saveAs();
    expect(savePipelineSpy).not.toHaveBeenCalledWith();
    expect(getAnnotationPipelinesSpy).not.toHaveBeenCalledTimes(2);
    expect(onPipelineClickSpy).not.toHaveBeenCalledWith();
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
    const selectNewPipelineSpy = jest.spyOn(component, 'onPipelineClick');

    component.selectedPipeline = mockPipelines[2];
    component.currentPipelineText = 'new content';

    component.save();
    expect(savePipelineSpy).toHaveBeenCalledWith('id3', 'name3', 'new content');
    expect(selectNewPipelineSpy).toHaveBeenCalledWith(new Pipeline('id3', 'name3', 'new content', 'user', 'loaded'));
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

    // Manually trigger (onInit) of editor
    monacoEditor.triggerEventHandler('onInit', { fake: 'editorInstance' });

    expect(editorInitSpy).toHaveBeenCalledWith();
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
    monacoEditor.triggerEventHandler('onInit', { fake: 'editorInstance' });

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
    jest.spyOn(socketNotificationsServiceMock, 'getPipelineNotifications').mockReturnValueOnce(
      of(new PipelineNotification('215', 'loaded'))
    );
    component.currentTemporaryPipelineId = '215';
    component.ngOnInit();
    expect(component.currentTemporaryPipelineStatus).toBe('loaded');
    expect(pipelineStateService.currentTemporaryPipelineStatus()).toBe('loaded');
  });

  it('should also update temporary pipeline status in state when new id arrives from notification', () => {
    jest.spyOn(socketNotificationsServiceMock, 'getPipelineNotifications').mockReturnValueOnce(
      of(new PipelineNotification('215', 'loading'))
    );
    component.currentTemporaryPipelineId = '';
    component.ngOnInit();
    expect(pipelineStateService.currentTemporaryPipelineStatus()).toBe('loading');
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
});
