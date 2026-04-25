import { Component, ElementRef, Input, OnInit, ViewChild } from '@angular/core';
import { NumberHistogram } from '../single-annotation';
import * as d3 from 'd3';
import { CommonModule } from '@angular/common';
import { FormatResultValuePipe } from '../format-result-value.pipe';

interface BinBar {
  index: number;
  bin: number;
  bar: number;
}

@Component({
  selector: 'app-number-histogram',
  imports: [CommonModule, FormatResultValuePipe],
  providers: [FormatResultValuePipe],
  templateUrl: './number-histogram.component.html',
  styleUrl: './number-histogram.component.css'
})
export class NumberHistogramComponent implements OnInit {
  @Input() public histogram: NumberHistogram = null;
  @Input() public scoreValues: number[] = [];
  public scorePercentages: Record<number, number> = {};


  @ViewChild('histogramContainer', {static: true}) public histogramContainer: ElementRef;

  public xLabels: Array<number>;
  public largeValuesDesc: string;
  public smallValuesDesc: string;

  public xScale: d3.ScaleBand<string>;

  private svg: d3.Selection<SVGElement, unknown, null, undefined>;


  public scaleXAxis: d3.ScaleThreshold<number, number, never>;
  public scaleYAxis: d3.ScaleLinear<number, number, never>
                     | d3.ScaleLogarithmic<number, number, never>;

  public ngOnInit(): void {
    this.scoreValues.forEach(v => this.calculatePercentage(v));
    this.drawHistogram();
  }

  public xLabelsWithDefaultValue(): number[] {
    if (this.xLabels === undefined) {
      if (this.histogram.bins.length < 10) {
        return this.histogram.bins.slice(0, -1);
      } else {
        if (!this.histogram.logScaleX) {
          return d3.ticks(this.histogram.bins[0], this.histogram.bins[this.histogram.bins.length - 1], 5);
        }
        const domainMin = this.histogram.bins[0] === 0.0 ? this.histogram.bins[1] : this.histogram.bins[0];
        const domainMax = this.histogram.bins[this.histogram.bins.length - 1];

        const magnitudeMin = Math.log10(domainMin);
        const magnitudeMax = Math.log10(domainMax);
        const count = Math.min(10, Math.floor(Math.abs(magnitudeMax - magnitudeMin)));

        return d3.scaleLog().domain([domainMin, domainMax]).ticks(count);
      }
    }
    return this.xLabels;
  }

  private drawHistogram(): void {
    const barsBinsArray: BinBar[] = [];
    for (let i = 0; i < this.histogram.bars.length; i++) {
      barsBinsArray[i] = {
        index: i,
        bin: this.histogram.bins[i],
        bar: this.histogram.bars[i]
      };
    }

    const width = 450.0;
    const height = 50;

    const svg = d3.select(
      this.histogramContainer.nativeElement
    ) as d3.Selection<SVGElement, unknown, null, undefined>;

    this.xScale = d3.scaleBand()
      .padding(0.1)
      .domain(Array.from(this.histogram.bars.keys()).map(x => x.toString()))
      .range([0, width]);

    this.scaleYAxis = d3.scaleLinear();
    let domainStart = 0;
    if (this.histogram.logScaleY) {
      this.scaleYAxis = d3.scaleLog();
      domainStart = 1;
    }
    this.scaleYAxis.range([height, 0]).domain([domainStart, d3.max(this.histogram.bars)]);

    this.drawXAxis(svg, width, height);

    const leftAxis = d3.axisLeft(this.scaleYAxis);
    const yAxisTicks = this.scaleYAxis.ticks(3).filter(tick => Number.isInteger(tick));
    leftAxis.tickValues(yAxisTicks).tickFormat(d3.format('d'));

    svg.append('g')
      .call(leftAxis);
    svg.selectAll('bar')
      .data(barsBinsArray)
      .enter().append('rect')
      .style('fill', 'steelblue')
      .attr('x', (d: BinBar) => this.xScale(d.index.toString()))
      .attr('width', this.xScale.bandwidth())
      .attr('y', (d: BinBar) => d.bar === 0 ? height : this.scaleYAxis(d.bar))
      .attr('height', (d: BinBar) => d.bar === 0 || d.bar === undefined ? 0 : height - this.scaleYAxis(d.bar));
    this.svg = svg;
  }

  private drawXAxis(
    svg: d3.Selection<SVGElement, unknown, null, undefined>,
    width: number,
    height: number
  ): void {
    const axisX = [0];
    const axisVals = [];

    for (let i = 0; i < this.histogram.bins.length - 1; i++) {
      const leftX = this.xScale(i.toString()) - this.xScale.step() * this.xScale.paddingOuter() / 2;

      axisX.push(leftX);
      axisVals.push(this.histogram.bins[i]);
    }
    axisX.push(width);
    axisVals.push(this.histogram.bins[this.histogram.bins.length - 1]);

    this.scaleXAxis = d3.scaleThreshold().range(axisX).domain(axisVals);

    svg.append('g')
      .attr('transform', `translate(0,${height})`)
      .call(
        d3.axisBottom(this.scaleXAxis)
          .tickValues(this.xLabelsWithDefaultValue())
          .tickFormat((_, i) => this.xLabelsWithDefaultValue()[i].toString())
      ).style('font-size', '12px');
  }

  private calculatePercentage(score: number): void {
    const barsTotalSum = d3.sum(this.histogram.bars);
    const index = this.histogram.bins
      .findIndex((b, i) => b === score || b < score && score < this.histogram.bins[i + 1]);

    const count = d3.sum(this.histogram.bars.slice(0, index));
    this.scorePercentages[score] = count / barsTotalSum * 100;
  }
}
