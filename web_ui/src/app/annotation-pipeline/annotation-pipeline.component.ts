import { CommonModule } from '@angular/common';
import {
  AfterViewInit,
  Component,
  effect,
  ElementRef,
  HostListener,
  NgZone,
  OnDestroy,
  OnInit,
  TemplateRef,
  ViewChild,
} from '@angular/core';
import { filter, map, Observable, of, startWith, Subscription, switchMap, take, tap } from 'rxjs';
import { JobsService } from '../job-creation/jobs.service';
import { Pipeline } from '../job-creation/pipelines';
import { FormControl, FormsModule, ReactiveFormsModule } from '@angular/forms';
import { MatAutocompleteModule, MatAutocompleteTrigger } from '@angular/material/autocomplete';
import { MatFormFieldModule } from '@angular/material/form-field';
import { AnnotationPipelineService } from '../annotation-pipeline.service';
import { MatDialog } from '@angular/material/dialog';
import { EditorComponent, MonacoEditorModule } from 'ngx-monaco-editor-v2';
import { editorConfig, initEditor } from './annotation-pipeline-editor.config';
import { UsersService } from '../users.service';
import { SocketNotificationsService } from '../socket-notifications/socket-notifications.service';
import { PipelineNotification, PipelineStatus } from '../socket-notifications/socket-notifications';
import { NewAnnotatorComponent } from '../new-annotator/new-annotator.component';
import { PipelineInfo } from '../annotation-pipeline';
import type * as Monaco from 'monaco-editor';
import { MatTooltip } from '@angular/material/tooltip';
import { OverlayModule } from '@angular/cdk/overlay';
import { AnnotationPipelineStateService } from './annotation-pipeline-state.service';
import { ViewportService } from '../viewport.service';

@Component({
  selector: 'app-annotation-pipeline',
  imports: [
    CommonModule,
    FormsModule,
    MatAutocompleteModule,
    MatFormFieldModule,
    ReactiveFormsModule,
    FormsModule,
    MonacoEditorModule,
    MatTooltip,
    OverlayModule
  ],
  templateUrl: './annotation-pipeline.component.html',
  styleUrl: './annotation-pipeline.component.css'
})

export class AnnotationPipelineComponent implements OnInit, OnDestroy, AfterViewInit {
  public pipelines : Pipeline[] = [];
  public currentPipelineText = '';
  public currentTemporaryPipelineId = '';
  public currentTemporaryPipelineStatus: PipelineStatus;
  public selectedPipeline: Pipeline = null;
  public configError = '';
  public filteredPipelines: Pipeline[] = null;
  public dropdownControl = new FormControl<string>('');
  @ViewChild('nameInput') public nameInputTemplateRef: TemplateRef<ElementRef>;
  @ViewChild('pipelineEditor') public pipelineEditorRef: EditorComponent;
  @ViewChild('pipelineInput') public pipelineInputRef: MatAutocompleteTrigger;
  public resizeObserver: ResizeObserver = null;
  public yamlEditorOptions = {};
  public isUserLoggedIn = false;
  public showConfimDeletePopup = false;
  public showConfimPipelineChangePopup = false;
  public showConfimPipelineCreatePopup = false;
  public showMobileActions = false;
  public socketNotificationSubscription: Subscription = new Subscription();
  public pipelineValidationSubscription: Subscription = new Subscription();
  public pipelineInfo: PipelineInfo;
  public disableActions: boolean;
  public invalidPipelineName = false;
  public pipelinesLoaded = false;
  public editorInstance: Monaco.editor.IStandaloneCodeEditor;
  public editorWidth: number;

  public constructor(
    private jobsService: JobsService,
    private annotationPipelineService: AnnotationPipelineService,
    private dialog: MatDialog,
    private userService: UsersService,
    private socketNotificationsService: SocketNotificationsService,
    private ngZone: NgZone,
    private pipelineStateService: AnnotationPipelineStateService,
    private viewportService: ViewportService,
  ) {
    effect(() => {
      this.editorWidth = this.pipelineStateService.editorWidth();
    });
  }

  public onEditorInit(editor: Monaco.editor.IStandaloneCodeEditor): void {
    this.editorInstance = editor;
    initEditor();
  }

  public ngOnInit(): void {
    this.userService.userData.pipe(
      filter((userData) => userData !== null),
    ).subscribe((userData) => {
      this.isUserLoggedIn = userData.loggedIn;
    });

    this.yamlEditorOptions = editorConfig;
    this.getPipelines();
    this.dropdownControl.valueChanges.pipe(
      startWith(''),
      map(value => this.filter(value || '')),
    ).subscribe(filtered => {
      this.filteredPipelines = filtered;
    });

    this.setupPipelineWebSocketConnection();
  }

