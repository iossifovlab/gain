import { Pipe, PipeTransform } from '@angular/core';

@Pipe({
  name: 'formatResultValue'
})
export class FormatResultValuePipe implements PipeTransform {
  public transform(value: string | number | Map<string, string | number> | string[], numberOfDigits?: number): string {
    const values: string[] = [];
    if (value instanceof Map) {
      value.forEach((v, k) => {
        if (typeof v === 'number') {
          values.push(`${k}:${this.formatNumber(v, numberOfDigits)}`);
        } else {
          values.push(`${k}:${v}`);
        }
      });
    } else if (typeof value === 'number') {
      return this.formatNumber(value, numberOfDigits);
    } else if (typeof value === 'string') {
      return value;
    } else if (Array.isArray(value)) {
      return value.join(', ');
    }
    return values.join('; ');
  }

  private formatNumber(value: number, numberOfDigits?: number): string {
    if (!isFinite(value)) {
      return value.toString();
    }

    const digits = numberOfDigits ?? 3;
    const precision = Math.abs(value) >= 100 && Math.abs(value) < 100000 ? 6 : digits;

    let s = value.toPrecision(precision);

    if (s.includes('.')) {
      if (s.includes('e')) {
        // Remove trailing zeros after last significant digit before exponent: e.g. 1.80e+6 → 1.8e+6
        s = s.replace(/(\.\d*?[1-9])0+(e)/, '$1$2');
        // Remove decimal point and all zeros when mantissa is whole: e.g. 1.000e+6 → 1e+6
        s = s.replace(/\.0+(e)/, '$1');
      } else {
        // Remove trailing zeros after last significant digit: e.g. 0.3226920 → 0.322692
        s = s.replace(/(\.\d*?[1-9])0+$/, '$1');
        // Remove decimal point and all zeros when value is whole: e.g. 12345.0 → 12345
        s = s.replace(/\.0+$/, '');
      }
    }

    // Pad exponent to at least two digits to match Python format: e.g. e+6 → e+06
    s = s.replace(/e([+-])0*(\d+)$/, (_, sign, exp: string) =>
      `e${sign}${exp.padStart(2, '0')}`
    );

    return s;
  }
}
