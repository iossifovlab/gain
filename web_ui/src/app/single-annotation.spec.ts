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

  it('should convert map value to Map object for object type', () => {
    const jsonValue = { MTHFR: 'missense', ABC: 'nonsense' };
    const result = Result.fromJson({ value: jsonValue, histogram: null }, 'object');
    expect(result.value).toStrictEqual(new Map<string, string>([['MTHFR', 'missense'], ['ABC', 'nonsense']]));
  });

  it('should store string array value of type list', () => {
    const arr = ['chr14:21391397:C:T:6.57e-06', 'chr14:21451170:A:G:6.57e-06'];
    const result = Result.fromJson({ value: arr, histogram: null }, 'list');
    expect(result.value).toStrictEqual(['chr14:21391397:C:T:6.57e-06', 'chr14:21451170:A:G:6.57e-06']);
  });

  it('should store number array value of type list', () => {
    const arr = [1.2, 3.4, 5.6];
    const result = Result.fromJson({ value: arr, histogram: null }, 'list');
    expect(result.value).toStrictEqual([1.2, 3.4, 5.6]);
  });

  it('should convert value of type boolean to string', () => {
    const result = Result.fromJson({ value: true, histogram: null }, 'bool');
    expect(result.value).toBe('true');
  });

  it('should return null value for bool type when value is null', () => {
    const result = Result.fromJson({ value: null, histogram: null }, 'bool');
    expect(result.value).toBeNull();
  });

  it('should convert annotatable dict value to Map', () => {
    const result = Result.fromJson({ value: { chrom: 'chr1', pos: 100 }, histogram: null }, 'annotatable');
    expect(result.value).toStrictEqual(new Map<string, string | number>([['chrom', 'chr1'], ['pos', 100]]));
  });

  it('should store array as-is for annotatable type with array value', () => {
    const result = Result.fromJson({ value: ['a', 'b'], histogram: null }, 'annotatable');
    expect(result.value).toStrictEqual(['a', 'b']);
  });

  it('should convert value of type int to number', () => {
    const result = Result.fromJson({ value: 42, histogram: null }, 'int');
    expect(result.value).toBe(42);
  });

  it('should convert value of type float to number', () => {
    const result = Result.fromJson({ value: 42.5, histogram: null }, 'float');
    expect(result.value).toBe(42.5);
  });

  it('should store string value', () => {
    const result = Result.fromJson({ value: 'unknown', histogram: null }, 'str');
    expect(result.value).toBe('unknown');
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
