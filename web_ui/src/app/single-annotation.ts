export class Annotatable {
  public constructor(
    public chromosome: string,
    public position: number,
    public reference: string,
    public alternative: string,
    public variantType: string,
    public positionStart: number,
    public positionEnd: number,
  ) {}

  public static fromJson(json: object): Annotatable {
    if (!json) {
      return undefined;
    }

    return new Annotatable(
      json['chrom'] as string,
      json['pos'] as number,
      json['ref'] as string,
      json['alt'] as string,
      json['type'] as string,
      json['pos_begin'] as number,
      json['pos_end'] as number,
    );
  }
}

export class Annotator {
  public constructor(
    public details: AnnotatorDetails,
    public attributes: Attribute[],
  ) {}

  public static fromJsonArray(jsonArray: object[]): Annotator[] {
    if (!jsonArray) {
      return undefined;
    }
    return jsonArray.map((json) => Annotator.fromJson(json));
  }

  public static fromJson(json: object): Annotator {
    if (!json) {
      return undefined;
    }

    return new Annotator(
      AnnotatorDetails.fromJson(json['details'] as object),
      Attribute.fromJsonArray(json['attributes'] as object[]),
    );
  }
}

export class Resource {
  public constructor(
    public resourceId: string,
    public resourceUrl: string,
  ) {}

  public static fromJsonArray(jsonArray: object[]): Resource[] {
    if (!jsonArray) {
      return undefined;
    }

    return jsonArray.map(json => Resource.fromJson(json));
  }

  public static fromJson(json: object): Resource {
    if (!json) {
      return undefined;
    }

    return new Resource(
      json['resource_id'] as string,
      json['resource_url'] as string,
    );
  }
}

export class AnnotatorDetails {
  public constructor(
    public name: string,
    public description: string,
    public resources: Resource[]
  ) {}

  public static fromJson(json: object): AnnotatorDetails {
    if (!json) {
      return undefined;
    }

    return new AnnotatorDetails(
      json['name'] as string,
      json['description'] as string,
      Resource.fromJsonArray(json['resources'] as { resourceId: string, resourceUrl: string }[])
    );
  }
}

export type ValueType = string | number | Map<string, string | number> | string[] | number[];

export class Result {
  public constructor(
    public value: ValueType,
    public histogramLink: string,
  ) {}

  public static fromJson(
    json: { histogram: string, value: number | string | boolean | string[] | object },
    type: string
  ): Result {
    if (!json) {
      return undefined;
    }

    let resultValue: ValueType = null;

    if (['int', 'float'].includes(type)) {
      resultValue = json['value'] as number;
    }

    if (type === 'str') {
      resultValue = json['value'] as string;
    }

    if (type === 'annotatable') {
      if (Array.isArray(json['value'])) {
        resultValue = json['value'] as string[];
      } else if (json['value'] !== null && typeof json['value'] === 'object') {
        resultValue = new Map<string, string | number>(Object.entries(json['value']));
      } else {
        resultValue = json['value'] as string;
      }
    }

    if (type === 'bool' && json['value'] !== null) {
      resultValue = (json['value'] as boolean).toString();
    }

    if (type === 'object') {
      if (Array.isArray(json['value'])) {
        resultValue = json['value'] as string[];
      } else if (json['value'] !== null && typeof json['value'] === 'object') {
        resultValue = new Map<string, string | number>(Object.entries(json['value']));
      }
    }

    if (type === 'list' && Array.isArray(json['value'])) {
      resultValue = json['value'];
    }

    return new Result(
      resultValue,
      json['histogram'],
    );
  }
}

export class Attribute {
  public constructor(
    public name: string,
    public description: string,
    public source: string,
    public aggregator: string | null,
    public result: Result,
    // true: the value belongs on the score histogram (no aggregation, or a
    // domain-preserving aggregator); false: the aggregated output is not a
    // score in that domain, so the histogram is hidden.
    public preservesDomain: boolean,
  ) {}