  public ngAfterViewInit(): void {
    const editorElement = this.pipelineEditorRef._editorContainer.nativeElement as HTMLElement;

    this.resizeObserver = new ResizeObserver(() => {
      const currentVw = (editorElement.clientWidth / window.innerWidth) * 100;
      if (this.editorWidth !== null) {
        // null = CSS mode (40vw)
        if (Math.abs(currentVw - this.editorWidth) > 2) {
          this.editorWidth = currentVw;
        }
      } else if (Math.abs(currentVw - 40) > 2) {
        this.editorWidth = currentVw;
      }
      if (!this.isEditorMaximized(editorElement) && !this.isEditorMinimized(editorElement)) {
        if (window.innerWidth <= 1200) {
          this.shrinkTextarea();
        } else {
          this.resolveComponentsVisibility(editorElement);
        }
      }
    });

    this.resizeObserver.observe(editorElement);
  }

  private setupPipelineWebSocketConnection(): void {
    this.socketNotificationSubscription = this.socketNotificationsService.getPipelineNotifications().subscribe({
      next: (notification: PipelineNotification) => {
        if (this.currentTemporaryPipelineId === notification.pipelineId) {
          this.currentTemporaryPipelineStatus = notification.status;
          this.pipelineStateService.currentTemporaryPipelineStatus.set(notification.status);
          return;
        }
        if (!this.currentTemporaryPipelineId && !this.pipelines.find(p => p.id === notification.pipelineId)) {
          this.currentTemporaryPipelineId = notification.pipelineId;
          this.currentTemporaryPipelineStatus = notification.status;
          this.pipelineStateService.currentTemporaryPipelineId.set(this.currentTemporaryPipelineId);
          this.pipelineStateService.currentTemporaryPipelineStatus.set(notification.status);
          return;
        }

        const pipeline = this.pipelines.find(p => p.id === notification.pipelineId);
        if (pipeline) {
          pipeline.status = notification.status;
        }
      },
      error: err => console.error(err)
    });
  }

  private isEditorMaximized(editorElement: HTMLElement): boolean {
    // editor's max-width is 95% of window width
    if (editorElement.clientWidth === Math.round(window.innerWidth * 0.95)) {
      this.expandTextarea();
      return true;
    }
    return false;
  }

  private isEditorMinimized(editorElement: HTMLElement): boolean {
    // editor's min-width is 40% of window width
    if (editorElement.clientWidth === Math.round(window.innerWidth * 0.40)) {
      this.shrinkTextarea();
      return true;
    }
    return false;
  }

  private resolveComponentsVisibility(editorElement: HTMLElement): void {
    const remainingWidth = window.innerWidth - editorElement.clientWidth;
    if (remainingWidth < 750) {
      this.hideParentComponents();
    } else if (remainingWidth > 750) {
      this.showParentComponents();
    }
  }

  private getPipelines(defaultPipelineId: string = ''): void {
    if (
      this.pipelineStateService.loadedWhileLoggedIn() === this.isUserLoggedIn &&
      !defaultPipelineId &&
      this.pipelineStateService.pipelines().length
    ) {
      this.pipelines = this.pipelineStateService.pipelines();
      this.filteredPipelines = this.pipelines;
      this.pipelinesLoaded = true;
      this.restoreState();
      return;
    }

    this.pipelinesLoaded = false;
    this.jobsService.getAnnotationPipelines().pipe(take(1)).subscribe({
      next: pipelines => {
        this.pipelines = pipelines;
        this.filteredPipelines = this.pipelines;
        this.pipelineStateService.pipelines.set(pipelines);
        this.pipelineStateService.loadedWhileLoggedIn.set(this.isUserLoggedIn);
        this.pipelinesLoaded = true;
        if (defaultPipelineId) {
          // Post-saveAs path. onPipelineClick would reset
          // currentPipelineText to the GET-response's stale (pre-edit)
          // content, silently dropping any user edits that landed in
          // the gap between the save POST returning and this GET
          // response (tb-348). Select without resetting the buffer so
          // displayUnsavedPipelineIndication picks up edits as a real
          // diff and adds the * indicator.
          const pipeline = this.pipelines.find(p => p.id === defaultPipelineId);
          if (pipeline) {
            this.selectPipelineAfterSave(pipeline);
          }
        } else {
          // Initial-load path. If the user navigated mid-flight (e.g.
          // clicked Annotation Jobs while the first GET /api/pipelines
          // was still pending), a SECOND GET fires from the new
          // component's ngOnInit and its late response can clobber any
          // text the user typed in the meantime — same race family as
          // tb-348 but triggered by navigation, not save (tb-l7c, CI
          // gain-web-e2e #158). Preserve the buffer when it carries
          // user content that differs from the default pipeline's
          // saved content.
          const firstPipeline = this.pipelines[0];
          const userHasTyped = this.currentPipelineText.trim() !== ''
            && this.currentPipelineText.trim() !== firstPipeline?.content.trim();
          if (userHasTyped && firstPipeline) {
            this.selectPipelineAfterSave(firstPipeline);
          } else {
            this.onPipelineClick(firstPipeline);
          }
        }
      },
      error: () => {
        this.disableActions = false;
      }});
  }

