import {
  Resource,
  AnnotatorDetails,
  Result,
  NumberHistogram,
  CategoricalHistogram,
  AnnotatableHistory,
} from './single-annotation';

describe('Resource', () => {
  it('should return undefined when fromJsonArray is called with null', () => {
    expect(Resource.fromJsonArray(null)).toBeUndefined();
  });

  it('should return undefined when fromJson is called with null', () => {
    expect(Resource.fromJson(null)).toBeUndefined();
  });
});

describe('AnnotatorDetails', () => {
  it('should return undefined when fromJson is called with null', () => {
    expect(AnnotatorDetails.fromJson(null)).toBeUndefined();
  });
});

describe('Result', () => {
  it('should return undefined when fromJson is called with null', () => {
    expect(Result.fromJson(null, 'str')).toBeUndefined();
  });

  it('should convert annotatable value to string', () => {
    const result = Result.fromJson({ value: 'chr1:100', histogram: null }, 'annotatable');
    expect(result.value).toBe('chr1:100');
  });

  it('should store array as-is for object type with array value', () => {
    const arr = ['missense', 'synonymous'];
    const result = Result.fromJson({ value: arr, histogram: null }, 'object');
    expect(result.value).toStrictEqual(['missense', 'synonymous']);
  });
});

describe('NumberHistogram', () => {
  it('should return undefined when fromJson is called with null', () => {
    expect(NumberHistogram.fromJson(null)).toBeUndefined();
  });
});

describe('CategoricalHistogram', () => {
  it('should return undefined when fromJson is called with null', () => {
    expect(CategoricalHistogram.fromJson(null)).toBeUndefined();
  });
});

describe('AnnotatableHistory', () => {
  it('should return undefined when fromJsonArray is called with null', () => {
    expect(AnnotatableHistory.fromJsonArray(null)).toBeUndefined();
  });

  it('should return undefined when fromJson is called with null', () => {
    expect(AnnotatableHistory.fromJson(null)).toBeUndefined();
  });

  it('should parse id, allele and note from JSON', () => {
    const result = AnnotatableHistory.fromJson({ id: 7, allele: 'chr1 100 A T', note: 'BRCA1 review' });
    expect(result).toStrictEqual(new AnnotatableHistory(7, 'chr1 100 A T', 'BRCA1 review'));
  });

  it('should default note to empty string when absent', () => {
    const result = AnnotatableHistory.fromJson({ id: 3, allele: 'chr2 500 G C' });
    expect(result.note).toBe('');
  });

  it('should parse an array of history entries', () => {
    const results = AnnotatableHistory.fromJsonArray([
      { id: 1, allele: 'chr1 100 A T', note: 'first' },
      { id: 2, allele: 'chr2 200 G C', note: '' },
    ]);
    expect(results).toStrictEqual([
      new AnnotatableHistory(1, 'chr1 100 A T', 'first'),
      new AnnotatableHistory(2, 'chr2 200 G C', ''),
    ]);
  });
});
