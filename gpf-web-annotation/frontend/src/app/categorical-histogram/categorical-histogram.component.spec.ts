import { ComponentFixture, TestBed } from '@angular/core/testing';

import { CategoricalHistogramComponent } from './categorical-histogram.component';
import { CategoricalHistogram } from '../single-annotation';

describe('CategoricalHistogramComponent', () => {
  let component: CategoricalHistogramComponent;
  let fixture: ComponentFixture<CategoricalHistogramComponent>;

  beforeEach(async() => {
    await TestBed.configureTestingModule({
      imports: [CategoricalHistogramComponent]
    }).compileComponents();

    fixture = TestBed.createComponent(CategoricalHistogramComponent);
    component = fixture.componentInstance;

    component.histogram = new CategoricalHistogram(
      [
        { name: 'A', value: 0 },
        { name: 'B', value: 10 },
        { name: 'C', value: 20 },
        { name: 'D', value: 30 },
        { name: 'E', value: 40 },
      ],
      ['A', 'B', 'C', 'D', 'E'],
      'largeValues',
      'smallValues',
      false,
      0
    );
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should initialize component properties', () => {
    component.ngOnInit();
    expect(component.labelRotation).toBe(0);
    expect(component.values).toStrictEqual([
      { name: 'A', value: 0 },
      { name: 'B', value: 10 },
      { name: 'C', value: 20 },
      { name: 'D', value: 30 },
      { name: 'E', value: 40 },
    ]);
  });

  it('should draw histogram', () => {
    component.ngOnInit();

    expect(component.xScale.domain()).toStrictEqual(['A', 'B', 'C', 'D', 'E']);
    expect(component.xScale.bandwidth()).toBe(79.41176470588235);
    expect(component.scaleYAxis.domain()).toStrictEqual([0, 40]);
    expect(component.scaleYAxis.range()).toStrictEqual([50, 0]);
    expect(component.scaleXAxis.domain()).toStrictEqual(['', 'A', 'B', 'C', 'D', 'E']);
    expect(component.scaleXAxis.range()).toStrictEqual([
      0,
      48.5294117647059,
      136.76470588235293,
      225,
      313.235294117647,
      401.4705882352941,
      450
    ]);
  });

  it('should check svg of histogram', () => {
    component.ngOnInit();

    const svg = (fixture.nativeElement as HTMLElement).querySelector('svg') as SVGElement;
    expect(svg.querySelectorAll('rect')).toHaveLength(5);
    expect(svg.querySelectorAll('g')).toHaveLength(14);
    expect(svg.querySelectorAll('line')).toHaveLength(10);
    expect(svg.querySelectorAll('text')).toHaveLength(10);
    expect(svg.querySelectorAll('text')[1].innerHTML).toBe('A<title>A</title>');
    expect(svg.querySelectorAll('text')[1].style.getPropertyValue('text-anchor')).toBe('center');
    expect(svg.querySelectorAll('text')[1].getAttribute('transform')).toBe('rotate(0)');
  });

  it('should draw histogram with log scale y', () => {
    component.histogram = new CategoricalHistogram(
      [
        { name: 'A', value: 0 },
        { name: 'B', value: 10 },
        { name: 'C', value: 20 },
        { name: 'D', value: 30 },
        { name: 'E', value: 40 },
      ],
      ['A', 'B', 'C', 'D', 'E'],
      'largeValues',
      'smallValues',
      true,
      0
    );
    component.ngOnInit();

    expect(component.xScale.domain()).toStrictEqual(['A', 'B', 'C', 'D', 'E']);
    expect(component.xScale.bandwidth()).toBe(79.41176470588235);
    expect(component.scaleYAxis.domain()).toStrictEqual([1, 40]);
    expect(component.scaleYAxis.range()).toStrictEqual([50, 0]);
    expect(component.scaleXAxis.domain()).toStrictEqual(['', 'A', 'B', 'C', 'D', 'E']);
    expect(component.scaleXAxis.range()).toStrictEqual([
      0,
      48.5294117647059,
      136.76470588235293,
      225,
      313.235294117647,
      401.4705882352941,
      450
    ]);
  });

  it('should check svg of histogram with label rotation 80', () => {
    component.histogram = new CategoricalHistogram(
      [
        { name: 'A', value: 0 },
        { name: 'B', value: 10 },
        { name: 'C', value: 20 },
        { name: 'D', value: 30 },
        { name: 'E', value: 40 },
      ],
      ['A', 'B', 'C', 'D', 'E'],
      'largeValues',
      'smallValues',
      false,
      80
    );
    component.ngOnInit();

    const svg = (fixture.nativeElement as HTMLElement).querySelector('svg') as SVGElement;
    expect(svg.querySelectorAll('text')[1].style.getPropertyValue('text-anchor')).toBe('end');
    expect(svg.querySelectorAll('text')[1].getAttribute('transform')).toBe('rotate(280)');
  });

  it('should check svg of histogram with label rotation 250', () => {
    component.histogram = new CategoricalHistogram(
      [
        { name: 'A', value: 0 },
        { name: 'B', value: 10 },
        { name: 'C', value: 20 },
        { name: 'D', value: 30 },
        { name: 'E', value: 40 },
      ],
      ['A', 'B', 'C', 'D', 'E'],
      'largeValues',
      'smallValues',
      false,
      250
    );
    component.ngOnInit();

    const svg = (fixture.nativeElement as HTMLElement).querySelector('svg') as SVGElement;
    expect(svg.querySelectorAll('text')[1].style.getPropertyValue('text-anchor')).toBe('start');
    expect(svg.querySelectorAll('text')[1].getAttribute('transform')).toBe('rotate(110)');
  });

  it('should init values in correct order', () => {
    component.histogram = new CategoricalHistogram(
      [
        {name: 'A', value: 10},
        {name: 'B', value: 20},
        {name: 'C', value: 30},
      ],
      ['B', 'C', 'A'],
      'large value descriptions',
      'small value descriptions',
      true,
      0
    );

    component.ngOnInit();
    expect(component.values[0].name).toBe('B');
    expect(component.values[1].name).toBe('C');
    expect(component.values[2].name).toBe('A');
  });

  it('should init values with only a certain number of them displayed', () => {
    component.histogram = new CategoricalHistogram(
      [
        {name: 'A', value: 10},
        {name: 'B', value: 20},
        {name: 'C', value: 30},
        {name: 'D', value: 40},
        {name: 'E', value: 50},
      ],
      ['A', 'B', 'C', 'D', 'E'],
      'large value descriptions',
      'small value descriptions',
      true,
      0,
      2,
    );

    component.ngOnInit();
    expect(component.values[0].name).toBe('A');
    expect(component.values[1].name).toBe('B');
    expect(component.values[2].name).toBe('Other values');
    expect(component.values[2].value).toBe(120);
    expect(component.xScale.domain()).toStrictEqual(['A', 'B', 'Other values']);
    expect(component.scaleXAxis.domain()).toStrictEqual(['', 'A', 'B', 'Other values (3)']);
  });

  it('should init values with only a percent fraction of them displayed', () => {
    component.histogram = new CategoricalHistogram(
      [
        {name: 'A', value: 10},
        {name: 'B', value: 20},
        {name: 'C', value: 30},
        {name: 'D', value: 40},
        {name: 'E', value: 50},
      ],
      ['A', 'B', 'C', 'D', 'E'],
      'large value descriptions',
      'small value descriptions',
      true,
      0,
      null,
      60,
    );
    fixture.detectChanges();
    component.ngOnInit();
    expect(component.values[0].name).toBe('A');
    expect(component.values[1].name).toBe('B');
    expect(component.values[2].name).toBe('C');
    expect(component.values[3].name).toBe('Other values');
    expect(component.values[3].value).toBe(90);
    expect(component.xScale.domain()).toStrictEqual(['A', 'B', 'C', 'Other values']);
    expect(component.scaleXAxis.domain()).toStrictEqual(['', 'A', 'B', 'C', 'Other values (2)']);
  });

  it('should shorten long labels', () => {
    component.histogram = new CategoricalHistogram(
      [
        {name: 'A', value: 10},
        {name: 'criteria_provided|_multiple_submitters|_no_conflicts', value: 20},
        {name: 'C', value: 30},
      ],
      ['A', 'criteria_provided|_multiple_submitters|_no_conflicts', 'C'],
      'large value descriptions',
      'small value descriptions',
      true,
      0,
      null,
      null,
    );

    component.ngOnInit();
    expect(component.values[1].name).toBe('criteria_provided|_multiple_submitters|_no_conflicts');
    const svg = (fixture.nativeElement as HTMLElement).querySelector('svg') as SVGElement;
    expect(svg.querySelectorAll('text')[2].innerHTML).toBe(
      'criteria_provided|_m...<title>criteria_provided|_multiple_submitters|_no_conflicts</title>'
    );
  });

  it('should display red mark on histogram and single score value', () => {
    component.scoreValues = ['Pathogenic'];
    fixture.detectChanges();
    const svg = (fixture.nativeElement as HTMLElement).querySelector('svg') as SVGElement;
    const redLine = svg.querySelector('.single-score-marker');
    expect(redLine).toBeTruthy();
    expect(redLine.getAttribute('x1')).toBe('450');
    expect(redLine.getAttribute('x2')).toBe('450');
    expect(redLine.getAttribute('y1')).toBe('48.75');
    expect(redLine.getAttribute('y2')).toBe('-5');
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

  it('should get correct coordinate for x when the value is not in axis x domain', () => {
    component.ngOnInit();
    expect(component.getCoordinate('abc')).toBe(450);
  });

  it('should get correct coordinate for x', () => {
    component.scoreValues = ['C', 'D', 'E'];
    component.ngOnInit();
    expect(component.getCoordinate('C')).toBe(225);

    component.histogram = new CategoricalHistogram(
      [
        {name: 'A', value: 10},
        {name: 'B', value: 20},
        {name: 'C', value: 30},
        {name: 'D', value: 40},
        {name: 'E', value: 50},
      ],
      ['A', 'B', 'C', 'D', 'E'],
      'large value descriptions',
      'small value descriptions',
      true,
      0,
      2,
    );
    component.ngOnInit();

    expect(component.getCoordinate('D')).toBe(370.16129032258067);
    expect(component.getCoordinate('E')).toBe(370.16129032258067);
  });

  it('should get correct coordinate for x when score value is on other values bar', () => {
    component.histogram = new CategoricalHistogram(
      [
        {name: 'A', value: 10},
        {name: 'B', value: 20},
        {name: 'C', value: 30},
        {name: 'D', value: 40},
        {name: 'E', value: 50},
      ],
      ['A', 'B', 'C', 'D', 'E'],
      'large value descriptions',
      'small value descriptions',
      true,
      0,
      2,
    );
    component.ngOnInit();

    component.scoreValues = ['D', 'E'];
    expect(component.getCoordinate('D')).toBe(370.16129032258067);
    expect(component.getCoordinate('E')).toBe(370.16129032258067);
  });
});