  private selectPipelineAfterSave(pipeline: Pipeline): void {
    this.configError = '';
    this.pipelineStateService.isConfigValid.set(true);
    this.selectedPipeline = pipeline;
    this.pipelineStateService.selectedPipelineId.set(pipeline.id);
    this.dropdownControl.setValue(pipeline.name);
    this.displayUnsavedPipelineIndication();
    this.clearTemporaryPipeline();
    this.disableActions = false;
    this.getPipelineInfo();
  }

  private restoreState(): void {
    const pipeline = this.pipelines.find(p => p.id === this.pipelineStateService.selectedPipelineId());
    this.currentTemporaryPipelineId = this.pipelineStateService.currentTemporaryPipelineId();
    this.currentTemporaryPipelineStatus = this.pipelineStateService.currentTemporaryPipelineStatus();
    this.currentPipelineText = this.pipelineStateService.currentPipelineText();

    if (pipeline) {
      this.selectedPipeline = pipeline;
      const name = this.isPipelineChanged() ? `${pipeline.name} *` : pipeline.name;
      this.dropdownControl.setValue(name);
      this.getPipelineInfo();
      this.clearTemporaryPipeline();
    }
  }

  private filter(value: string): Pipeline[] {
    const filterValue = this.normalizeValue(value);
    return this.pipelines.filter(p => this.normalizeValue(p.name).includes(filterValue));
  }

  private normalizeValue(value: string): string {
    return value.toLowerCase().replace(/\s/g, '');
  }

  public resetState(): void {
    this.onPipelineClick(this.pipelines[0]);
  }

  public isConfigValid(): void {
    if (!this.pipelinesLoaded) {
      return;
    }
    this.unselectPublicPipeline();
    this.displayUnsavedPipelineIndication();

    this.pipelineStateService.currentPipelineText.set(this.currentPipelineText);
    this.pipelineStateService.isConfigValid.set(false);

    this.pipelineValidationSubscription.unsubscribe();
    this.pipelineValidationSubscription = this.jobsService.validatePipelineConfig(this.currentPipelineText).pipe(
      take(1)
    ).subscribe((errorReason: string) => {
      this.configError = errorReason;
      if (!this.configError) {
        this.pipelineStateService.isConfigValid.set(true);
        if (this.isPipelineChanged()) {
          // Save pipeline as temporary when valid
          this.autoSave().subscribe(() => this.getPipelineInfo());
        } else {
          this.getPipelineInfo();
        }
      } else {
        this.pipelineStateService.isConfigValid.set(false);
      }
    });
  }

  private displayUnsavedPipelineIndication(): void {
    if (!this.selectedPipeline) {
      return;
    }

    if (this.isPipelineChanged() && !this.dropdownControl.value.includes(' *')) {
      this.dropdownControl.setValue(this.dropdownControl.value + ' *');
    } else if (!this.isPipelineChanged() && this.dropdownControl.value.includes(' *')) {
      this.dropdownControl.setValue(this.dropdownControl.value.replace(' *', ''));
      this.pipelineStateService.selectedPipelineId.set(this.selectedPipeline?.id || '');
      this.clearTemporaryPipeline();
    }
  }

  private unselectPublicPipeline(): void {
    if (this.selectedPipeline && this.selectedPipeline.type === 'default' && this.isPipelineChanged()) {
      this.selectedPipeline = null;
      this.pipelineStateService.selectedPipelineId.set('');
      this.dropdownControl.setValue('');
    }
  }

