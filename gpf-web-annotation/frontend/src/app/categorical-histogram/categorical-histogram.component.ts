import { Component, ElementRef, Input, OnInit, ViewChild } from '@angular/core';
import { CategoricalHistogram } from '../single-annotation';
import { CommonModule } from '@angular/common';
import * as d3 from 'd3';
import { cloneDeep } from 'lodash';

@Component({
  selector: 'app-categorical-histogram',
  imports: [CommonModule],
  templateUrl: './categorical-histogram.component.html',
  styleUrl: './categorical-histogram.component.css'
})
export class CategoricalHistogramComponent implements OnInit {
  @Input() public histogram: CategoricalHistogram;
  @Input() public scoreValues: string[] = [];

  @ViewChild('histogramContainer', {static: true}) public histogramContainer: ElementRef;

  // Values used as histogram bars
  public values: {name: string, value: number}[] = [];
  // Omitted values that are combined in one custom bar
  public otherValueNames: string[] = [];

  private barCount: number;
  // private readonly defaultBarCount = 20;
  private readonly defaultBarCount = 5;
  public labelRotation = 0;

  private svg: d3.Selection<SVGElement, unknown, null, undefined>;


  private categoricalValueMax = 1000;

  public xScale: d3.ScaleBand<string>;
  public scaleXAxis: d3.ScaleOrdinal<string, number, never>;
  public scaleYAxis: d3.ScaleLinear<number, number, never>
                      | d3.ScaleLogarithmic<number, number, never>;

  public ngOnInit(): void {
    this.calculateBarCount();

    this.values = cloneDeep(this.histogram.values);
    this.formatValues();
    this.labelRotation = this.histogram.labelRotation;

    this.drawHistogram();
  }

  private calculateBarCount(): void {
    this.barCount = this.histogram.values.length;
    if (this.histogram.displayedValuesCount) {
      this.barCount = this.histogram.displayedValuesCount;
    } else if (this.histogram.displayedValuesPercent) {
      this.barCount = Math.floor(this.histogram.values.length / 100 * this.histogram.displayedValuesPercent);
    } else {
      this.barCount = this.defaultBarCount;
    }
  }

  // Sort and combine other values
  private formatValues(): void {
    this.values.sort((a, b) => {
      if (this.histogram.valueOrder?.length) {
        return this.histogram.valueOrder.indexOf(a.name) - this.histogram.valueOrder.indexOf(b.name);
      }
      if (a.value < b.value) {
        return 1;
      }
      return -1;
    });

    if (this.barCount < this.values.length) {
      const otherValues = this.values
        .splice(this.barCount, this.values.length);
      this.otherValueNames = otherValues.map(v => v.name);
      const otherSum = otherValues.reduce((acc, v) => acc + v.value, 0);
      this.values.push({name: 'Other values', value: otherSum});
    }
  }

  private drawHistogram(): void {
    d3.select(this.histogramContainer.nativeElement).selectAll('g').remove();
    d3.select(this.histogramContainer.nativeElement).selectAll('rect').remove();

    const width = 450.0;
    const height = 50;

    const svg = d3.select(
      this.histogramContainer.nativeElement
    ) as d3.Selection<SVGElement, unknown, null, undefined>;

    this.xScale = d3.scaleBand()
      .padding(0.1)
      .domain(this.values.map(v => v.name))
      .range([0, width]);

    this.scaleYAxis = d3.scaleLinear();
    let domainStart = 0;
    if (this.histogram.logScaleY) {
      this.scaleYAxis = d3.scaleLog();
      domainStart = 1;
    }
    this.scaleYAxis.range([height, 0]).domain([domainStart, d3.max(this.values.map(v => v.value))]);

    this.drawXAxis(svg, width, height);

    const leftAxis = d3.axisLeft(this.scaleYAxis);
    let yAxisTicks = this.scaleYAxis.ticks(3).filter((tick) => Number.isInteger(tick));
    // Force usage of 4 ticks even if d3 creates more
    if (yAxisTicks.length > 3) {
      yAxisTicks = [
        yAxisTicks[0],
        yAxisTicks[Math.floor(yAxisTicks.length / 3)],
        yAxisTicks[Math.floor(yAxisTicks.length / 3 * 2)],
        yAxisTicks[yAxisTicks.length - 1]
      ];
    }
    leftAxis.tickValues(yAxisTicks).tickFormat(d3.format('d'));

    svg.append('g')
      .call(leftAxis);
    svg.selectAll('bar')
      .data(this.values)
      .enter().append('rect')
      .style('fill', 'steelblue')
      .attr('x', (v: { name: string, value: number }) => this.xScale(v.name))
      .attr('width', this.xScale.bandwidth())
      .attr('y', (v: { name: string, value: number }) => v.value === 0 ? height : this.scaleYAxis(v.value))
      .attr('height', (v: { name: string, value: number }) =>
        v.value === 0 || v.value === undefined ? 0 : height - this.scaleYAxis(v.value))
      .attr('id', (v: { name: string, value: number }) => v.name);

    this.svg = svg;
  }

