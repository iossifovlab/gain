import { Component, effect, ElementRef, inject, Input, TemplateRef, ViewChild } from '@angular/core';
import { Annotator, Attribute, SingleAnnotationReport } from '../single-annotation';
import { CommonModule } from '@angular/common';
import { MarkdownModule } from 'ngx-markdown';
import { HistogramWrapperComponent } from '../histogram-wrapper/histogram-wrapper.component';
import { EffectTableComponent } from '../effect-table/effect-table.component';
import { saveAs } from 'file-saver';
import { MatDialog } from '@angular/material/dialog';
import { FormatResultValuePipe } from '../format-result-value.pipe';
import { SingleAnnotationReportStateService } from './single-annotation-report-state.service';
import { ViewportService } from '../viewport.service';
import { isArray } from 'lodash';

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
  public sortState = new Map<Attribute, { column: string; direction: 'asc' | 'desc' }>();

  private readonly dialog = inject(MatDialog);
  private readonly singleAnnotationReportStateService = inject(SingleAnnotationReportStateService);
  private readonly viewportService = inject(ViewportService);

  public constructor() {
    effect(() => {
      this.showFullReport = this.singleAnnotationReportStateService.isFullReport();
    });
  }

  public showInfo(annotator: Annotator): void {
    const isMobile = this.viewportService.isMobile();
    this.dialog.open(this.infoModalRef, {
      data: annotator,
      width: isMobile ? '95vw' : '50vw',
      maxWidth: isMobile ? '95vw' : '1000px',
      minWidth: isMobile ? 'unset' : '500px',
      maxHeight: isMobile ? '70vh' : '700px',
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
          if (isArray(val)) {
            value = val.join(';');
          } else if (typeof val === 'object') {
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

  public sort(column: string, attribute: Attribute): void {
    const current = this.sortState.get(attribute);
    if (current && current.column === column) {
      this.sortState.set(attribute, { column: column, direction: current.direction === 'asc' ? 'desc' : 'asc' });
    } else {
      this.sortState.set(attribute, { column: column, direction: 'asc' });
    }
    this.sortData(attribute);
  }

  public getSortIcon(column: string, attribute: Attribute): string {
    const state = this.sortState.get(attribute);
    if (!state || state.column !== column) {
      return 'unfold_more';
    }
    return state.direction === 'asc' ? 'keyboard_arrow_up' : 'keyboard_arrow_down';
  }

  public sortData(attribute: Attribute): void {
    const state = this.sortState.get(attribute);
    if (!state) {
      return;
    }
    const { column, direction } = state;
    const cmpValues = (a: string | number, b: string | number): number => {
      if (typeof a === 'number' && typeof b === 'number') {
        return a - b;
      }
      return String(a).localeCompare(String(b), undefined, { sensitivity: 'base' });
    };
    if (this.isValueArray(attribute.result.value)) {
      attribute.result.value = [...attribute.result.value].sort((a, b) => {
        const cmp = cmpValues(a, b);
        return direction === 'asc' ? cmp : -cmp;
      });
      return;
    }
    if (!this.isValueMap(attribute.result.value)) {
      return;
    }
    if (column === 'Key') {
      attribute.result.value = new Map([...attribute.result.value.entries()].sort((a, b) => {
        const cmp = cmpValues(a[0], b[0]);
        return direction === 'asc' ? cmp : -cmp;
      }));
    } else {
      attribute.result.value = new Map([...attribute.result.value.entries()].sort((a, b) => {
        const cmp = cmpValues(a[1], b[1]);
        return direction === 'asc' ? cmp : -cmp;
      }));
    }
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
