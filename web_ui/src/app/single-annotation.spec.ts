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
});
