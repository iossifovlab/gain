import { ComponentFixture, TestBed } from '@angular/core/testing';

import { NumberHistogramComponent } from './number-histogram.component';
import { NumberHistogram } from '../single-annotation';

const mockHistogram = new NumberHistogram(
  [207, 211, 506],
  [1, 2, 3, 4],
  'small values',
  'large values',
  1,
  3,
  false,
  false
);

describe('NumberHistogramComponent', () => {
  let component: NumberHistogramComponent;
  let fixture: ComponentFixture<NumberHistogramComponent>;

  beforeEach(async() => {
    await TestBed.configureTestingModule({
      imports: [NumberHistogramComponent]
    }).compileComponents();

    fixture = TestBed.createComponent(NumberHistogramComponent);
    component = fixture.componentInstance;

    component.histogram = mockHistogram;
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should display red mark on histogram and single score value', () => {
    component.scoreValues = [2];
    fixture.detectChanges();
    const svg = (fixture.nativeElement as HTMLElement).querySelector('svg') as SVGElement;
    const redLine = svg.querySelector('.single-score-marker');
    expect(redLine).toBeTruthy();
    expect(redLine.getAttribute('x1')).toBe('115.2439024390244');
    expect(redLine.getAttribute('x2')).toBe('115.2439024390244');
    expect(redLine.getAttribute('y1')).toBe('49.90118577075099');
    expect(redLine.getAttribute('y2')).toBe('-20');
    expect(redLine.getAttribute('style')).toBe('stroke: rgb(255, 0, 0); stroke-width: 2;');
  });

  it('should not display red mark on histogram and value when there is no value', () => {
    component.scoreValues = [];
    fixture.detectChanges();
    const svg = (fixture.nativeElement as HTMLElement).querySelector('svg') as SVGElement;
    const redLine = svg.querySelector('.single-score-marker');
    expect(redLine).toBeFalsy();

    const valueText = svg.querySelector('.single-score-value');
    expect(valueText).toBeFalsy();
  });

  it('should set up xScale, scaleXAxis and scaleYAxis correctly', () => {
    component.ngOnInit();
    expect(component.histogram.bars).toHaveLength(4);
    expect(component.histogram.bins).toHaveLength(4);
    expect(component.xScale.domain()).toStrictEqual(['0', '1', '2', '3']);
    expect(component.xScale.range()).toStrictEqual([0, 450]);

    expect(component.scaleYAxis.range()).toStrictEqual([50, 0]);
    expect(component.scaleYAxis.domain()).toStrictEqual([0, 506]);

    expect(component.scaleXAxis.range()).toStrictEqual([
      0,
      5.487804878048774,
      115.2439024390244,
      225,
      450
    ]);
    expect(component.scaleXAxis.domain()).toStrictEqual([1, 2, 3, 4]);
  });

  it('should create svg rects for each bar', () => {
    component.ngOnInit();
    const svg = (fixture.nativeElement as HTMLElement).querySelector('svg') as SVGElement;
    const rects = svg.querySelectorAll('rect');
    expect(rects).toHaveLength(4);
    expect(rects[0].getAttribute('style')).toContain('fill: steelblue');
    expect(rects[0].getAttribute('y')).toBe('29.54545454545454');
    expect(rects[0].getAttribute('height')).toBe('20.45454545454546');
    expect(rects[3].getAttribute('height')).toBe('0');
  });

  it('should create svg x and y axis ticks', () => {
    component.ngOnInit();
    const svg = (fixture.nativeElement as HTMLElement).querySelector('svg') as SVGElement;
    const ticks = svg.querySelectorAll('.tick text');
    expect(ticks).toHaveLength(6);
    // x axis
    expect(ticks[0].textContent).toBe('1');
    expect(ticks[1].textContent).toBe('2');
    expect(ticks[2].textContent).toBe('3');
    // y axis
    expect(ticks[3].textContent).toBe('0');
    expect(ticks[4].textContent).toBe('200');
    expect(ticks[5].textContent).toBe('400');
  });

  it('should use log scale for Y axis when logScaleY is true', () => {
    component.histogram = new NumberHistogram(
      [1, 2, 3],
      [1, 2, 3, 4],
      'small values',
      'large values',
      1,
      3,
      false,
      true
    );
    component.ngOnInit();
    expect(component.scaleYAxis.domain()[0]).toBe(1);
  });

  it('should handle bars with zero value', () => {
    component.histogram = new NumberHistogram(
      [0, 2, 3],
      [1, 2, 3, 4],
      'small values',
      'large values',
      1,
      3,
      false,
      false
    );
    fixture.detectChanges();
    const svg = (fixture.nativeElement as HTMLElement).querySelector('svg') as SVGElement;
    const rects = svg.querySelectorAll('rect');
    expect(rects[0].getAttribute('y')).toBe('50');
    expect(rects[0].getAttribute('height')).toBe('0');
  });

  it('should return xLabels if defined', () => {
    component.xLabels = [10, 20, 30];
    expect(component.xLabelsWithDefaultValue()).toStrictEqual([10, 20, 30]);
  });

  it('should return bins.slice(0, -1) if bins.length < 10 and xLabels is undefined', () => {
    component.xLabels = undefined;
    component.histogram = new NumberHistogram(
      [1, 2, 3],
      [1, 2, 3, 4],
      'small values',
      'large values',
      1,
      3,
      false,
      false
    );
    expect(component.xLabelsWithDefaultValue()).toStrictEqual([1, 2, 3]);
  });

  it('should return d3.ticks for bins.length >= 10 and logScaleX is false', () => {
    component.xLabels = undefined;
    component.histogram = new NumberHistogram(
      Array(10).fill(1) as number[],
      [0, 1, 2, 3, 4, 5, 6, 7, 8],
      'small values',
      'large values',
      0,
      10,
      false,
      false
    );
    const result = component.xLabelsWithDefaultValue();
    expect(result).toHaveLength(8);
    expect(result).toStrictEqual([0, 1, 2, 3, 4, 5, 6, 7]);
  });

  it('should return log scale ticks for bins.length >= 10 and logScaleX is true', () => {
    component.xLabels = undefined;
    component.histogram = new NumberHistogram(
      Array(10).fill(1) as number[],
      [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11],
      'small values',
      'large values',
      1,
      10,
      true, // logScaleX
      false
    );
    expect(component.xLabelsWithDefaultValue()).toStrictEqual([1, 10]);
  });

  it('should use bins[1] as domainMin if bins[0] is 0 for logScaleX', () => {
    component.xLabels = undefined;
    component.histogram = new NumberHistogram(
      Array(10).fill(1) as number[],
      [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11],
      'small values',
      'large values',
      0,
      10,
      true,
      false
    );
    expect(component.xLabelsWithDefaultValue()).toStrictEqual([1, 10]);
  });

  it('should return if bins are more than 10 and logScaleX is false', () => {
    component.xLabels = undefined;
    component.histogram = new NumberHistogram(
      Array(10).fill(1) as number[],
      [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
      'small values',
      'large values',
      0,
      20,
      false, // logScaleX
      false
    );
    expect(component.xLabelsWithDefaultValue()).toStrictEqual([0, 2, 4, 6, 8, 10, 12, 14]);
  });

  it('should calculate percentages on the left side of the marker', () => {
    component.histogram = new NumberHistogram(
      [
        3765247,
        3918870,
        3751174,
        3656219,
        3454114,
        3182165,
        2995438,
        2784151,
        2514213,
        2432587,
        2301329,
        2138563,
        2122635,
        1853306,
        1756723,
        1644419,
        1518522,
        1407111,
        1313372,
        1187499,
        1159685,
        1054256,
        983408,
        884363,
        848126,
        808438,
        706420,
        747526,
        636016,
        591762,
        579828,
        528944,
        567640,
        503320,
        481138,
        442560,
        430389,
        354681,
        389647,
        305479,
        351804,
        330166,
        326853,
        255288,
        274990,
        262451,
        228895,
        235079,
        127340,
        188912,
        167699,
        207746,
        188879,
        95162,
        80528,
        120271,
        87088,
        49173,
        36772,
        45636,
        61186,
        63557,
        34026,
        34782,
        40070,
        23490,
        46638,
        37593,
        24236,
        9899,
        38700,
        19099,
        6846,
        4260,
        14355,
        262,
        4889,
        2931,
        1326,
        10542,
        3419,
        4571,
        7968,
        0,
        12390,
        0,
        30915,
        0,
        3640,
        0,
        0,
        0,
        5470,
        0,
        0,
        0,
        0,
        0,
        11809,
        7419
      ],
      [
        0.0,
        0.05,
        0.1,
        0.15000000000000002,
        0.2,
        0.25,
        0.30000000000000004,
        0.35000000000000003,
        0.4,
        0.45,
        0.5,
        0.55,
        0.6000000000000001,
        0.65,
        0.7000000000000001,
        0.75,
        0.8,
        0.8500000000000001,
        0.9,
        0.9500000000000001,
        1.0,
        1.05,
        1.1,
        1.1500000000000001,
        1.2000000000000002,
        1.25,
        1.3,
        1.35,
        1.4000000000000001,
        1.4500000000000002,
        1.5,
        1.55,
        1.6,
        1.6500000000000001,
        1.7000000000000002,
        1.75,
        1.8,
        1.85,
        1.9000000000000001,
        1.9500000000000002,
        2.0,
        2.0500000000000003,
        2.1,
        2.15,
        2.2,
        2.25,
        2.3000000000000003,
        2.35,
        2.4000000000000004,
        2.45,
        2.5,
        2.5500000000000003,
        2.6,
        2.6500000000000004,
        2.7,
        2.75,
        2.8000000000000003,
        2.85,
        2.9000000000000004,
        2.95,
        3.0,
        3.0500000000000003,
        3.1,
        3.1500000000000004,
        3.2,
        3.25,
        3.3000000000000003,
        3.35,
        3.4000000000000004,
        3.45,
        3.5,
        3.5500000000000003,
        3.6,
        3.6500000000000004,
        3.7,
        3.75,
        3.8000000000000003,
        3.85,
        3.9000000000000004,
        3.95,
        4.0,
        4.05,
        4.1000000000000005,
        4.15,
        4.2,
        4.25,
        4.3,
        4.3500000000000005,
        4.4,
        4.45,
        4.5,
        4.55,
        4.6000000000000005,
        4.65,
        4.7,
        4.75,
        4.800000000000001,
        4.8500000000000005,
        4.9,
        4.95,
        5.0
      ],
      'less pathogenic',
      'more pathogenic',
      0.000246197207718,
      4.99964404664,
      false,
      false
    );
    component.scoreValues = [1.109279583175];
    fixture.detectChanges();
    expect(component.scorePercentages).toStrictEqual({ 1.109279583175: 77.5629975258748 });
  });
});
