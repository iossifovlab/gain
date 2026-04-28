import { Component, ViewChild, OnInit, NgZone, HostListener, effect } from '@angular/core';
import { take } from 'rxjs';
import { AnnotationPipelineComponent } from '../annotation-pipeline/annotation-pipeline.component';
import { CommonModule } from '@angular/common';
import { SingleAnnotationComponent } from '../single-annotation/single-annotation.component';
import { AnnotatablesTableComponent } from '../annotatables-table/annotatables-table.component';
import { UsersService } from '../users.service';
import { AnnotationPipelineService } from '../annotation-pipeline.service';
import { AnnotationPipelineStateService } from '../annotation-pipeline/annotation-pipeline-state.service';

@Component({
  selector: 'app-single-annotation-wrapper',
  imports: [
    CommonModule,
    AnnotationPipelineComponent,
    SingleAnnotationComponent,
    AnnotatablesTableComponent,
  ],
  templateUrl: './single-annotation-wrapper.component.html',
  styleUrl: './single-annotation-wrapper.component.css'
})

export class SingleAnnotationWrapperComponent implements OnInit {
  public creationError = '';
  @ViewChild(AnnotationPipelineComponent) public pipelinesComponent: AnnotationPipelineComponent;
  @ViewChild(AnnotatablesTableComponent) public annotatablesTableComponent: AnnotatablesTableComponent;
  @ViewChild(SingleAnnotationComponent) public singleAnnotationComponent: SingleAnnotationComponent;
  public hideComponents = false;
  public hideHistory = false;
  public isUserLoggedIn = false;


  public constructor(
      private userService: UsersService,
      private ngZone: NgZone,
      private annotationPipelineService: AnnotationPipelineService,
      private pipelineStateService: AnnotationPipelineStateService,
  ) {
    effect(() => {
      const id = this.pipelineStateService.currentTemporaryPipelineId() ||
        this.pipelineStateService.selectedPipelineId();
      if (id) {
        this.resetSingleAnnotationReport();
        this.annotationPipelineService.loadPipeline(id).pipe(take(1)).subscribe();
      }
    });

    effect(() => {
      this.pipelineStateService.currentPipelineText();
      this.resetSingleAnnotationReport();
    });
  }

  public ngOnInit(): void {
    this.userService.userData.pipe(
    ).subscribe((userData) => {
      this.isUserLoggedIn = userData.loggedIn;
    });
  }

  @HostListener('window:beforeunload', ['$event'])
  public beforeUnload(event: BeforeUnloadEvent): void {
    if (this.pipelinesComponent.isPipelineChanged()) {
      event.preventDefault(); // display the confirmation dialog
    }
  }

  public autoSavePipeline(): void {
    if (!this.pipelineStateService.isConfigValid()) {
      return;
    }
    if (this.pipelinesComponent.isPipelineChanged()) {
      this.pipelinesComponent.autoSave().pipe(take(1)).subscribe(() => {
        this.annotate();
      });
    } else {
      this.annotate();
    }
  }

  private annotate(): void {
    this.singleAnnotationComponent.annotate();
  }

  public triggerSingleAnnotation(annotatable: string): void {
    this.singleAnnotationComponent.setAnnotatable(annotatable);
    this.autoSavePipeline();
  }

  public resetSingleAnnotationReport(): void {
    this.singleAnnotationComponent?.resetReport();
  }

  public refreshAnnotatablesTable(): void {
    this.annotatablesTableComponent.refreshTable();
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
