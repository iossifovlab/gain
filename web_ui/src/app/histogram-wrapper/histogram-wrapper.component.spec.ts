import { ComponentFixture, TestBed } from '@angular/core/testing';

import { HistogramWrapperComponent } from './histogram-wrapper.component';
import { Observable, of } from 'rxjs';
import { CategoricalHistogram, NumberHistogram } from '../single-annotation';
import { SingleAnnotationService } from '../single-annotation.service';

const mockCategoricalHistogram = new CategoricalHistogram(
  [
    { name: '1', value: 10 },
    { name: '2', value: 20 },
    { name: '3', value: 30 },
    { name: '4', value: 40 },
    { name: '5', value: 50 },
    { name: '6', value: 60 },
  ],
  ['1', '2', '3', '4', '5', '6', '7', '8'],
  'weak evidence for association with ASD',
  'strong evidence for association with ASD',
  false,
  90,
  null,
  null
);

const mockNumberHistogram = new NumberHistogram(
  [1, 2, 3],
  [1, 2, 3],
  'small values',
  'large values',
  0,
  10,
  false,
  false
);

class SingleAnnotationServiceMock {
  public getHistogram(histogramUrl: string): Observable<CategoricalHistogram | NumberHistogram> {
    return histogramUrl.includes('categorical') ? of(mockCategoricalHistogram) : of(mockNumberHistogram);
  }
}

describe('HistogramWrapperComponent', () => {
  let component: HistogramWrapperComponent;
  let fixture: ComponentFixture<HistogramWrapperComponent>;
  const singleAnnotationServiceMock = new SingleAnnotationServiceMock();

  beforeEach(async() => {
    await TestBed.configureTestingModule({
      imports: [HistogramWrapperComponent],
      providers: [
        { provide: SingleAnnotationService, useValue: singleAnnotationServiceMock }
      ]
    }).compileComponents();

    fixture = TestBed.createComponent(HistogramWrapperComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should not request histogram when the link is missing', () => {
    const getHistogramSpy = jest.spyOn(singleAnnotationServiceMock, 'getHistogram');
    component.ngOnInit();
    expect(getHistogramSpy).not.toHaveBeenCalledWith();
    expect(component.histogram).toBeNull();
  });

  it('should display categorical histogram only', () => {
    component.histogramUrl = 'histograms/categorical?test=1';
    component.value = '5';
    component.ngOnInit();
    fixture.detectChanges();

    const catHistogram = (fixture.nativeElement as HTMLElement).getElementsByTagName('app-categorical-histogram')[0];
    const numHistogram = (fixture.nativeElement as HTMLElement).getElementsByTagName('app-number-histogram')[0];
    expect(component.isCategoricalHistogram(component.histogram)).toBeTruthy();
    expect(component.isNumberHistogram(component.histogram)).toBeFalsy();
    expect(catHistogram).toBeTruthy();
    expect(numHistogram).toBeFalsy();
  });

  it('should display number histogram only', () => {
    component.histogramUrl = 'histograms/number?test=1';
    component.value = 10;
    component.ngOnInit();
    fixture.detectChanges();

    const catHistogram = (fixture.nativeElement as HTMLElement).getElementsByTagName('app-categorical-histogram')[0];
    const numHistogram = (fixture.nativeElement as HTMLElement).getElementsByTagName('app-number-histogram')[0];
    expect(component.isCategoricalHistogram(component.histogram)).toBeFalsy();
    expect(component.isNumberHistogram(component.histogram)).toBeTruthy();
    expect(catHistogram).toBeFalsy();
    expect(numHistogram).toBeTruthy();
  });

  it('should parse score values to number array', () => {
    expect(component.getValuesAsNumber('10')).toStrictEqual([10]);
    expect(component.getValuesAsNumber(5)).toStrictEqual([5]);
    expect(component.getValuesAsNumber('not a number')).toStrictEqual([]);
  });

  it('should parse score valuea to string array', () => {
    expect(component.getValuesAsString('value')).toStrictEqual(['value']);
    expect(component.getValuesAsString(5)).toStrictEqual(['5']);
    expect(component.getValuesAsString(null)).toStrictEqual([]);
    expect(component.getValuesAsString('')).toStrictEqual([]);
  });

  it('should parse Map values to number array', () => {
    const map = new Map([['a', 1], ['b', 2], ['c', 3]]);
    expect(component.getValuesAsNumber(map)).toStrictEqual([1, 2, 3]);
  });

  it('should parse Map values to string array', () => {
    const map = new Map([['x', 10], ['y', 20]]);
    expect(component.getValuesAsString(map)).toStrictEqual(['10', '20']);
  });

  it('should return array as-is from getValuesAsString', () => {
    const arr = ['alpha', 'beta', 'gamma'];
    expect(component.getValuesAsString(arr)).toBe(arr);
  });

  it('should return number array as-is from getValuesAsNumber', () => {
    const arr = [1.2, 3.4, 5.6];
    expect(component.getValuesAsNumber(arr)).toBe(arr);
  });

  it('should stringify number array from getValuesAsString', () => {
    expect(component.getValuesAsString([1, 2, 3])).toStrictEqual(['1,2,3']);
  });
});