  public onPipelineClick(pipeline: Pipeline): void {
    if (!pipeline) {
      return;
    }
    this.configError = '';
    this.pipelineStateService.isConfigValid.set(true);
    this.selectedPipeline = pipeline;
    this.pipelineStateService.selectedPipelineId.set(pipeline.id);

    this.currentPipelineText = pipeline.content;
    this.pipelineStateService.currentPipelineText.set(pipeline.content);

    this.dropdownControl.setValue(this.selectedPipeline.name);
    this.clearTemporaryPipeline();
    this.disableActions = false;

    this.getPipelineInfo();
  }

  private getPipelineInfo(): void {
    this.pipelineInfo = null;
    this.pipelineStateService.pipelineInfo.set(null);
    this.annotationPipelineService.getPipelineInfo(this.currentTemporaryPipelineId || this.selectedPipeline.id).pipe(
      take(1)
    ).subscribe({
      next: res => {
        this.pipelineInfo = res;
        this.pipelineStateService.pipelineInfo.set(res);
      },
      error: () => {
        this.pipelineInfo = null;
        this.pipelineStateService.pipelineInfo.set(null);
      }
    });
  }

  public selectPipelineByName(pipelineName: string): void {
    const pipeline = this.pipelines.find(p => p.name === pipelineName);
    if (pipeline) {
      this.onPipelineClick(pipeline);
    }
  }

  public clearTemporaryPipeline(): void {
    this.currentTemporaryPipelineId = '';
    this.currentTemporaryPipelineStatus = null;
    this.pipelineStateService.currentTemporaryPipelineId.set('');
    this.pipelineStateService.currentTemporaryPipelineStatus.set(null);
  }

  public clearPipelineInput(): void {
    if (this.areThereUnsavedChanges()) {
      this.pipelineInputRef.setDisabledState(true);
      this.showConfimPipelineChangePopup = true;
    } else {
      this.dropdownControl.setValue('');
      this.pipelineInputRef.openPanel();
    }
  }

  public confirmChange(confirm: boolean): void {
    this.showConfimPipelineChangePopup = false;
    this.pipelineInputRef.setDisabledState(false);
    if (confirm) {
      this.dropdownControl.setValue('');

      // open dropdown after Angular is done updating the view
      this.ngZone.onStable.pipe(take(1)).subscribe(() => {
        this.pipelineInputRef.openPanel();
      });
    }
  }

  public confirmCreate(confirm: boolean): void {
    this.showConfimPipelineCreatePopup = false;
    if (confirm) {
      this.doClear();
    }
  }

  public displayPipelineNameInInput(): void {
    if (!this.selectedPipeline || this.dropdownControl.value) {
      return;
    }

    this.dropdownControl.setValue(this.selectedPipeline.name);
    this.displayUnsavedPipelineIndication();
  }

  public openAnnotatorFormModal(isResourceWorkflow = false): void {
    const isMobile = this.viewportService.isMobile();
    const newAnnotatorModal = this.dialog.open(NewAnnotatorComponent, {
      id: 'newAnnotator',
      data: {
        pipelineId: this.currentTemporaryPipelineId || this.selectedPipeline?.id,
        isResourceWorkflow: isResourceWorkflow
      },
      height: isMobile ? '85vh' : '70vh',
      width: isMobile ? '95vw' : '80vw',
      maxWidth: isMobile ? '95vw' : '1500px',
      minWidth: isMobile ? 'unset' : '500px',
    });

    newAnnotatorModal.afterClosed().subscribe((result: string) => {
      if (result) {
        this.currentPipelineText += result;
        this.pipelineStateService.currentPipelineText.set(this.currentPipelineText);
        this.autoScroll();
      }
    });
  }

  private autoScroll(): void {
    const editor = this.pipelineEditorRef['_editor'] as Monaco.editor.IStandaloneCodeEditor;
    const contentChangeDisposable = editor.onDidChangeModelContent(() => {
      editor.revealLine(editor.getModel().getLineCount(), 1);
      contentChangeDisposable.dispose();
    });
  }

  public clearPipeline(): void {
    if (!this.currentPipelineText && !this.selectedPipeline) {
      return;
    }

    if (this.areThereUnsavedChanges()) {
      this.showConfimPipelineCreatePopup = true;
    } else {
      this.doClear();
    }
  }

  public doClear(): void {
    this.pipelineInfo = null;
    this.pipelineStateService.pipelineInfo.set(null);
    this.selectedPipeline = null;
    this.pipelineStateService.selectedPipelineId.set('');
    this.currentPipelineText = '';
    this.pipelineStateService.currentPipelineText.set('');
    this.dropdownControl.setValue('');
    this.clearTemporaryPipeline();
    this.isConfigValid();
  }