  private drawXAxis(
    svg: d3.Selection<SVGElement, unknown, null, undefined>,
    width: number,
    height: number,
  ): void {
    const axisX: number[] = [0];
    const axisVals: string[] = [''];

    this.values.forEach(value => {
      const leftX = this.xScale(value.name) + this.xScale.bandwidth() / 2;
      axisX.push(leftX);
      if (value.name === 'Other values') {
        axisVals.push(value.name + ` (${this.otherValueNames.length})`);
      } else {
        axisVals.push(value.name);
      }
    });

    axisX.push(width);
    axisVals.push('');
    this.scaleXAxis = d3.scaleOrdinal(axisVals, axisX);
    svg.append('g')
      .attr('transform', `translate(0,${height})`)
      .call(
        d3.axisBottom(this.scaleXAxis)
      ).style('font-size', '12px');


    this.rotateLabels(svg);
    this.addLabelsTitle(svg);
  }

  private rotateLabels(svg: d3.Selection<SVGElement, unknown, null, undefined>): void {
    let calculatedRotation = this.labelRotation;
    calculatedRotation %= 360;
    if (calculatedRotation !== 0 && calculatedRotation !== 90) {
      calculatedRotation = 360 - this.labelRotation; // Backend framework rotates labels in reverse
    }
    let anchorRotation = 'end';

    if (calculatedRotation === 0 || calculatedRotation === 180) {
      anchorRotation = 'center';
    } else if (calculatedRotation > 0 && calculatedRotation < 180) {
      anchorRotation = 'start';
    }
    svg.selectAll('text')
      .style('text-anchor', anchorRotation)
      .attr('transform-origin', '0 9%')
      .attr('transform', `rotate(${calculatedRotation})`);
  }

  private addLabelsTitle(svg: d3.Selection<SVGElement, unknown, null, undefined>): void {
    this.values.forEach(value => {
      svg.selectAll('text').filter(
        (d: unknown) => {
          const label = d as string;
          return label === value.name || label.includes('Other values');
        }
      ).each((d: unknown, i, labels) => {
        let labelText = d as string;
        const maxLabelLen = 20;
        if (labelText.length > maxLabelLen) {
          labelText = labelText.slice(0, maxLabelLen).concat('...');
        }
        // eslint-disable-next-line no-invalid-this
        d3.select(labels[i]).text(labelText);

        // add hover text on each label
        if (value.name === 'Other values') {
          // eslint-disable-next-line no-invalid-this
          d3.select(labels[i]).append('title').text(value.name + ` (${this.otherValueNames.length})`);
        } else {
          // eslint-disable-next-line no-invalid-this
          d3.select(labels[i]).append('title').text(value.name);
        }
      });
    });
  }

  public getCoordinate(score: string): number {
    const xAxisValues = this.scaleXAxis.domain();
    const fullOtherValues = xAxisValues.find(v => v.includes('Other values'));
    return this.otherValueNames.includes(score) ?
      this.scaleXAxis(fullOtherValues) : this.scaleXAxis(score);
  }
}
