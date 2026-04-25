import { TextShortenPipe } from './text-shorten.pipe';

describe('TextShortenPipe', () => {
  it('create an instance', () => {
    const pipe = new TextShortenPipe();
    expect(pipe).toBeTruthy();
  });

  it('should cut string if length is more than 5 and replace it with ellipsis', () => {
    const pipe = new TextShortenPipe();
    expect(pipe.transform('too long string', 5)).toBe('too l...');
  });

  it('should cut string if length is more than default and replace it with ellipsis', () => {
    const pipe = new TextShortenPipe();
    expect(pipe.transform('too long string, need to be cut')).toBe('too long string, nee...');
  });

  it('should return original string if length is less than the max symbols', () => {
    const pipe = new TextShortenPipe();
    expect(pipe.transform('short string')).toBe('short string');
  });
});