  private areThereUnsavedChanges(): boolean {
    return (
      this.dropdownControl.value.includes(' *') ||
      this.currentTemporaryPipelineId
    ) &&
      Boolean(this.currentPipelineText);
  }

  public saveAs(): void {
    this.disableActions = true;
    const newNameModalRef = this.dialog.open(this.nameInputTemplateRef, {
      id: 'setPipelineName',
      width: this.viewportService.isMobile() ? '90vw' : '30vw',
      maxWidth: this.viewportService.isMobile() ? '90vw' : '700px'
    });

    newNameModalRef.afterClosed().pipe(
      switchMap((name: string) => {
        if (name) {
          return this.annotationPipelineService.savePipeline('', name, this.currentPipelineText);
        }
        this.disableActions = false;
        return of(null);
      }),
    ).subscribe((pipelineId: string) => {
      if (!pipelineId) {
        return;
      }
      this.getPipelines(pipelineId);
    });
  }

  public autoSave(): Observable<string> {
    return this.annotationPipelineService.savePipeline(
      this.currentTemporaryPipelineId,
      '',
      this.currentPipelineText,
    ).pipe(
      tap((pipelineId: string) => {
        // Set what ID should be used for the next autosave
        // it's better to reuse the same temporary pipeline
        if (this.currentTemporaryPipelineId === '') {
          this.currentTemporaryPipelineId = pipelineId;
          this.pipelineStateService.currentTemporaryPipelineId.set(pipelineId);
        }
      })
    );
  }

  public save(): void {
    if (!this.isPipelineChanged()) {
      return;
    }

    this.disableActions = true;

    this.annotationPipelineService.savePipeline(
      this.selectedPipeline.id,
      this.selectedPipeline.name,
      this.currentPipelineText,
    ).subscribe((pipelineId: string) => {
      if (!pipelineId) {
        return;
      }
      this.getPipelines(pipelineId);
    });
  }

  public delete(): void {
    this.annotationPipelineService.deletePipeline(this.selectedPipeline.id).subscribe(() => {
      // Reset the editor buffer before the post-delete getPipelines().
      // Otherwise the no-arg branch's userHasTyped heuristic (tb-l7c) sees
      // the deleted pipeline's content as "user-typed" and routes through
      // selectPipelineAfterSave + displayUnsavedPipelineIndication, which
      // appends a stray * to the default pipeline that takes its place.
      this.currentPipelineText = '';
      this.pipelineStateService.currentPipelineText.set('');
      this.pipelineStateService.pipelines.set([]);
      this.getPipelines();
    });
    this.showConfimDeletePopup = false;
  }

  public saveName(name: string): void {
    if (this.pipelines.some(p => p.name === name)) {
      this.invalidPipelineName = true;
      return;
    }
    this.invalidPipelineName = false;
    this.dialog.getDialogById('setPipelineName').close(name);
  }

  public cancel(): void {
    this.invalidPipelineName = false;
    this.dialog.getDialogById('setPipelineName').close();
  }

  public isPipelineChanged(): boolean {
    return this.selectedPipeline?.content.trim() !== this.currentPipelineText.trim();
  }

  @HostListener('window:keydown.meta.s', ['$event'])
  @HostListener('document:keydown.control.s', ['$event'])
  public onKeydownHandler(event: Event): void {
    event.preventDefault();
    if (this.selectedPipeline && this.selectedPipeline.type === 'user' && !this.configError && this.isUserLoggedIn) {
      this.save();
    }
  }

  public expandTextarea(): void {
    this.editorWidth = 95; // 95vw
    this.hideParentComponents();
  }

  public shrinkTextarea(): void {
    this.editorWidth = null; // null lets CSS width: 40vw take over
    this.showParentComponents();
  }

  @HostListener('document:click')
  public onDocumentClick(): void {
    this.showMobileActions = false;
  }

  @HostListener('window:resize')
  public onWindowResize(): void {
    if (window.innerWidth <= 1200) {
      this.shrinkTextarea();
    }
  }

  private showParentComponents(): void {
    this.pipelineStateService.hideComponents.set(false);
  }

  private hideParentComponents(): void {
    if (window.innerWidth > 1200) {
      this.pipelineStateService.hideComponents.set(true);
    }
  }

  public ngOnDestroy(): void {
    this.pipelineStateService.editorWidth.set(this.editorWidth);

    if (this.resizeObserver) {
      this.resizeObserver.disconnect();
    }
    this.socketNotificationSubscription.unsubscribe();
  }
}
