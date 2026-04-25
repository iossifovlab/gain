import { Component, effect, ElementRef, Input, TemplateRef, ViewChild } from '@angular/core';
import { Annotator, SingleAnnotationReport } from '../single-annotation';
import { CommonModule } from '@angular/common';
import { MarkdownModule } from 'ngx-markdown';
import { HistogramWrapperComponent } from '../histogram-wrapper/histogram-wrapper.component';
import { EffectTableComponent } from '../effect-table/effect-table.component';
import { saveAs } from 'file-saver';
import { MatDialog } from '@angular/material/dialog';
import { FormatResultValuePipe } from '../format-result-value.pipe';
import { SingleAnnotationReportStateService } from './single-annotation-report-state.service';

@Component({
  selector: 'app-single-annotation-report',
  imports: [
    CommonModule,
    MarkdownModule,
    HistogramWrapperComponent,
    EffectTableComponent,
    FormatResultValuePipe
  ],
  templateUrl: './single-annotation-report.component.html',
  styleUrl: './single-annotation-report.component.css'
})
export class SingleAnnotationReportComponent {
  @Input() public report: SingleAnnotationReport = null;
  public tableViewSources = ['effect_details', 'gene_effects'];
  public showFullReport: boolean;
  @ViewChild('infoModal') public infoModalRef: TemplateRef<ElementRef>;


  public constructor(
    private dialog: MatDialog,
    private singleAnnotationReportStateService: SingleAnnotationReportStateService
  ) {
    effect(() => {
      this.showFullReport = this.singleAnnotationReportStateService.isFullReport();
    });
  }

  public showInfo(annotator: Annotator): void {
    this.dialog.open(this.infoModalRef, {
      data: annotator,
      width: '50vw',
      maxWidth: '1000px',
      minWidth: '500px',
      maxHeight: '700px',
    });
  }

  public toggleView(): void {
    this.singleAnnotationReportStateService.isFullReport.set(!this.showFullReport);
  }

  public saveReport(): void {
    const fileName = `${this.report.annotatable.chromosome}_${this.report.annotatable.position}`
      + `_${this.report.annotatable.reference}_${this.report.annotatable.alternative}`
      + '_report.tsv';

    let reportLines: string = 'Attribute name\tValue\tDescription\n';

    reportLines += `chromosome\t${this.report.annotatable.chromosome}\n`;
    reportLines += `position\t${this.report.annotatable.position}\n`;
    reportLines += `reference\t${this.report.annotatable.reference}\n`;
    reportLines += `alternative\t${this.report.annotatable.alternative}\n`;

    this.report.annotators.forEach(annotator => {
      annotator.attributes.forEach(attribute => {
        let value = '';
        const val = attribute.result.value;
        if (val instanceof Map) {
          val.forEach((v, k) => {
            value += `${k}:${v};`;
          });
          if (value.length > 0) {
            value = value.slice(0, -1); // Remove trailing ;
          }
        } else if (val !== null) {
          if (typeof val === 'object') {
            try {
              value = JSON.stringify(val);
            } catch {
              value = String(val);
            }
          } else {
            value = String(val);
          }
        } else {
          value = '';
        }
        const description = attribute.description.replace(/\r?\n/g, ' ').trim();
        reportLines += `${attribute.name}\t${value}\t${description}\n`;
      });
    });
    reportLines.trim();
    const content = new Blob([reportLines], {type: 'text/plain;charset=utf-8'});
    saveAs(content, fileName);
  }

  public isValueMap(value: unknown): value is Map<string, string | number> {
    return value instanceof Map;
  }

  public isValueArray(value: unknown): value is string[] {
    return Array.isArray(value);
  }

  public asArray(value: unknown): string[] {
    return value as string[];
  }

  public asMapEntries(value: unknown): [string, string | number][] {
    return Array.from((value as Map<string, string | number>).entries());
  }
}
