import { Component, Input, OnInit } from '@angular/core';
import { SingleAnnotationService } from '../single-annotation.service';
import { CategoricalHistogram, NumberHistogram } from '../single-annotation';
import { NumberHistogramComponent } from '../number-histogram/number-histogram.component';
import { CategoricalHistogramComponent } from '../categorical-histogram/categorical-histogram.component';
import { CommonModule } from '@angular/common';

@Component({
  selector: 'app-histogram-wrapper',
  imports: [CommonModule, NumberHistogramComponent, CategoricalHistogramComponent],
  templateUrl: './histogram-wrapper.component.html'
})
export class HistogramWrapperComponent implements OnInit {
  @Input() public histogramUrl: string;
  @Input() public value: string | number | Map<string, string | number> | string[];
  public histogram: CategoricalHistogram | NumberHistogram = null;

  public constructor(private singleAnnotationService: SingleAnnotationService) { }

  public ngOnInit(): void {
    if (this.histogramUrl) {
      this.singleAnnotationService.getHistogram(this.histogramUrl).subscribe((histogram) => {
        this.histogram = histogram;
      });
    }
  }

  public getValuesAsNumber(value: string | number | Map<string, string | number> | string[]): number[] {
    if (typeof value === 'number') {
      return [value];
    }
    if (value instanceof Map) {
      return [...value.values()].map(v => Number(v));
    }
    const parsed = Number(value);
    if (!value || isNaN(parsed)) {
      return [];
    }
    return [parsed];
  }

  public getValuesAsString(value: string | number | Map<string, string | number> | string[]): string[] {
    if (!value) {
      return [];
    }
    if (typeof value === 'string') {
      return [value];
    }

    if (value instanceof Map) {
      return [...value.values()].map(v => String(v));
    }

    if (Array.isArray(value)) {
      return value;
    }

    return [value.toString()];
  }

  public isCategoricalHistogram(arg: object): arg is CategoricalHistogram {
    return arg instanceof CategoricalHistogram;
  }

  public isNumberHistogram(arg: object): arg is NumberHistogram {
    return arg instanceof NumberHistogram;
  }
}
