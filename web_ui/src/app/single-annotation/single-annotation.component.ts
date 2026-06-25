import { Component, EventEmitter, OnInit, Output, inject } from '@angular/core';
import { environment } from '../../../environments/environment';

import { FormControl, FormsModule, ReactiveFormsModule } from '@angular/forms';
import { SingleAnnotationReportComponent } from '../single-annotation-report/single-annotation-report.component';
import { SingleAnnotationService } from '../single-annotation.service';
import { SingleAnnotationReport, Annotatable } from '../single-annotation';
import { UsersService } from '../users.service';
import { distinctUntilChanged, filter, Subscription } from 'rxjs';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatMenuModule } from '@angular/material/menu';
import { AnnotationPipelineStateService } from '../annotation-pipeline/annotation-pipeline-state.service';


@Component({
  selector: 'app-single-annotation',
  imports: [
    FormsModule,
    SingleAnnotationReportComponent,
    MatProgressSpinnerModule,
    MatMenuModule,
    ReactiveFormsModule
  ],
  templateUrl: './single-annotation.component.html',
  styleUrl: './single-annotation.component.css'
})
export class SingleAnnotationComponent implements OnInit {
  public readonly environment = environment;
  public annotatableInput: FormControl<string>;
  public report: SingleAnnotationReport = null;
  @Output() public annotatableUpdateEmit = new EventEmitter<void>();
  @Output() public autoSaveTrigger = new EventEmitter<void>();
  private getReportSubscription = new Subscription();
  public loading = false;
  private annotatableJson: Annotatable;
  public examples: string[];
  public annotateErrorMessage: string = '';

  private readonly pipelineStateService: AnnotationPipelineStateService = inject(AnnotationPipelineStateService);
  private readonly singleAnnotationService: SingleAnnotationService = inject(SingleAnnotationService);
  private readonly userService: UsersService = inject(UsersService);

  public ngOnInit(): void {
    this.examples = [
      'chr1 11796321 G A',
      'chr1:11796321:G:A',
      'chr1 11796321 G>A',
      'chr1:11796321:G>A',
      'chr1 11796321 11800000',
      'chr1:11796321-11800000',
      'chr1 11796321',
      'chr1:11796321',
      'chr1 11796321 G GT',
      'chr1 11,796,321 11,800,000',
    ];

    this.annotatableInput = new FormControl('');

    this.annotatableInput.valueChanges.pipe(
      distinctUntilChanged(),
    ).subscribe(value => {
      this.report = null;
      this.annotatableJson = undefined;
      if (value && !this.isAnnotatableValid(value)) {
        this.annotatableInput.setErrors({ invalidAnnotatable: true });
      } else {
        this.annotatableInput.setErrors(null);
      }
    });
  }

  public triggerPipelineAutoSave(): void {
    this.autoSaveTrigger.emit();
  }

  public annotate(): void {
    const pipelineId = this.pipelineStateService.currentTemporaryPipelineId() ||
      this.pipelineStateService.selectedPipelineId() ||
      '';
    if (this.annotatableInput.valid && pipelineId) {
      this.getReport(pipelineId);
    } else {
      this.annotatableJson = undefined;
      this.report = null;
    }
  }

  public disableGo(): boolean {
    const pipelineId = this.pipelineStateService.currentTemporaryPipelineId() ||
      this.pipelineStateService.selectedPipelineId() ||
      '';
    return !(this.annotatableInput.value &&
      this.annotatableInput.valid &&
      Boolean(pipelineId) &&
      this.pipelineStateService.isConfigValid());
  }

  private isAnnotatableValid(annotatable: string): boolean {
    const trimmedValue: string = annotatable.trim();

    const parts = this.splitAnnotatable(trimmedValue);

    if (parts.length === 4) {
      this.annotatableJson = new Annotatable(
        parts[0],
        Number(parts[1].replaceAll(',', '')),
        parts[2],
        parts[3],
        null,
        null,
        null
      );
      return this.isPosValid(parts[1]) && this.isRefValid(parts[2]) && this.isAltValid(parts[3]);
    }

    if (parts.length === 3) {
      this.annotatableJson = new Annotatable(
        parts[0],
        null,
        null,
        null,
        null,
        Number(parts[1].replaceAll(',', '')),
        Number(parts[2].replaceAll(',', '')),
      );

      return this.isPosValid(parts[1]) &&
        this.isPosValid(parts[2]) &&
        Number(parts[1].replaceAll(',', '')) <= Number(parts[2].replaceAll(',', ''));
    }

    if (parts.length === 2) {
      this.annotatableJson = new Annotatable(
        parts[0],
        Number(parts[1].replaceAll(',', '')),
        null,
        null,
        null,
        null,
        null
      );
      return this.isPosValid(parts[1]);
    }
    this.annotatableJson = undefined;
    return false;
  }

  private splitAnnotatable(annotatble: string): string[] {
    const parts = annotatble.split(/[: \t]+/);
    const [chrom, pos, ref, alt] = parts;

    if (!pos) {
      return [chrom];
    }

    if (pos.includes('-')) {
      const [posBeg, posEnd] = pos.split('-');
      return [chrom, posBeg, posEnd];
    }

    if (!ref && !alt) {
      return [chrom, pos];
    }

    if (ref.includes('>')) {
      const [r, a] = ref.split('>');
      return [chrom, pos, r, a];
    }

    if (ref && !alt) {
      return [chrom, pos, ref];
    }

    return [chrom, pos, ref, alt];
  }

  private isPosValid(position: string): boolean {
    if (position.includes(',')) {
      const formattedPosition = position.replace(/(?<=\d),(?=(\d{3})+(?!\d))/g, '');
      return !formattedPosition.includes(',') && position !== '' && !isNaN(Number(position.replaceAll(',', '')));
    } else {
      return position !== '' && !isNaN(Number(position));
    }
  }

  private isRefValid(reference: string): boolean {
    return reference !== '' && this.areBasesValid(reference);
  }

  private areBasesValid(bases: string): boolean {
    const validBases = ['A', 'C', 'G', 'T', 'N', 'a', 'c', 'g', 't', 'n'];
    const bList = bases.split('');
    return bList.every(b => validBases.includes(b));
  }

  private isAltValid(alternative: string): boolean {
    return alternative !== '' && this.areBasesValid(alternative);
  }

  public setAnnotatable(historyAnnotatble: string): void {
    this.annotatableInput.setValue(historyAnnotatble);
    this.resetReport();
  }

  public resetReport(): void {
    this.report = null;
  }

  private getReport(pipelineId: string): void {
    if (!pipelineId || this.disableGo()) {
      return;
    }
    this.getReportSubscription.unsubscribe();
    this.loading = true;
    this.getReportSubscription = this.singleAnnotationService.getReport(
      this.annotatableJson,
      pipelineId
    ).subscribe({
      next: report => {
        this.loading = false;
        this.report = report;
        this.triggerAnnotatblesTableUpdate();
      },
      error: (err: Error) => {
        this.loading = false;
        this.annotateErrorMessage = err.message;
      }
    });
  }

  private triggerAnnotatblesTableUpdate(): void {
    this.userService.userData.pipe(
      filter((userData) => userData !== null),
    ).subscribe((userData) => {
      if (userData.loggedIn) {
        this.annotatableUpdateEmit.emit();
      }
    });
  }
}