  public static fromJsonArray(jsonArray: object[]): Attribute[] {
    if (!jsonArray) {
      return undefined;
    }
    return jsonArray.map((json) => Attribute.fromJson(json));
  }

  public static fromJson(json: object): Attribute {
    if (!json) {
      return undefined;
    }

    return new Attribute(
      json['name'] as string,
      json['description'] as string,
      json['source'] as string,
      (json['aggregator'] as string) ?? null,
      Result.fromJson(
        json['result'] as { histogram: string; value: string | number | boolean; },
        json['type'] as string
      ),
      // The backend still sends null for non-aggregated attributes; treat it as
      // true since the single value belongs on the histogram. TODO: the backend
      // should be updated to send only true/false.
      (json['preserves_domain'] as boolean | null) !== false,
    );
  }
}

export class NumberHistogram {
  public constructor(
    public readonly bars: number[],
    public readonly bins: number[],
    public readonly smallValuesDesc: string,
    public readonly largeValuesDesc: string,
    public readonly rangeMin: number,
    public readonly rangeMax: number,
    public readonly logScaleX: boolean,
    public readonly logScaleY: boolean,
  ) {
    if (bins.length === (bars.length + 1)) {
      bars.push(0);
    }
  }

  public static fromJson(json: object): NumberHistogram {
    if (!json) {
      return undefined;
    }

    return new NumberHistogram(
      json['bars'] as number[],
      json['bins'] as number[],
      json['small_values_desc'] as string,
      json['large_values_desc'] as string,
      /* eslint-disable @typescript-eslint/no-unsafe-member-access */
      json['config']['view_range']['min'] as number,
      json['config']['view_range']['max'] as number,
      json['config']['x_log_scale'] as boolean,
      json['config']['y_log_scale'] as boolean,
      /* eslint-enable */
    );
  }
}

export class CategoricalHistogram {
  public constructor(
    public readonly values: {name: string, value: number}[],
    public readonly valueOrder: string[],
    public readonly smallValuesDesc: string,
    public readonly largeValuesDesc: string,
    public readonly logScaleY: boolean,
    public readonly labelRotation: number,
    public readonly displayedValuesCount: number = null,
    public readonly displayedValuesPercent: number = null,
  ) { }

  public static fromJson(json: object): CategoricalHistogram {
    if (!json) {
      return undefined;
    }

    const values: {name: string, value: number}[] = [];
    Object.keys(json['values'] as object).forEach(key => {
      // eslint-disable-next-line @typescript-eslint/no-unsafe-assignment, @typescript-eslint/no-unsafe-member-access
      values.push({name: key, value: json['values'][key]});
    });

    return new CategoricalHistogram(
      values,
      /* eslint-disable @typescript-eslint/no-unsafe-member-access */
      json['config']['value_order'] as string[],
      json['small_values_desc'] as string,
      json['large_values_desc'] as string,
      json['config']['y_log_scale'] as boolean,
      json['config']['label_rotation'] as number,
      /* eslint-enable */
    );
  }
}

export class SingleAnnotationReport {
  public constructor(
    public annotatable: Annotatable,
    public annotators: Annotator[],
  ) {}

  public static fromJson(json: object): SingleAnnotationReport {
    if (!json) {
      return undefined;
    }

    return new SingleAnnotationReport(
      Annotatable.fromJson(json['annotatable'] as object),
      Annotator.fromJsonArray(json['annotators'] as object[]),
    );
  }
}

export class AnnotatableHistory {
  public constructor(
    public id: number,
    public name: string,
    public note: string,
  ) {}

  public static fromJsonArray(jsonArray: object[]): AnnotatableHistory[] {
    if (!jsonArray) {
      return undefined;
    }
    return jsonArray.map((json) => AnnotatableHistory.fromJson(json));
  }


  public static fromJson(json: object): AnnotatableHistory {
    if (!json) {
      return undefined;
    }

    return new AnnotatableHistory(
      json['id'] as number,
      json['allele'] as string,
      (json['note'] as string) ?? '',
    );
  }
}
