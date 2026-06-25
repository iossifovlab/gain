import { Component, inject, Input, OnInit } from '@angular/core';
import { SingleAnnotationService } from '../single-annotation.service';
import { CategoricalHistogram, NumberHistogram, ValueType } from '../single-annotation';
import { NumberHistogramComponent } from '../number-histogram/number-histogram.component';
import { CategoricalHistogramComponent } from '../categorical-histogram/categorical-histogram.component';


@Component({
  selector: 'app-histogram-wrapper',
  imports: [NumberHistogramComponent, CategoricalHistogramComponent],
  templateUrl: './histogram-wrapper.component.html'
})
export class HistogramWrapperComponent implements OnInit {
  @Input() public histogramUrl: string;
  @Input() public value: ValueType;
  public histogram: CategoricalHistogram | NumberHistogram = null;

  private readonly singleAnnotationService = inject(SingleAnnotationService);

  public ngOnInit(): void {
    if (this.histogramUrl) {
      this.singleAnnotationService.getHistogram(this.histogramUrl).subscribe((histogram) => {
        this.histogram = histogram;
      });
    }
  }

  public getValuesAsNumber(value: ValueType): number[] {
    if (Array.isArray(value) && value.every(v => typeof v === 'number')) {
      return value;
    }

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

  public getValuesAsString(value: ValueType): string[] {
    if (!value) {
      return [];
    }
    if (typeof value === 'string') {
      return [value];
    }

    if (value instanceof Map) {
      return [...value.values()].map(v => String(v));
    }

    if (Array.isArray(value) && value.every(v => typeof v === 'string')) {
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
