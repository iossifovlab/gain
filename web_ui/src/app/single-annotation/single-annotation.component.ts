import { Component, EventEmitter, OnInit, Output } from '@angular/core';
import { environment } from '../../../environments/environment';
import { CommonModule } from '@angular/common';
import { FormControl, FormsModule, ReactiveFormsModule } from '@angular/forms';
import { SingleAnnotationReportComponent } from '../single-annotation-report/single-annotation-report.component';
import { SingleAnnotationService } from '../single-annotation.service';
import { SingleAnnotationReport, Annotatable } from '../single-annotation';
import { UsersService } from '../users.service';
import { distinctUntilChanged, Subscription } from 'rxjs';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatMenuModule } from '@angular/material/menu';
import { AnnotationPipelineStateService } from '../annotation-pipeline/annotation-pipeline-state.service';


@Component({
  selector: 'app-single-annotation',
  imports: [
    CommonModule,
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
  public alleleInput: FormControl<string>;
  public report: SingleAnnotationReport = null;
  @Output() public alleleUpdateEmit = new EventEmitter<void>();
  @Output() public autoSaveTrigger = new EventEmitter<void>();
  private getReportSubscription = new Subscription();
  public loading = false;
  private alleleJson: Annotatable;
  public examples: string[];

  public constructor(
    private singleAnnotationService: SingleAnnotationService,
    private userService: UsersService,
    private pipelineStateService: AnnotationPipelineStateService,
  ) { }

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

    this.alleleInput = new FormControl('');

    this.alleleInput.valueChanges.pipe(
      distinctUntilChanged(),
    ).subscribe(value => {
      this.report = null;
      this.alleleJson = undefined;
      if (value && !this.isAlleleValid(value)) {
        this.alleleInput.setErrors({ invalidAllele: true });
      } else {
        this.alleleInput.setErrors(null);
      }
    });
  }

  public triggerPipelineAutoSave(): void {
    this.autoSaveTrigger.emit();
  }

  public annotateAllele(): void {
    const pipelineId = this.pipelineStateService.currentTemporaryPipelineId() ||
      this.pipelineStateService.selectedPipelineId() ||
      '';
    if (this.alleleInput.valid && pipelineId) {
      this.getReport(pipelineId);
    } else {
      this.alleleJson = undefined;
      this.report = null;
    }
  }

  public disableGo(): boolean {
    const pipelineId = this.pipelineStateService.currentTemporaryPipelineId() ||
      this.pipelineStateService.selectedPipelineId() ||
      '';
    return !(this.alleleInput.value &&
      this.alleleInput.valid &&
      Boolean(pipelineId) &&
      this.pipelineStateService.isConfigValid());
  }

  private isAlleleValid(allele: string): boolean {
    const trimmedValue: string = allele.trim();

    const parts = this.splitAllele(trimmedValue);

    if (parts.length === 4) {
      this.alleleJson = new Annotatable(
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
      this.alleleJson = new Annotatable(
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
      this.alleleJson = new Annotatable(
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
    this.alleleJson = undefined;
    return false;
  }

  private splitAllele(allele: string): string[] {
    const parts = allele.split(/[: \t]+/);
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

  public setAllele(historyAllele: string): void {
    this.alleleInput.setValue(historyAllele);
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
      this.alleleJson,
      pipelineId
    ).subscribe({
      next: report => {
        this.loading = false;
        this.report = report;
        this.triggerAllelesTableUpdate();
      },
      error: () => {
        this.loading = false;
      }
    });
  }

  private triggerAllelesTableUpdate(): void {
    this.userService.userData.pipe(
    ).subscribe((userData) => {
      if (userData.loggedIn) {
        this.alleleUpdateEmit.emit();
      }
    });
  }
}
