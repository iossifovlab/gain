import { FormatResultValuePipe } from './format-result-value.pipe';

describe('FormatResultValuePipe', () => {
  it('create an instance', () => {
    const pipe = new FormatResultValuePipe();
    expect(pipe).toBeTruthy();
  });

  it('should format string value', () => {
    const pipe = new FormatResultValuePipe();
    expect(pipe.transform('missense')).toBe('missense');
    expect(pipe.transform(
      'ENST00000376583.7:MTHFR:missense:263/697(Ala->Val)|ENST00000376585.6:MTHFR:missense:263/697(Ala->Val)'
    )).toBe('ENST00000376583.7:MTHFR:missense:263/697(Ala->Val)|ENST00000376585.6:MTHFR:missense:263/697(Ala->Val)');
  });

  it('should format number value', () => {
    const pipe = new FormatResultValuePipe();
    expect(pipe.transform(0)).toBe('0');
    expect(pipe.transform(10, 5)).toBe('10');
    expect(pipe.transform(123)).toBe('123');
    expect(pipe.transform(1234, 5)).toBe('1234');
    expect(pipe.transform(1801133, 3)).toBe('1.8e+06');
    expect(pipe.transform(123456789, 3)).toBe('1.23e+08');
    expect(pipe.transform(0.32269200682640076, 7)).toBe('0.322692');
    expect(pipe.transform(0.123456, 3)).toBe('0.123');
    expect(pipe.transform(123456.123456)).toBe('1.23e+05');
    expect(pipe.transform(12345.5)).toBe('12345.5');
    expect(pipe.transform(1234.5)).toBe('1234.5');
    expect(pipe.transform(123.5)).toBe('123.5');
    expect(pipe.transform(12345)).toBe('12345');
    expect(pipe.transform(1234)).toBe('1234');
    expect(pipe.transform(123)).toBe('123');
    expect(pipe.transform(12345.0)).toBe('12345');
    expect(pipe.transform(1234.0)).toBe('1234');
    expect(pipe.transform(123.0)).toBe('123');
    expect(pipe.transform(12.0, 4)).toBe('12');
  });


  it('should format value of type map with strings', () => {
    const pipe = new FormatResultValuePipe();
    const map = new Map<string, string>([['MTHFR', 'missense'], ['ABC', 'nonsense']]);
    expect(pipe.transform(map)).toBe('MTHFR:missense; ABC:nonsense');
  });

  it('should format value of type map with string and number', () => {
    const pipe = new FormatResultValuePipe();
    const map = new Map<string, number>([['MTHFR', 15454], ['ABC', 52]]);
    expect(pipe.transform(map, 3)).toBe('MTHFR:15454; ABC:52');
  });

  it('should format value of type array with strings', () => {
    const pipe = new FormatResultValuePipe();
    expect(pipe.transform(['MTHFR', 'ABC'])).toBe('MTHFR, ABC');
  });
});
