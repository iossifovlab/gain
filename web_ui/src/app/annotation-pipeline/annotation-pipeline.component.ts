import { CommonModule } from '@angular/common';
import {
  AfterViewInit,
  Component,
  ElementRef,
  EventEmitter,
  HostListener,
  NgZone,
  OnDestroy,
  OnInit,
  Output,
  TemplateRef,
  ViewChild,
} from '@angular/core';
import { map, Observable, of, startWith, Subscription, switchMap, take, tap } from 'rxjs';
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
  public displayFullScreenButton = true;
  public displayResetScreenButton = false;
  public editorSize: 'small' | 'full' | 'custom' = 'small';
  @Output() public tiggerHidingComponents = new EventEmitter<boolean>();
  public isUserLoggedIn = false;
  public showConfimDeletePopup = false;
  public showConfimPipelineChangePopup = false;
  public showConfimPipelineCreatePopup = false;
  public socketNotificationSubscription: Subscription = new Subscription();
  public pipelineValidationSubscription: Subscription = new Subscription();
  public pipelineInfo: PipelineInfo;
  public disableActions: boolean;
  public invalidPipelineName = false;
  public pipelinesLoaded = false;

  public constructor(
    private jobsService: JobsService,
    private annotationPipelineService: AnnotationPipelineService,
    private dialog: MatDialog,
    private userService: UsersService,
    private socketNotificationsService: SocketNotificationsService,
    private ngZone: NgZone,
    private pipelineStateService: AnnotationPipelineStateService,
  ) { }

  public onEditorInit(): void {
    initEditor();
  }

  public ngOnInit(): void {
    this.userService.userData.pipe(
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
      if (!this.isEditorMaximized(editorElement) && !this.isEditorMinimized(editorElement)) {
        this.resolveComponentsVisibility(editorElement);
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
      error: err => {
        console.error(err);
        if (err instanceof CloseEvent && err.type === 'close') {
          this.socketNotificationSubscription.unsubscribe();
          this.setupPipelineWebSocketConnection();
        }
      },
      complete: () => {
        this.socketNotificationSubscription.unsubscribe();
        this.setupPipelineWebSocketConnection();
      }
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

  private resolveResizeButtonsVisibility(editorElement: HTMLElement): void {
    const maxWidth = Math.round(window.innerWidth * 0.95);
    const minWidth = Math.round(window.innerWidth * 0.40);
    if (
      editorElement.clientWidth < maxWidth && editorElement.clientWidth > minWidth ||
      editorElement.clientWidth < minWidth
    ) {
      this.displayFullScreenButton = true;
      this.displayResetScreenButton = true;
      this.editorSize = 'custom';
    }
  }

  private resolveComponentsVisibility(editorElement: HTMLElement): void {
    const parentWidth = document.getElementById('annotation-container').clientWidth;
    if (editorElement.clientWidth > parentWidth/1.5) {
      this.hideParentComponents();
    } else if (editorElement.clientWidth < parentWidth/1.5) {
      this.showParentComponents();
    }
    this.resolveResizeButtonsVisibility(editorElement);
  }

  private getPipelines(defaultPipelineId: string = ''): void {
    if (this.pipelineStateService.pipelines().length && !defaultPipelineId) {
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
        this.pipelinesLoaded = true;
        if (defaultPipelineId) {
          this.onPipelineClick(this.pipelines.find(p => p.id === defaultPipelineId));
        } else {
          this.onPipelineClick(this.pipelines[0]);
        }
      },
      error: () => {
        this.disableActions = false;
      }});
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
    const newAnnotatorModal = this.dialog.open(NewAnnotatorComponent, {
      id: 'newAnnotator',
      data: {
        pipelineId: this.currentTemporaryPipelineId || this.selectedPipeline?.id,
        isResourceWorkflow: isResourceWorkflow
      },
      height: '70vh',
      width: '80vw',
      maxWidth: '1500px',
      minWidth: '500px'
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
      width: '30vw',
      maxWidth: '700px'
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
    this.displayFullScreenButton = false;
    this.displayResetScreenButton = true;
    this.editorSize = 'full';

    this.hideParentComponents();
  }

  public shrinkTextarea(): void {
    this.displayFullScreenButton = true;
    this.displayResetScreenButton = false;
    this.editorSize = 'small';

    this.showParentComponents();
  }

  private showParentComponents(): void {
    this.tiggerHidingComponents.emit(false);
  }

  private hideParentComponents(): void {
    this.tiggerHidingComponents.emit(true);
  }

  public ngOnDestroy(): void {
    if (this.resizeObserver) {
      this.resizeObserver.disconnect();
    }
  }

  public editorWidth(): string {
    if (this.editorSize === 'full') {
      return '95vw';
    } else if (this.editorSize === 'small') {
      return '40vw';
    } else {
      return 'auto';
    }
  }

  public editorHeight(): string {
    if (this.editorSize === 'full') {
      return '70vh';
    } else if (this.editorSize === 'small') {
      return '40vh';
    } else {
      return 'auto';
    }
  }
}
