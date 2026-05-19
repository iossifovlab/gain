import {
  AnnotatorConfig,
  ResourcePage,
  Resource,
  AttributeData,
  AttributePage,
  ResourceAnnotatorConfigs,
  ResourceAnnotator,
} from './annotator';

describe('annotator null-guard factory methods', () => {
  it('should return undefined from AnnotatorConfig.fromJson when called with null', () => {
    expect(AnnotatorConfig.fromJson(null)).toBeUndefined();
  });

  it('should return undefined from ResourcePage.fromJson when called with null', () => {
    expect(ResourcePage.fromJson(null)).toBeUndefined();
  });

  it('should return undefined from Resource.fromJsonArray when called with null', () => {
    expect(Resource.fromJsonArray(null)).toBeUndefined();
  });

  it('should return undefined from Resource.fromJson when called with null', () => {
    expect(Resource.fromJson(null)).toBeUndefined();
  });

  it('should return undefined from AttributeData.fromJsonArray when called with null', () => {
    expect(AttributeData.fromJsonArray(null)).toBeUndefined();
  });

  it('should return undefined from AttributeData.fromJson when called with null', () => {
    expect(AttributeData.fromJson(null)).toBeUndefined();
  });

  it('should return undefined from AttributePage.fromJson when called with null', () => {
    expect(AttributePage.fromJson(null)).toBeUndefined();
  });

  it('should return undefined from ResourceAnnotatorConfigs.fromJson when called with null', () => {
    expect(ResourceAnnotatorConfigs.fromJson(null)).toBeUndefined();
  });

  it('should return undefined from ResourceAnnotator.fromJsonArray when called with null', () => {
    expect(ResourceAnnotator.fromJsonArray(null)).toBeUndefined();
  });

  it('should return undefined from ResourceAnnotator.fromJson when called with null', () => {
    expect(ResourceAnnotator.fromJson(null)).toBeUndefined();
  });

  it('should map non-null array in ResourceAnnotator.fromJsonArray', () => {
    // eslint-disable-next-line camelcase
    const result = ResourceAnnotator.fromJsonArray([{ annotator_type: 'effect_annotator', gene_models: 'hg19' }]);
    expect(result).toHaveLength(1);
    expect(result[0].annotatorType).toBe('effect_annotator');
  });
});
