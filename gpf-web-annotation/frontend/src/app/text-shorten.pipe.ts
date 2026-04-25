import { Pipe, PipeTransform } from '@angular/core';

@Pipe({
  name: 'textShorten'
})
export class TextShortenPipe implements PipeTransform {
  public transform(value: string, maxSymbols: number = 20): string {
    return value.length > maxSymbols ? value.slice(0, maxSymbols) + '...' : value;
  }
}
