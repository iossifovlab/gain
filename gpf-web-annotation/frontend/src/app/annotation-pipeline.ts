export class PipelineInfo {
  public constructor(
    public attributesCount: number,
    public annotatorsCount: number,
    public annotatables: string[],
    public geneLists: string[],
  ) {}

  public static fromJson(json: object): PipelineInfo {
    if (!json) {
      return undefined;
    }

    return new PipelineInfo(
      json['attributes_count'] as number,
      json['annotators_count'] as number,
      json['annotatables'] as string[],
      json['gene_lists'] as string[],
    );
  }
}